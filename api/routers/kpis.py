"""
KPI (Key Performance Indicator) endpoints for dashboard metrics.
"""

import logging
from fastapi import APIRouter, Query, HTTPException
from typing import List, Dict, Any
from database import get_db
from validators import validate_run_id

router = APIRouter(prefix="/kpis", tags=["KPIs"])
logger = logging.getLogger(__name__)


@router.get("/summary", response_model=dict)
async def get_kpi_summary(run_id: str = Query(..., description="Simulation run ID")):
    """
    Get summary KPI metrics for dashboard cards
    
    Args:
        run_id: Simulation run ID
        
    Returns:
        KPI summary with total deliveries, active trucks, avg delivery time, etc.
    """
    # Validate run_id format
    run_id = validate_run_id(run_id)
    
    try:
        db = get_db()
        
        # Total deliveries - count delivery_complete events
        delivery_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "events")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["event_type"] == "delivery_complete")
            |> count()
        '''
        delivery_results = db.query(delivery_query)
        total_deliveries = delivery_results[0].get('_value', 0) if delivery_results else 0
        
        # Active trucks - count trucks with status in_transit
        try:
            active_trucks_query = f'''
            from(bucket: "{db.bucket}")
                |> range(start: -7d)
                |> filter(fn: (r) => r["_measurement"] == "truck_telemetry")
                |> filter(fn: (r) => r["run_id"] == "{run_id}")
                |> filter(fn: (r) => r["_field"] == "status")
                |> filter(fn: (r) => r["_value"] == "in_transit")
                |> group(columns: ["truck_id"])
                |> last()
                |> group()
                |> count()
            '''
            active_results = db.query(active_trucks_query)
            active_trucks = active_results[0].get('_value', 0) if active_results else 0
        except (ValueError, KeyError) as e:
            logger.warning(f"Active trucks query error (expected): {type(e).__name__}")
            active_trucks = 0
        except Exception as e:
            logger.error(f"UNEXPECTED active trucks query error: {type(e).__name__}: {e}")
            active_trucks = 0
        
        # Average delivery time - Calculate from timestamp differences
        # Match order_placed → delivery_complete events by order_id
        try:
            # Get all delivery_complete events with timestamps
            delivery_times_query = f'''
            from(bucket: "{db.bucket}")
                |> range(start: -7d)
                |> filter(fn: (r) => r["_measurement"] == "events")
                |> filter(fn: (r) => r["run_id"] == "{run_id}")
                |> filter(fn: (r) => r["event_type"] == "delivery_complete" or r["event_type"] == "order_placed")
                |> filter(fn: (r) => r["_field"] == "timestamp" or r["_field"] == "order_id")
                |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
                |> sort(columns: ["_time"])
            '''
            events = db.query(delivery_times_query)
            
            # Build dictionary of order_id -> (placed_time, delivery_time)
            order_times = {}
            for event in events:
                order_id = event.get('order_id')
                timestamp = event.get('timestamp')
                event_type = event.get('event_type')
                
                if order_id and timestamp:
                    if order_id not in order_times:
                        order_times[order_id] = {}
                    if event_type == 'order_placed':
                        order_times[order_id]['placed'] = timestamp
                    elif event_type == 'delivery_complete':
                        order_times[order_id]['delivered'] = timestamp
            
            # Calculate average delivery time from completed orders
            delivery_durations = []
            for order_id, times in order_times.items():
                if 'placed' in times and 'delivered' in times:
                    duration = times['delivered'] - times['placed']
                    if duration > 0:  # Sanity check
                        delivery_durations.append(duration)
            
            if delivery_durations:
                avg_delivery_time = sum(delivery_durations) / len(delivery_durations)
            else:
                # Fallback to 0 if no completed deliveries found
                avg_delivery_time = 0.0
        except Exception as e:
            # If calculation fails, fall back to 0
            logger.warning(f"Could not calculate avg delivery time: {e}")
            avg_delivery_time = 0.0
        
        # Current accidents - count active accidents by tracking starts and ends
        accident_start_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "events")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["event_type"] == "accident_start")
            |> count()
        '''
        accident_end_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "events")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["event_type"] == "accident_end")
            |> count()
        '''
        starts = db.query(accident_start_query)
        ends = db.query(accident_end_query)
        accident_starts = starts[0].get('_value', 0) if starts else 0
        accident_ends = ends[0].get('_value', 0) if ends else 0
        current_accidents = max(0, accident_starts - accident_ends)  # Active = starts - ends
        
        # Total inventory - get latest from each warehouse/retailer and sum in Python
        wh_inventory_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "warehouse_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["_field"] == "current_inventory_kg")
            |> group(columns: ["warehouse_id"])
            |> last()
        '''
        wh_results = db.query(wh_inventory_query)
        wh_inventory = sum(r.get('_value', 0) for r in wh_results)
        
        ret_inventory_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "retailer_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["_field"] == "current_inventory_kg")
            |> group(columns: ["retailer_id"])
            |> last()
        '''
        ret_results = db.query(ret_inventory_query)
        ret_inventory = sum(r.get('_value', 0) for r in ret_results)
        
        total_inventory = wh_inventory + ret_inventory
        
        return {
            "total_deliveries": int(total_deliveries),
            "active_trucks": int(active_trucks),
            "avg_delivery_time_minutes": round(avg_delivery_time, 1),
            "current_accidents": current_accidents,
            "total_inventory_kg": round(total_inventory, 1)
        }
    except (ValueError, TypeError) as e:
        # Data type conversion errors
        logger.error(f"KPI summary calculation error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Data processing error: {str(e)}")
    except (KeyError, IndexError) as e:
        # Missing data in query results
        logger.warning(f"KPI summary missing data: {type(e).__name__}: {e}")
        raise HTTPException(status_code=404, detail=f"Incomplete data for run_id {run_id}")
    except Exception as e:
        # Unexpected errors (likely database/query issues)
        logger.error(f"UNEXPECTED KPI summary error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kpis/fleet", response_model=List[dict])
async def get_fleet_status(run_id: str = Query(..., description="Simulation run ID")):
    """
    Get fleet status across all warehouses
    
    Args:
        run_id: Simulation run ID
        
    Returns:
        Fleet utilization per warehouse
    """
    try:
        db = get_db()
        
        # Get all warehouse IDs
        entities = db.get_entity_ids(run_id)
        warehouse_ids = entities['warehouse_ids']
        
        result = []
        for wh_id in warehouse_ids:
            # Get warehouse state for fleet info
            wh_state = db.get_warehouse_state(run_id, wh_id)
            
            if wh_state:
                state = wh_state[0]
                available = state.get('available_trucks', 0)
                in_transit = state.get('trucks_in_transit', 0)
                loading = state.get('trucks_loading', 0)
                total = available + in_transit + loading
                
                result.append({
                    "warehouse_id": wh_id,
                    "trucks_idle": int(available),
                    "trucks_in_transit": int(in_transit),
                    "trucks_loading": int(loading),
                    "total_fleet": int(total)
                })
        
        return result
    except (ValueError, TypeError) as e:
        # Data processing errors
        logger.error(f"Fleet status calculation error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Data processing error: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Missing data fields
        logger.warning(f"Fleet status missing data: {type(e).__name__}: {e}")
        raise HTTPException(status_code=404, detail=f"Incomplete fleet data for run_id {run_id}")
    except Exception as e:
        # Unexpected errors
        logger.error(f"UNEXPECTED fleet status error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
