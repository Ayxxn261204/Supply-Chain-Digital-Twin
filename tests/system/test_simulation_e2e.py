"""End-to-end system tests for the supply chain simulation.

These tests validate that the entire simulation works correctly from start to finish,
including all module interactions, data flow, and realistic outputs.
"""
import pytest
import yaml
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))


@pytest.fixture
def minimal_system_config():
    """Create minimal configuration for system testing."""
    return {
        'simulation': {
            'duration_days': 7,
            'time_step_minutes': 60,  # 1 hour steps for faster testing
            'start_date': '2024-06-01'  # Summer
        },
        'weather': {
            'state_probabilities': {
                'clear': 0.7,
                'light_rain': 0.15,
                'rain': 0.10,
                'heavy_rain': 0.04,
                'fog': 0.01
            },
            'average_duration_hours': {
                'clear': 12,
                'light_rain': 4,
                'rain': 6,
                'heavy_rain': 3,
                'fog': 4
            }
        },
        'traffic': {
            'weather_speed_reduction': {
                'clear': 1.0,
                'light_rain': 0.9,
                'rain': 0.8,
                'heavy_rain': 0.6,
                'fog': 0.7
            },
            'jam_density_per_lane': {
                'motorway': 150,
                'primary': 100,
                'secondary': 80,
                'residential': 60
            },
            'capacity_per_lane': {
                'motorway': 50,
                'primary': 35,
                'secondary': 28,
                'residential': 20
            },
            'base_density_fraction': {
                'motorway': 0.3,
                'primary': 0.35,
                'secondary': 0.4,
                'residential': 0.45
            },
            'time_of_day_multipliers': [
                0.3, 0.3, 0.3, 0.3, 0.4, 0.6,  # 0-5am: Low traffic
                0.9, 1.2, 1.5, 1.3, 1.0, 0.9,  # 6-11am: Morning rush
                0.8, 0.8, 0.9, 1.0, 1.1, 1.4,  # 12-5pm: Afternoon
                1.5, 1.3, 1.0, 0.8, 0.5, 0.4   # 6-11pm: Evening rush, night
            ],
            'night_speed_reduction': 0.95,
            'weather_density_increase': {
                'clear': 1.0,
                'light_rain': 1.1,
                'rain': 1.2,
                'heavy_rain': 1.3,
                'fog': 1.4
            }
        },
        'disruptions': {
            'accidents': {
                'base_rate_per_million_vkm': {
                    'motorway': 0.001,
                    'trunk': 0.002,
                    'primary': 0.003,
                    'secondary': 0.004,
                    'tertiary': 0.005,
                    'residential': 0.001
                },
                'time_multipliers': {
                    'day': 1.0,
                    'evening': 1.3,
                    'night': 1.8
                },
                'weather_multipliers': {
                    'clear': 1.0,
                    'light_rain': 1.5,
                    'rain': 2.5,
                    'heavy_rain': 4.0,
                    'fog': 3.5
                },
                'severity_distribution': {
                    'minor': 0.70,
                    'moderate': 0.25,
                    'severe': 0.05
                },
                'duration_mean': 45,
                'duration_std': 20,
                'duration_min': 15,
                'duration_max': 180
            }
        }
    }


