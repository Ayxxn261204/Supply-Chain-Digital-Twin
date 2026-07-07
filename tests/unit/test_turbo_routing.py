"""
Unit and property-based tests for the Turbo Routing Rework.

Covers:
  5.1  base_weight = length_km / speed_limit_kmh (normal segment)
  5.2  base_weight = length_km / MIN_SPEED_FALLBACK_KMH (zero speed)
  5.3  update_smart(accidents=[]) does NOT iterate road_network.segments
  5.4  get_fuel_consumption_liters call count = 0 during Stage 1 A*
  5.5  get_fuel_consumption_liters call count = len(winner_path) during Stage 2
  5.6  reroute_interval = max(configured, timestep) for representative pairs
  5.7  _on_accident_alert resets last_reroute_check when segment is on route
  5.8  Property: base_weight > 0 for any length_km > 0, speed_limit_kmh >= 0
  5.9  Property: get_traffic_density() in [0.1, jam_density × 0.98]
  5.10 Property: find_path() returns dict with all required keys
"""

import pytest
import math
from unittest.mock import Mock, patch, MagicMock, call

# Graceful hypothesis import — property tests are skipped if not installed
try:
    from hypothesis import given, settings, assume
    from hypothesis import strategies as st
    _hypothesis_available = True
except ImportError:
    _hypothesis_available = False
    # Provide no-op stubs so the module loads cleanly
    def given(*args, **kwargs):
        return lambda f: pytest.mark.skip(reason="hypothesis not installed")(f)
    def settings(*args, **kwargs):
        return lambda f: f
    def assume(cond):
        pass
    class st:  # type: ignore
        @staticmethod
        def floats(**kwargs): return None
        @staticmethod
        def integers(**kwargs): return None
        @staticmethod
        def sampled_from(seq): return None

from src.simulation.network.road_segment import RoadSegment, MIN_SPEED_FALLBACK_KMH
from src.simulation.network.traffic_model import TrafficModel
from src.simulation.network.router import Router


# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------

def _make_segment(length_km=2.0, speed_limit_kmh=50.0, road_type='primary',
                  lanes=2, zone_type='RESIDENTIAL'):
    """Create a minimal RoadSegment for testing."""
    return RoadSegment(
        segment_id='test_seg',
        start_node=1,
        end_node=2,
        length_km=length_km,
        road_type=road_type,
        speed_limit_kmh=speed_limit_kmh,
        lanes=lanes,
        osm_data={},
        zone_type=zone_type,
        zone_multiplier=1.0,
    )


def _make_traffic_config(ripple_min_pressure=1.0, bfs_max_depth_highway=7,
                         bfs_max_depth_city=3):
    return {
        'simulation': {'start_date': '2024-01-01'},
        'traffic': {
            'jam_density_per_lane': {
                'motorway': 120, 'trunk': 110, 'primary': 100,
                'secondary': 90, 'tertiary': 80, 'residential': 60,
            },
            'base_density_fraction': {
                'motorway': 0.15, 'trunk': 0.12, 'primary': 0.10,
                'secondary': 0.08, 'tertiary': 0.06, 'residential': 0.05,
            },
            'weather_speed_reduction': {'clear': 1.0, 'rain': 0.85},
            'weather_density_increase': {'clear': 1.0, 'rain': 1.2},
            'night_speed_reduction': 0.90,
            'ripple_min_pressure': ripple_min_pressure,
            'bfs_max_depth_highway': bfs_max_depth_highway,
            'bfs_max_depth_city': bfs_max_depth_city,
        },
    }


def _make_router_config(recalculation_interval=15, time_step=5):
    return {
        'routing': {
            'recalculation_interval_minutes': recalculation_interval,
            'recalculation_threshold': 0.10,
            'cache_enabled': False,
            'cache_ttl_minutes': 30,
            'cost_weights': {
                'distance_km': 1.0,
                'time_minutes': 0.5,
                'fuel_liters': 2.0,
                'road_quality': 5.0,
            },
        },
        'simulation': {'time_step_minutes': time_step},
    }


def _make_truck_agent(configured_interval, time_step):
    """Create a minimal TruckAgent for reroute_interval tests."""
    from src.simulation.agents.truck_agent import TruckAgent

    truck = Mock()
    truck.truck_id = 'T001'
    truck.status = 'idle'
    truck.current_location = (21.0, 79.0)
    truck.current_node = 1
    truck.cargo_batches = []
    truck.current_load_kg = 0.0
    truck.truck_type = Mock()
    truck.truck_type.name = 'medium'
    truck.truck_type.capacity_kg = 3000
    truck.truck_type.maneuverability_index = 1.0

    road_network = Mock()
    router = Mock()
    config = {
        'routing': {
            'recalculation_interval_minutes': configured_interval,
            'recalculation_threshold': 0.10,
        },
        'simulation': {'time_step_minutes': time_step},
        'cargo': {},
        'logging': {},
        'sensors': {},
        'driver_model': {},
        'truck_types': {'medium': {}},
    }
    return TruckAgent(truck, road_network, router, config,
                      ai_manager=None, event_bus=None)


