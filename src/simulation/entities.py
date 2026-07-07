from __future__ import annotations
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime
from dataclasses import dataclass
import logging
import random
import math
from .business_models import HybridRSLModel



# --- Merged from accident.py ---
"""Accident entity for traffic disruptions."""



class Accident:
    """
    Traffic accident that can block a road segment or involve a truck.
    
    Two types of accidents:
    1. Road accidents: Block segment, slow traffic
    2. Truck accidents: Total loss - truck destroyed, cargo lost
    
    Accidents have:
    - Duration (how long the segment is blocked)
    - Severity (minor, moderate, severe)
    - Location (which segment is affected)
    - Involved truck (if truck-involved accident)
    """
    
    def __init__(self, accident_id: str, segment_id: str, 
                 start_time: float, duration_minutes: float, 
                 severity: str, involved_truck_id: Optional[str] = None):
        """
        Initialize an Accident.
        
        Args:
            accident_id: Unique identifier
            segment_id: ID of affected road segment
            start_time: Simulation time when accident occurs
            duration_minutes: How long segment is blocked
            severity: 'minor', 'moderate', or 'severe'
            involved_truck_id: ID of truck involved (if truck accident)
        """
        self.accident_id = accident_id
        self.segment_id = segment_id
        self.start_time = start_time
        self.duration_minutes = duration_minutes
        self.severity = severity
        self.end_time = start_time + duration_minutes
        self.is_active = False
        
        # Truck involvement (NEW)
        self.involved_truck_id = involved_truck_id
        self.is_truck_accident = involved_truck_id is not None
        
    def activate(self):
        """Mark accident as active (segment is blocked)."""
        self.is_active = True
        
    def clear(self):
        """Mark accident as cleared (segment is unblocked)."""
        self.is_active = False
        
    def __repr__(self) -> str:
        truck_info = f", truck={self.involved_truck_id}" if self.is_truck_accident else ""
        return (f"Accident(id={self.accident_id}, segment={self.segment_id}, "
                f"severity={self.severity}, duration={self.duration_minutes:.0f}min, "
                f"active={self.is_active}{truck_info})")



# --- Merged from driver.py ---
"""Driver entity for modeling human factors in supply chain."""


# Module-level logger
logger = logging.getLogger(__name__)

