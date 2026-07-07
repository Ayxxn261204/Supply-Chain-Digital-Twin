"""
Unit tests for configuration loading.
"""

import pytest
from src.data.config_loader import ConfigLoader, load_config


def test_config_loader_loads_file():
    """Test that config loader can load the YAML file."""
    loader = ConfigLoader("config/simulation_config.yaml")
    config = loader.load()
    
    assert config is not None
    assert isinstance(config, dict)


def test_config_has_required_sections():
    """Test that config has all required sections."""
    config = load_config("config/simulation_config.yaml")
    
    required_sections = ['simulation', 'logging', 'warehouses', 'retailers',
                        'truck_types', 'traffic', 'disruptions', 'cargo']
    
    for section in required_sections:
        assert section in config, f"Missing section: {section}"


def test_simulation_section():
    """Test simulation section parameters."""
    config = load_config("config/simulation_config.yaml")
    sim = config['simulation']
    
    assert 'duration_days' in sim
    assert 1 <= sim['duration_days'] <= 365
    assert 'time_step_minutes' in sim
    assert 1 <= sim['time_step_minutes'] <= 60
    assert 'location' in sim
    assert 'bounding_box' in sim['location']


def test_warehouse_configs():
    """Test warehouse configurations."""
    loader = ConfigLoader("config/simulation_config.yaml")
    loader.load()
    
    warehouses = loader.get_warehouse_configs()
    assert len(warehouses) >= 1
    
    for wh in warehouses:
        assert 'id' in wh
        assert 'name' in wh
        assert 'location' in wh
        assert 'lat' in wh['location']
        assert 'lon' in wh['location']
        assert 'fleet' in wh
        assert len(wh['fleet']) >= 1


def test_truck_type_configs():
    """Test truck type configurations."""
    loader = ConfigLoader("config/simulation_config.yaml")
    loader.load()
    
    for truck_type in ['small', 'medium', 'large']:
        config = loader.get_truck_type_config(truck_type)
        assert 'capacity_kg' in config
        assert 'fuel_tank_liters' in config
        assert 'fuel_consumption_empty_l_per_100km' in config
        assert 'fuel_consumption_full_l_per_100km' in config
        assert 'max_speed_kmh' in config
        assert 'fuel_efficiency_by_speed' in config
        assert 'loading_time_per_ton_minutes' in config
        assert 'unloading_time_per_ton_minutes' in config
        assert config['capacity_kg'] > 0
        assert config['fuel_tank_liters'] > 0
        # Validate full load uses more fuel than empty
        assert config['fuel_consumption_full_l_per_100km'] > config['fuel_consumption_empty_l_per_100km']


def test_retailer_config():
    """Test retailer configuration."""
    loader = ConfigLoader("config/simulation_config.yaml")
    loader.load()
    
    retailer_config = loader.get_retailer_config()
    assert 'count' in retailer_config
    assert retailer_config['count'] >= 1
    assert 'demand' in retailer_config
    assert 'inventory' in retailer_config


def test_traffic_config():
    """Test traffic configuration."""
    loader = ConfigLoader("config/simulation_config.yaml")
    loader.load()
    
    traffic = loader.get_traffic_config()
    assert 'capacity_per_lane' in traffic
    assert 'jam_density_per_lane' in traffic
    assert 'base_density_fraction' in traffic
    assert 'time_of_day_multipliers' in traffic
    assert 'weather_speed_reduction' in traffic
    assert 'weather_density_increase' in traffic
    assert len(traffic['time_of_day_multipliers']) == 24


def test_disruption_config():
    """Test disruption configuration."""
    loader = ConfigLoader("config/simulation_config.yaml")
    loader.load()
    
    disruptions = loader.get_disruption_config()
    assert 'accidents' in disruptions
    assert 'base_rate_per_million_vkm' in disruptions['accidents']
    assert 'time_multipliers' in disruptions['accidents']
    assert 'weather_multipliers' in disruptions['accidents']
    assert 'density_effect_enabled' in disruptions['accidents']


def test_cargo_config():
    """Test cargo configuration."""
    loader = ConfigLoader("config/simulation_config.yaml")
    loader.load()
    
    cargo = loader.get_cargo_config()
    assert 'product_type' in cargo
    assert 'target_temperature_celsius' in cargo
    assert 'initial_rsl_hours' in cargo


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
