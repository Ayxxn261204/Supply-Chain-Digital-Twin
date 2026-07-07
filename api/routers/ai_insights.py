"""
AI Insights API Router

Exposes live AI/ML predictions computed from the latest simulation state:
  - RSL at delivery prediction per in-transit truck (Arrhenius physics)
  - ETA prediction per in-transit truck (from truck_state route_progress)
  - Demand forecast per retailer (exponential smoothing on recent sales)
  - Next 3-hour weather forecast (from real weather dataset)
  - RL routing activity (route_changed events with reason=rl_policy)

All predictions are computed on-the-fly from InfluxDB state — no separate
AI service needed.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Dict, List, Any, Optional
import math
import logging

from database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# RSL physics (mirrors HybridRSLModel.calculate_decay_rate)
# ---------------------------------------------------------------------------

_Ea = 120000.0   # J/mol
_A  = 1.3e20
_R  = 8.314      # J/(mol·K)
_OPTIMAL_HUMIDITY = 90.0
_HUMIDITY_SENSITIVITY = 0.01

def _arrhenius_decay_rate(temp_c: float, humidity: float) -> float:
    """Return RSL decay rate (% per hour) using Arrhenius equation."""
    T_k = max(250.0, temp_c + 273.15)
    try:
        base = _A * math.exp(-_Ea / (_R * T_k))
    except OverflowError:
        base = 0.5
    hum_factor = 1.0 + abs(humidity - _OPTIMAL_HUMIDITY) * _HUMIDITY_SENSITIVITY
    rate = base * hum_factor
    return max(0.001, min(10.0, rate))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/ai/truck-predictions")
async def get_truck_ai_predictions(
    run_id: str = Query(..., description="Simulation run ID")
) -> List[Dict[str, Any]]:
    """
    Per-truck AI predictions for in-transit trucks.

    Returns for each truck:
      - predicted_rsl_at_delivery: estimated RSL% when truck arrives
      - rsl_recommendation: "ok" | "warn" | "reject"
      - estimated_eta_minutes: remaining travel time estimate
      - rl_reroutes: number of RL-policy reroutes this run
    """
    try:
        db = get_db()

        # Get latest truck state
        truck_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r["_measurement"] == "truck_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> group(columns: ["truck_id", "_field"])
            |> sort(columns: ["_time"], desc: true)
            |> limit(n: 1)
            |> group(columns: ["truck_id"])
            |> pivot(rowKey: ["truck_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        trucks = db.query(truck_query)

        # Get latest weather for temperature/humidity
        weather_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r["_measurement"] == "weather_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["_field"] == "temperature_celsius" or r["_field"] == "humidity_percent")
            |> last()
            |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        '''
        weather_records = db.query(weather_query)
        temp_c = weather_records[0].get("temperature_celsius", 28.0) if weather_records else 28.0
        humidity = weather_records[0].get("humidity_percent", 60.0) if weather_records else 60.0

        # Get RL reroute counts per truck (reason field contains "rl_strategy_*")
        rl_query = f'''
        import "strings"
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "events")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["event_type"] == "route_changed")
            |> filter(fn: (r) => r["_field"] == "reason")
            |> filter(fn: (r) => strings.containsStr(v: r["_value"], substr: "rl_strategy"))
            |> group(columns: ["truck_id"])
            |> count()
        '''
        rl_records = db.query(rl_query)
        rl_counts: Dict[str, int] = {}
        for rec in rl_records:
            tid = rec.get("truck_id")
            if tid:
                rl_counts[tid] = int(rec.get("_value", 0))

        # Compute predictions
        results = []
        decay_rate = _arrhenius_decay_rate(temp_c, humidity)

        for truck in trucks:
            truck_id = truck.get("truck_id")
            if not truck_id:
                continue

            status_code = truck.get("status_code", 0.0)
            # status_code 2.0 = in_transit (matches engine.py status_codes)
            if status_code != 2.0:
                results.append({
                    "truck_id": truck_id,
                    "status": "idle",
                    "predicted_rsl_at_delivery": None,
                    "rsl_recommendation": None,
                    "estimated_eta_minutes": None,
                    "rl_reroutes": rl_counts.get(truck_id, 0),
                })
                continue

            # Estimate remaining travel time
            # speed_kmh is in truck_telemetry, not truck_state — use a reasonable default
            # route_progress is the segment index written to truck_state
            speed = float(truck.get("speed_kmh", 40) or 40)  # fallback to 40 km/h city speed
            route_progress = float(truck.get("route_progress", 0) or 0)

            if route_progress > 0 and speed > 0:
                estimated_remaining_km = max(0.5, 8.0 * (1.0 - min(1.0, route_progress / 20.0)))
                eta_minutes = (estimated_remaining_km / max(1.0, speed)) * 60.0
            else:
                eta_minutes = 15.0  # default fallback

            # RSL prediction — cargo_rsl is now written to truck_state via get_state()
            cargo_rsl = float(truck.get("cargo_rsl", 100) or 100)
            rsl_decay_over_eta = decay_rate * (eta_minutes / 60.0)
            predicted_rsl = max(0.0, cargo_rsl - rsl_decay_over_eta)

            # Recommendation
            min_acceptable = 72.0  # hours
            total_shelf_life = 336.0  # hours
            predicted_rsl_hours = (predicted_rsl / 100.0) * total_shelf_life
            if predicted_rsl <= 0:
                recommendation = "reject"
            elif predicted_rsl_hours < (min_acceptable + 12.0):  # +12h safety margin
                recommendation = "warn"
            else:
                recommendation = "ok"

            results.append({
                "truck_id": truck_id,
                "status": "in_transit",
                "current_rsl": round(cargo_rsl, 1),
                "predicted_rsl_at_delivery": round(predicted_rsl, 1),
                "rsl_recommendation": recommendation,
                "estimated_eta_minutes": round(eta_minutes, 0),
                "rl_reroutes": rl_counts.get(truck_id, 0),
            })

        return results

    except Exception as e:
        logger.error(f"truck-predictions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ai/retailer-predictions")
async def get_retailer_ai_predictions(
    run_id: str = Query(..., description="Simulation run ID")
) -> List[Dict[str, Any]]:
    """
    Per-retailer demand forecast for next 24 hours.

    Uses exponential smoothing on recent sales events from InfluxDB.
    Returns predicted_demand_kg_24h and confidence level.
    """
    try:
        db = get_db()

        # Get recent sales events per retailer
        sales_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "events")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["event_type"] == "sale")
            |> filter(fn: (r) => r["_field"] == "quantity_kg")
            |> group(columns: ["retailer_id"])
            |> sort(columns: ["_time"])
        '''
        sales_records = db.query(sales_query)

        # Group by retailer and compute smoothed demand rate
        from collections import defaultdict
        retailer_sales: Dict[str, List[float]] = defaultdict(list)
        for rec in sales_records:
            rid = rec.get("retailer_id")
            qty = rec.get("_value")
            if rid and qty is not None:
                retailer_sales[rid].append(float(qty))

        # Get latest retailer state for current inventory
        ret_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r["_measurement"] == "retailer_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> group(columns: ["retailer_id", "_field"])
            |> sort(columns: ["_time"], desc: true)
            |> limit(n: 1)
            |> group(columns: ["retailer_id"])
            |> pivot(rowKey: ["retailer_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        retailers = db.query(ret_query)

        results = []
        alpha = 0.3  # EMA smoothing factor

        for retailer in retailers:
            rid = retailer.get("retailer_id")
            if not rid:
                continue

            sales = retailer_sales.get(rid, [])
            current_inventory = float(retailer.get("current_inventory_kg", 0) or 0)
            service_level = float(retailer.get("service_level_percent", 100) or 100)

            if len(sales) >= 3:
                # Exponential moving average
                ema = sales[0]
                for s in sales[1:]:
                    ema = alpha * s + (1 - alpha) * ema
                # Scale to 24h: assume ~2 customers/hour × 24h = 48 events
                # but we have actual event count, so scale by time
                n_sales = len(sales)
                # Rough: if we have n sales over the sim duration, extrapolate to 24h
                # Use service_level as a proxy for demand pressure
                demand_pressure = 2.0 - (service_level / 100.0)  # higher stockouts = more demand
                predicted_24h = ema * min(48, n_sales) * demand_pressure
                confidence = "high" if n_sales >= 20 else ("medium" if n_sales >= 5 else "low")
            else:
                # Not enough data — use base rate from config (75 kg/customer × 2/hr × 24hr)
                predicted_24h = 75.0 * 2 * 24 * 0.3  # conservative estimate
                confidence = "low"

            results.append({
                "retailer_id": rid,
                "current_inventory_kg": round(current_inventory, 1),
                "predicted_demand_kg_24h": round(max(0, predicted_24h), 1),
                "confidence": confidence,
                "sales_observations": len(sales),
                "service_level_pct": round(service_level, 1),
            })

        return results

    except Exception as e:
        logger.error(f"retailer-predictions error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ai/weather-forecast")
async def get_weather_forecast(
    run_id: str = Query(..., description="Simulation run ID")
) -> Dict[str, Any]:
    """
    Current weather + next 3 hours forecast from the real weather dataset.

    Uses the simulation's current_time to look ahead in the weather parquet file.
    """
    try:
        db = get_db()

        # Get current simulation time and weather
        weather_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r["_measurement"] == "weather_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["_field"] == "temperature_celsius" or r["_field"] == "humidity_percent" or r["_field"] == "timestamp")
            |> last()
            |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        '''
        weather_records = db.query(weather_query)

        if not weather_records:
            raise HTTPException(status_code=404, detail="No weather data found")

        latest = weather_records[0]
        current_sim_time = float(latest.get("timestamp", 0) or 0)
        current_temp = float(latest.get("temperature_celsius", 28.0) or 28.0)
        current_humidity = float(latest.get("humidity_percent", 60.0) or 60.0)
        current_state = latest.get("state", "clear")

        # Try to load weather dataset for forecast
        forecast_hours = []
        try:
            import sys
            from pathlib import Path
            ROOT = Path(__file__).resolve().parent.parent.parent
            sys.path.insert(0, str(ROOT))
            from src.data.loaders import WeatherDataLoader

            dataset_path = ROOT / "data" / "processed" / "weather" / "nagpur_2023.parquet"
            if dataset_path.exists():
                loader = WeatherDataLoader(str(dataset_path))
                loader.load()
                for h in range(1, 4):  # next 3 hours
                    future_time = current_sim_time + h * 60
                    data = loader.get_weather_at_time(future_time)
                    forecast_hours.append({
                        "hours_ahead": h,
                        "state": data["state"],
                        "temperature_celsius": round(data["temperature"], 1),
                        "humidity_percent": round(data["humidity"], 1),
                    })
        except Exception:
            pass  # Forecast unavailable, return current only

        return {
            "current": {
                "state": current_state,
                "temperature_celsius": round(current_temp, 1),
                "humidity_percent": round(current_humidity, 1),
                "sim_time_minutes": current_sim_time,
            },
            "forecast": forecast_hours,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"weather-forecast error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ai/rl-activity")
async def get_rl_activity(
    run_id: str = Query(..., description="Simulation run ID")
) -> Dict[str, Any]:
    """
    System-level RL routing activity for the current simulation run.

    Returns:
      - strategy_counts: how many times each strategy (balanced/speed/fuel) was chosen
      - total_reroutes: total RL-triggered reroutes
      - strategy_pct: percentage breakdown per strategy
      - heuristic_reroutes: reroutes triggered by heuristic fallback (non-RL)
    """
    try:
        db = get_db()

        # Query all route_changed events for this run
        query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "events")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["event_type"] == "route_changed")
            |> filter(fn: (r) => r["_field"] == "reason")
            |> keep(columns: ["_value", "truck_id"])
        '''
        records = db.query(query)

        strategy_counts: Dict[str, int] = {
            'balanced': 0,
            'speed': 0,
            'fuel': 0,
            'heuristic': 0,
        }

        for rec in records:
            reason = str(rec.get('_value', ''))
            if 'balanced' in reason:
                strategy_counts['balanced'] += 1
            elif 'speed' in reason:
                strategy_counts['speed'] += 1
            elif 'fuel' in reason:
                strategy_counts['fuel'] += 1
            else:
                strategy_counts['heuristic'] += 1

        rl_total = strategy_counts['balanced'] + strategy_counts['speed'] + strategy_counts['fuel']
        grand_total = rl_total + strategy_counts['heuristic']

        strategy_pct = {
            k: round(v / grand_total * 100.0, 1) if grand_total > 0 else 0.0
            for k, v in strategy_counts.items()
        }

        return {
            'strategy_counts': strategy_counts,
            'total_rl_reroutes': rl_total,
            'total_reroutes': grand_total,
            'strategy_pct': strategy_pct,
        }

    except Exception as e:
        logger.error(f"rl-activity error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