class Driver:
    """
    Models a truck driver with fatigue, skill levels, and break requirements.
    
    Features:
    - Skill levels (Novice, Experienced, Expert) affecting efficiency
    - Fatigue accumulation based on driving time
    - Mandatory break enforcement
    - Sleep requirements after long shifts
    - Speed penalties for fatigue
    """
    
    def __init__(self, driver_id: str, config: Dict[str, Any]):
        """
        Initialize Driver.
        
        Args:
            driver_id: Unique identifier
            config: Driver configuration from simulation_config.yaml
        """
        self.driver_id = driver_id
        self.config = config
        
        # Skill level
        self.skill_level = self._assign_skill_level()
        
        # State
        self.driving_time_since_break = 0.0  # minutes
        self.total_shift_time = 0.0  # minutes
        self.accumulated_fatigue = 0.0  # Total fatigue in minutes (NEW)
        self.is_on_break = False
        self.is_sleeping = False  # NEW
        self.break_remaining_time = 0.0  # minutes
        self.sleep_remaining_time = 0.0  # NEW
        self.fatigue_level = 0.0  # 0.0 to 1.0
        
        # Limits
        self.max_continuous_driving = config.get('mandatory_break_after_hours', 4.5) * 60
        self.break_duration = config.get('break_duration_minutes', 45)
        self.shift_duration = config.get('shift_duration_hours', 10) * 60
        self.sleep_duration = 8 * 60  # 8 hours sleep required (NEW)
        self.max_fatigue_before_sleep = 10 * 60  # 10 hours driving requires sleep (NEW)
        
    def _assign_skill_level(self) -> str:
        """Assign skill level based on configured distribution."""
        dist = self.config.get('skill_distribution', {'experienced': 1.0})
        levels = list(dist.keys())
        weights = list(dist.values())
        return random.choices(levels, weights=weights)[0]
    
    def update(self, time_step_minutes: float, is_driving: bool):
        """
        Update driver state.
        
        Args:
            time_step_minutes: Simulation time step
            is_driving: Whether the truck is currently moving
        """
        # Handle sleep (NEW)
        if self.is_sleeping:
            self.sleep_remaining_time -= time_step_minutes
            if self.sleep_remaining_time <= 0:
                self._wake_up()
            return
        
        # Handle break
        if self.is_on_break:
            self.break_remaining_time -= time_step_minutes
            if self.break_remaining_time <= 0:
                self._end_break()
            # Passive fatigue recovery continues during breaks
            # (resting, not sleeping — 25% of accumulation rate)
            passive_recovery = time_step_minutes * 0.25
            self.accumulated_fatigue = max(0.0, self.accumulated_fatigue - passive_recovery)
            return

        if is_driving:
            self.driving_time_since_break += time_step_minutes
            self.total_shift_time += time_step_minutes
            self.accumulated_fatigue += time_step_minutes  # NEW
            
            # Increase fatigue
            # Simple model: linear increase over shift
            self.fatigue_level = min(1.0, self.total_shift_time / self.shift_duration)
            
            # Check for mandatory sleep (NEW)
            if self.accumulated_fatigue >= self.max_fatigue_before_sleep:
                self.go_to_sleep()
                return
            
            # Check for mandatory break
            if self.driving_time_since_break >= self.max_continuous_driving:
                self.take_break()
        else:
            # Passive recovery while idle (not sleeping, not on break)
            # Fatigue decays at ~25% of accumulation rate while resting
            passive_recovery = time_step_minutes * 0.25
            self.accumulated_fatigue = max(0.0, self.accumulated_fatigue - passive_recovery)
                
    def take_break(self):
        """Start a mandatory break."""
        self.is_on_break = True
        self.break_remaining_time = self.break_duration
        self.driving_time_since_break = 0.0
        # Fatigue recovers slightly on break
        self.accumulated_fatigue = max(0.0, self.accumulated_fatigue - (self.break_duration * 0.3))
        
    def _end_break(self):
        """End the current break."""
        self.is_on_break = False
    
    def go_to_sleep(self):
        """Start mandatory sleep period (NEW)."""
        self.is_sleeping = True
        self.sleep_remaining_time = self.sleep_duration
        logger.info(f"[DRIVER] {self.driver_id} going to sleep - exhausted after {self.accumulated_fatigue/60:.1f}hrs")
    
    def _wake_up(self):
        """Wake up after sleep (NEW)."""
        self.is_sleeping = False
        self.accumulated_fatigue = 0.0  # Fully rested
        self.total_shift_time = 0.0
        self.fatigue_level = 0.0
        logger.info(f"[DRIVER] {self.driver_id} woken up - fully rested")
        
    def get_speed_multiplier(self) -> float:
        """
        Get speed multiplier based on skill and fatigue.
        
        Fatigue now has stronger effect on speed (NEW).
        """
        # Skill effect
        skill_mult = {
            'novice': 0.95,
            'experienced': 1.0,
            'expert': 1.05
        }.get(self.skill_level, 1.0)
        
        # Fatigue effect (stronger penalties - NEW)
        fatigue_hours = self.accumulated_fatigue / 60.0
        if fatigue_hours > 10:
            fatigue_penalty = 0.25  # 25% slower when exhausted
        elif fatigue_hours > 8:
            fatigue_penalty = 0.15  # 15% slower when very tired
        elif fatigue_hours > 6:
            fatigue_penalty = 0.08  # 8% slower when tired
        else:
            fatigue_penalty = 0.0
        
        return skill_mult * (1.0 - fatigue_penalty)
    
    def is_exhausted(self) -> bool:
        """Check if driver is dangerously exhausted (NEW)."""
        return self.accumulated_fatigue / 60.0 > 8.0
        
    def get_fuel_efficiency_multiplier(self) -> float:
        """Get fuel efficiency multiplier (1.0 = normal, >1.0 = worse)."""
        # Skill effect
        skill_mult = {
            'novice': 1.10,      # 10% more fuel
            'experienced': 1.0,
            'expert': 0.95       # 5% less fuel
        }.get(self.skill_level, 1.0)
        
        return skill_mult

    def __repr__(self) -> str:
        if self.is_sleeping:
            status = "Sleeping"
        elif self.is_on_break:
            status = "Break"
        else:
            status = "Driving"
        return (f"Driver({self.driver_id}, {self.skill_level}, {status}, "
                f"Fatigue: {self.accumulated_fatigue/60:.1f}hrs)")



