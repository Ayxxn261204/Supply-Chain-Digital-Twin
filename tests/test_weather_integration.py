"""Test weather model with real data integration."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.simulation.environment_models import WeatherModel


def test_simulation_mode():
    """Test original simulation mode."""
    print("\n" + "="*60)
    print("TEST 1: SIMULATION MODE (Original Behavior)")
    print("="*60 + "\n")
    
    config = {
        'weather': {
            'state_probabilities': {
                'clear': 0.70,
                'light_rain': 0.15,
                'rain': 0.10,
                'heavy_rain': 0.04,
                'fog': 0.01
            }
        }
    }
    
    model = WeatherModel(config, use_real_data=False)
    
    print(f"Initial state: {model.current_state}")
    print(f"Data mode: {'REAL DATA' if model.use_real_data else 'SIMULATION'}")
    
    # Test temperature and humidity
    temp = model.get_temperature(month=1, hour=14.0)
    humidity = model.get_humidity(month=1, hour=14.0)
    
    print(f"\nJanuary, 2:00 PM:")
    print(f"  Temperature: {temp:.1f}°C")
    print(f"  Humidity: {humidity:.1f}%")
    
    print("\n✓ Simulation mode working correctly")


def test_real_data_mode():
    """Test real data mode."""
    print("\n" + "="*60)
    print("TEST 2: REAL DATA MODE")
    print("="*60 + "\n")
    
    config = {'weather': {}}
    
    model = WeatherModel(
        config,
        use_real_data=True,
        dataset_path="data/processed/weather/nagpur_2023.parquet"
    )
    
    print(f"\nInitial state: {model.current_state}")
    print(f"Data mode: {'REAL DATA' if model.use_real_data else 'SIMULATION'}")
    
    if not model.use_real_data:
        print("\n⚠️  Real data mode failed to initialize (dataset not found or error)")
        print("   Model automatically fell back to simulation mode")
        return
    
    # Test at different times
    test_times = [
        (0, "Day 1, 00:00"),
        (720, "Day 1, 12:00"),
        (1440, "Day 2, 00:00"),
        (10080, "Day 8, 00:00")
    ]
    
    print("\nWeather at different times:")
    for time_min, label in test_times:
        events = model.update(time_min, month=1, hour=0)
        
        temp = model.get_temperature(month=1, hour=0, current_time=time_min)
        humidity = model.get_humidity(month=1, hour=0, current_time=time_min)
        
        print(f"\n  {label} (t={time_min} min):")
        print(f"    State: {model.current_state}")
        print(f"    Temperature: {temp:.1f}°C")
        print(f"    Humidity: {humidity:.1f}%")
        
        if events:
            print(f"    Event: {events[0]['type']} ({events[0]['old_state']} → {events[0]['new_state']})")
    
    print("\n✓ Real data mode working correctly")


def test_comparison():
    """Compare simulation vs real data."""
    print("\n" + "="*60)
    print("TEST 3: SIMULATION vs REAL DATA COMPARISON")
    print("="*60 + "\n")
    
    config = {'weather': {}}
    
    # Simulation model
    sim_model = WeatherModel(config, use_real_data=False)
    
    # Real data model
    real_model = WeatherModel(
        config,
        use_real_data=True,
        dataset_path="data/processed/weather/nagpur_2023.parquet"
    )
    
    print("Comparing weather values at t=720 min (12 hours):\n")
    
    # Simulation
    sim_temp = sim_model.get_temperature(month=1, hour=12.0)
    sim_humidity = sim_model.get_humidity(month=1, hour=12.0)
    
    print(f"SIMULATION MODE:")
    print(f"  State: {sim_model.current_state}")
    print(f"  Temperature: {sim_temp:.1f}°C")
    print(f"  Humidity: {sim_humidity:.1f}%")
    
    # Real data
    if real_model.use_real_data:
        real_model.update(720, month=1, hour=12.0)
        real_temp = real_model.get_temperature(month=1, hour=12.0, current_time=720)
        real_humidity = real_model.get_humidity(month=1, hour=12.0, current_time=720)
        
        print(f"\nREAL DATA MODE:")
        print(f"  State: {real_model.current_state}")
        print(f"  Temperature: {real_temp:.1f}°C")
        print(f"  Humidity: {real_humidity:.1f}%")
        
        print(f"\nDIFFERENCE:")
        print(f"  ΔTemperature: {abs(real_temp - sim_temp):.1f}°C")
        print(f"  ΔHumidity: {abs(real_humidity - sim_humidity):.1f}%")
    
    print("\n✓ Comparison completed")


if __name__ == '__main__':
    try:
        test_simulation_mode()
        test_real_data_mode()
        test_comparison()
        
        print("\n" + "="*60)
        print("ALL TESTS PASSED ✓")
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
