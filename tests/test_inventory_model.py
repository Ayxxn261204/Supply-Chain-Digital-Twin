"""Unit tests for InventoryModel."""

import pytest
from src.simulation.business_models import InventoryModel


@pytest.fixture
def inventory_config():
    """Create inventory configuration for testing."""
    return {
        'service_level': 0.95,
        'lead_time_days_mean': 1.5,
        'lead_time_days_std': 0.5,
        'review_period_hours': 6,
        'order_quantity_method': 'EOQ',
        'holding_cost_per_kg_per_day': 0.05,
        'ordering_cost_per_order': 50,
        'order_up_to_days': 7,
        'demand_forecast_window_days': 14,
        'demand_smoothing_alpha': 0.3,
        'min_order_quantity_kg': 100,
        'max_order_quantity_kg': 5000
    }


@pytest.fixture
def inventory_model(inventory_config):
    """Create inventory model for testing."""
    return InventoryModel(inventory_config)


def test_inventory_model_initialization(inventory_model):
    """Test inventory model initializes correctly."""
    assert inventory_model.service_level == 0.95
    assert inventory_model.z_score > 0  # Should be ~1.65 for 95%
    assert inventory_model.forecasted_demand_rate == 0.0  # No data yet
    assert len(inventory_model.demand_history_full) == 0


def test_record_demand(inventory_model):
    """Test recording demand updates history."""
    inventory_model.record_demand(100, 50)
    assert len(inventory_model.demand_history_full) == 1
    entry = inventory_model.demand_history_full[0]
    assert entry['time'] == 100
    assert entry['demand'] == 50


def test_demand_history_window(inventory_model):
    """Test demand history is limited to 1000 entries."""
    # Add 1001 entries — oldest should be dropped
    for i in range(1001):
        inventory_model.record_demand(float(i), 10.0)
    assert len(inventory_model.demand_history_full) == 1000


def test_forecast_update(inventory_model):
    """Test demand forecast updates with new data."""
    # Record several demands
    for i in range(10):
        inventory_model.record_demand(i * 60, 10)  # 10 kg every hour
    
    # Forecast should be updated
    assert inventory_model.forecasted_demand_rate > 0


def test_exponential_smoothing(inventory_model):
    """Test exponential smoothing of demand forecast."""
    # Record initial demand
    for i in range(5):
        inventory_model.record_demand(i * 60, 10)
    
    initial_forecast = inventory_model.forecasted_demand_rate
    
    # Record higher demand
    for i in range(5, 10):
        inventory_model.record_demand(i * 60, 20)
    
    # Forecast should increase but not jump immediately (smoothing)
    assert inventory_model.forecasted_demand_rate > initial_forecast
    assert inventory_model.forecasted_demand_rate < 20 / 60  # Not fully at new rate


def test_reorder_point_calculation(inventory_model):
    """Test reorder point calculation."""
    # Set up some demand history
    for i in range(20):
        inventory_model.record_demand(i * 60, 10)
    
    reorder_point = inventory_model.calculate_reorder_point()
    
    # Should be positive
    assert reorder_point > 0
    
    # Should include safety stock
    lead_time_demand = inventory_model.forecasted_demand_rate * inventory_model.lead_time_mean
    assert reorder_point > lead_time_demand  # Includes safety stock


def test_reorder_point_with_variability(inventory_model):
    """Test reorder point increases with demand variability."""
    # Low variability demand
    for i in range(20):
        inventory_model.record_demand(i * 60, 10)
    reorder_low_var = inventory_model.calculate_reorder_point()
    
    # Reset and add high variability demand
    inventory_model.demand_history_full.clear()
    inventory_model.forecasted_demand_rate = 0
    for i in range(20):
        demand = 10 if i % 2 == 0 else 30  # Alternating
        inventory_model.record_demand(i * 60, demand)
    reorder_high_var = inventory_model.calculate_reorder_point()
    
    # Higher variability should lead to higher reorder point (more safety stock)
    assert reorder_high_var > reorder_low_var


def test_should_reorder_below_point(inventory_model):
    """Test should_reorder returns True when below reorder point."""
    # Set up demand
    for i in range(20):
        inventory_model.record_demand(i * 60, 10)
    
    reorder_point = inventory_model.calculate_reorder_point()
    
    # Inventory below reorder point
    assert inventory_model.should_reorder(reorder_point - 10)


def test_should_not_reorder_above_point(inventory_model):
    """Test should_reorder returns False when above reorder point."""
    # Set up demand
    for i in range(20):
        inventory_model.record_demand(i * 60, 10)
    
    reorder_point = inventory_model.calculate_reorder_point()
    
    # Inventory above reorder point
    assert not inventory_model.should_reorder(reorder_point + 100)


def test_calculate_order_quantity_eoq(inventory_model):
    """Test order quantity calculation using EOQ method."""
    # Set up demand
    for i in range(20):
        inventory_model.record_demand(i * 60, 10)
    
    current_inventory = 100
    order_qty = inventory_model.calculate_order_quantity(current_inventory)
    
    # Should be positive
    assert order_qty > 0
    
    # Should bring inventory up to S
    order_up_to = inventory_model.calculate_order_up_to_level()
    assert order_qty == pytest.approx(order_up_to - current_inventory, rel=0.01)


def test_calculate_order_quantity_fixed_days(inventory_config):
    """Test order quantity calculation using fixed days method."""
    inventory_config['order_quantity_method'] = 'fixed_days'
    model = InventoryModel(inventory_config)
    
    # Set up demand
    for i in range(20):
        model.record_demand(i * 60, 10)
    
    current_inventory = 100
    order_qty = model.calculate_order_quantity(current_inventory)
    
    # Should be positive
    assert order_qty > 0


def test_order_quantity_respects_min(inventory_model):
    """Test order quantity respects minimum constraint."""
    # Set up very low demand
    for i in range(20):
        inventory_model.record_demand(i * 60, 0.1)
    
    # High current inventory
    current_inventory = 1000
    order_qty = inventory_model.calculate_order_quantity(current_inventory)
    
    # Should be 0 if below minimum
    assert order_qty == 0.0


def test_order_quantity_respects_max(inventory_model):
    """Test order quantity respects maximum constraint."""
    # Set up very high demand
    for i in range(20):
        inventory_model.record_demand(i * 60, 100)
    
    # Very low current inventory
    current_inventory = 0
    order_qty = inventory_model.calculate_order_quantity(current_inventory)
    
    # Should not exceed maximum
    assert order_qty <= 5000


def test_s_S_policy_behavior(inventory_model):
    """Test (s,S) policy behavior."""
    # Set up demand
    for i in range(20):
        inventory_model.record_demand(i * 60, 10)
    
    s = inventory_model.calculate_reorder_point()
    S = inventory_model.calculate_order_up_to_level()
    
    # S should be greater than s
    assert S > s
    
    # When inventory is at S, should not reorder
    assert not inventory_model.should_reorder(S)
    
    # When inventory drops to s, should reorder
    assert inventory_model.should_reorder(s)
    
    # Order quantity should bring inventory from s to S
    order_qty = inventory_model.calculate_order_quantity(s)
    assert order_qty == pytest.approx(S - s, rel=0.01)
