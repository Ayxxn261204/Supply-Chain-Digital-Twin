"""
Phase 2 fix verification tests.

Covers:
  - validators.py  : broken regex string literal ($ outside string)
  - routing_env.py : season always "monsoon" → derive from start_date
  - warehouse_agent.py : inventory_model reads from config, not hardcoded fallback
"""
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config(start_date="2024-07-15"):
    return {
        "simulation": {
            "duration_days": 1,
            "time_step_minutes": 5,
            "start_date": start_date,
            "location": {
                "bounding_box": {
                    "north": 21.25, "south": 21.05,
                    "east": 79.20, "west": 79.00,
                }
            },
        },
        "routing": {
            "recalculation_interval_minutes": 5,
            "recalculation_threshold": 0.10,
            "cache_enabled": False,
            "cache_ttl_minutes": 30,
            "rl_enabled": False,
        },
        "cargo": {"target_temperature_celsius": 4.0, "initial_rsl_hours": 336.0},
        "quality_control": {"min_acceptable_rsl_hours": 72.0},
        "warehouse_operations": {"batch_size_kg": 1000},
        "warehouse": {
            "reorder_point_kg": 5000,
            "restock_amount_kg": 20000,
            "restock_lead_time_minutes": 1440,
            "inventory_management": {
                "service_level": 0.90,
                "lead_time_days_mean": 2.0,
                "lead_time_days_std": 0.3,
                "review_period_hours": 12.0,
                "order_quantity_method": "EOQ",
                "holding_cost_per_kg_per_day": 0.05,
                "ordering_cost_per_order": 50,
                "order_up_to_days": 14,
                "demand_forecast_window_days": 7,
                "demand_smoothing_alpha": 0.3,
                "min_order_quantity_kg": 5000,
                "max_order_quantity_kg": 50000,
            },
        },
        "sensors": {"gps_noise": 0.00005, "temp_noise": 0.5, "stock_noise_kg": 2.0},
        "driver_model": {
            "shift_duration_hours": 10,
            "mandatory_break_after_hours": 5,
            "break_duration_minutes": 30,
            "skill_distribution": {"experienced": 1.0},
        },
        "truck_types": {
            "medium": {
                "capacity_kg": 3000,
                "fuel_tank_liters": 150,
                "fuel_consumption_empty_l_per_100km": 28,
                "fuel_consumption_full_l_per_100km": 38,
                "fuel_efficiency_by_speed": {
                    "20_kmh": 1.25, "30_kmh": 1.15, "40_kmh": 1.05,
                    "50_kmh": 1.00, "60_kmh": 1.08, "70_kmh": 1.18, "80_kmh": 1.30,
                },
                "max_speed_kmh": 90,
                "cost_per_liter": 1.5,
                "loading_time_per_ton_minutes": 5,
                "unloading_time_per_ton_minutes": 3,
                "is_refrigerated": True,
                "insulation_factor": 0.05,
                "maneuverability_index": 1.0,
                "stop_and_go_fuel_multiplier": 1.25,
            }
        },
        "traffic": {
            "jam_density_per_lane": {
                "motorway": 120, "trunk": 110, "primary": 100,
                "secondary": 90, "tertiary": 80, "residential": 60,
            }
        },
        "ai": {
            "prediction": {"enabled": False},
            "optimization": {
                "enabled": False,
                "reward_weights": {
                    "time": 0.2, "spoilage": 2.0, "fuel": 0.05,
                    "delivery": 20.0, "rsl_bonus": 1.0,
                },
                "checkpoint_dir": "models/rl",
                "route_pool_size": 10,
            },
        },
        "weather": {
            "state_probabilities": {"clear": 1.0},
            "average_duration_hours": {"clear": 24},
        },
    }


def _make_truck_type(cfg):
    from simulation.entities import TruckType
    c = cfg["truck_types"]["medium"]
    return TruckType(
        name="medium",
        capacity_kg=c["capacity_kg"],
        fuel_tank_liters=c["fuel_tank_liters"],
        fuel_consumption_empty_l_per_100km=c["fuel_consumption_empty_l_per_100km"],
        fuel_consumption_full_l_per_100km=c["fuel_consumption_full_l_per_100km"],
        fuel_efficiency_by_speed=c["fuel_efficiency_by_speed"],
        max_speed_kmh=c["max_speed_kmh"],
        cost_per_liter=c["cost_per_liter"],
        loading_time_per_ton_minutes=c["loading_time_per_ton_minutes"],
        unloading_time_per_ton_minutes=c["unloading_time_per_ton_minutes"],
        is_refrigerated=c["is_refrigerated"],
        insulation_factor=c["insulation_factor"],
        maneuverability_index=c["maneuverability_index"],
        stop_and_go_fuel_multiplier=c["stop_and_go_fuel_multiplier"],
    )


