"""Shared test fixtures and configuration for pytest.

This file is automatically loaded by pytest and provides reusable
fixtures for all test files.
"""
import pytest
import os
import sys
from pathlib import Path
from datetime import datetime

# Add src to path for imports
src_path = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_path))


# ============================================================================
# Configuration Fixtures
# ============================================================================

@pytest.fixture
def minimal_config():
    """Minimal simulation configuration for fast unit tests."""
    return {
        'duration_days': 1,
        'time_step_minutes': 60,
        'start_date': '2024-11-15',
        'random_seed': 42,
        
        'weather': {
            'state_probabilities': {
                'clear': 0.70,
                'light_rain': 0.15,
                'rain': 0.10,
                'heavy_rain': 0.04,
                'fog': 0.01
            },
            'average_duration_hours': {
                'clear': 12,
                'light_rain': 3,
                'rain': 4,
                'heavy_rain': 2,
                'fog': 3
            }
        },
        
        'disruption': {
            'accident_enabled': True,
            'base_accident_rates': {
                'motorway': 0.001,
                'trunk': 0.002,
                'primary': 0.003,
                'secondary': 0.004,
                'tertiary': 0.005,
                'residential': 0.001
            },
            'weather_multipliers': {
                'clear': 1.0,
               'light_rain': 1.5,
                'rain': 2.5,
                'heavy_rain': 4.0,
                'fog': 3.5
            }
        }
    }


@pytest.fixture
def sample_warehouse_config():
    """Sample warehouse configuration."""
    return {
        'id': 'TEST_WH001',
        'name': 'Test Warehouse',
        'location': {'lat': 21.15, 'lon': 79.05},
        'initial_inventory_kg': 10000,
        'restock_schedule': {
            'day_of_week': 0,
            'time_of_day': '06:00',
            'quantity_kg': 5000
        }
    }


@pytest.fixture
def sample_retailer_config():
    """Sample retailer configuration."""
    return {
        'id': 'TEST_RET001',
        'name': 'Test Retailer',
        'location': {'lat': 21.10, 'lon': 79.10},
        'initial_inventory_kg': 500,
        'demand_rate_kg_per_day': 100,
        'reorder_point_kg': 200,
        'reorder_quantity_kg': 500
    }


# ============================================================================
# Entity Fixtures
# ============================================================================

@pytest.fixture
def fresh_orange_batch():
    """Create a fresh orange batch for testing."""
    from simulation.entities import OrangeBatch
    return OrangeBatch(
        batch_id='TEST_BATCH_001',
        quantity=1000.0,
        harvest_date=datetime(2024, 11, 15)
    )


@pytest.fixture
def aged_orange_batch():
    """Create an aged orange batch for spoilage testing."""
    from simulation.entities import OrangeBatch
    batch = OrangeBatch(
        batch_id='TEST_BATCH_002',
        quantity=500.0,
        harvest_date=datetime(2024, 10, 1)  # 45 days old
    )
    # Age it significantly
    for _ in range(30):
        batch.update_rsl(25.0, 60.0, _ * 1440)  # 25°C, 60% RH, daily
    return batch


# ============================================================================
# Mock Fixtures
# ============================================================================

@pytest.fixture
def mock_road_network(mocker):
    """Mock road network for isolated testing."""
    from simulation.network.road_segment import RoadSegment
    
    mock_network = mocker.Mock()
    
    # Create sample segments
    segment1 = RoadSegment(
        segment_id='TEST_SEG_001',
        start_node=1,
        end_node=2,
        length_km=10.0,
        road_type='primary',
        speed_limit_kmh=60,
        lanes=2
    )
    
    mock_network.segments = {
        (1, 2, 0): segment1
    }
    mock_network.get_segment.return_value = segment1
    
    return mock_network


@pytest.fixture
def mock_mqtt_client(mocker):
    """Mock MQTT client for event logging tests."""
    mock_client = mocker.Mock()
    mock_client.publish = mocker.Mock(return_value=True)
    mock_client.connected = True
    return mock_client


# ============================================================================
# Utility Fixtures
# ============================================================================

@pytest.fixture
def temp_output_dir(tmp_path):
    """Temporary directory for test outputs."""
    output_dir = tmp_path / "test_output"
    output_dir.mkdir()
    return output_dir


# ============================================================================
# Pytest Hooks
# ============================================================================

def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
    config.addinivalue_line(
        "markers", "system: mark test as a system test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow-running"
    )