# ---------------------------------------------------------------------------
# Unit tests (class-based, @pytest.mark.unit)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTurboRouting:

    # 5.1 ----------------------------------------------------------------
    def test_base_weight_normal_segment(self):
        """base_weight = length_km / speed_limit_kmh for a normal segment."""
        seg = _make_segment(length_km=2.5, speed_limit_kmh=50.0)
        expected = 2.5 / 50.0
        assert seg.base_weight == pytest.approx(expected)

    # 5.2 ----------------------------------------------------------------
    def test_base_weight_zero_speed_fallback(self):
        """base_weight = length_km / MIN_SPEED_FALLBACK_KMH when speed_limit = 0."""
        seg = _make_segment(length_km=1.0, speed_limit_kmh=0.0)
        expected = 1.0 / MIN_SPEED_FALLBACK_KMH
        assert seg.base_weight == pytest.approx(expected)

    # 5.3 ----------------------------------------------------------------
    def test_update_smart_no_accidents_skips_segment_iteration(self):
        """update_smart(accidents=[]) must NOT iterate road_network.segments."""
        config = _make_traffic_config()
        model = TrafficModel(config)

        road_network = Mock()
        # Make segments a MagicMock so we can track __iter__ and .values calls
        road_network.segments = MagicMock()

        model.update_smart(road_network, [], current_time=0.0, active_accidents=[])

        road_network.segments.__iter__.assert_not_called()
        road_network.segments.values.assert_not_called()

    # 5.4 + 5.5 ----------------------------------------------------------
    def test_router_two_stage_fuel_call_count(self):
        """
        Stage 1 A* must call get_fuel_consumption_liters 0 times.
        Stage 2 refine must call it exactly once per winner-path segment (2 total).
        """
        import networkx as nx

        # Build minimal 3-node graph
        graph = nx.MultiDiGraph()
        graph.add_edge(1, 2, key=0, segment_id='s1', length=1.0)
        graph.add_edge(2, 3, key=0, segment_id='s2', length=1.0)

        seg1 = _make_segment(length_km=1.0, speed_limit_kmh=50.0)
        seg1.segment_id = 's1'
        seg2 = _make_segment(length_km=1.0, speed_limit_kmh=50.0)
        seg2.segment_id = 's2'

        road_network = Mock()
        road_network.graph = graph
        road_network.traffic_model = None  # No traffic model — uses base_weight only
        road_network.get_segment.side_effect = lambda sid: {'s1': seg1, 's2': seg2}.get(sid)
        road_network.get_segment_by_nodes.side_effect = (
            lambda u, v, k: {'s1': seg1, 's2': seg2}.get(f's{u}')
        )
        road_network.get_node_position.return_value = (21.0, 79.0)

        router = Router(road_network, _make_router_config())
        truck_cfg = {
            'capacity_kg': 3000,
            'fuel_consumption_empty_l_per_100km': 28,
            'fuel_consumption_full_l_per_100km': 38,
            'fuel_efficiency_by_speed': {'50_kmh': 1.0},
        }

        with patch.object(seg1, 'get_fuel_consumption_liters',
                          wraps=seg1.get_fuel_consumption_liters) as spy1, \
             patch.object(seg2, 'get_fuel_consumption_liters',
                          wraps=seg2.get_fuel_consumption_liters) as spy2:

            route = router.find_path(1, 3, truck_cfg, 0.5,
                                     avoid_blocked=False, current_time=0.0)

            total_fuel_calls = spy1.call_count + spy2.call_count
            # Exactly once per winner-path segment (Stage 2 only)
            assert total_fuel_calls == 2
            assert route is not None
            required_keys = {
                'nodes', 'segments', 'segment_objects',
                'total_distance_km', 'total_time_minutes',
                'total_fuel_liters', 'total_cost',
            }
            assert required_keys.issubset(set(route.keys()))

    # 5.6 ----------------------------------------------------------------
    def test_reroute_interval_adapts_to_timestep(self):
        """reroute_interval = max(configured, timestep) for representative pairs."""
        cases = [
            (15, 5,  15),   # configured > timestep → use configured
            (15, 20, 20),   # timestep > configured → snap up to timestep
            (5,  1,  5),    # configured > timestep → use configured
            (1,  12, 12),   # timestep > configured → snap up to timestep
        ]
        for configured, timestep, expected in cases:
            agent = _make_truck_agent(configured, timestep)
            assert agent.reroute_interval == expected, (
                f"configured={configured}, timestep={timestep}: "
                f"expected {expected}, got {agent.reroute_interval}"
            )

    # 5.7 ----------------------------------------------------------------
    def test_accident_alert_resets_reroute_timer(self):
        """_on_accident_alert resets last_reroute_check when segment is on remaining route."""
        agent = _make_truck_agent(15, 5)

        agent.truck.status = 'in_transit'
        agent.truck.current_route = {'segments': ['seg_A', 'seg_B', 'seg_C']}
        agent.truck.route_progress = 0
        agent.last_reroute_check = 1000.0

        agent._on_accident_alert({'segment_id': 'seg_B'})

        # After the alert, last_reroute_check must be set to -reroute_interval
        # so that (current_time - last_reroute_check) >= reroute_interval on the next tick
        assert agent.last_reroute_check == -agent.reroute_interval


