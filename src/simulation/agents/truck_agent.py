"""TruckAgent - Autonomous agent for truck operations."""

import logging
from typing import Optional, List, Dict, Tuple
from ..entities import Truck
from ..entities import OrangeBatch
from ..entities import Driver
from ..network.router import Router
from ..network.road_network import RoadNetwork
from ..sensor import GPSSensor, TemperatureSensor, StockSensor
from ..ekf import TruckEKF
from ..edge_brain import EdgeBrain
import numpy as np

# Module-level logger
logger = logging.getLogger(__name__)


class TruckAgent:
    """
    Autonomous agent managing truck operations.
    
    Responsibilities:
    - Move along route consuming fuel
    - Monitor cargo condition (temperature, RSL)
    - Dynamic rerouting based on traffic/accidents
    - Fuel management and low fuel warnings
    - Publish telemetry data
    """
    
    def __init__(self, truck: Truck, road_network: RoadNetwork, 
                 router: Router, config: Dict, ai_manager=None, event_bus=None):
        """
        Initialize TruckAgent.
        
        Args:
            truck: Truck entity to control
            road_network: Road network for navigation
            router: Router for pathfinding
            config: Configuration dict
            ai_manager: Optional AIManager for RL routing and RSL prediction
        """
        self.truck = truck
        self.road_network = road_network
        self.router = router
        self.config = config  # Store for refueling params
        self.ai_manager = ai_manager  # AI/ML ecosystem manager
        
        # Configuration
        routing_config = config.get('routing', {})
        cargo_config = config.get('cargo', {})
        logging_config = config.get('logging', {})
        sensor_config = config.get('sensors', {})
        
        # IoT Sensors (New Layer)
        self.sensors = {
            'gps': GPSSensor(
                f"gps_{truck.truck_id}", 
                {'noise_std_dev': sensor_config.get('gps_noise', 0.00005)}
            ),
            'temp': TemperatureSensor(
                f"temp_{truck.truck_id}",
                {'noise_std_dev': sensor_config.get('temp_noise', 0.5)}
            ),
            'stock': StockSensor(
                f"stock_{truck.truck_id}",
                {'noise_std_dev': sensor_config.get('stock_noise_kg', 2.0)}
            )
        }
        
        # Reroute interval: clamp to at least one timestep so the check always
        # fires regardless of what timestep the simulation is running at.
        # e.g. config=5min, timestep=1min → fires every 5 ticks (correct)
        #      config=5min, timestep=10min → fires every tick (correct — better than never)
        _configured_interval = routing_config.get('recalculation_interval_minutes', 5)
        _time_step = config.get('simulation', {}).get('time_step_minutes', 5)
        self.reroute_interval = max(_configured_interval, _time_step)
        self.reroute_threshold = routing_config.get('recalculation_threshold', 0.10)  # 10% improvement
        self.rsl_critical_threshold = routing_config.get('rsl_critical_threshold', 70.0)  # % RSL — config-driven
        self.low_fuel_threshold = 20.0  # 20% fuel remaining triggers warning
        
        # State tracking
        self.last_reroute_check = 0.0
        self.last_telemetry_time = 0.0
        self.current_speed_kmh = 0.0
        
        # Events to publish
        self.events_to_publish = []
        
        # Track ambient temperature for telemetry (canvas trucks use ambient)
        self.last_ambient_temperature = 25.0
        
        # Driver Model
        driver_config = config.get('driver_model', {})
        self.driver = Driver(f"DRV_{truck.truck_id}", driver_config)
        
        # Refueling state
        self.is_refueling = False
        self.refuel_remaining_time = 0.0
        
        # Enforce initial risk points
        self.truck.risk_points = 0.0
        
        logger.debug(f"TruckAgent {self.truck.truck_id} initialized (Route-Aware + RSL Sync)")
        
        # Loading/Unloading state (new)
        self.current_task_remaining_min = 0.0
        
        # Weather tracking (initialized before first update)
        self.current_weather = 'clear'  # Default until first weather update from engine

        # Perceived State — EKF-fused clean estimates (replaces raw _perceived_* fields)
        self._perceived_location = truck.current_location
        self._perceived_temp = 25.0
        self._perceived_load = 0.0

        # Extended Kalman Filter: one per truck, fuses GPS/Temp/Stock sensor streams
        self._ekf = TruckEKF(
            truck_id=truck.truck_id,
            gps_noise=sensor_config.get('gps_noise', 0.00005),
            temp_noise=sensor_config.get('temp_noise', 0.5),
            stock_noise=sensor_config.get('stock_noise_kg', 2.0),
        )

        # Edge Brain: lightweight per-truck emergency routing AI
        # Fires BEFORE the Central Brain (OptimizationPod/PPO) when
        # a local emergency is detected (critical RSL, low fuel, accident ahead).
        self._edge_brain = EdgeBrain(truck_id=truck.truck_id, config=config)

        # Subscribe to EventBus for reactive responses
        if event_bus is not None:
            event_bus.subscribe('accident_alert', self._on_accident_alert)
            event_bus.subscribe('weather_change', self._on_weather_change)

    def _on_accident_alert(self, data: dict):
        """
        React to an accident alert from the EventBus.

        If the truck is in transit and its current route passes through the
        affected segment, force an immediate reroute check on the next update.
        This is proactive — the truck doesn't have to physically reach the
        blocked segment before rerouting.
        """
        if self.truck.status != 'in_transit':
            return
        if not self.truck.current_route:
            return

        affected_segment = data.get('segment_id')
        if not affected_segment:
            return

        # Check if the affected segment is anywhere on the remaining route
        segments = self.truck.current_route.get('segments', [])
        remaining = segments[self.truck.route_progress:]
        if affected_segment in remaining:
            # Force reroute check on next update by resetting the interval timer.
            # Setting to -(reroute_interval) ensures the elapsed check fires on the
            # very next tick regardless of current simulation time.
            self.last_reroute_check = -self.reroute_interval
            logger.info(
                f"[EVENTBUS] Truck {self.truck.truck_id} proactively rerouting "
                f"— accident on segment {affected_segment} is on its planned route."
            )

    def _on_weather_change(self, data: dict):
        """
        React to a weather change from the EventBus.

        Updates the truck's current weather state immediately so that
        accident probability and cargo decay use the correct conditions
        without waiting for the next engine update cycle.
        """
        new_state = data.get('new_state')
        if new_state:
            self.current_weather = new_state

        
    def update(self, current_time: float, time_step: float, ambient_temperature: float = 25.0, ambient_humidity: float = 50.0, current_weather: str = 'clear') -> List[Dict]:
        """
        Update truck state for one time step.
        
        Args:
            current_time: Current simulation time
            time_step: Time step duration
            ambient_temperature: Current ambient temperature
            ambient_humidity: Current relative humidity (%)
            current_weather: Current weather state (clear, rain, etc.)
            
        Returns:
            List of events generated
        """
        # Phase 3 IoT Layer: raw sensor reads → EKF fusion → clean perceived state
        gps_raw   = self.sensors['gps'].read(self.truck.current_location, current_time)
        temp_raw  = self.sensors['temp'].read(ambient_temperature, current_time)
        stock_raw = self.sensors['stock'].read(self.truck.current_load_kg, current_time)

        clean_loc, clean_temp, clean_stock = self._ekf.update(
            gps_reading=gps_raw,
            temp_reading=temp_raw,
            stock_reading=stock_raw,
            true_location=self.truck.current_location,
            true_temp=ambient_temperature,
            true_stock=self.truck.current_load_kg,
        )
        self._perceived_location = clean_loc
        self._perceived_temp     = clean_temp
        self._perceived_load     = clean_stock

        # Clear events from previous step
        self.events_to_publish = []
        
        # Store current weather for accident checks
        self.current_weather = current_weather
        

        # Handle refueling (blocks all other actions)
        if self.is_refueling:
            self.refuel_remaining_time -= time_step
            if self.refuel_remaining_time <= 0:
                self.is_refueling = False
                self.truck.refuel()
                # Log actual refuel completion with correct duration
                self.events_to_publish.append({
                    'type': 'refueled',
                    'truck_id': self.truck.truck_id,
                    'time': current_time,
                    'fuel_added_liters': self.truck.truck_type.fuel_tank_liters
                })
            # Don't check fuel while refueling - only after completion
            return self.events_to_publish
        
        # If truck is idle, nothing to update
        if self.truck.status == 'idle':
            return self.events_to_publish
        
        # If loading, handle loading time
        if self.truck.status == 'loading':
            self.current_task_remaining_min -= time_step
            if self.current_task_remaining_min <= 0:
                # Loading finished -> Depart
                self.truck.status = "in_transit"
                self.events_to_publish.append({
                    'type': 'loading_complete',
                    'truck_id': self.truck.truck_id,
                    'time': current_time,
                    'order_id': self.truck.assigned_order_id
                })
                
                # Now trigger departure event (moved from assign_delivery)
                segments = self.truck.current_route.get('segment_objects', []) if self.truck.current_route else []
                self.events_to_publish.append({
                    'type': 'truck_departure',
                    'truck_id': self.truck.truck_id,
                    'time': current_time,
                    'order_id': self.truck.assigned_order_id,
                    'destination': self.truck.destination_node,
                    'cargo_kg': self.truck.current_load_kg,
                    'route_segments': len(segments)
                })
            return self.events_to_publish

        # Handle Unloading Delay
        if self.truck.status == "unloading":
            self.current_task_remaining_min -= time_step
            if self.current_task_remaining_min <= 0:
                # Unloading finished
                self.truck.status = "unloading_complete"
                self.events_to_publish.append({
                    'type': 'unloading_complete',
                    'truck_id': self.truck.truck_id,
                    'time': current_time,
                    'order_id': self.truck.assigned_order_id
                })
            return self.events_to_publish
            
        # Update driver state
        is_moving = self.truck.status == "in_transit" and not self.is_refueling
        self.driver.update(time_step, is_moving)
        
        # Check for driver sleep (blocks all movement)
        if self.driver.is_sleeping:
            return self.events_to_publish
        
        if self.truck.status == "in_transit":
            # Check for driver break
            if self.driver.is_on_break:
                # Truck stops during break
                return self.events_to_publish

            self._update_movement(current_time, time_step)
            self._update_cargo(current_time, time_step, ambient_temperature, ambient_humidity)
            self._check_rerouting(current_time)
            self._check_fuel_level(current_time)
            
            # Publish telemetry every timestep (synchronized with snapshots)
            if current_time - self.last_telemetry_time >= time_step:
                self._publish_telemetry(current_time)
                self.last_telemetry_time = current_time
        
        return self.events_to_publish
    
    def _publish_telemetry(self, current_time: float):
        """
        Publish high-frequency telemetry for InfluxDB dashboard.

        Emits a single flat 'telemetry' event combining:
        - IoT sensor reads (GPS with noise, temperature, stock weight)
        - Zone and ripple backpressure from the traffic model
        - Cargo RSL, fuel, speed, driver state
        - Ground-truth values for comparison/debugging

        The engine routes 'telemetry' events to log_truck_telemetry(),
        which writes them to the truck_telemetry InfluxDB measurement.
        Field names match what database.py and the API routers query.
        """
        # --- Current segment for zone / ripple data ---
        planned = getattr(self.truck, 'current_route', None)
        prog = getattr(self.truck, 'route_progress', 0)
        current_seg = None
        if planned and 'segment_objects' in planned:
            if prog < len(planned['segment_objects']):
                current_seg = planned['segment_objects'][prog]

        zone = getattr(current_seg, 'zone_type', 'HIGHWAY')
        ripple = 0.0
        if current_seg and hasattr(self.road_network, 'traffic_model'):
            ripple = getattr(
                self.road_network.traffic_model, 'current_ripples', {}
            ).get(current_seg.segment_id, 0.0)

        # --- Cargo RSL ---
        avg_rsl = 100.0
        if self.truck.cargo_batches:
            avg_rsl = sum(b.current_rsl for b in self.truck.cargo_batches) / len(self.truck.cargo_batches)

        # --- IoT sensor reads: use EKF-fused perceived state (already computed in update()) ---
        # _perceived_location, _perceived_temp, _perceived_load are clean EKF estimates.
        gps_location = self._perceived_location   # EKF-cleaned (lat, lon)
        temp_reading = self._perceived_temp        # EKF-cleaned °C
        cargo_reading = self._perceived_load       # EKF-cleaned kg

        # --- Emit flat telemetry event ---
        # Field names match database.py queries and API routers exactly.
        self.events_to_publish.append({
            'type': 'telemetry',
            'truck_id': self.truck.truck_id,
            'time': current_time,
            # Location (EKF-cleaned IoT) — engine extracts lat/lon separately
            'location': gps_location,
            'location_true': self.truck.current_location,
            # Motion
            'speed_kmh': self.current_speed_kmh,
            # Fuel
            'fuel_liters': self.truck.current_fuel_liters,
            'fuel_percent': self.truck.get_fuel_percentage(),
            # Cargo
            'cargo_kg': cargo_reading,
            'cargo_rsl': round(avg_rsl, 2),
            # Temperature
            'temperature_celsius': temp_reading,
            'temperature_true': self.last_ambient_temperature,
            # Driver
            'driver_fatigue': self.driver.fatigue_level,
            'driver_fatigue_hours': round(self.driver.accumulated_fatigue / 60.0, 3),
            'driver_status': 'Break' if self.driver.is_on_break else (
                'Sleeping' if self.driver.is_sleeping else 'Driving'
            ),
            # Spatial / traffic context
            'zone_type': zone,
            'ripple_level': ripple,
            'risk_points': self.truck.risk_points,
            'current_segment': getattr(current_seg, 'segment_id', 'unknown'),
            # EKF sensor confidence metrics
            **self._ekf.get_diagnostics(),
            # Status
            'status': self.truck.status,
            'source': 'ekf_fused',
        })

    
    def _update_risk_and_fatigue(self, distance_km: float, duration_min: float, segment, weather: str):
        """
        Update risk points and driver fatigue based on conditions.
        
        Args:
            distance_km: Distance traveled in this sub-step
            duration_min: Duration of this sub-step
            segment: Current road segment
            weather: Current weather condition
        """
        # 1. Accumulate Risk Points
        # Speed risk: increases exponentially above 60 km/h
        speed_risk = max(0.0, (self.current_speed_kmh - 60) / 40.0) ** 1.2
        
        # Weather risk mapping
        weather_risk_map = {
            'clear': 0.0, 'partly_cloudy': 0.1, 'cloudy': 0.2,
            'light_rain': 0.4, 'rain': 0.8, 'heavy_rain': 1.5, 'fog': 1.2
        }
        weather_risk = weather_risk_map.get(weather, 0.0)
        
        # Road risk mapping
        road_risk_map = {
            'motorway': 0.0, 'trunk': 0.1, 'primary': 0.2,
            'secondary': 0.4, 'tertiary': 0.6, 'residential': 0.5, 'unclassified': 0.5
        }
        road_risk = road_risk_map.get(segment.road_type if segment else 'secondary', 0.3)
        
        # Fatigue risk: increases with driver fatigue score
        fatigue_risk = min(1.0, self.driver.accumulated_fatigue / (8 * 60))
        
        # Base risk + modifiers
        base_risk_per_km = 1.0
        total_risk_increment = (base_risk_per_km + speed_risk + weather_risk + road_risk + fatigue_risk) * distance_km
        self.truck.risk_points += total_risk_increment
        
        # 2. Dynamic Risk Decay (prevents permanent risk build-up)
        # Decay rate: 0.1 points per minute of driving (subtle)
        self.truck.risk_points = max(0.0, self.truck.risk_points - 0.05 * duration_min)

    def _update_movement(self, current_time: float, time_step: float):
        """
        Move truck along route, consuming fuel.
        
        Tracks position within current segment realistically.
        
        Args:
            current_time: Current simulation time in minutes
            time_step: Time step duration in minutes
        """
        time_remaining_in_step = time_step
        
        while time_remaining_in_step > 0.001:
            # Check if route exists and has segment_objects
            if not self.truck.current_route:
                self._handle_arrival(current_time)
                return
            
            # Get segment objects from route
            segments = self.truck.current_route.get('segment_objects', [])
            if not segments or self.truck.route_progress >= len(segments):
                # Reached destination
                self._handle_arrival(current_time)
                return
            
            # Get current road segment
            segment = segments[self.truck.route_progress]
            
            # Check if segment is blocked
            if segment.is_blocked:
                # Trigger immediate reroute
                self._perform_reroute(current_time, reason="blocked_segment")
                return
            
            # NEW: Use LTM-aware maneuverability logic from RoadSegment
            # Calculate effective segment speed for this specific truck type
            effective_segment_speed = segment.get_travel_time_minutes(
                truck_type_config={'maneuverability_index': self.truck.truck_type.maneuverability_index}
            )
            # segment.get_travel_time_minutes returns time, we need speed:
            # speed = dist / time
            if effective_segment_speed > 0 and segment.length_km > 0.0001:
                 effective_speed_kmh = (segment.length_km / effective_segment_speed) * 60.0
            else:
                 effective_speed_kmh = 1.0 # Jammed or zero-length safety
            
            # Calculate speed (limited by truck max speed, effective segment speed, and driver factors)
            max_speed = min(
                self.truck.truck_type.max_speed_kmh,
                effective_speed_kmh
            )
            
            # Apply driver speed multiplier (skill/fatigue)
            self.current_speed_kmh = max_speed * self.driver.get_speed_multiplier()
            
            # Sync speed to truck entity for snapshot logging
            self.truck.current_speed_kmh = self.current_speed_kmh
            
            # Calculate distance that can be traveled in this time step
            distance_available_km = (self.current_speed_kmh / 60.0) * time_remaining_in_step
            
            # SCIENTIFIC FIX (M4): Handle idling fuel consumption if stuck in traffic
            # Guarded against zero-length segments or arriving state
            if self.current_speed_kmh < 2.0 and not segment.is_blocked:
                self.truck.consume_idling_fuel(time_remaining_in_step)
            
            # Calculate remaining distance in current segment
            segment_remaining_km = segment.length_km - self.truck.segment_distance_traveled
            
            if distance_available_km >= segment_remaining_km:
                # Will complete current segment and possibly more
                distance_to_travel = segment_remaining_km
                
                # Calculate fuel penalty for high-density traffic (Stop-and-Go)
                jam_density = self.config.get('traffic', {}).get('jam_density_per_lane', {}).get(segment.road_type, 100)
                density_ratio = segment.current_traffic_density / jam_density if jam_density > 0 else 0
                
                traffic_fuel_multiplier = 1.0
                if density_ratio > 0.7: # Critical congestion
                    # Apply the truck-type specific stop-and-go penalty
                    traffic_fuel_multiplier = self.truck.truck_type.stop_and_go_fuel_multiplier
                
                # Consume fuel for this segment (adjusted by driver efficiency and traffic)
                fuel_mult = self.driver.get_fuel_efficiency_multiplier() * traffic_fuel_multiplier
                self.truck.consume_fuel(distance_to_travel, self.current_speed_kmh, efficiency_multiplier=fuel_mult)
                
                # Update location to end of segment
                self.truck.current_location = segment.end_location
                self.truck.current_node = segment.end_node

                # Update physical state: Risk Accumulation for this segment portion
                time_spent_on_segment = (distance_to_travel / self.current_speed_kmh) * 60.0 if self.current_speed_kmh > 0 else 0
                self._update_risk_and_fatigue(distance_to_travel, time_spent_on_segment, segment, self.current_weather)

                # Feed completed segment traversal into ETA model for online learning
                if self.ai_manager is not None and time_spent_on_segment > 0:
                    hour = (current_time / 60.0) % 24.0
                    self.ai_manager.record_segment_traversal(
                        segment, time_spent_on_segment, hour, self.current_weather
                    )

                # Move to next segment
                self.truck.route_progress += 1
                self.truck.segment_distance_traveled = 0.0
                
                # Check if reached destination
                segments = self.truck.current_route.get('segment_objects', [])
                if self.truck.route_progress >= len(segments):
                    self._handle_arrival(current_time)
                    return

                time_remaining_in_step -= time_spent_on_segment
                
                if time_remaining_in_step > 0.01:
                    continue
                return
            else:
                # Will not complete current segment in this time step
                fuel_mult = self.driver.get_fuel_efficiency_multiplier()
                self.truck.consume_fuel(distance_available_km, self.current_speed_kmh, efficiency_multiplier=fuel_mult)
                
                # Update physical state: Risk Accumulation for this step's portion
                self.truck.segment_distance_traveled += distance_available_km
                self._update_risk_and_fatigue(distance_available_km, time_remaining_in_step, segment, self.current_weather)
                
                # Interpolate exact position for telemetry
                self.truck.current_location = segment.interpolate_position(self.truck.segment_distance_traveled)
                
                # Check for truck accident (probabilistic based on risk points)
                if self._check_truck_accident(current_time, segment):
                    return  # Truck destroyed or stopped, stop processing movement
                
                time_remaining_in_step = 0.0
    
    def _update_cargo(self, current_time: float, time_step: float, ambient_temperature: float, ambient_humidity: float):
        """Update cargo quality (RSL) - all trucks are canvas-covered (no refrigeration).

        Canvas tarpaulins provide partial shade against direct solar radiation but
        do not cool the cargo.  A truck-type-specific shade_factor (0.82–0.88)
        attenuates the *solar component* of the heat load:

            effective_temp = ambient + solar_addition * (1 - shade_factor)

        We approximate solar_addition as the delta between peak afternoon ambient
        and a reference 25 °C baseline, capped at 0 so we never *reduce* temp
        below ambient on cool days.  On a 42 °C Nagpur afternoon this gives roughly
        a 2–4 °C reduction inside the tarp — realistic and scientifically defensible.
        """
        if not self.truck.cargo_batches:
            return

        self.last_ambient_temperature = ambient_temperature

        # Read shade_factor directly from the TruckType entity (set from config at startup)
        shade_factor = self.truck.truck_type.shade_factor

        # Solar addition: how much extra heat the sun adds above 25 °C baseline.
        # This is the portion the tarp can partially block.
        solar_addition = max(0.0, ambient_temperature - 25.0)
        effective_temp = ambient_temperature - solar_addition * (1.0 - shade_factor)

        # Humidity inside canvas body stays close to ambient (no humidity control)
        effective_humidity = ambient_humidity

        # Derive current segment from route progress
        seg = None
        if (self.truck.current_route and
                self.truck.route_progress < len(self.truck.current_route.get('segment_objects', []))):
            seg = self.truck.current_route['segment_objects'][self.truck.route_progress]


        vibe_map = {
            'motorway': 0.8, 'trunk': 1.0, 'primary': 1.2,
            'secondary': 1.5, 'tertiary': 1.8, 'residential': 2.0
        }
        vibration_g = vibe_map.get(getattr(seg, 'road_type', 'secondary'), 1.2)

        for batch in self.truck.cargo_batches:
            batch.update_rsl(effective_temp, effective_humidity, current_time, vibration_g)

            if batch.is_spoiled():
                self.events_to_publish.append({
                    'type': 'cargo_spoiled',
                    'truck_id': self.truck.truck_id,
                    'batch_id': batch.batch_id,
                    'time': current_time
                })

    def _apply_strategy_action(self, action: int, current_time: float, reason: str = 'ai') -> bool:
        """
        Translate a strategy action (0=balanced, 1=speed, 2=fuel) into a new route.

        Shared by BOTH the Edge Brain (local emergency override) and the
        Central Brain (PPO macro strategy) so the routing call is never
        duplicated.  Returns True if a new route was successfully applied.

        Args:
            action:       0=balanced, 1=speed, 2=fuel
            current_time: Current simulation time in minutes
            reason:       Label for the route_changed event ('edge_emergency' or 'central_ppo')
        """
        strategy_names = ['balanced', 'speed', 'fuel']
        strategy_name = strategy_names[action] if action in {0, 1, 2} else 'balanced'

        # Cost weights: read from config, fall back to hardcoded defaults
        default_weights = [
            {'distance_km': 1.0, 'time_minutes': 0.5, 'fuel_liters': 2.0},   # balanced
            {'distance_km': 0.5, 'time_minutes': 2.0, 'fuel_liters': 0.5},   # speed
            {'distance_km': 1.0, 'time_minutes': 0.3, 'fuel_liters': 4.0},   # fuel
        ]
        strategies = self.config.get('routing', {}).get('strategies', {})
        weights = strategies.get(strategy_name, default_weights[action])

        truck_type_dict = {
            'capacity_kg': self.truck.truck_type.capacity_kg,
            'fuel_consumption_empty_l_per_100km': self.truck.truck_type.fuel_consumption_empty_l_per_100km,
            'fuel_consumption_full_l_per_100km': self.truck.truck_type.fuel_consumption_full_l_per_100km,
            'fuel_efficiency_by_speed': self.truck.truck_type.fuel_efficiency_by_speed,
        }

        try:
            new_route = self.router.find_path(
                self.truck.current_node,
                self.truck.destination_node,
                truck_type_config=truck_type_dict,
                load_fraction=self.truck.get_load_factor(),
                avoid_blocked=True,
                current_time=current_time,
                cost_weights=weights,
            )
            if new_route is not None:
                self.truck.current_route = new_route
                self.truck.route_progress = 0
                self.truck.segment_distance_traveled = 0.0
                self.events_to_publish.append({
                    'type': 'route_changed',
                    'truck_id': self.truck.truck_id,
                    'time': current_time,
                    'reason': f'{reason}_{strategy_name}',
                    'improvement_percent': 0,
                    'old_segments': 0,
                    'new_segments': len(new_route.get('segments', [])),
                })
                return True
        except Exception as e:
            logger.debug(f'[{reason}] Strategy reroute failed for {self.truck.truck_id}: {e}')
        return False

    def _check_rerouting(self, current_time: float):
        """Periodically check if rerouting would improve delivery time.

        RSL-urgency override: if cargo RSL drops below the critical threshold,
        force an immediate reroute check that prioritises speed over fuel cost.
        This is adaptive behaviour — the truck responds to real-time cargo state
        without waiting for the next scheduled interval.

        If the OptimizationPod (RL policy) is enabled, it is consulted first.
        The heuristic cost-comparison runs as a fallback when RL is disabled
        or returns a no-op action.

        Args:
            current_time: Current simulation time in minutes
        """
        # --- RSL-urgency override ---
        # If cargo is critically fresh-sensitive, force an immediate reroute check
        # regardless of the normal interval. This makes routing adaptive to cargo state.
        if self.truck.cargo_batches:
            avg_rsl = sum(b.current_rsl for b in self.truck.cargo_batches) / len(self.truck.cargo_batches)
            if avg_rsl < self.rsl_critical_threshold:
                # Force check — reset timer so it fires now
                self.last_reroute_check = current_time - self.reroute_interval

        # Only check at specified intervals
        if current_time - self.last_reroute_check < self.reroute_interval:
            return

        self.last_reroute_check = current_time

        # --- EDGE BRAIN: Emergency override (fires BEFORE Central Brain) ---
        # Build the compact 5-D edge observation and check for emergencies.
        # If an emergency is detected, Edge Brain returns an action immediately
        # and we skip the Central Brain (PPO) for this tick.
        if not self.truck.current_route or not self.truck.destination_node:
            return

        if self.ai_manager is not None:
            # Gather inputs for Edge obs
            avg_rsl = (
                sum(b.current_rsl for b in self.truck.cargo_batches)
                / len(self.truck.cargo_batches)
                if self.truck.cargo_batches else 100.0
            )
            fuel_pct = self.truck.get_fuel_percentage()

            # Accident-ahead: check next 3 segments on planned route
            planned_segs = self.truck.current_route.get('segment_objects', [])
            prog = self.truck.route_progress
            accident_ahead = any(
                getattr(s, 'is_blocked', False)
                for s in planned_segs[prog:prog + 3]
                if s is not None
            )

            # Ripple on current segment
            curr_seg = planned_segs[prog] if prog < len(planned_segs) else None
            ripple = 0.0
            if curr_seg and hasattr(self.road_network, 'traffic_model'):
                ripple = self.road_network.traffic_model.current_ripples.get(
                    curr_seg.segment_id, 0.0
                )

            # ETA from remaining segments (physics fallback)
            eta_minutes = sum(
                s.get_travel_time_minutes()
                for s in planned_segs[prog:]
                if s is not None
            )

            edge_obs = self._edge_brain.build_observation(
                rsl_pct=avg_rsl,
                fuel_pct=fuel_pct,
                accident_ahead=accident_ahead,
                ripple_level=ripple,
                eta_minutes=eta_minutes,
            )

            edge_action = self._edge_brain.select_action(edge_obs, current_time)

            if edge_action is not None:
                # Emergency detected — apply Edge action and bypass Central Brain
                self._apply_strategy_action(
                    action=edge_action,
                    current_time=current_time,
                    reason='edge_emergency',
                )
                return

        # --- CENTRAL BRAIN: RL policy query (PPO / OptimizationPod) ---
        if (self.ai_manager is not None
                and self.ai_manager.optimization_pod.enabled):
            obs = self._build_obs(current_time)
            if obs is not None:
                action = self.ai_manager.select_routing_action(self.truck.truck_id, obs)
                self._apply_strategy_action(
                    action=action,
                    current_time=current_time,
                    reason='central_ppo',
                )
            return  # Don't fall through to heuristic when RL is enabled


        # --- Heuristic cost-comparison fallback ---
        
        segments = self.truck.current_route.get('segment_objects', [])
        if not segments:
            return
        
        # Use ETAForecaster if trained, otherwise fall back to physics
        current_dt_hour = (current_time / 60.0) % 24.0
        weather = self.current_weather

        if (self.ai_manager is not None
                and self.ai_manager.prediction_pod.eta is not None
                and self.ai_manager.prediction_pod.eta._n_samples >= self.ai_manager.prediction_pod.eta.min_samples):
            # Learned ETA: accounts for real traffic patterns at this hour
            current_route_cost = self.ai_manager.prediction_pod.eta.predict_route(
                self.truck.current_route, current_dt_hour, weather,
                self.truck.route_progress
            )
        else:
            # Physics fallback
            current_route_cost = sum(
                seg.get_travel_time_minutes()
                for seg in segments[self.truck.route_progress:]
            )
        
        # Find alternative route from current position
        try:
            # Convert TruckType to dict for router
            truck_type_dict = {
                'capacity_kg': self.truck.truck_type.capacity_kg,
                'fuel_consumption_empty_l_per_100km': self.truck.truck_type.fuel_consumption_empty_l_per_100km,
                'fuel_consumption_full_l_per_100km': self.truck.truck_type.fuel_consumption_full_l_per_100km,
                'fuel_efficiency_by_speed': self.truck.truck_type.fuel_efficiency_by_speed
            }
            
            new_route = self.router.find_path(
                self.truck.current_node,
                self.truck.destination_node,
                truck_type_config=truck_type_dict,
                load_fraction=self.truck.get_load_factor(),
                avoid_blocked=True,
                current_time=current_time
            )
            
            if new_route:
                new_segments = new_route.get('segment_objects', [])
                new_route_cost = sum(seg.get_travel_time_minutes() for seg in new_segments)
                
                # When cargo RSL is critical, accept even small improvements (2% vs normal 10%)
                # so the truck aggressively seeks the fastest path to preserve freshness.
                avg_rsl = (sum(b.current_rsl for b in self.truck.cargo_batches) / len(self.truck.cargo_batches)
                           if self.truck.cargo_batches else 100.0)
                effective_threshold = 0.02 if avg_rsl < self.rsl_critical_threshold else self.reroute_threshold

                # Switch if new route is significantly better
                if current_route_cost > 0:
                    improvement = (current_route_cost - new_route_cost) / current_route_cost
                    if improvement > effective_threshold:
                        reason = "rsl_urgency_reroute" if avg_rsl < self.rsl_critical_threshold else "better_route"
                        self._perform_reroute(current_time, reason=reason,
                                             improvement=improvement)
        except (AttributeError, KeyError, ValueError, IndexError) as e:
            # Routing failed due to missing data or invalid state - continue with current route
            # These are expected exceptions from routing logic, not bugs
            pass
        except Exception as e:
            # Unexpected exception - log it for debugging
            logger.warning(f"Unexpected error in rerouting check for truck {self.truck.truck_id}: {type(e).__name__}: {e}")
            # Continue with current route
    
    def _build_obs(self, current_time: float):
        """
        Layout (OBS_DIM=25):
          0: rsl_norm
          1: critical_flag
          2: fuel_norm
          3: load_norm
          4: dist_to_dest_norm
          5-9: traffic_0..4 (density)
          10-14: accident_0..4
          15: accident_ahead_in_planned
          16: has_planned_route
          17: current_zone (0.1=Office, 0.4=Shopping, 0.7=Res, 1.0=Hwy)
          18-21: neighbor_ripples (0-1 current backpressure)
          22: time_of_day_norm
          23: sin(hour)
          24: cos(hour)
        """
        import math as _math
        import numpy as np

        if self.truck.current_node is None:
            return None

        # RSL
        if self.truck.cargo_batches:
            avg_rsl = sum(b.current_rsl for b in self.truck.cargo_batches) / len(self.truck.cargo_batches)
        else:
            avg_rsl = 100.0
        rsl_norm = avg_rsl / 100.0
        critical_flag = 1.0 if avg_rsl < 70.0 else 0.0

        # Fuel & load
        fuel_norm = self.truck.get_fuel_percentage() / 100.0
        # Phase 3 FIX: Use perceived load instead of ground truth for RL obs
        load_norm = self._perceived_load / self.truck.truck_type.capacity_kg
        
        # Route logic
        planned = getattr(self.truck, 'current_route', None)
        has_planned = 1.0 if planned and planned.get('segments') else 0.0
        
        prog = getattr(self.truck, 'route_progress', 0)
        remaining_km = 0.0
        accident_ahead = 0.0
        if has_planned:
            segs = planned.get('segment_objects', [])
            # Phase 3 FIX: Calculate actual remaining kilometers instead of segment counts
            for s in segs[prog:]:
                if s: remaining_km += s.length_km
                
            # Check next 5 segments for accidents
            lookahead = segs[prog:prog+5]
            for s in lookahead:
                if s and getattr(s, 'is_blocked', False):
                    accident_ahead = 1.0
                    break
                    
        # Normalize distance (typical max route 100km)
        dist_to_dest_norm = min(1.0, remaining_km / 100.0)

        # Adjacent traffic (up to 5 neighbors)
        neighbors = self.road_network.get_adjacent_nodes(self.truck.current_node)
        traffic_obs = []
        accident_obs = []
        for i in range(5):
            if i < len(neighbors):
                seg_id = self.road_network.get_segment_id_between(
                    self.truck.current_node, neighbors[i]
                )
                seg = self.road_network.get_segment(seg_id) if seg_id else None
                jam_density = self.config.get('traffic', {}).get('jam_density_per_lane', {}).get(getattr(seg, 'road_type', 'secondary'), 100)
                density = (seg.current_traffic_density / jam_density if seg is not None else 0.0)
                is_acc = 1.0 if (seg and getattr(seg, 'is_blocked', False)) else 0.0
                traffic_obs.append(float(min(1.0, max(0.0, density))))
                accident_obs.append(is_acc)
            else:
                traffic_obs.append(0.0)
                accident_obs.append(0.0)

        # ------------------------------------------------------------------
        # NEW HIGH-FIDELITY FEATURES (Zone & Ripple Radar)
        # ------------------------------------------------------------------
        # Current Zone awareness
        current_seg_obj = None
        if planned and planned.get('segment_objects'):
             if prog < len(planned['segment_objects']):
                 current_seg_obj = planned['segment_objects'][prog]
        
        # Fallback to any neighboring segment for zone data if at a node
        if not current_seg_obj and neighbors:
            f_seg_id = self.road_network.get_segment_id_between(self.truck.current_node, neighbors[0])
            current_seg_obj = self.road_network.get_segment(f_seg_id) if f_seg_id else None

        zone_map = {"OFFICE": 0.1, "SHOPPING": 0.4, "RESIDENTIAL": 0.7, "HIGHWAY": 1.0}
        zone_val = zone_map.get(getattr(current_seg_obj, 'zone_type', 'HIGHWAY'), 0.0)
        
        # Ripple Radar (Backpressure on 4 neighbors)
        ripple_obs = []
        for i in range(4):
            if i < len(neighbors):
                seg_id = self.road_network.get_segment_id_between(self.truck.current_node, neighbors[i])
                # Unified Ripple Radar: Access traffic backpressure via RoadNetwork link
                ripple = 0.0
                if hasattr(self.road_network, 'traffic_model'):
                     ripple = getattr(self.road_network.traffic_model, 'current_ripples', {}).get(seg_id, 0.0)
                
                # Normalize by actual neighbor segment road-type jam density for Brain-Sync consistency
                n_seg = self.road_network.get_segment(seg_id) if seg_id else None
                n_road_type = getattr(n_seg, 'road_type', 'secondary') if n_seg else 'secondary'
                jam_d = self.config.get('traffic', {}).get('jam_density_per_lane', {}).get(n_road_type, 100)
                ripple_obs.append(min(1.0, ripple / jam_d))
            else:
                ripple_obs.append(0.0)

        # Time encoding
        hour_frac = (current_time / 60.0) % 24.0
        hour_rad = hour_frac * (2.0 * _math.pi / 24.0)
        time_norm = hour_frac / 24.0

        return np.clip(np.array(
            [
                rsl_norm,
                critical_flag,
                fuel_norm,
                load_norm,
                dist_to_dest_norm,
                *traffic_obs,
                *accident_obs,
                accident_ahead,
                has_planned,
                zone_val,
                *ripple_obs,
                time_norm,
                _math.sin(hour_rad),
                _math.cos(hour_rad),
            ],
            dtype=np.float32,
        ), -1.0, 1.0)

    def _perform_reroute(self, current_time: float, reason: str, improvement: float = 0.0):
        """
        Execute rerouting to avoid blockage or improve time.
        
        Args:
            current_time: Current simulation time
            reason: Reason for rerouting
            improvement: Percentage improvement (if applicable)
        """
        if not self.truck.destination_node:
            return
        
        try:
            # Convert TruckType to dict for router
            truck_type_dict = {
                'capacity_kg': self.truck.truck_type.capacity_kg,
                'fuel_consumption_empty_l_per_100km': self.truck.truck_type.fuel_consumption_empty_l_per_100km,
                'fuel_consumption_full_l_per_100km': self.truck.truck_type.fuel_consumption_full_l_per_100km,
                'fuel_efficiency_by_speed': self.truck.truck_type.fuel_efficiency_by_speed
            }
            
            new_route = self.router.find_path(
                self.truck.current_node,
                self.truck.destination_node,
                truck_type_config=truck_type_dict,
                load_fraction=self.truck.get_load_factor(),
                avoid_blocked=True,
                current_time=current_time
            )
            
            if new_route:
                old_segments = self.truck.current_route.get('segment_objects', []) if self.truck.current_route else []
                old_route_length = len(old_segments) - self.truck.route_progress
                new_segments = new_route.get('segment_objects', [])
                
                self.truck.current_route = new_route
                self.truck.route_progress = 0
                self.truck.segment_distance_traveled = 0.0
                
                self.events_to_publish.append({
                    'type': 'route_changed',
                    'truck_id': self.truck.truck_id,
                    'time': current_time,
                    'reason': reason,
                    'improvement_percent': improvement * 100,
                    'old_segments': old_route_length,
                    'new_segments': len(new_segments)
                })
        except Exception as e:
            # Rerouting failed
            self.events_to_publish.append({
                'type': 'reroute_failed',
                'truck_id': self.truck.truck_id,
                'time': current_time,
                'reason': str(e)
            })
    
    def _check_fuel_level(self, current_time: float):
        """
        Check fuel level and handle refueling with realistic time delays.
        
        Realistic refueling model:
        - Calculate refuel time based on tank capacity and rate (40 L/min)
        - Truck remains stationary during refueling
        - Logs refuel start and completion events
        
        Args:
            current_time: Current simulation time
        """
        # Check if fuel depleted - need to refuel
        if self.truck.current_fuel_liters <= 0 and not self.is_refueling:
            # Calculate refueling time based on tank capacity
            refuel_rate = self.config.get('truck_types', {}).get(self.truck.truck_type.name, {}).get('refueling', {}).get('rate_liters_per_minute', 40.0)
            setup_time = self.config.get('truck_types', {}).get(self.truck.truck_type.name, {}).get('refueling', {}).get('setup_time_minutes', 0.5)
            
            liters_needed = self.truck.truck_type.fuel_tank_liters
            refuel_time = (liters_needed / refuel_rate) + setup_time
            
            # Enter refuel state
            self.is_refueling = True
            self.refuel_remaining_time = refuel_time
            
            # Log refuel start
            self.events_to_publish.append({
                'type': 'truck_refuel_start',
                'truck_id': self.truck.truck_id,
                'time': current_time,
                'location': self.truck.current_location,
                'current_fuel_liters': self.truck.current_fuel_liters,
                'liters_to_add': liters_needed,
                'estimated_duration_minutes': refuel_time,
                'reason': 'fuel_depleted'
            })
            
            logger.info(f"[REFUEL] Truck {self.truck.truck_id} refueling at t={current_time:.1f}min "
                       f"(+{liters_needed:.1f}L, {refuel_time:.1f}min)")
            return
        
        # Warn if fuel low but not depleted
        if not self.is_refueling:
            fuel_pct = self.truck.get_fuel_percentage()
            low_fuel_threshold = self.config.get('truck_types', {}).get(self.truck.truck_type.name, {}).get('refueling', {}).get('low_fuel_threshold_percent', 20.0)
            
            if fuel_pct < low_fuel_threshold and fuel_pct > 0:
                self.events_to_publish.append({
                    'type': 'truck_low_fuel_warning',
                    'truck_id': self.truck.truck_id,
                    'time': current_time,
                    'fuel_percentage': fuel_pct,
                    'fuel_liters': self.truck.current_fuel_liters
                })
    
    def _check_truck_accident(self, current_time: float, segment) -> bool:
        """
        Check if truck is involved in an accident (total loss).
        
        Accident probability based on:
        - Driver fatigue
        - Weather conditions
        - Traffic density
        - Road type
        
        Args:
            current_time: Current simulation time
            segment: Current road segment
            
        Returns:
            True if truck destroyed in accident, False otherwise
        """
        import random
        
        # Base accident rate per km (very low)
        base_rate = 0.00001  # 1 in 100,000 km
        
        # Fatigue multiplier
        fatigue_hours = self.driver.accumulated_fatigue / 60.0
        if fatigue_hours > 10:
            fatigue_mult = 4.0
        elif fatigue_hours > 8:
            fatigue_mult = 2.5
        elif fatigue_hours > 6:
            fatigue_mult = 1.5
        else:
            fatigue_mult = 1.0
        
        # Weather multiplier (validate and use actual current weather)
        VALID_WEATHER_MULTIPLIERS = {
            'clear': 1.0,
            'partly_cloudy': 1.0,
            'cloudy': 1.1,
            'light_rain': 1.5,   # 50% more dangerous
            'rain': 2.0,          # 2x more dangerous
            'heavy_rain': 3.0,    # 3x more dangerous!
            'fog': 2.5            # 2.5x more dangerous
        }
        
        weather_mult = VALID_WEATHER_MULTIPLIERS.get(self.current_weather, None)
        if weather_mult is None:
            logger.warning(f"[WEATHER] Unknown weather state '{self.current_weather}' for truck {self.truck.truck_id} - defaulting to 'clear' (1.0x)")
            weather_mult = 1.0
        

        # Speed multiplier (higher speed = higher risk)
        speed_mult = 1.0 + (max(0, self.current_speed_kmh - 80) / 100.0)
        
        # Road type multiplier
        road_mult = {
            'primary': 0.8,
            'secondary': 1.0,
            'tertiary': 1.2,
            'residential': 1.1,
            'unpaved': 2.0,
            'dirt': 3.0
        }.get(getattr(segment, 'road_type', 'secondary'), 1.0)
        
        # Calculate total probability
        accident_prob = base_rate * fatigue_mult * weather_mult * speed_mult * road_mult
        
        # Check if accident occurs
        if random.random() < accident_prob:
            self._handle_truck_destruction(current_time, segment)
            return True
        
        return False
    
    def _handle_truck_destruction(self, current_time: float, segment):
        """
        Handle complete truck destruction from accident.

        Total loss scenario:
        - Truck permanently destroyed
        - All cargo lost
        - Order cleaned up from warehouse active_orders
        - Retailer pending_order cleared so it can reorder
        """
        # Mark truck as destroyed
        self.truck.status = 'destroyed'

        order_id = getattr(self.truck, 'assigned_order_id', None)

        # Log catastrophic accident event
        self.events_to_publish.append({
            'type': 'truck_accident_total_loss',
            'truck_id': self.truck.truck_id,
            'time': current_time,
            'location': self.truck.current_location,
            'segment_id': getattr(segment, 'segment_id', 'unknown'),
            'cargo_lost_kg': self.truck.current_load_kg,
            'batches_destroyed': len(self.truck.cargo_batches),
            'assigned_order_id': order_id,
            'driver_fatigue_hours': self.driver.accumulated_fatigue / 60.0,
            'speed_kmh': self.current_speed_kmh
        })

        logger.critical(
            f"[ACCIDENT] Truck {self.truck.truck_id} DESTROYED at t={current_time:.1f}min - "
            f"Total loss! Cargo: {self.truck.current_load_kg:.1f}kg lost"
        )

        # Destroy cargo
        self.truck.cargo_batches = []
        self.truck.current_load_kg = 0.0

        # Emit a cleanup event so the engine can notify warehouse + retailer.
        # The engine's _feed_ai_manager and warehouse update loop will pick this up.
        if order_id:
            self.events_to_publish.append({
                'type': 'truck_destroyed_cleanup',
                'truck_id': self.truck.truck_id,
                'order_id': order_id,
                'time': current_time,
            })
    
    def _handle_arrival(self, current_time: float):
        """
        Handle truck arrival at destination (retailer).
        
        The truck has arrived at the retailer. The warehouse will handle
        the actual delivery when it detects the truck has arrived.
        
        Args:
            current_time: Current simulation time
        """
        self.truck.status = "arrived"
        
        self.events_to_publish.append({
            'type': 'truck_arrival',
            'truck_id': self.truck.truck_id,
            'time': current_time,
            'destination': self.truck.destination_node,
            'cargo_kg': self.truck.current_load_kg,
            'fuel_remaining': self.truck.current_fuel_liters,
            'order_id': self.truck.assigned_order_id
        })
    
    def assign_delivery(self, destination_node: int, order_id: str, 
                       batches: List[OrangeBatch], current_time: float) -> bool:
        """
        Assign a delivery to this truck.
        
        Args:
            destination_node: Destination node ID
            order_id: Order ID
            batches: List of OrangeBatch objects to deliver
            current_time: Current simulation time
            
        Returns:
            True if assignment successful, False otherwise
        """
        # Check capacity
        if not self.truck.load_cargo(batches):
            logger.warning(f"[ASSIGNMENT FAILED] Truck {self.truck.truck_id}: Cargo exceeds capacity "
                          f"(Need: {sum(b.quantity for b in batches):.1f}kg, "
                          f"Available: {self.truck.truck_type.capacity_kg - self.truck.current_load_kg:.1f}kg)")
            return False
        
        # Calculate route
        try:
            # Convert TruckType to dict for router
            truck_type_dict = {
                'capacity_kg': self.truck.truck_type.capacity_kg,
                'fuel_consumption_empty_l_per_100km': self.truck.truck_type.fuel_consumption_empty_l_per_100km,
                'fuel_consumption_full_l_per_100km': self.truck.truck_type.fuel_consumption_full_l_per_100km,
                'fuel_efficiency_by_speed': self.truck.truck_type.fuel_efficiency_by_speed
            }

            # Query RL policy for initial routing strategy (if enabled).
            # This ensures the RL influences the very first route, not just reroutes.
            cost_weights = None
            if self.ai_manager is not None and self.ai_manager.optimization_pod.enabled:
                obs = self._build_obs(current_time)
                if obs is not None:
                    action = self.ai_manager.select_routing_action(self.truck.truck_id, obs)
                    strategy_names = ['balanced', 'speed', 'fuel']
                    strategy_name = strategy_names[action] if action in {0, 1, 2} else 'balanced'
                    strategies = self.config.get('routing', {}).get('strategies', {})
                    default_weights = [
                        {'distance_km': 1.0, 'time_minutes': 0.5, 'fuel_liters': 2.0},
                        {'distance_km': 0.5, 'time_minutes': 2.0, 'fuel_liters': 0.5},
                        {'distance_km': 1.0, 'time_minutes': 0.3, 'fuel_liters': 4.0},
                    ]
                    cost_weights = strategies.get(strategy_name, default_weights[action])
                    logger.debug(
                        f"[DISPATCH-RL] Truck {self.truck.truck_id}: strategy={strategy_name} "
                        f"(action={action}) for order {order_id}"
                    )

            route = self.router.find_path(
                self.truck.current_node,
                destination_node,
                truck_type_config=truck_type_dict,
                load_fraction=self.truck.get_load_factor(),
                avoid_blocked=True,
                current_time=current_time,
                cost_weights=cost_weights,
            )
            
            if not route:
                # Unload cargo if routing failed
                self.truck.unload_cargo()
                logger.warning(f"[ASSIGNMENT FAILED] Truck {self.truck.truck_id}: No route found from node "
                              f"{self.truck.current_node} to {destination_node} (Order: {order_id})")
                return False
            
            # Assign route and destination
            self.truck.current_route = route
            self.truck.route_progress = 0
            self.truck.segment_distance_traveled = 0.0
            self.truck.destination_node = destination_node
            self.truck.assigned_order_id = order_id
            
            # Start Loading Process
            load_kg_tons = self.truck.current_load_kg / 1000.0
            load_rate = self.truck.truck_type.loading_time_per_ton_minutes
            loading_time = max(10.0, load_kg_tons * load_rate) # Minimum 10 mins loading
            
            self.truck.status = "loading"
            self.current_task_remaining_min = loading_time
            
            self.events_to_publish.append({
                'type': 'loading_started',
                'truck_id': self.truck.truck_id,
                'time': current_time,
                'order_id': order_id,
                'duration_minutes': loading_time,
                'cargo_kg': self.truck.current_load_kg
            })
            
            logger.info(f"[LOADING] Truck {self.truck.truck_id} loading {self.truck.current_load_kg:.1f}kg "
                       f"for order {order_id} to node {destination_node} (Duration: {loading_time:.1f}min)")
            
            # Note: Removal of immediate 'truck_departure' event. 
            # It will now be fired when loading completes.
            

            
            return True
            
        except Exception as e:
            # Routing failed, unload cargo
            self.truck.unload_cargo()
            logger.error(f"[ASSIGNMENT FAILED] Truck {self.truck.truck_id}: Exception during assignment - {type(e).__name__}: {str(e)} "
                        f"(Order: {order_id}, Destination: {destination_node})")
            return False
    
    def start_unloading(self, current_time: float):
        """
        Transition truck to unloading state.
        
        Args:
            current_time: Current simulation time
        """
        if self.truck.status != "arrived":
            return
            
        load_kg_tons = self.truck.current_load_kg / 1000.0
        unload_rate = self.truck.truck_type.unloading_time_per_ton_minutes
        unloading_time = max(10.0, load_kg_tons * unload_rate) # Minimum 10 mins unloading
        
        self.truck.status = "unloading"
        self.current_task_remaining_min = unloading_time
        
        event = {
            'type': 'unloading_started',
            'truck_id': self.truck.truck_id,
            'time': current_time,
            'order_id': self.truck.assigned_order_id,
            'duration_minutes': unloading_time
        }
        self.events_to_publish.append(event)
        return event
    
    def get_state(self) -> Dict:
        """
        Get current truck state for snapshot logging.

        Field names match database.py get_complete_state() and
        get_latest_truck_positions() queries exactly so the dashboard
        receives correct values.
        """
        location = self.truck.current_location
        if isinstance(location, tuple):
            lat, lon = location
        else:
            lat, lon = 0.0, 0.0

        return {
            'truck_id': self.truck.truck_id,
            'warehouse_id': self.truck.warehouse_id,
            'truck_type': self.truck.truck_type.name,
            'status': self.truck.status,
            # Flat lat/lon fields (required by API queries)
            'latitude': round(lat, 6),
            'longitude': round(lon, 6),
            # Nested location dict (kept for backward compat with _log_snapshots)
            'location': {'lat': lat, 'lon': lon},
            'current_node': self.truck.current_node,
            'destination_node': self.truck.destination_node,
            # Fuel — field name matches API query 'fuel_percent'
            'fuel_percent': round(self.truck.get_fuel_percentage(), 1),
            'fuel_liters': round(self.truck.current_fuel_liters, 2),
            # Speed — field name matches API query 'speed_kmh'
            'speed_kmh': round(self.truck.current_speed_kmh, 1),
            # Cargo — field names match API queries
            'current_load_kg': round(self.truck.current_load_kg, 2),
            'cargo_kg': round(self.truck.current_load_kg, 2),  # alias for get_latest_truck_positions
            'load_factor': round(self.truck.get_load_factor(), 3),
            'capacity_kg': self.truck.truck_type.capacity_kg,
            'cargo_batches_count': len(self.truck.cargo_batches),
            'cargo_rsl': round(
                sum(b.current_rsl for b in self.truck.cargo_batches) / len(self.truck.cargo_batches), 2
            ) if self.truck.cargo_batches else 100.0,
            # Route
            'route_progress': self.truck.route_progress,
            'assigned_order_id': self.truck.assigned_order_id or '',
            # Cumulative metrics
            'total_distance_km': round(self.truck.total_distance_km, 2),
            'total_fuel_consumed_liters': round(self.truck.total_fuel_consumed_liters, 2),
            'total_deliveries': self.truck.total_deliveries,
            # Driver
            'cargo_temperature': round(self.last_ambient_temperature, 2),
        }
    
    def __repr__(self) -> str:
        return f"TruckAgent({self.truck})"