import pytest
from src.simulation.environment_models import WeatherModel

class TestWeatherModel:
    @pytest.fixture
    def config(self):
        return {
            'weather': {
                'state_probabilities': {
                    'clear': 0.5,
                    'rain': 0.5
                },
                'average_duration_hours': {
                    'clear': 1,
                    'rain': 1
                }
            }
        }

    def test_initialization(self, config):
        """Test that WeatherModel initializes with correct default state."""
        model = WeatherModel(config)
        assert model.current_state == 'clear'
        assert model.state_durations['clear'] == 60  # 1 hour = 60 mins

    def test_state_persistence(self, config):
        """Test that weather state persists for the duration."""
        model = WeatherModel(config)
        model.state_duration = 60  # Force 60 mins duration
        model.state_start_time = 0
        
        # At 30 mins (within duration), no state change should occur
        events = model.update(current_time=30)
        assert events == []  # No transition events
        assert model.current_state == 'clear'

    def test_state_transition(self, config):
        """Test that weather changes after duration expires."""
        model = WeatherModel(config)
        model.state_duration = 60
        model.state_start_time = 0
        
        # Check at 61 mins (should trigger change)
        # We can't predict the next state due to randomness, but we can check if time updated
        model.update(current_time=61)
        
        # Start time should have updated to 61
        assert model.state_start_time == 61
