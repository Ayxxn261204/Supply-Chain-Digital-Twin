"""Unit tests for TrafficModel (LTM — Linked Traffic Model)."""

import pytest
from unittest.mock import Mock
from src.simulation.network.traffic_model import TrafficModel


@pytest.fixture
def traffic_config():
    return {
        'simulation': {'start_time': '2024-07-15 08:00:00'},
        'traffic': {
            'weather_speed_reduction': {
                'clear': 1.0, 'light_rain': 0.9, 'rain': 0.8,
                'heavy_rain': 0.6, 'fog': 0.7,
            },
            'weather_density_increase': {
                'clear': 1.0, 'rain': 1.2, 'heavy_rain': 1.3, 'fog': 1.4,
            },
            'jam_density_per_lane': {
                'motorway': 120, 'trunk': 110, 'primary': 100,
                'secondary': 90, 'tertiary': 80, 'residential': 60,
            },
            'base_density_fraction': {
                'motorway': 0.15, 'trunk': 0.12, 'primary': 0.10,
                'secondary': 0.08, 'tertiary': 0.06, 'residential': 0.05,
            },
            'night_speed_reduction': 0.90,
        },
    }


def _seg(road_type='primary', speed_limit=60.0, lanes=2, density=20.0):
    s = Mock()
    s.road_type = road_type
    s.speed_limit_kmh = speed_limit
    s.lanes = lanes
    s.current_traffic_density = density
    s.zone_type = 'RESIDENTIAL'
    s.zone_multiplier = 1.0
    s.segment_id = 'test_seg'
    s.is_blocked = False
    return s


@pytest.mark.unit
class TestTrafficModel:

    def test_model_initialization(self, traffic_config):
        model = TrafficModel(traffic_config)
        assert hasattr(model, 'config')
        assert model.current_weather == 'clear'
        assert hasattr(model, 'jam_density_per_lane')

    def test_greenshields_model_at_zero_density(self, traffic_config):
        model = TrafficModel(traffic_config)
        seg = _seg(density=0.0)
        speed = model.get_current_speed(seg, 720.0)
        assert speed > 0
        assert speed <= seg.speed_limit_kmh

    def test_greenshields_model_speed_degrades_with_density(self, traffic_config):
        model = TrafficModel(traffic_config)
        model.set_environment('clear', 7)
        low  = _seg(density=10.0)
        med  = _seg(density=50.0)
        high = _seg(density=90.0)
        s_low  = model.get_current_speed(low,  720.0)
        s_med  = model.get_current_speed(med,  720.0)
        s_high = model.get_current_speed(high, 720.0)
        assert s_low >= s_med >= s_high

    def test_weather_reduces_speed(self, traffic_config):
        model = TrafficModel(traffic_config)
        seg = _seg(density=20.0)
        model.set_environment('clear', 7)
        s_clear = model.get_current_speed(seg, 720.0)
        model.set_environment('rain', 7)
        s_rain = model.get_current_speed(seg, 720.0)
        model.set_environment('heavy_rain', 7)
        s_heavy = model.get_current_speed(seg, 720.0)
        assert s_clear >= s_rain >= s_heavy

    def test_fog_reduces_speed(self, traffic_config):
        model = TrafficModel(traffic_config)
        seg = _seg(density=20.0)
        model.set_environment('clear', 7)
        s_clear = model.get_current_speed(seg, 720.0)
        model.set_environment('fog', 7)
        s_fog = model.get_current_speed(seg, 720.0)
        assert s_fog < s_clear

    def test_traffic_density_varies_by_time_of_day(self, traffic_config):
        model = TrafficModel(traffic_config)
        seg = _seg()
        d_midnight = model.get_traffic_density(seg, 0.0)
        d_rush     = model.get_traffic_density(seg, 9 * 60)  # 9 AM
        assert d_rush >= d_midnight

    def test_update_segment_sets_density_and_speed(self, traffic_config):
        """update_all_segments sets density and speed on segments."""
        model = TrafficModel(traffic_config)
        seg = _seg()

        class MockNetwork:
            segments = {'s': seg}
            def get_upstream_neighbors(self, _): return []

        model.update_all_segments(MockNetwork(), 720.0)
        assert seg.current_traffic_density >= 0
        assert seg.current_speed_kmh >= 0

    def test_speed_never_exceeds_limit(self, traffic_config):
        model = TrafficModel(traffic_config)
        model.set_environment('clear', 7)
        seg = _seg(speed_limit=40.0, density=5.0)
        speed = model.get_current_speed(seg, 720.0)
        assert speed <= seg.speed_limit_kmh