# --- Merged from orange_batch.py ---
"""Orange batch entity with quality degradation modeling."""



class OrangeBatch:
    """Perishable orange batch with quality degradation based on Arrhenius equation."""
    
    def __init__(self, batch_id: str, quantity: int, 
                 harvest_date: datetime, current_time: float, initial_quality: float = 100.0):
        """
        Initialize an OrangeBatch.
        
        Args:
            batch_id: Unique identifier for the batch
            quantity: Number of units in the batch
            harvest_date: Date when oranges were harvested
            initial_quality: Initial quality percentage (default 100.0)
        """
        self.batch_id = batch_id
        self.quantity = quantity
        self.harvest_date = harvest_date
        self.initial_quality = initial_quality
        self.current_rsl = initial_quality  # Remaining Shelf Life percentage
        self.last_update_time: float = current_time  # Last time RSL was updated
        
        # Initialize Hybrid RSL Model (Physics-informed)
        self.rsl_model = HybridRSLModel()
        
    def update_rsl(self, temperature_celsius: float, humidity_percent: float, current_time: float, vibration_g: float = 0.0):
        """
        Update RSL using Data-Driven Spoilage Proxy (or fallback).
        """
        # Calculate time elapsed in hours
        time_elapsed_hours = (current_time - self.last_update_time) / 60.0
        
        if time_elapsed_hours <= 0:
            return

        # Use Hybrid RSL Model (Physics-informed)
        decay_rate_per_hour = self.rsl_model.calculate_decay_rate(temperature_celsius, humidity_percent, vibration_g)
        degradation = decay_rate_per_hour * time_elapsed_hours
        
        # Update RSL
        self.current_rsl = max(0, min(100, self.current_rsl - degradation))
        
        # Update last update time
        self.last_update_time = current_time
        
    def is_spoiled(self) -> bool:
        """
        Check if the batch is spoiled.
        
        Returns:
            True if current_rsl <= 0, False otherwise
        """
        return self.current_rsl <= 0
    
    def is_acceptable_quality(self, min_rsl_hours: float = 72.0) -> bool:
        """
        Check if batch quality is acceptable for delivery/sale.
        
        Retailers need minimum RSL to have time to sell before spoilage.
        Default threshold: 72 hours (3 days)
        
        Args:
            min_rsl_hours: Minimum remaining shelf life in hours
            
        Returns:
            True if acceptable, False if should be rejected
        """
        # Convert RSL percentage to hours (assuming 14-day total shelf life)
        total_shelf_life_hours = 14 * 24  # 336 hours
        remaining_hours = (self.current_rsl / 100.0) * total_shelf_life_hours
        
        return remaining_hours >= min_rsl_hours
    
    def __repr__(self) -> str:
        return (f"OrangeBatch(id={self.batch_id}, quantity={self.quantity}, "
                f"rsl={self.current_rsl:.2f}%)")



# --- Merged from order.py ---
"""Order entity for tracking delivery requests."""