def _make_warehouse_agent(cfg):
    from simulation.agents.warehouse_agent import WarehouseAgent
    rn = MagicMock()
    rn.get_nearest_node.return_value = 1
    router = MagicMock()
    truck_types = {"medium": _make_truck_type(cfg)}
    return WarehouseAgent(
        warehouse_id="WH001",
        location=(21.15, 79.05),
        initial_inventory_kg=10000.0,
        fleet_config=[{"type": "medium", "count": 1}],
        truck_types=truck_types,
        road_network=rn,
        router=router,
        config=cfg,
    )


# ===========================================================================
# Fix 1 — validators.py: broken regex ($ outside string)
# ===========================================================================

class TestValidateRunId:
    """validators.validate_run_id must accept valid IDs and reject invalid ones."""

    def test_valid_run_id_accepted(self):
        """A correctly formatted run_id must be returned unchanged."""
        import sys
        sys.path.insert(0, str(ROOT / "api"))
        from validators import validate_run_id
        result = validate_run_id("sim-20241215-143025")
        assert result == "sim-20241215-143025"

    def test_valid_run_id_different_date(self):
        """Another valid run_id must also pass."""
        import sys
        sys.path.insert(0, str(ROOT / "api"))
        from validators import validate_run_id
        result = validate_run_id("sim-20260330-000348")
        assert result == "sim-20260330-000348"

    def test_empty_run_id_raises_400(self):
        """Empty string must raise HTTPException 400."""
        import sys
        sys.path.insert(0, str(ROOT / "api"))
        from validators import validate_run_id
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_run_id("")
        assert exc_info.value.status_code == 400

    def test_wrong_prefix_raises_400(self):
        """run_id with wrong prefix must raise HTTPException 400."""
        import sys
        sys.path.insert(0, str(ROOT / "api"))
        from validators import validate_run_id
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_run_id("run-20241215-143025")
        assert exc_info.value.status_code == 400

    def test_too_short_date_raises_400(self):
        """run_id with 6-digit date must raise HTTPException 400."""
        import sys
        sys.path.insert(0, str(ROOT / "api"))
        from validators import validate_run_id
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_run_id("sim-241215-143025")
        assert exc_info.value.status_code == 400

    def test_trailing_garbage_raises_400(self):
        """run_id with trailing characters must raise HTTPException 400.
        This is the exact bug that was present: the $ was outside the string,
        so 'sim-20241215-143025-extra' would have matched the broken pattern."""
        import sys
        sys.path.insert(0, str(ROOT / "api"))
        from validators import validate_run_id
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_run_id("sim-20241215-143025-extra")
        assert exc_info.value.status_code == 400

    def test_none_equivalent_raises_400(self):
        """Whitespace-only string must raise HTTPException 400."""
        import sys
        sys.path.insert(0, str(ROOT / "api"))
        from validators import validate_run_id
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_run_id("   ")
        assert exc_info.value.status_code == 400


# ===========================================================================
# Fix 2 — routing_env.py: season derived from start_date
# ===========================================================================

