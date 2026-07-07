"""
Configuration loader for simulation parameters.

Loads and validates the simulation_config.yaml file.
"""

import yaml
from typing import Dict, Any, List
from pathlib import Path


class ConfigLoader:
    """Loads and validates simulation configuration from YAML."""
    
    def __init__(self, config_path: str = "config/simulation_config.yaml"):
        """
        Initialize config loader.
        
        Args:
            config_path: Path to configuration YAML file
        """
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        
    def load(self) -> Dict[str, Any]:
        """
        Load configuration from YAML file.
        
        Returns:
            Configuration dictionary
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If YAML is invalid
            ValueError: If configuration is invalid
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        
        with open(self.config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        # Validate configuration
        self._validate()
        
        return self.config
    
    def _validate(self):
        """
        Validate configuration structure and values.
        
        Raises:
            ValueError: If configuration is invalid
        """
        # Check required top-level sections
        required_sections = ['simulation', 'logging', 'warehouses', 'retailers', 
                           'truck_types', 'traffic', 'disruptions', 'cargo']
        for section in required_sections:
            if section not in self.config:
                raise ValueError(f"Missing required configuration section: {section}")
        
        # Validate simulation section
        sim = self.config['simulation']
        if not (1 <= sim.get('duration_days', 0) <= 365):
            raise ValueError("duration_days must be between 1 and 365")
        if not (1 <= sim.get('time_step_minutes', 0) <= 60):
            raise ValueError("time_step_minutes must be between 1 and 60")
        
        # Validate location
        if 'location' not in sim or 'bounding_box' not in sim['location']:
            raise ValueError("simulation.location.bounding_box is required")
        
        # Validate warehouses
        if not self.config['warehouses']:
            raise ValueError("At least one warehouse is required")
        for wh in self.config['warehouses']:
            self._validate_warehouse(wh)
        
        # Validate retailers
        if self.config['retailers'].get('count', 0) < 1:
            raise ValueError("retailers.count must be at least 1")
        
        # Validate truck types
        for truck_type in ['small', 'medium', 'large']:
            if truck_type not in self.config['truck_types']:
                raise ValueError(f"Missing truck type definition: {truck_type}")
            self._validate_truck_type(self.config['truck_types'][truck_type])
        
        print("[OK] Configuration validated successfully")
    
    def _validate_warehouse(self, warehouse: Dict[str, Any]):
        """Validate warehouse configuration."""
        required_fields = ['id', 'name', 'location', 'initial_inventory_kg', 
                          'restock_schedule', 'fleet']
        for field in required_fields:
            if field not in warehouse:
                raise ValueError(f"Warehouse missing required field: {field}")
        
        # Validate location
        if 'lat' not in warehouse['location'] or 'lon' not in warehouse['location']:
            raise ValueError(f"Warehouse {warehouse['id']} missing lat/lon")
        
        # Validate fleet
        if not warehouse['fleet']:
            raise ValueError(f"Warehouse {warehouse['id']} must have at least one truck")
        for truck_group in warehouse['fleet']:
            if 'type' not in truck_group or 'count' not in truck_group:
                raise ValueError(f"Warehouse {warehouse['id']} fleet entry missing type or count")
            if truck_group['count'] < 1:
                raise ValueError(f"Warehouse {warehouse['id']} truck count must be >= 1")
    
    def _validate_truck_type(self, truck_type: Dict[str, Any]):
        """Validate truck type configuration."""
        required_fields = ['capacity_kg', 'fuel_tank_liters', 'fuel_consumption_empty_l_per_100km',
                          'fuel_consumption_full_l_per_100km', 'max_speed_kmh', 'cost_per_liter',
                          'loading_time_per_ton_minutes', 'unloading_time_per_ton_minutes']
        for field in required_fields:
            if field not in truck_type:
                raise ValueError(f"Truck type missing required field: {field}")
            if truck_type[field] <= 0:
                raise ValueError(f"Truck type {field} must be positive")
        
        # Validate fuel efficiency curve exists
        if 'fuel_efficiency_by_speed' not in truck_type:
            raise ValueError("Truck type missing fuel_efficiency_by_speed")
        
        # Validate that full load consumption is higher than empty
        if truck_type['fuel_consumption_full_l_per_100km'] <= truck_type['fuel_consumption_empty_l_per_100km']:
            raise ValueError("Full load fuel consumption must be higher than empty load")
    
    def get_warehouse_configs(self) -> List[Dict[str, Any]]:
        """Get list of warehouse configurations."""
        return self.config.get('warehouses', [])
    
    def get_retailer_config(self) -> Dict[str, Any]:
        """Get retailer configuration."""
        return self.config.get('retailers', {})
    
    def get_truck_type_config(self, truck_type: str) -> Dict[str, Any]:
        """
        Get configuration for specific truck type.
        
        Args:
            truck_type: 'small', 'medium', or 'large'
            
        Returns:
            Truck type configuration dictionary
        """
        return self.config.get('truck_types', {}).get(truck_type, {})
    
    def get_traffic_config(self) -> Dict[str, Any]:
        """Get traffic simulation configuration."""
        return self.config.get('traffic', {})
    
    def get_disruption_config(self) -> Dict[str, Any]:
        """Get disruption simulation configuration."""
        return self.config.get('disruptions', {})
    
    def get_cargo_config(self) -> Dict[str, Any]:
        """Get cargo monitoring configuration."""
        return self.config.get('cargo', {})
    
    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration."""
        return self.config.get('logging', {})
    
    def get_simulation_config(self) -> Dict[str, Any]:
        """Get simulation parameters."""
        return self.config.get('simulation', {})


def load_config(config_path: str = "config/simulation_config.yaml") -> Dict[str, Any]:
    """
    Convenience function to load configuration.
    
    Args:
        config_path: Path to configuration YAML file
        
    Returns:
        Configuration dictionary
    """
    loader = ConfigLoader(config_path)
    return loader.load()