class Order:
    """Order entity representing a retailer's delivery request."""
    
    def __init__(self, order_id: str, retailer_id: str, warehouse_id: str,
                 quantity_kg: float, timestamp: float, priority: float = 1.0,
                 retailer_node_id: Optional[str] = None):
        """
        Initialize an Order.
        
        Args:
            order_id: Unique identifier
            retailer_id: ID of requesting retailer
            warehouse_id: ID of fulfilling warehouse
            quantity_kg: Quantity requested in kg
            timestamp: Simulation time when order was placed
            priority: Priority level (higher = more urgent)
            retailer_node_id: Road network node ID of retailer (for routing)
        """
        self.order_id = order_id
        self.retailer_id = retailer_id
        self.warehouse_id = warehouse_id
        self.quantity_kg = quantity_kg
        self.timestamp = timestamp
        self.priority = priority
        self.retailer_node_id = retailer_node_id  # For routing
        
        # Status tracking
        self.status = "pending"  # pending, assigned, in_transit, delivered, cancelled
        self.assigned_truck_id: Optional[str] = None
        self.assigned_time: Optional[float] = None
        self.departure_time: Optional[float] = None
        self.delivery_time: Optional[float] = None
        
        # Batch tracking
        self.batch_ids = []  # List of OrangeBatch IDs assigned to this order
        self.delivered_rsl_pct: Optional[float] = None  # Tracked at delivery
        
    def assign_truck(self, truck_id: str, current_time: float):
        """Assign a truck to this order."""
        self.assigned_truck_id = truck_id
        self.assigned_time = current_time
        self.status = "assigned"
    
    def mark_departed(self, current_time: float):
        """Mark order as departed from warehouse."""
        self.departure_time = current_time
        self.status = "in_transit"
    
    def mark_delivered(self, current_time: float):
        """Mark order as delivered."""
        self.delivery_time = current_time
        self.status = "delivered"
    
    def mark_cancelled(self):
        """Mark order as cancelled."""
        self.status = "cancelled"
    
    def get_wait_time(self, current_time: float) -> float:
        """
        Get time order has been waiting.
        
        Args:
            current_time: Current simulation time
            
        Returns:
            Wait time in minutes
        """
        if self.status == "pending":
            return current_time - self.timestamp
        elif self.assigned_time:
            return self.assigned_time - self.timestamp
        return 0.0
    
    def get_delivery_time(self) -> Optional[float]:
        """
        Get total time from order to delivery.
        
        Returns:
            Delivery time in minutes, or None if not delivered
        """
        if self.delivery_time:
            return self.delivery_time - self.timestamp
        return None
    
    def __repr__(self) -> str:
        return (f"Order(id={self.order_id}, retailer={self.retailer_id}, "
                f"qty={self.quantity_kg:.0f}kg, status={self.status})")


# --- Merged from truck.py ---
"""Truck entity with type-specific attributes."""



@dataclass
class TruckType:
    """Type definition for trucks with operational characteristics."""
    
    name: str
    capacity_kg: int
    fuel_tank_liters: float
    fuel_consumption_empty_l_per_100km: float
    fuel_consumption_full_l_per_100km: float
    fuel_efficiency_by_speed: dict  # speed_kmh -> multiplier
    max_speed_kmh: float
    cost_per_liter: float
    loading_time_per_ton_minutes: float
    unloading_time_per_ton_minutes: float
    refueling: Optional[Dict] = None
    shade_factor: float = 0.85                 # Canvas tarp: reduces effective solar heat (0.82–0.88)
    maneuverability_index: float = 1.0         # Agile in traffic (Small > 1.0, Large < 1.0)
    stop_and_go_fuel_multiplier: float = 1.0   # Penalty for mass-inertia in jams
    
    def to_router_config(self) -> dict:
        """Convert to dictionary format expected by the Router."""
        return {
            'capacity_kg': self.capacity_kg,
            'fuel_consumption_empty_l_per_100km': self.fuel_consumption_empty_l_per_100km,
            'fuel_consumption_full_l_per_100km': self.fuel_consumption_full_l_per_100km,
            'fuel_efficiency_by_speed': self.fuel_efficiency_by_speed
        }


