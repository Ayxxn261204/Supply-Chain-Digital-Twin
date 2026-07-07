"""Unit tests for Truck entity."""

import pytest
from src.simulation.entities import Truck, TruckType
from src.simulation.entities import OrangeBatch
from datetime import datetime


@pytest.fixture
def small_truck_type():
    """Create a small truck type for testing."""
    return TruckType(
        name="small",
        capacity_kg=1000,
        fuel_tank_liters=80,
        fuel_consumption_empty_l_per_100km=22,
        fuel_consumption_full_l_per_100km=30,
        fuel_efficiency_by_speed={
            "20_kmh": 1.25,
            "30_kmh": 1.15,
            "40_kmh": 1.05,
            "50_kmh": 1.00,
            "60_kmh": 1.08,
            "70_kmh": 1.18,
            "80_kmh": 1.30
        },
        max_speed_kmh=80,
        cost_per_liter=1.5,
        loading_time_per_ton_minutes=6,
        unloading_time_per_ton_minutes=4
    )


@pytest.fixture
def truck(small_truck_type):
    """Create a truck for testing."""
    return Truck("truck_001", small_truck_type, "WH001", (21.1458, 79.0882))


def test_truck_initialization(truck, small_truck_type):
    """Test truck is initialized correctly."""
    assert truck.truck_id == "truck_001"
    assert truck.truck_type == small_truck_type
    assert truck.warehouse_id == "WH001"
    assert truck.current_fuel_liters == 80  # Full tank
    assert truck.current_load_kg == 0
    assert truck.status == "idle"


def test_load_factor_empty(truck):
    """Test load factor calculation when empty."""
    assert truck.get_load_factor() == 0.0


def test_load_factor_half(truck):
    """Test load factor calculation when half loaded."""
    truck.current_load_kg = 500
    assert truck.get_load_factor() == 0.5


def test_load_factor_full(truck):
    """Test load factor calculation when full."""
    truck.current_load_kg = 1000
    assert truck.get_load_factor() == 1.0


def test_load_factor_overload(truck):
    """Test load factor caps at 1.0 even if overloaded."""
    truck.current_load_kg = 1500
    assert truck.get_load_factor() == 1.0


def test_fuel_consumption_empty_optimal_speed(truck):
    """Test fuel consumption when empty at optimal speed."""
    rate = truck.get_fuel_consumption_rate(50)  # Optimal speed
    assert rate == pytest.approx(22.0, rel=0.01)  # Empty consumption


def test_fuel_consumption_full_optimal_speed(truck):
    """Test fuel consumption when full at optimal speed."""
    truck.current_load_kg = 1000
    rate = truck.get_fuel_consumption_rate(50)
    assert rate == pytest.approx(30.0, rel=0.01)  # Full consumption


def test_fuel_consumption_half_load(truck):
    """Test fuel consumption at half load."""
    truck.current_load_kg = 500
    rate = truck.get_fuel_consumption_rate(50)
    expected = (22.0 + 30.0) / 2  # Linear interpolation
    assert rate == pytest.approx(expected, rel=0.01)


def test_fuel_consumption_speed_penalty(truck):
    """Test fuel consumption increases at non-optimal speeds."""
    rate_optimal = truck.get_fuel_consumption_rate(50)
    rate_slow = truck.get_fuel_consumption_rate(20)
    rate_fast = truck.get_fuel_consumption_rate(80)
    
    assert rate_slow > rate_optimal  # Worse at slow speed
    assert rate_fast > rate_optimal  # Worse at high speed


def test_consume_fuel(truck):
    """Test fuel consumption updates state correctly."""
    initial_fuel = truck.current_fuel_liters
    consumed = truck.consume_fuel(10, 50)  # 10 km at 50 km/h
    
    assert consumed > 0
    assert truck.current_fuel_liters == initial_fuel - consumed
    assert truck.total_fuel_consumed_liters == consumed
    assert truck.total_distance_km == 10


def test_fuel_cannot_go_negative(truck):
    """Test fuel is clamped to zero."""
    truck.current_fuel_liters = 1.0
    truck.consume_fuel(100, 50)  # Consume more than available
    assert truck.current_fuel_liters == 0.0


def test_refuel_full(truck):
    """Test refueling to full tank."""
    truck.current_fuel_liters = 20
    truck.refuel()
    assert truck.current_fuel_liters == 80


def test_refuel_partial(truck):
    """Test partial refueling."""
    truck.current_fuel_liters = 20
    truck.refuel(30)
    assert truck.current_fuel_liters == 50


def test_refuel_cannot_exceed_capacity(truck):
    """Test refueling caps at tank capacity."""
    truck.current_fuel_liters = 70
    truck.refuel(50)
    assert truck.current_fuel_liters == 80


def test_fuel_percentage(truck):
    """Test fuel percentage calculation."""
    assert truck.get_fuel_percentage() == 100.0
    truck.current_fuel_liters = 40
    assert truck.get_fuel_percentage() == 50.0
    truck.current_fuel_liters = 0
    assert truck.get_fuel_percentage() == 0.0


def test_is_low_fuel(truck):
    """Test low fuel detection."""
    assert not truck.is_low_fuel()  # Full tank
    truck.current_fuel_liters = 15  # 18.75%
    assert truck.is_low_fuel()  # Below 20%


def test_load_cargo_success(truck):
    """Test loading cargo within capacity."""
    batches = [
        OrangeBatch("batch1", 300, datetime.now(), 0.0),
        OrangeBatch("batch2", 400, datetime.now(), 0.0)
    ]
    
    success = truck.load_cargo(batches)
    assert success
    assert truck.current_load_kg == 700
    assert len(truck.cargo_batches) == 2


def test_load_cargo_exceeds_capacity(truck):
    """Test loading cargo that exceeds capacity."""
    batches = [
        OrangeBatch("batch1", 800, datetime.now(), 0.0),
        OrangeBatch("batch2", 400, datetime.now(), 0.0)
    ]
    
    success = truck.load_cargo(batches)
    assert not success
    assert truck.current_load_kg == 0  # Nothing loaded
    assert len(truck.cargo_batches) == 0


def test_unload_cargo(truck):
    """Test unloading cargo."""
    batches = [
        OrangeBatch("batch1", 300, datetime.now(), 0.0),
        OrangeBatch("batch2", 400, datetime.now(), 0.0)
    ]
    truck.load_cargo(batches)
    
    unloaded = truck.unload_cargo()
    assert len(unloaded) == 2
    assert truck.current_load_kg == 0
    assert len(truck.cargo_batches) == 0


def test_loading_time_calculation(truck):
    """Test loading time calculation."""
    truck.current_load_kg = 1000  # 1 ton
    time = truck.get_loading_time_minutes()
    assert time == 6  # 6 minutes per ton


def test_unloading_time_calculation(truck):
    """Test unloading time calculation."""
    truck.current_load_kg = 1000  # 1 ton
    time = truck.get_unloading_time_minutes()
    assert time == 4  # 4 minutes per ton
