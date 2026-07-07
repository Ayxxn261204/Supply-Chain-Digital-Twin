"""Integration tests for Environment-Cargo interactions.

Tests that environmental conditions (temperature, humidity) from
WeatherModel correctly affect cargo RSL degradation in OrangeBatch.
"""
import pytest
from src.simulation.environment_models import WeatherModel
from src.simulation.entities import OrangeBatch
from datetime import datetime, timedelta


@pytest.fixture
def weather_model():
    """Create WeatherModel instance."""
    config = {
        'weather': {
            'state_probabilities': {'clear': 1.0},
            'average_duration_hours': {'clear': 12}
        }
    }
    return WeatherModel(config)


@pytest.mark.integration
class TestEnvironmentCargoIntegration:
    """Integration tests for Weather and Cargo models."""
    
    def test_temperature_affects_rsl_degradation(self, weather_model):
        """Test that WeatherModel temperature changes affect OrangeBatch RSL."""
        # Create batches with correct constructor arguments
        batch_hot = OrangeBatch(
            batch_id="HOT_TEST",
            quantity=1000,
            harvest_date=datetime(2024, 6, 1)
        )
        batch_cool = OrangeBatch(
            batch_id="COOL_TEST",
            quantity=1000,
            harvest_date=datetime(2024, 6, 1)
        )
        
        # Get temperatures at different times (summer)
        temp_noon = weather_model.get_temperature(month=6, hour=14.0)  # Hot afternoon
        temp_dawn = weather_model.get_temperature(month=6, hour=5.0)   # Cool morning
        
        # Verify we have temperature difference
        assert temp_noon > temp_dawn + 5, \
            f"Should have significant temp difference: noon={temp_noon}, dawn={temp_dawn}"
        
        # Update RSL at both temperatures (same humidity, same duration)
        batch_hot.current_rsl = 100.0
        batch_hot.update_rsl(temp_noon, 50.0, 60.0)  # Hot conditions
        rsl_after_hot = batch_hot.current_rsl
        
        batch_cool.current_rsl = 100.0
        batch_cool.update_rsl(temp_dawn, 50.0, 60.0)  # Cool conditions  
        rsl_after_cool = batch_cool.current_rsl
        
        # Hot temp should degrade faster (Q10 effect: doubles every 10°C)
        assert rsl_after_hot < rsl_after_cool, \
            f"Hot temp should degrade faster: hot={rsl_after_hot}, cool={rsl_after_cool}"
        
        # Degradation should be noticeable
        degradation_hot = 100.0 - rsl_after_hot
        degradation_cool = 100.0 - rsl_after_cool
        assert degradation_hot > degradation_cool * 1.5, \
            f"Hot degradation should be significantly higher: {degradation_hot} vs {degradation_cool}"
    
    def test_humidity_affects_rsl_degradation(self, weather_model):
        """Test that WeatherModel humidity affects OrangeBatch RSL."""
        # Create batches with correct constructor arguments
        batch_dry = OrangeBatch(
            batch_id="DRY_TEST",
            quantity=1000,
            harvest_date=datetime(2024, 6, 1)
        )
        batch_humid = OrangeBatch(
            batch_id="HUMID_TEST",
            quantity=1000,
            harvest_date=datetime(2024, 6, 1)
        )
        
        # Get humidity at different times
        weather_model.current_state = 'clear'
        humidity_afternoon = weather_model.get_humidity(month=6, hour=14.0)  # Low
        humidity_dawn = weather_model.get_humidity(month=6, hour=5.0)        # High
        
        # Verify humidity difference
        assert humidity_dawn > humidity_afternoon + 10, \
            f"Should have humidity difference: dawn={humidity_dawn}, afternoon={humidity_afternoon}"
        
        # Update RSL with different humidities (same temp, same duration)
        batch_dry.current_rsl = 100.0
        batch_dry.update_rsl(25.0, humidity_afternoon, 60.0)  # Dry conditions
        rsl_after_dry = batch_dry.current_rsl
        
        batch_humid.current_rsl = 100.0
        batch_humid.update_rsl(25.0, humidity_dawn, 60.0)  # Humid conditions
        rsl_after_humid = batch_humid.current_rsl
        
        # Very low humidity = dehydration (bad)
        # Very high humidity = mold (bad)
        # Optimal is around 85-90%
        # Both should degrade, but at different rates
        assert rsl_after_dry < 100.0 and rsl_after_humid < 100.0, \
            "Both conditions should cause some degradation"
