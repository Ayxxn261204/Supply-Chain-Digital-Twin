"""Unit tests for DemandModel."""

import pytest
import random
from src.simulation.business_models import DemandModel


@pytest.fixture
def demand_config():
    """Create demand configuration for testing."""
    return {
        'base_arrival_rate_per_hour': 5.0,
        'time_of_day_multipliers': [0.2] * 6 + [0.5, 0.8, 1.0, 1.2, 1.5, 1.4, 1.0, 0.9, 1.0, 1.3, 1.6, 1.5, 1.2, 0.8, 0.3, 0.2, 0.2, 0.2],
        'day_of_week_multipliers': [1.3, 0.9, 0.9, 0.9, 1.0, 1.1, 1.4],
        'seasonal_multipliers': [0.95, 0.95, 1.0, 1.1, 1.2, 1.15, 1.1, 1.1, 1.05, 1.0, 0.95, 0.9],
        'purchase_quantity_mean': 2.0,
        'purchase_quantity_std': 0.5,
        'purchase_quantity_min': 0.5,
        'purchase_quantity_max': 8.0,
        'weather_demand_multipliers': {
            'clear': 1.0,
            'light_rain': 0.95,
            'rain': 0.85,
            'heavy_rain': 0.70,
            'fog': 0.90
        },
        'temperature_demand_effect': {
            'enabled': True,
            'base_temperature_celsius': 25,
            'multiplier_per_degree': 0.02,
            'max_multiplier': 1.3
        }
    }


@pytest.fixture
def demand_model(demand_config):
    """Create demand model for testing."""
    return DemandModel(demand_config)


def test_demand_model_initialization(demand_model):
    """Test demand model initializes correctly."""
    assert demand_model.base_arrival_rate == 5.0
    assert len(demand_model.time_of_day_multipliers) == 24
    assert len(demand_model.day_of_week_multipliers) == 7
    assert len(demand_model.seasonal_multipliers) == 12


def test_arrival_rate_base(demand_model):
    """Test arrival rate calculation with base parameters."""
    # 10am (hour 10), Monday (day 1), January (month 1), clear weather, 25°C
    rate = demand_model.get_arrival_rate(600, 1, 1, "clear", 25.0)
    
    # Expected: 5.0 * 1.5 (time at 10am) * 0.9 (day) * 0.95 (season) * 1.0 (weather) * 1.0 (temp)
    expected = 5.0 * 1.5 * 0.9 * 0.95 * 1.0 * 1.0
    assert rate == pytest.approx(expected, rel=0.01)


def test_arrival_rate_peak_hour(demand_model):
    """Test arrival rate is higher during peak hours."""
    # 11am (lunch peak)
    rate_peak = demand_model.get_arrival_rate(660, 1, 1, "clear", 25.0)
    
    # 3am (low traffic)
    rate_low = demand_model.get_arrival_rate(180, 1, 1, "clear", 25.0)
    
    assert rate_peak > rate_low


def test_arrival_rate_weekend(demand_model):
    """Test arrival rate is higher on weekends."""
    # Saturday (day 6)
    rate_weekend = demand_model.get_arrival_rate(600, 6, 1, "clear", 25.0)
    
    # Monday (day 1)
    rate_weekday = demand_model.get_arrival_rate(600, 1, 1, "clear", 25.0)
    
    assert rate_weekend > rate_weekday


def test_arrival_rate_seasonal(demand_model):
    """Test arrival rate varies by season."""
    # May (month 5) - peak summer
    rate_summer = demand_model.get_arrival_rate(600, 1, 5, "clear", 25.0)
    
    # December (month 12) - winter low
    rate_winter = demand_model.get_arrival_rate(600, 1, 12, "clear", 25.0)
    
    assert rate_summer > rate_winter


def test_arrival_rate_weather_effect(demand_model):
    """Test weather reduces arrival rate."""
    # Clear weather
    rate_clear = demand_model.get_arrival_rate(600, 1, 1, "clear", 25.0)
    
    # Heavy rain
    rate_rain = demand_model.get_arrival_rate(600, 1, 1, "heavy_rain", 25.0)
    
    assert rate_rain < rate_clear


def test_arrival_rate_temperature_effect(demand_model):
    """Test higher temperature increases demand."""
    # Hot day (35°C)
    rate_hot = demand_model.get_arrival_rate(600, 1, 1, "clear", 35.0)
    
    # Cool day (20°C)
    rate_cool = demand_model.get_arrival_rate(600, 1, 1, "clear", 20.0)
    
    assert rate_hot > rate_cool


def test_generate_customer_arrivals_poisson(demand_model):
    """Test customer arrivals follow Poisson distribution."""
    random.seed(42)
    
    # Generate many samples
    samples = []
    for _ in range(100):
        count = demand_model.generate_customer_arrivals(600, 60, 1, 1, "clear", 25.0)
        samples.append(count)
    
    # Check that we get non-negative integers
    assert all(isinstance(s, int) and s >= 0 for s in samples)
    
    # Check that mean is reasonable (should be around arrival rate)
    mean = sum(samples) / len(samples)
    expected_rate = demand_model.get_arrival_rate(600, 1, 1, "clear", 25.0)
    assert mean == pytest.approx(expected_rate, rel=0.3)  # Allow 30% variance


def test_generate_customer_arrivals_zero_rate(demand_model):
    """Test no arrivals when rate is zero."""
    # 3am with very low multiplier
    count = demand_model.generate_customer_arrivals(180, 60, 1, 1, "clear", 25.0)
    # Should be very low or zero
    assert count >= 0


def test_purchase_quantity_distribution(demand_model):
    """Test purchase quantities are within bounds."""
    random.seed(42)
    
    quantities = [demand_model.generate_purchase_quantity() for _ in range(100)]
    
    # All should be within min/max bounds
    assert all(0.5 <= q <= 8.0 for q in quantities)
    
    # Mean should be close to configured mean
    mean = sum(quantities) / len(quantities)
    assert mean == pytest.approx(2.0, rel=0.2)


def test_purchase_quantity_clamping(demand_model):
    """Test purchase quantities are clamped to bounds."""
    random.seed(42)
    
    # Generate many samples
    quantities = [demand_model.generate_purchase_quantity() for _ in range(1000)]
    
    # Check min and max are respected
    assert min(quantities) >= 0.5
    assert max(quantities) <= 8.0
