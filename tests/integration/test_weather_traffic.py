"""Integration tests for Weather-Traffic interactions.

Tests that weather changes correctly propagate to traffic model
and affect speed calculations and density.
"""
import pytest
from src.simulation.environment_models import WeatherModel
from src.simulation.network.traffic_model import TrafficModel
from unittest.mock import Mock


@pytest.fixture
def weather_model():
    """Create WeatherModel instance."""
    config = {
        'weather': {
            'state_probabilities': {'clear': 0.7, 'rain': 0.3},
            'average_duration_hours': {'clear': 12, 'rain': 6}
        }
    }
    return WeatherModel(config)


@pytest.fixture
def traffic_model():
    """Create TrafficModel instance."""
    config = {
        'traffic': {
            'weather_speed_reduction': {
                'clear': 1.0,
                'rain': 0.8,
                'heavy_rain': 0.6,
                'fog': 0.7
            },
            'jam_density_per_lane': {'primary': 100},
            'capacity_per_lane': {'primary': 35},
            'base_density_fraction': {'primary': 0.35},
            'time_of_day_multipliers': [0.3] * 24,
            'night_speed_reduction': 0.95,
            'weather_density_increase': {
                'clear': 1.0,
                'rain': 1.2,
                'heavy_rain': 1.3
            }
        }
    }
    return TrafficModel(config)


@pytest.fixture
def mock_segment():
    """Create mock road segment."""
    segment = Mock()
    segment.speed_limit_kmh = 60.0
    segment.road_type = 'primary'
    segment.lanes = 2
    segment.current_traffic_density = 30.0
    return segment


@pytest.mark.integration
class TestWeatherTrafficIntegration:
    """Integration tests for Weather and Traffic models."""
    
    def test_weather_speed_reduction_cascade(self, weather_model, traffic_model, mock_segment):
        """Test that weather changes propagate to traffic speed reductions."""
        # Clear weather
        weather_model.current_state = 'clear'
        traffic_model.set_weather('clear')
        speed_clear = traffic_model.get_current_speed(mock_segment, 720.0)
        
        # Change to heavy rain
        weather_model.current_state = 'heavy_rain'
        traffic_model.set_weather('heavy_rain')
        speed_heavy_rain = traffic_model.get_current_speed(mock_segment, 720.0)
        
        # Heavy rain should significantly reduce speed (at least 30%)
        assert speed_heavy_rain < speed_clear * 0.7, \
            f"Heavy rain should reduce speed by >30%: {speed_clear} → {speed_heavy_rain}"
        
        # Verify it's due to weather reduction (0.6x factor)
        expected_ratio = 0.6
        actual_ratio = speed_heavy_rain / speed_clear
        assert abs(actual_ratio - expected_ratio) < 0.15, \
            f"Speed ratio should be ~{expected_ratio}, got {actual_ratio}"
    
    def test_weather_increases_traffic_density(self, weather_model, traffic_model, mock_segment):
        """Test that adverse weather increases cautious driving (higher density)."""
        # Clear weather density
        weather_model.current_state = 'clear'
        traffic_model.set_weather('clear')
        density_clear = traffic_model.get_traffic_density(mock_segment, 720.0)
        
        # Heavy rain density
        weather_model.current_state = 'heavy_rain'
        traffic_model.set_weather('heavy_rain')
        density_rain = traffic_model.get_traffic_density(mock_segment, 720.0)
        
        # Rain should increase density (people drive more cautiously)
        assert density_rain > density_clear, \
            f"Rain should increase density: {density_clear} → {density_rain}"
        
        # Should be roughly 1.3x increase based on config
        expected_ratio = 1.3
        actual_ratio = density_rain / density_clear
        assert 1.1 < actual_ratio < 1.5, \
            f"Density ratio should be ~{expected_ratio}, got {actual_ratio}"