# ---------------------------------------------------------------------------
# Property-based tests (standalone functions, @pytest.mark.unit + @given)
# ---------------------------------------------------------------------------

@pytest.mark.unit
@given(
    length_km=st.floats(min_value=0.001, max_value=1000.0,
                        allow_nan=False, allow_infinity=False),
    speed_limit_kmh=st.floats(min_value=0.0, max_value=200.0,
                              allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_base_weight_always_positive(length_km, speed_limit_kmh):
    """**Validates: Requirements 1.5** — base_weight > 0 for all valid inputs."""
    seg = _make_segment(length_km=length_km, speed_limit_kmh=speed_limit_kmh)
    assert seg.base_weight > 0


@pytest.mark.unit
@given(
    current_time=st.floats(min_value=0.0, max_value=10080.0,
                           allow_nan=False, allow_infinity=False),
    road_type=st.sampled_from(['motorway', 'primary', 'secondary', 'residential']),
    lanes=st.integers(min_value=1, max_value=4),
)
@settings(max_examples=200)
def test_get_traffic_density_bounded(current_time, road_type, lanes):
    """**Validates: Requirements 4.1** — get_traffic_density in [0.1, jam_density × 0.98]."""
    config = _make_traffic_config()
    model = TrafficModel(config)
    seg = _make_segment(road_type=road_type, lanes=lanes)
    density = model.get_traffic_density(seg, current_time)
    jam_density = config['traffic']['jam_density_per_lane'].get(road_type, 100) * lanes
    assert 0.1 <= density <= jam_density * 0.98


@pytest.mark.unit
@given(
    length1=st.floats(min_value=0.1, max_value=10.0,
                      allow_nan=False, allow_infinity=False),
    length2=st.floats(min_value=0.1, max_value=10.0,
                      allow_nan=False, allow_infinity=False),
    speed=st.floats(min_value=10.0, max_value=120.0,
                    allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100)
def test_find_path_returns_required_keys(length1, length2, speed):
    """**Validates: Requirements 6.5** — find_path returns dict with all required keys."""
    import networkx as nx

    graph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, segment_id='s1', length=length1)
    graph.add_edge(2, 3, key=0, segment_id='s2', length=length2)

    seg1 = _make_segment(length_km=length1, speed_limit_kmh=speed)
    seg1.segment_id = 's1'
    seg2 = _make_segment(length_km=length2, speed_limit_kmh=speed)
    seg2.segment_id = 's2'

    road_network = Mock()
    road_network.graph = graph
    road_network.traffic_model = None
    road_network.get_segment.side_effect = lambda sid: {'s1': seg1, 's2': seg2}.get(sid)
    road_network.get_segment_by_nodes.side_effect = (
        lambda u, v, k: seg1 if u == 1 else seg2
    )
    road_network.get_node_position.return_value = (21.0, 79.0)

    router = Router(road_network, _make_router_config())
    truck_cfg = {
        'capacity_kg': 3000,
        'fuel_consumption_empty_l_per_100km': 28,
        'fuel_consumption_full_l_per_100km': 38,
        'fuel_efficiency_by_speed': {'50_kmh': 1.0},
    }
    route = router.find_path(1, 3, truck_cfg, 0.5,
                             avoid_blocked=False, current_time=0.0)
    assert route is not None
    required_keys = {
        'nodes', 'segments', 'segment_objects',
        'total_distance_km', 'total_time_minutes',
        'total_fuel_liters', 'total_cost',
    }
    assert required_keys.issubset(set(route.keys()))
    assert route['total_fuel_liters'] >= 0
    assert route['total_distance_km'] >= 0