class TestRoutingEnvSeason:
    """RoutingEnv.season must be derived from simulation.start_date, not hardcoded."""

    def _make_env(self, start_date):
        """Build a RoutingEnv with a mock road network (no OSM needed)."""
        from simulation.routing_env import RoutingEnv

        cfg = _minimal_config(start_date=start_date)

        # Mock road network with a minimal graph
        import networkx as nx
        G = nx.MultiDiGraph()
        G.add_node(1, y=21.15, x=79.05)
        G.add_node(2, y=21.16, x=79.06)
        G.add_edge(1, 2, key=0, length=1000, highway="primary", segment_id="1_2_0")

        rn = MagicMock()
        rn.graph = G
        rn.get_segment.return_value = None
        rn.get_segment_id_between.return_value = None
        rn.get_adjacent_nodes.return_value = []

        tm = MagicMock()
        tm.jam_density_per_lane = cfg["traffic"]["jam_density_per_lane"]
        tm.current_ripples = {}

        router = MagicMock()
        # Return None so _build_route_pool produces an empty pool (no OSM needed)
        router.find_path.return_value = None

        env = RoutingEnv(
            config=cfg,
            road_network=rn,
            traffic_model=tm,
            router=router,
            max_steps=10,
        )
        return env

    def test_summer_month_april(self):
        """start_date in April → season == 'summer'."""
        env = self._make_env("2024-04-15")
        assert env.season == "summer"

    def test_summer_month_march(self):
        """start_date in March → season == 'summer'."""
        env = self._make_env("2024-03-01")
        assert env.season == "summer"

    def test_summer_month_may(self):
        """start_date in May → season == 'summer'."""
        env = self._make_env("2024-05-31")
        assert env.season == "summer"

    def test_monsoon_month_july(self):
        """start_date in July → season == 'monsoon'."""
        env = self._make_env("2024-07-15")
        assert env.season == "monsoon"

    def test_monsoon_month_june(self):
        """start_date in June → season == 'monsoon'."""
        env = self._make_env("2024-06-01")
        assert env.season == "monsoon"

    def test_monsoon_month_september(self):
        """start_date in September → season == 'monsoon'."""
        env = self._make_env("2024-09-30")
        assert env.season == "monsoon"

    def test_winter_month_january(self):
        """start_date in January → season == 'winter'."""
        env = self._make_env("2024-01-15")
        assert env.season == "winter"

    def test_winter_month_december(self):
        """start_date in December → season == 'winter'."""
        env = self._make_env("2024-12-01")
        assert env.season == "winter"

    def test_winter_month_november(self):
        """start_date in November → season == 'winter'."""
        env = self._make_env("2024-11-15")
        assert env.season == "winter"

    def test_summer_doubles_spoilage_weight(self):
        """Summer season must double _w_spoilage relative to base config value."""
        env = self._make_env("2024-04-15")
        base_spoilage = _minimal_config()["ai"]["optimization"]["reward_weights"]["spoilage"]
        assert env._w_spoilage == pytest.approx(base_spoilage * 2.0)

    def test_monsoon_multiplies_time_weight(self):
        """Monsoon season must multiply _w_time by 1.5 relative to base config value."""
        env = self._make_env("2024-07-15")
        base_time = _minimal_config()["ai"]["optimization"]["reward_weights"]["time"]
        assert env._w_time == pytest.approx(base_time * 1.5)

    def test_winter_leaves_weights_unchanged(self):
        """Winter season must not modify reward weights."""
        env = self._make_env("2024-01-15")
        base = _minimal_config()["ai"]["optimization"]["reward_weights"]
        assert env._w_spoilage == pytest.approx(base["spoilage"])
        assert env._w_time == pytest.approx(base["time"])

    def test_invalid_start_date_falls_back_to_winter(self):
        """Malformed start_date must not crash — falls back to winter (month=1)."""
        env = self._make_env("not-a-date")
        assert env.season == "winter"


# ===========================================================================
# Fix 3 — warehouse_agent.py: inventory_model reads from config
# ===========================================================================

class TestWarehouseInventoryModelConfig:
    """WarehouseAgent.inventory_model must use values from config, not hardcoded defaults."""

    def test_service_level_from_config(self):
        """inventory_model.service_level must equal config value (0.90), not hardcoded 0.95."""
        cfg = _minimal_config()
        # Config sets service_level = 0.90 (different from hardcoded 0.95)
        wh = _make_warehouse_agent(cfg)
        assert wh.inventory_model.service_level == pytest.approx(0.90)

    def test_lead_time_mean_from_config(self):
        """inventory_model.lead_time_mean must equal config value converted to minutes."""
        cfg = _minimal_config()
        # Config sets lead_time_days_mean = 2.0 → 2 * 24 * 60 = 2880 minutes
        wh = _make_warehouse_agent(cfg)
        assert wh.inventory_model.lead_time_mean == pytest.approx(2.0 * 24 * 60)

    def test_review_period_from_config(self):
        """inventory_model.review_period must equal config value converted to minutes."""
        cfg = _minimal_config()
        # Config sets review_period_hours = 12.0 → 12 * 60 = 720 minutes
        wh = _make_warehouse_agent(cfg)
        assert wh.inventory_model.review_period == pytest.approx(12.0 * 60)

    def test_min_order_quantity_from_config(self):
        """inventory_model.min_order_qty must equal config value."""
        cfg = _minimal_config()
        wh = _make_warehouse_agent(cfg)
        assert wh.inventory_model.min_order_qty == pytest.approx(5000.0)

    def test_max_order_quantity_from_config(self):
        """inventory_model.max_order_qty must equal config value."""
        cfg = _minimal_config()
        wh = _make_warehouse_agent(cfg)
        assert wh.inventory_model.max_order_qty == pytest.approx(50000.0)

    def test_fallback_when_section_missing(self):
        """When inventory_management section is absent, warehouse must still initialise."""
        cfg = _minimal_config()
        # Remove the inventory_management section entirely
        del cfg["warehouse"]["inventory_management"]
        wh = _make_warehouse_agent(cfg)
        # Must not crash; fallback defaults are used
        assert wh.inventory_model is not None
        assert wh.inventory_model.service_level > 0

    def test_config_values_differ_from_hardcoded_defaults(self):
        """Verify the test config intentionally differs from the old hardcoded defaults
        so we can confirm the config path is actually being read."""
        cfg = _minimal_config()
        inv = cfg["warehouse"]["inventory_management"]
        # service_level 0.90 != hardcoded 0.95
        assert inv["service_level"] != 0.95
        # lead_time_days_mean 2.0 != hardcoded 1.0
        assert inv["lead_time_days_mean"] != 1.0
        # review_period_hours 12.0 != hardcoded 24.0
        assert inv["review_period_hours"] != 24.0
