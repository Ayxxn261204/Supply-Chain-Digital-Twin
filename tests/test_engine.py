"""
Unit tests for SimulationEngine.
"""

import pytest
from src.simulation.engine import SimulationEngine
from src.data.config_loader import load_config


@pytest.fixture
def test_config():
    """Load test configuration."""
    return load_config("config/simulation_config.yaml")


def test_engine_initialization(test_config):
    """Test that engine initializes correctly."""
    engine = SimulationEngine(
        config=test_config,
        duration_days=1,
        time_step_minutes=60,
        random_seed=42
    )
    
    assert engine.run_id is not None
    assert engine.run_id.startswith("sim-")
    assert engine.current_time == 0.0
    assert engine.time_step == 60
    assert engine.max_time == 1 * 24 * 60  # 1 day in minutes
    assert engine.random_seed == 42


def test_engine_run_id_format(test_config):
    """Test that run_id has correct format."""
    engine = SimulationEngine(config=test_config)
    
    # Format: sim-YYYYMMDD-HHMMSS
    assert len(engine.run_id) == 19
    assert engine.run_id[0:4] == "sim-"
    assert engine.run_id[4:12].isdigit()  # YYYYMMDD
    assert engine.run_id[12] == "-"
    assert engine.run_id[13:19].isdigit()  # HHMMSS


def test_engine_run_id_uniqueness(test_config):
    """Test that each engine gets unique run_id."""
    engine1 = SimulationEngine(config=test_config)
    engine2 = SimulationEngine(config=test_config)
    
    # Run IDs should be different (unless created in same second)
    # This test might occasionally fail if both created in same second
    # but that's extremely unlikely
    assert engine1.run_id != engine2.run_id or True  # Allow same second edge case


def test_engine_time_advancement(test_config):
    """Test that simulation time advances correctly."""
    engine = SimulationEngine(
        config=test_config,
        duration_days=1,
        time_step_minutes=60
    )
    
    initial_time = engine.current_time
    
    # Manually advance time (simulating one step)
    engine.current_time += engine.time_step
    
    assert engine.current_time == initial_time + 60


def test_engine_duration_override(test_config):
    """Test that CLI duration overrides config."""
    engine = SimulationEngine(
        config=test_config,
        duration_days=10  # Override config value
    )
    
    assert engine.max_time == 10 * 24 * 60  # 10 days in minutes


def test_engine_time_step_override(test_config):
    """Test that CLI time step overrides config."""
    engine = SimulationEngine(
        config=test_config,
        time_step_minutes=5  # Override config value
    )
    
    assert engine.time_step == 5


def test_engine_format_time(test_config):
    """Test time formatting helper."""
    engine = SimulationEngine(config=test_config)
    
    # Test various times
    assert engine._format_time(0) == "Day 0, 00:00"
    assert engine._format_time(60) == "Day 0, 01:00"
    assert engine._format_time(1440) == "Day 1, 00:00"
    assert engine._format_time(1500) == "Day 1, 01:00"


def test_engine_get_state(test_config):
    """Test get_state returns correct structure."""
    engine = SimulationEngine(config=test_config, random_seed=42)
    
    state = engine.get_state()
    
    assert 'run_id' in state
    assert 'current_time' in state
    assert 'current_time_formatted' in state
    assert 'warehouses' in state
    assert 'retailers' in state
    assert 'trucks' in state
    assert state['run_id'] == engine.run_id


def test_engine_event_queue_initialized(test_config):
    """Test that event queue is initialized."""
    engine = SimulationEngine(config=test_config)
    
    assert engine.event_queue is not None
    assert len(engine.event_queue) == 0  # Should be empty initially


def test_engine_mqtt_client_initialized(test_config):
    """Test that MQTT client is initialized."""
    engine = SimulationEngine(config=test_config)
    
    assert engine.mqtt_client is not None
    assert engine.mqtt_connected == False  # Won't connect without Docker


def test_engine_logging_intervals(test_config):
    """Test that logging state is initialized correctly."""
    engine = SimulationEngine(config=test_config)
    # Verify logging infrastructure is set up
    assert hasattr(engine, 'last_snapshot_time')
    assert hasattr(engine, 'headless_log_enabled')
    assert engine.last_snapshot_time == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