@pytest.mark.system
class TestSimulationSystemE2E:
    """End-to-end system tests for the entire simulation."""
    
    @pytest.mark.slow
    def test_simulation_basic_structure(self, minimal_system_config):
        """Test that simulation components can be instantiated without errors."""
        from src.simulation.environment_models import WeatherModel
        from src.simulation.network.traffic_model import TrafficModel
        from src.simulation.environment_models import DisruptionModel
        
        # Instantiate all core models
        try:
            weather = WeatherModel(minimal_system_config)
            traffic = TrafficModel(minimal_system_config)
            disruption = DisruptionModel(minimal_system_config)
            
            assert weather is not None
            assert traffic is not None
            assert disruption is not None
            
            # Verify they have expected attributes
            assert hasattr(weather, 'current_state')
            assert hasattr(traffic, 'current_weather')
            assert hasattr(disruption, 'base_rates')
            
        except Exception as e:
            pytest.fail(f"Failed to instantiate simulation components: {e}")
    
    @pytest.mark.slow
    def test_models_interact_without_errors(self, minimal_system_config):
        """Test that models can interact with each other."""
        from src.simulation.environment_models import WeatherModel
        from src.simulation.network.traffic_model import TrafficModel
        from unittest.mock import Mock
        
        weather = WeatherModel(minimal_system_config)
        traffic = TrafficModel(minimal_system_config)
        
        # Simulate one update cycle
        try:
            # Weather update
            weather_events = weather.update(current_time=60.0, month=6, hour=1.0)
            assert isinstance(weather_events, list)
            
            # Traffic receives weather
            traffic.set_weather(weather.current_state)
            assert traffic.current_weather == weather.current_state
            
            # Traffic updates segment
            mock_segment = Mock()
            mock_segment.road_type = 'primary'
            mock_segment.speed_limit_kmh = 60.0
            mock_segment.lanes = 2
            
            traffic.update_segment(mock_segment, 60.0)
            
            # Segment should have traffic attributes set
            assert hasattr(mock_segment, 'current_traffic_density')
            assert hasattr(mock_segment, 'current_speed_kmh')
            
        except Exception as e:
            pytest.fail(f"Model interaction failed: {e}")
    
    @pytest.mark.slow
    def test_metrics_stay_within_bounds(self, minimal_system_config):
        """Test that simulation metrics stay within realistic bounds over time."""
        from src.simulation.environment_models import WeatherModel
        from src.simulation.network.traffic_model import TrafficModel
        from unittest.mock import Mock
        
        weather = WeatherModel(minimal_system_config)
        traffic = TrafficModel(minimal_system_config)
        
        # Simulate 7 days (168 hours)
        current_time = 0.0
        time_step = 60.0  # 1 hour
        duration = 7 * 24 * 60  # 7 days in minutes
        
        temp_values = []
        humidity_values = []
        speed_values = []
        
        mock_segment = Mock()
        mock_segment.road_type = 'primary'
        mock_segment.speed_limit_kmh = 60.0
        mock_segment.lanes = 2
        
        try:
            while current_time < duration:
                # Update weather
                month = 6  # June
                hour = (current_time / 60) % 24
                
                weather.update(current_time, month, hour)
                temp = weather.get_temperature(month, hour)
                humidity = weather.get_humidity(month, hour)
                
                # Update traffic
                traffic.set_weather(weather.current_state)
                traffic.update_segment(mock_segment, current_time)
                speed = mock_segment.current_speed_kmh
                
                # Collect metrics
                temp_values.append(temp)
                humidity_values.append(humidity)
                speed_values.append(speed)
                
                # Validate bounds
                assert -10 <= temp <= 50, f"Temperature out of bounds: {temp}"
                assert 0 <= humidity <= 100, f"Humidity out of bounds: {humidity}"
                assert 0 <= speed <= mock_segment.speed_limit_kmh, \
                    f"Speed exceeds limit: {speed} > {mock_segment.speed_limit_kmh}"
                
                current_time += time_step
                
            # Check we collected data for 7 days
            assert len(temp_values) == 168, f"Expected 168 hours of data, got {len(temp_values)}"
            
            # Check realistic averages
            avg_temp = sum(temp_values) / len(temp_values)
            avg_humidity = sum(humidity_values) / len(humidity_values)
            
            # June should have reasonable temperatures
            assert 20 < avg_temp < 40, f"Average temp unrealistic for June: {avg_temp}°C"
            assert 30 < avg_humidity < 90, f"Average humidity unrealistic: {avg_humidity}%"
            
        except AssertionError:
            raise
        except Exception as e:
            pytest.fail(f"Simulation failed during execution: {e}")
