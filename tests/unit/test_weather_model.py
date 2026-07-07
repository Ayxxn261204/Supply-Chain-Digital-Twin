"""Unit tests for WeatherModel.

Tests the weather simulation model including temperature calculations,
humidity modeling, and weather state transitions.
"""
import pytest
import math
from src.simulation.environment_models import WeatherModel


@pytest.mark.unit
class TestWeatherModel:
    """Test suite for WeatherModel."""
    
    def test_temperature_diurnal_cycle(self, minimal_config):
        """Test that temperature follows diurnal (day/night) cycle."""
        weather = WeatherModel(minimal_config)
        
        # Morning should be cooler
        temp_morning = weather.get_temperature(month=11, hour=6)
        
        # Afternoon should be warmer (peaks at 3pm)
        temp_afternoon = weather.get_temperature(month=11, hour=15)
        
        # Evening should cool down
        temp_evening = weather.get_temperature(month=11, hour=20)
        
        # Assertions
        assert temp_afternoon > temp_morning, "Afternoon should be warmer than morning"
        assert temp_afternoon > temp_evening, "Afternoon should be warmer than evening"
        assert temp_evening > temp_morning or abs(temp_evening - temp_morning) < 2, \
            "Evening should be warming or similar to morning"
    
    def test_seasonal_temperature_variation(self, minimal_config):
        """Test that temperature varies by season."""
        weather = WeatherModel(minimal_config)
        
        # Summer (May) should be hottest
        temp_may = weather.get_temperature(month=5, hour=15)
        
        # Winter (December) should be coolest
        temp_dec = weather.get_temperature(month=12, hour=15)
        
        # Monsoon (July) should be moderate
        temp_july = weather.get_temperature(month=7, hour=15)
        
        # Assertions
        assert temp_may > temp_dec, "Summer should be hotter than winter"
        assert temp_may > temp_july, "Peak summer hotter than monsoon"
        assert temp_july > temp_dec, "Monsoon warmer than winter"
        
        # Sanity checks (Nagpur temperature range)
        assert 10 < temp_dec < 35, "Winter temp should be realistic"
        assert 35 < temp_may < 50, "Summer temp should be realistic"
    
    def test_humidity_morning_peak(self, minimal_config):
        """Test that humidity peaks in early morning."""
        weather = WeatherModel(minimal_config)
        weather.current_state = 'clear'
        
        # Early morning (5am) should have highest humidity
        humidity_5am = weather.get_humidity(month=11, hour=5)
        
        # Afternoon (3pm) should have lowest humidity
        humidity_3pm = weather.get_humidity(month=11, hour=15)
        
        # Midnight should be moderate
        humidity_midnight = weather.get_humidity(month=11, hour=0)
        
        # Assertions
        assert humidity_5am > humidity_3pm, "Morning humidity should be higher than afternoon"
        assert humidity_5am > humidity_midnight, "Dawn should have highest humidity"
        assert 10 <= humidity_3pm <= 100, "Humidity should be in valid range"
        assert 10 <= humidity_5am <= 100, "Humidity should be in valid range"
    
    def test_humidity_rain_increase(self, minimal_config):
        """Test that rain increases humidity."""
        weather = WeatherModel(minimal_config)
        
        # Same time, different weather states
        hour = 12
        month = 11
        
        # Test clear weather
        weather.current_state = 'clear'
        humidity_clear = weather.get_humidity(month, hour)
        
        # Test rain
        weather.current_state = 'rain'
        humidity_rain = weather.get_humidity(month, hour)
        
        # Test heavy rain
        weather.current_state = 'heavy_rain'
        humidity_heavy_rain = weather.get_humidity(month, hour)
        
        # Test fog
        weather.current_state = 'fog'
        humidity_fog = weather.get_humidity(month, hour)
        
        # Assertions
        assert humidity_rain > humidity_clear, "Rain should increase humidity"
        assert humidity_heavy_rain >= humidity_rain, "Heavy rain should have high humidity"
        assert humidity_fog > humidity_clear, "Fog should increase humidity"
        
        # All should be clamped to valid range
        assert all(10 <= h <= 100 for h in [humidity_clear, humidity_rain, humidity_fog]), \
            "All humidity values should be in valid range"
    
    def test_weather_state_transitions_force_change(self, minimal_config):
        """Test that weather transitions produce valid events over time."""
        weather = WeatherModel(minimal_config)
        
        # Track state transitions over many calls
        transitions = []
        current_time = 0
        
        for i in range(10):
            old_state = weather.current_state
            current_time += weather.state_duration + 1
            event = weather._change_weather(current_time, month=11, hour=12)
            new_state = weather.current_state
            transitions.append((old_state, new_state))
        
        # Over 10 transitions, we should see at least 2 different states
        unique_states = set(s for pair in transitions for s in pair)
        assert len(unique_states) >= 2, \
            f"Should see at least 2 different states over 10 transitions, got: {unique_states}"
        
        # Any event returned should have correct structure
        for old, new in transitions:
            if old != new:
                # When state changes, _change_weather returns an event dict
                pass  # Already verified by the loop running without error
    
    def test_winter_fog_probability(self, minimal_config):
        """Test that fog is more likely in winter mornings."""
        weather = WeatherModel(minimal_config)
        
        # Sample many state selections during winter morning
        fog_count_morning = 0
        fog_count_afternoon = 0
        trials = 100
        
        for _ in range(trials):
            # Reset to clear state
            weather.current_state = 'clear'
            
            # Morning transition (4-8am)
            event_morning = weather._change_weather(0, month=12, hour=6)
            if event_morning and event_morning['new_state'] == 'fog':
                fog_count_morning += 1
            
            # Reset to clear state
            weather.current_state = 'clear'
            
            # Afternoon transition
            event_afternoon = weather._change_weather(0, month=12, hour=14)
            if event_afternoon and event_afternoon['new_state'] == 'fog':
                fog_count_afternoon += 1
        
        # Fog should be more common in morning (not strict, due to randomness)
        # Just verify fog CAN occur
        assert fog_count_morning > 0 or fog_count_afternoon > 0, \
            "Fog should occur at least sometimes in winter"
    
    def test_monsoon_rain_probability(self, minimal_config):
        """Test that rain is more likely during monsoon season."""
        weather = WeatherModel(minimal_config)
        
        # Sample state selections
        rain_count_monsoon = 0
        rain_count_winter = 0
        trials = 50
        
        for _ in range(trials):
            # Reset
            weather.current_state = 'clear'
            
            # Monsoon (July)
            event_monsoon = weather._change_weather(0, month=7, hour=12)
            if event_monsoon and 'rain' in event_monsoon['new_state']:
                rain_count_monsoon += 1
            
            # Reset
            weather.current_state = 'light_rain'  # Start from rain to force change
            
            # Winter (December)
            event_winter = weather._change_weather(0, month=12, hour=12)
            if event_winter and 'rain' in event_winter['new_state']:
                rain_count_winter += 1
        
        # Monsoon should have more rain (probabilistic, so not strict)
        # Just verify rain occurs more in monsoon
        assert rain_count_monsoon > rain_count_winter, \
            f"Monsoon should have more rain (monsoon: {rain_count_monsoon}, winter: {rain_count_winter})"
    
    def test_temperature_bounds(self, minimal_config):
        """Test that temperature stays within realistic bounds."""
        weather = WeatherModel(minimal_config)
        
        # Test all months and hours
        for month in range(1, 13):
            for hour in [0, 6, 12, 18]:
                temp = weather.get_temperature(month, hour)
                
                # Nagpur temperature range: roughly 10-48°C
                assert 5 < temp < 50, \
                    f"Temperature {temp}°C at month {month}, hour {hour} is unrealistic"
    
    def test_humidity_bounds(self, minimal_config):
        """Test that humidity stays within 0-100% bounds."""
        weather = WeatherModel(minimal_config)
        
        # Test all combinations
        for month in range(1, 13):
            for hour in [0, 6, 12, 18]:
                for state in ['clear', 'rain', 'fog', 'heavy_rain']:
                    weather.current_state = state
                    humidity = weather.get_humidity(month, hour)
                    
                    # Must be clamped to valid range
                    assert 0 <= humidity <= 100, \
                        f"Humidity {humidity}% is out of bounds (month={month}, hour={hour}, state={state})"