class Truck:
    """Physical truck entity with fuel, cargo, and location tracking."""
    
    def __init__(self, truck_id: str, truck_type: TruckType, 
                 warehouse_id: str, initial_location: Tuple[float, float]):
        """
        Initialize a Truck.
        
        Args:
            truck_id: Unique identifier
            truck_type: TruckType definition with operational parameters
            warehouse_id: ID of owning warehouse
            initial_location: (lat, lon) starting position
        """
        self.truck_id = truck_id
        self.truck_type = truck_type
        self.warehouse_id = warehouse_id
        
        # Location tracking
        self.current_location = initial_location  # (lat, lon)
        self.current_node = None  # Current road network node
        
        # Fuel management
        self.current_fuel_liters = truck_type.fuel_tank_liters  # Start full
        self.total_fuel_consumed_liters = 0.0
        
        # Cargo tracking
        self.current_load_kg = 0.0
        self.cargo_batches = []  # List of OrangeBatch objects
        
        # Status tracking
        self.status = "idle"  # idle, loading, in_transit, unloading, refueling
        self.assigned_order_id: Optional[str] = None
        
        # Route tracking
        self.current_route: Optional[List] = None  # List of road segments
        self.route_progress = 0  # Index in current_route
        self.segment_distance_traveled = 0.0  # Distance traveled in current segment (km)
        self.destination_node = None
        
        # Operational metrics
        self.current_speed_kmh = 0.0  # Current speed for dashboard display
        self.total_distance_km = 0.0
        self.total_deliveries = 0
        self.last_reroute_time = 0.0
        
        # Accumulative Risk (used by DisruptionModel for targeted truck accidents)
        self.risk_points = 0.0
        
    def get_load_factor(self) -> float:
        """
        Calculate current load as fraction of capacity.
        
        Returns:
            Load factor between 0.0 (empty) and 1.0 (full)
        """
        return min(1.0, self.current_load_kg / self.truck_type.capacity_kg)
    
    def get_fuel_consumption_rate(self, speed_kmh: float) -> float:
        """
        Calculate fuel consumption rate in L/100km based on load and speed.
        
        Uses linear interpolation between empty and full consumption rates,
        then applies speed-dependent efficiency multiplier.
        
        Args:
            speed_kmh: Current speed in km/h
            
        Returns:
            Fuel consumption in L/100km
        """
        load_factor = self.get_load_factor()
        
        # Linear interpolation between empty and full consumption
        base_consumption = (
            self.truck_type.fuel_consumption_empty_l_per_100km * (1 - load_factor) +
            self.truck_type.fuel_consumption_full_l_per_100km * load_factor
        )
        
        # Apply speed efficiency multiplier (interpolate between speed brackets)
        speed_multiplier = self._get_speed_efficiency_multiplier(speed_kmh)
        
        return base_consumption * speed_multiplier
    
    def _get_speed_efficiency_multiplier(self, speed_kmh: float) -> float:
        """
        Get fuel efficiency multiplier for given speed using linear interpolation.
        
        Args:
            speed_kmh: Current speed
            
        Returns:
            Efficiency multiplier (1.0 = optimal, >1.0 = worse)
        """
        # Extract speed brackets from config (e.g., "20_kmh" -> 20)
        speed_brackets = sorted([
            int(k.split('_')[0]) 
            for k in self.truck_type.fuel_efficiency_by_speed.keys()
        ])
        
        # Clamp speed to valid range
        speed_kmh = max(speed_brackets[0], min(speed_brackets[-1], speed_kmh))
        
        # Find surrounding brackets for interpolation
        lower_speed = speed_brackets[0]
        upper_speed = speed_brackets[-1]
        
        for i in range(len(speed_brackets) - 1):
            if speed_brackets[i] <= speed_kmh <= speed_brackets[i + 1]:
                lower_speed = speed_brackets[i]
                upper_speed = speed_brackets[i + 1]
                break
        
        # Get multipliers for surrounding speeds
        lower_key = f"{lower_speed}_kmh"
        upper_key = f"{upper_speed}_kmh"
        lower_mult = self.truck_type.fuel_efficiency_by_speed[lower_key]
        upper_mult = self.truck_type.fuel_efficiency_by_speed[upper_key]
        
        # Linear interpolation
        if upper_speed == lower_speed:
            return lower_mult
        
        t = (speed_kmh - lower_speed) / (upper_speed - lower_speed)
        return lower_mult + t * (upper_mult - lower_mult)
    
    def consume_idling_fuel(self, duration_minutes: float) -> float:
        """
        Consume fuel while the truck engine is idling (e.g., stuck in a traffic jam).

        All trucks in this simulation are canvas-covered (non-refrigerated), so
        idling rate is the standard diesel engine idle: ~1.5 L/hour.
        """
        # Standard engine idle rate for diesel trucks: ~1.5 L/hour
        idling_rate_l_min = 1.5 / 60.0

        fuel_idling = idling_rate_l_min * duration_minutes
        self.current_fuel_liters -= fuel_idling
        self.total_fuel_consumed_liters += fuel_idling
        self.current_fuel_liters = max(0.0, self.current_fuel_liters)
        return fuel_idling


    def consume_fuel(self, distance_km: float, speed_kmh: float, efficiency_multiplier: float = 1.0) -> float:
        """
        Consume fuel for given distance and speed.
        
        Args:
            distance_km: Distance traveled
            speed_kmh: Average speed during travel
            efficiency_multiplier: Multiplier for driver efficiency (1.0 = normal)
            
        Returns:
            Fuel consumed in liters
        """
        consumption_rate = self.get_fuel_consumption_rate(speed_kmh)
        # Apply efficiency multiplier (e.g., 1.1 = 10% more fuel used)
        consumption_rate *= efficiency_multiplier
        
        fuel_consumed = (consumption_rate / 100.0) * distance_km
        
        self.current_fuel_liters -= fuel_consumed
        self.total_fuel_consumed_liters += fuel_consumed
        self.total_distance_km += distance_km
        
        # Clamp fuel to non-negative
        self.current_fuel_liters = max(0.0, self.current_fuel_liters)
        
        return fuel_consumed
    
    def refuel(self, amount_liters: Optional[float] = None):
        """
        Refuel the truck.
        
        Args:
            amount_liters: Amount to refuel (None = fill tank)
        """
        if amount_liters is None:
            self.current_fuel_liters = self.truck_type.fuel_tank_liters
        else:
            self.current_fuel_liters = min(
                self.truck_type.fuel_tank_liters,
                self.current_fuel_liters + amount_liters
            )
    
    def get_fuel_percentage(self) -> float:
        """Get current fuel level as percentage of tank capacity."""
        return (self.current_fuel_liters / self.truck_type.fuel_tank_liters) * 100.0
    
    def is_low_fuel(self, threshold: float = 20.0) -> bool:
        """Check if fuel is below threshold percentage."""
        return self.get_fuel_percentage() < threshold
    
    def load_cargo(self, batches: List) -> bool:
        """
        Load cargo batches onto truck.
        
        Args:
            batches: List of OrangeBatch objects
            
        Returns:
            True if loaded successfully, False if exceeds capacity
        """
        total_weight = sum(batch.quantity for batch in batches)
        
        if self.current_load_kg + total_weight > self.truck_type.capacity_kg:
            return False
        
        self.cargo_batches.extend(batches)
        self.current_load_kg += total_weight
        return True
    
    def unload_cargo(self) -> List:
        """
        Unload all cargo from truck.
        
        Returns:
            List of OrangeBatch objects that were unloaded
        """
        unloaded = self.cargo_batches.copy()
        self.cargo_batches.clear()
        self.current_load_kg = 0.0
        return unloaded
    
    def get_loading_time_minutes(self) -> float:
        """Calculate time required to load current cargo."""
        tons = self.current_load_kg / 1000.0
        return tons * self.truck_type.loading_time_per_ton_minutes
    
    def get_unloading_time_minutes(self) -> float:
        """Calculate time required to unload current cargo."""
        tons = self.current_load_kg / 1000.0
        return tons * self.truck_type.unloading_time_per_ton_minutes
    
    def get_state(self) -> dict:
        """
        Get current truck state for snapshot logging.
        
        Returns:
            Dictionary with truck state data for InfluxDB
        """
        return {
            'truck_id': self.truck_id,
            'warehouse_id': self.warehouse_id,
            'truck_type': self.truck_type.name,
            'status': self.status,
            'location': {
                'lat': self.current_location[0],
                'lon': self.current_location[1]
            },
            'current_load_kg': round(self.current_load_kg, 2),
            'load_factor': round(self.get_load_factor(), 3),
            'current_fuel_liters': round(self.current_fuel_liters, 2),
            'fuel_percent': round(self.get_fuel_percentage(), 1),
            'speed_kmh': round(self.current_speed_kmh, 1),  # Current speed for dashboard
            'total_distance_km': round(self.total_distance_km, 2),
            'total_fuel_consumed_liters': round(self.total_fuel_consumed_liters, 2),
            'total_deliveries': self.total_deliveries,
            'assigned_order_id': self.assigned_order_id if self.assigned_order_id else '',
            'route_progress': self.route_progress,
            'cargo_batches_count': len(self.cargo_batches)
        }
    
    def __repr__(self) -> str:
        return (f"Truck(id={self.truck_id}, type={self.truck_type.name}, "
                f"status={self.status}, load={self.current_load_kg:.0f}kg, "
                f"fuel={self.get_fuel_percentage():.1f}%)")
