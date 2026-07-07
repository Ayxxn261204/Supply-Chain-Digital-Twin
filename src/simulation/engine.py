"""
Time-stepped simulation engine for Digital Twin supply chain.

This module implements a hybrid time-stepped and event-driven simulation
architecture that replaces the previous SimPy-based approach.
"""

import argparse
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import random
import os
import logging
import time
from pathlib import Path

from .events import EventQueue, AccidentStartEvent, AccidentEndEvent, EventBus
from .network import RoadNetwork, Router, TrafficModel
from .environment_models import WeatherModel
from .environment_models import DisruptionModel
from .ai_manager import AIManager
try:
    from data.mqtt_client import MQTTClientWrapper
    from data.config_loader import ConfigLoader
except ImportError:
    # Fallback for when running as script
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from data.mqtt_client import MQTTClientWrapper
    from data.config_loader import ConfigLoader

# InfluxDB for direct writes
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# Module-level logger
logger = logging.getLogger(__name__)


class SimulationEngine:
    """
    Main simulation engine that orchestrates the supply chain digital twin.
    
    Uses a hybrid approach:
    - Time-stepped updates (default 1 minute intervals) for continuous processes
    - Event queue for discrete occurrences (orders, deliveries, accidents)
    """
    
    def __init__(self, config: Dict[str, Any], duration_days: Optional[int] = None,
                 time_step_minutes: Optional[int] = None, random_seed: Optional[int] = None,
                 speed_multiplier: Optional[float] = None, start_date: Optional[str] = None,
                 steps: Optional[int] = None, headless: bool = False):
        """
        Initialize the simulation engine.
        
        Args:
            config: Configuration dictionary loaded from YAML
            duration_days: Override config duration (1-365 days)
            time_step_minutes: Override config time step (1-60 minutes)
            random_seed: Random seed for reproducibility
            speed_multiplier: Simulation speed (None = max speed)
            start_date: Override config start date (YYYY-MM-DD format)
            steps: Optional simulation steps (minutes) to run, overrides duration_days
            headless: If True, disable all external logging (MQTT/InfluxDB) and speed control
        """
        self.config = config
        self.headless = headless
        
        # Time management
        self.current_time = 0.0  # minutes since start
        
        # Parse Start Date (for seasonality) - CLI parameter overrides config
        start_date_str = start_date or config.get('simulation', {}).get('start_date', '2023-01-01')
        try:
            self.sim_start_datetime = datetime.strptime(start_date_str, "%Y-%m-%d")
            logger.info(f"Simulation start date: {start_date_str}")
        except ValueError:
            logger.warning(f"Invalid start_date format '{start_date_str}', defaulting to 2023-01-01")
            self.sim_start_datetime = datetime(2023, 1, 1)
            
        self.time_step = time_step_minutes or config.get('simulation', {}).get('time_step_minutes', 1)
        
        # Define duration_days_config unconditionally so it's always available for logging
        duration_days_config = duration_days or config.get('simulation', {}).get('duration_days', 7)
        
        if steps is not None:
            self.max_time = float(steps * self.time_step)
            logger.info(f"Simulation duration set by steps: {steps} ({self.max_time} minutes)")
        else:
            self.max_time = float(duration_days_config * 24 * 60)
        
        # Generate unique run ID
        self.run_id = f"sim-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        # Set random seed for reproducibility
        if random_seed is not None:
            random.seed(random_seed)
            self.random_seed = random_seed
        else:
            self.random_seed = None
        
        # Speed Control
        self.speed_multiplier = speed_multiplier or config.get('simulation', {}).get('speed_multiplier', None)
        
        # Progress logging - track last logged day to avoid missing logs with non-dividing timesteps
        self.last_logged_day = -1
        
        # Components
        self.road_network: Optional[RoadNetwork] = None
        self.router: Optional[Router] = None
        self.traffic_model: Optional[TrafficModel] = None
        self.weather_model: Optional = None  # WeatherModel (Phase 4)
        self.disruption_model: Optional = None  # DisruptionModel (Phase 4)
        self.warehouses: List = []
        self.retailers: List = []
        self.trucks: List = []
        self.event_queue = EventQueue()
        self.event_bus = EventBus()  # NEW: Reactive internal communication
        self.active_accidents: List = []  # Track active accidents
        self._telemetry_buffer: List[Point] = []  # Buffer for batched writes
        
        # Status code mapping for InfluxDB telemetry (numeric fields avoid type conflicts)
        self.status_codes = {
            'idle': 0.0, 'loading': 1.0, 'in_transit': 2.0,
            'unloading': 3.0, 'unloading_complete': 4.0,
            'arrived': 5.0, 'refueling': 6.0, 'destroyed': 7.0
        }
        
        # AI Ecosystem Manager (Phase 4)
        self.ai_manager = AIManager(self.config)
        
        # Initialize MQTT client for logging (Skip if headless)
        self.mqtt_connected = False
        if not self.headless:
            mqtt_broker = os.getenv('MQTT_BROKER', 'localhost')
            mqtt_port = int(os.getenv('MQTT_PORT', '1883'))
            self.mqtt_client = MQTTClientWrapper(
                broker=mqtt_broker,
                port=mqtt_port,
                client_id=f"sim-engine-{self.run_id}"
            )
        
        # Initialize InfluxDB client for direct writes (Skip if headless)
        self.influx_connected = False
        if not self.headless:
            influx_url = os.getenv('INFLUX_URL', 'http://localhost:8086')
            influx_token = os.getenv('INFLUX_TOKEN', 'my-super-secret-auth-token')
            influx_org = os.getenv('INFLUX_ORG', 'digital-twin')
            influx_bucket = os.getenv('INFLUX_BUCKET', 'supply-chain')
            
            try:
                self.influx_client = InfluxDBClient(url=influx_url, token=influx_token, org=influx_org)
                
                # OPTIMIZATION 3: Async batched writes
                from influxdb_client.client.write_api import WriteOptions
                write_options = WriteOptions(
                    batch_size=100,
                    flush_interval=1_000,
                    jitter_interval=0,
                    retry_interval=5_000,
                    max_retries=3,
                    max_retry_delay=30_000,
                    exponential_base=2
                )
                self.influx_write_api = self.influx_client.write_api(write_options=write_options)
                self.influx_bucket = influx_bucket
                self.influx_connected = True
                logger.info(f"InfluxDB connected: {influx_url} (async batched writes enabled)")
            except Exception as e:
                logger.warning(f"InfluxDB connection failed: {e}")
        
        # Skip MQTT/Influx logging if headless
        if self.headless:
            logger.info("HEADLESS MODE: External logging and speed control disabled")
        
        # Logging configuration
        self.last_snapshot_time = 0
        # Headless logging fallback (CSV)
        self.headless_log_enabled = headless
        self.log_dir = Path("data/logs/telemetry")
        if self.headless_log_enabled:
             self.log_dir.mkdir(parents=True, exist_ok=True)
             self._init_csv_loggers()
             
        self.last_heartbeat_time = 0.0  # Tracks real-time (time.time()), not simulation time
        self.heartbeat_interval = 60.0  # Real-time seconds between heartbeats
        
        logger.info("SimulationEngine initialized")
        logger.info(f"Run ID: {self.run_id}")
        logger.info(f"Duration: {duration_days_config} days ({self.max_time} minutes)")
        logger.info(f"Time step: {self.time_step} minute(s)")
        if self.random_seed is not None:
            logger.info(f"Random seed: {self.random_seed}")
    
    @property
    def all_trucks(self):
        """Live view of all trucks across all warehouses."""
        return [t for wh in self.warehouses for t in wh.trucks]
    
    def _init_csv_loggers(self):
        """Initialize CSV files for headless telemetry and event logging."""
        import csv
        self.telemetry_csv_path = self.log_dir / f"telemetry_{self.run_id}.csv"
        self.event_csv_path = self.log_dir / f"events_{self.run_id}.csv"
        
        # Write headers
        with open(self.telemetry_csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'entity_type', 'entity_id', 'status_code', 'fuel', 'rsl', 'lat', 'lon'])
            
        with open(self.event_csv_path, 'w', newline='') as f:
             writer = csv.writer(f)
             writer.writerow(['timestamp', 'event_type', 'data'])

    def initialize_road_network(self, use_cache: bool = True) -> None:
        """
        Initialize road network from OpenStreetMap.
        
        Args:
            use_cache: If True, use cached OSM data if available
        """
        logger.info("=" * 80)
        logger.info("Initializing Road Network")
        logger.info("=" * 80)
        
        # Create road network
        self.road_network = RoadNetwork(self.config)
        self.road_network.load(use_cache=use_cache)
        
        # Create traffic model and link it to the road network for agent awareness
        self.traffic_model = TrafficModel(self.config)
        self.road_network.traffic_model = self.traffic_model
        
        # Initialize traffic state for all segments (one-time at startup)
        logger.info("Initializing traffic state for entire network...")
        self.traffic_model.initialize_network(self.road_network, self.current_time)
        
        # Create router
        self.router = Router(self.road_network, self.config)
        
        # Log network stats
        stats = self.road_network.get_stats()
        logger.info("Network Statistics:")
        logger.info(f"  Nodes: {stats['num_nodes']}")
        logger.info(f"  Segments: {stats['num_segments']}")
        logger.info(f"  Total length: {stats['total_length_km']:.2f} km")
        logger.info(f"  Road types: {stats['road_type_counts']}")
        logger.info("=" * 80)
    
    def initialize_weather_and_disruptions(self) -> None:
        """Initialize weather and disruption models for Phase 4."""
        logger.info("=" * 80)
        logger.info("Initializing Weather & Disruptions")
        logger.info("=" * 80)
        
        # Initialize WeatherModel (Phase 3)
        if 'weather' in self.config:
            # Check if real data mode is enabled in configuration
            weather_config = self.config.get('weather', {})
            use_real_data = weather_config.get('use_real_data', False)
            dataset_path = weather_config.get('dataset_path', None)
            
            self.weather_model = WeatherModel(
                self.config,
                use_real_data=use_real_data,
                dataset_path=dataset_path
            )
            
            # CRITICAL FIX: Initialize weather with correct season at t=0
            initial_month = self.sim_start_datetime.month
            initial_hour = self.sim_start_datetime.hour + (self.sim_start_datetime.minute / 60.0)
            self.weather_model.update(0, month=initial_month, hour=initial_hour)
            
            data_mode = "REAL DATA" if self.weather_model.use_real_data else "SIMULATION"
            logger.info(f"Weather system enabled ({data_mode} mode, Starting: {self.weather_model.current_state}, Month {initial_month})")
            
            # Phase 1 SYNC: Inform traffic model of the initial weather state
            if self.traffic_model:
                self.traffic_model.set_environment(self.weather_model.current_state, initial_month)
        else:
            self.weather_model = None
            logger.info("Weather system disabled")
        
        # Initialize DisruptionModel (Phase 3)
        if 'disruptions' in self.config:
            self.disruption_model = DisruptionModel(self.config)
            logger.info("Disruption system enabled")
        else:
            self.disruption_model = None
            logger.info("Disruption system disabled")
        logger.info("=" * 80)
    
    def schedule_warehouse_restocking(self) -> None:
        """
        Schedule warehouse restocking events based on configuration.
        
        Each warehouse has a restock schedule (day of week, time of day, quantity).
        This method schedules all restock events for the simulation duration.
        """
        from .events import WarehouseRestockEvent
        
        logger.info("Scheduling warehouse restocking events...")
        
        for warehouse in self.warehouses:
            if not hasattr(warehouse, 'restock_schedule'):
                continue
            
            schedule = warehouse.restock_schedule
            day_of_week = schedule['day_of_week']  # 0=Sunday, 6=Saturday
            time_str = schedule['time_of_day']  # e.g., "06:00"
            quantity_kg = schedule['quantity_kg']
            
            # Parse time
            hour, minute = map(int, time_str.split(':'))
            time_of_day_minutes = hour * 60 + minute
            
            # Schedule restock events for each week in simulation
            current_week = 0
            while True:
                # Calculate restock time
                # Start of week 0 is day 0 (Sunday)
                restock_time = (current_week * 7 + day_of_week) * 24 * 60 + time_of_day_minutes
                
                if restock_time >= self.max_time:
                    break
                
                # Schedule event
                event = WarehouseRestockEvent(
                    time=restock_time,
                    warehouse_id=warehouse.warehouse_id,
                    product_type='oranges',
                    quantity_kg=quantity_kg
                )
                self.event_queue.schedule(event)
                
                current_week += 1
        
        logger.info("Restocking events scheduled")

        # Register warehouses with AIManager for cross-agent coordination
        if self.ai_manager is not None and self.warehouses:
            self.ai_manager.register_warehouses(self.warehouses)
            logger.info(f"[AIManager] Registered {len(self.warehouses)} warehouses for cross-agent sync")
        
        # OPTIMIZATION 5: Pre-compute common routes
        if self.router and self.warehouses and self.retailers:
            truck_types_config = self.config.get('truck_types', {})
            self.router.precompute_common_routes(
                self.warehouses,
                self.retailers,
                truck_types_config
            )
    
    def run(self):
        """
        Execute the main simulation loop.

        Loop structure:
        1. Connect to external services (MQTT / InfluxDB) unless headless
        2. Log startup parameters
        3. Process events, update agents/environment, log, advance time
        4. Cleanup on completion
        """
        logger.info("=" * 80)
        logger.info(f"Starting simulation: {self.run_id}")
        logger.info("=" * 80)

        # Connect to MQTT broker (skip if headless)
        if not self.headless:
            try:
                self.mqtt_connected = self.mqtt_client.connect()
            except Exception as e:
                logger.warning(f"MQTT connection failed ({e}). Logging will be disabled.")
                logger.warning("Make sure Docker containers are running (docker-compose up -d)")
                self.mqtt_connected = False

        # Log simulation parameters at startup
        self._log_startup_parameters()

        self.last_snapshot_time = self.current_time
        self.last_logged_day = int(self.current_time / (24 * 60))

        while self.current_time < self.max_time:
            self.step()

        # Simulation complete
        logger.info("=" * 80)
        logger.info(f"Simulation complete: {self.run_id}")
        logger.info(f"Total time simulated: {self.current_time / 60 / 24:.2f} days")
        logger.info("=" * 80)

        self._cleanup()

    def step(self):
        """
        Execute a single simulation time step.
        
        Returns:
            Dict containing basic loop metrics
        """
        loop_start = time.time()
        
        # 1. Process scheduled events
        if self.event_queue:
            self._process_events()

        # 1b. Apply any pending scenario commands from the dashboard
        self._apply_scenario_commands()

        # 2. Update all agents
        self._update_agents()
        
        # 3. Update environment
        self._update_environment()
        
        # 4. Check for new stochastic events
        self._check_stochastic_events()
        
        # 5. Logging
        self._handle_logging()
        
        # 6. Heartbeat (liveness detection - real-time based)
        self._check_and_write_heartbeat()
        
        # 7. Advance time
        self.current_time += self.time_step
        
        # Progress indicator (every simulated day)
        current_day = int(self.current_time / (24 * 60))
        if current_day > self.last_logged_day:
            logger.info(f"[Simulation time: Day {current_day}]")
            self.last_logged_day = current_day
        
        # Speed Control: Sleep to maintain target speed (Skip if headless)
        if self.speed_multiplier and not self.headless:
            target_duration = (self.time_step * 60.0) / self.speed_multiplier
            elapsed = time.time() - loop_start
            if elapsed < target_duration:
                time.sleep(target_duration - elapsed)
        
        return {
            'sim_time': self.current_time,
            'real_time_step': time.time() - loop_start
        }
    
    def _apply_scenario_commands(self) -> None:
        """
        Read and apply pending scenario commands written by the dashboard API.

        Commands are stored in data/scenario_commands.json as a list of dicts.
        Each command has an 'applied' flag; once applied it is marked True so it
        is not re-applied on subsequent ticks.  The file is only written when a
        command is actually applied, keeping I/O minimal.
        """
        import json
        from pathlib import Path

        cmd_file = Path("data/scenario_commands.json")
        if not cmd_file.exists():
            return

        try:
            with open(cmd_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return  # Corrupt or locked — skip this tick

        commands = data.get("commands", [])
        pending = [c for c in commands if not c.get("applied", False)]
        if not pending:
            return

        modified = False
        for cmd in pending:
            ctype = cmd.get("type")
            try:
                if ctype == "inject_accident" and self.road_network:
                    raw_seg_id = cmd["segment_id"]

                    # Resolve "truck:TRUCK_ID" placeholder to the truck's current segment
                    if raw_seg_id.startswith("truck:"):
                        truck_id = raw_seg_id[len("truck:"):]
                        resolved = None
                        for wh in self.warehouses:
                            for truck in wh.trucks:
                                if truck.truck_id == truck_id and truck.current_route:
                                    segs = truck.current_route.get("segments", [])
                                    prog = getattr(truck, "route_progress", 0)
                                    if prog < len(segs):
                                        resolved = segs[prog]
                                        break
                            if resolved:
                                break
                        if not resolved:
                            logger.warning(f"[SCENARIO] Could not resolve segment for truck {truck_id}")
                            cmd["applied"] = True
                            modified = True
                            continue
                        segment_id = resolved
                    else:
                        segment_id = raw_seg_id

                    seg = self.road_network.get_segment(segment_id)
                    if seg:
                        severity = cmd.get("severity", "moderate")
                        seg.set_accident(severity=severity)
                        # Schedule automatic clearance via the event queue
                        duration = float(cmd.get("duration_minutes", 30))
                        from .events import AccidentEndEvent
                        clear_time = self.current_time + duration
                        self.event_queue.schedule(
                            AccidentEndEvent(time=clear_time, segment_id=segment_id)
                        )
                        # Publish alert so trucks reroute proactively
                        self.event_bus.publish("accident_alert", {"segment_id": segment_id})
                        logger.info(
                            f"[SCENARIO] Injected {severity} accident on {segment_id} "
                            f"for {duration:.0f} min"
                        )

                elif ctype == "adjust_demand":
                    retailer_id = cmd.get("retailer_id")
                    multiplier  = float(cmd.get("multiplier", 1.0))
                    for retailer in self.retailers:
                        if retailer.retailer_id == retailer_id:
                            retailer.demand_model.base_arrival_rate *= multiplier
                            logger.info(
                                f"[SCENARIO] Demand for {retailer_id} adjusted ×{multiplier:.2f} "
                                f"→ {retailer.demand_model.base_arrival_rate:.2f} customers/hr"
                            )
                            break

                elif ctype == "trigger_stockout":
                    warehouse_id = cmd.get("warehouse_id")
                    for wh in self.warehouses:
                        if wh.warehouse_id == warehouse_id:
                            wh.current_inventory_kg = 0.0
                            wh.inventory_batches    = []
                            wh._perceived_inventory_kg = 0.0
                            logger.info(f"[SCENARIO] Stockout triggered at {warehouse_id}")
                            break

            except Exception as e:
                logger.warning(f"[SCENARIO] Failed to apply command {ctype}: {e}")

            cmd["applied"] = True
            modified = True

        if modified:
            try:
                tmp = cmd_file.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                tmp.replace(cmd_file)
            except OSError as e:
                logger.warning(f"[SCENARIO] Could not write command file: {e}")

    def _process_events(self):
        """Process all events scheduled for current time."""
        events = self.event_queue.get_events_at(self.current_time)
        for event in events:
            event.execute(self)
    
    def _update_agents(self):
        """Update all agents' continuous processes."""
        # Calculate time context for agents
        current_dt = self.sim_start_datetime + timedelta(minutes=self.current_time)
        
        # Python weekday(): 0=Mon. Formula (weekday+1)%7 maps to: Mon?1, Tue?2, ..., Sun?0
        # Matches config convention where day_of_week_multipliers[0] = Sunday
        day_of_week = (current_dt.weekday() + 1) % 7
        
        month = current_dt.month
        hour = current_dt.hour + (current_dt.minute / 60.0)
        
        weather = self.weather_model.current_state if self.weather_model else "clear"
        
        # Phase 1 SYNC: Update traffic model with latest weather/season for peak-wave broadening
        if self.traffic_model:
            self.traffic_model.set_environment(weather, month)

        # Get temperature and humidity from weather model
        if self.weather_model and hasattr(self.weather_model, 'get_temperature'):
            temperature = self.weather_model.get_temperature(month, hour, current_time=self.current_time)
            if hasattr(self.weather_model, 'get_humidity'):
                humidity = self.weather_model.get_humidity(month, hour, current_time=self.current_time)
            else:
                humidity = self.weather_model.humidity if hasattr(self.weather_model, 'humidity') else 50.0
        else:
            # Fallback to inline defaults if weather model unavailable
            humidity = 50.0  # Default moderate humidity
            temperature = 25.0  # Default comfortable temperature
            # Apply time-of-day variation for realism
            import math
            time_effect = 5.0 * math.cos((hour - 14) * math.pi / 12)
            weather_effect = -2.0 if weather in ['rain', 'light_rain'] else 0.0
            temperature = 25.0 + time_effect + weather_effect
        
        # Retailers: process customers, check inventory
        for retailer in self.retailers:
            if hasattr(retailer, 'update'):
                ret_events = retailer.update(
                    self.current_time, 
                    self.time_step,
                    day_of_week, 
                    month, 
                    weather, 
                    temperature,
                    humidity,  # Pass humidity for consistency
                    engine=self  # Pass engine so retailers can schedule order events
                )
                # Feed sale/stockout events to AI manager
                if ret_events:
                    for event in ret_events:
                        self._feed_ai_manager(event)
        
        # Warehouses: process orders, allocate trucks
        for warehouse in self.warehouses:
            if hasattr(warehouse, 'update'):
                events = warehouse.update(
                    self.current_time, 
                    self.time_step, 
                    ambient_temperature=temperature, 
                    ambient_humidity=humidity,
                    current_weather=weather,
                    engine=self
                )
                
                # Log generated events (including telemetry)
                if events:
                    for event in events:
                        if event['type'] == 'telemetry':
                            # Extract truck_id and remove type/truck_id from data
                            t_id = event['truck_id']
                            data = {k: v for k, v in event.items() if k not in ['type', 'truck_id']}
                            self.log_truck_telemetry(t_id, data)
                        else:
                            self.log_event(event['type'], event)
                            # Feed AI manager with relevant events
                            self._feed_ai_manager(event)
    
    def _update_environment(self):
        """Update environmental conditions (traffic, weather)."""
        # Calculate time context
        current_dt = self.sim_start_datetime + timedelta(minutes=self.current_time)
        month = current_dt.month
        hour = current_dt.hour + (current_dt.minute / 60.0)

        # Update weather state
        if self.weather_model:
            # Capture weather change events
            weather_events = self.weather_model.update(self.current_time, month=month, hour=hour)
            
            # Publish weather events
            for event in weather_events:
                event['month'] = month
                event['hour'] = hour
                if self.mqtt_connected:
                    self.mqtt_client.publish('sim/weather', event)
                
                # Write to InfluxDB for dashboard weather panel
                if self.influx_connected and 'new_state' in event:
                    try:
                        point = Point("weather_transitions") \
                            .tag("run_id", self.run_id) \
                            .tag("from_state", event.get('old_state', 'unknown')) \
                            .tag("to_state", event['new_state']) \
                            .field("state", event['new_state']) \
                            .field("timestamp", float(self.current_time)) \
                            .field("month", month) \
                            .field("hour", hour)
                        self.influx_write_api.write(bucket=self.influx_bucket, record=point)
                        logger.info(f"[WEATHER] Weather transition: {event.get('old_state')} ? {event['new_state']}")
                    except Exception as e:
                        logger.warning(f"InfluxDB weather write failed: {e}")
                
                # INTERNAL REACTIVE EVENT: Always notify agents regardless of InfluxDB state
                if 'new_state' in event:
                    self.event_bus.publish('weather_change', event)
            
            # Update traffic model with current weather and month
            if self.traffic_model:
                self.traffic_model.set_environment(self.weather_model.current_state, month)
        
        # Update traffic on all road segments with accident awareness
        if self.traffic_model and self.road_network:
            self.road_network.update_traffic(
                self.traffic_model, self.current_time,
                active_accidents=self.active_accidents,
                trucks=self.all_trucks
            )
    
    def _get_active_truck_segments(self) -> set:
        """
        Get set of road segment IDs where trucks are currently traveling.
        
        This is used to limit accident generation to only road segments
        with active truck traffic, making accidents more realistic and
        reducing accident frequency from 111/day to 1-3/day.
        
        Returns:
            Set of segment IDs where trucks are in transit
        """
        active_segments = set()
        
        for warehouse in self.warehouses:
            for truck in warehouse.trucks:
                # Only count trucks that are in transit (moving)
                if truck.status != "in_transit":
                    continue
                
                # Get current route and segment
                if not hasattr(truck, 'current_route') or not truck.current_route:
                    continue
                
                route = truck.current_route
                
                # Try to get current segment from route
                segments = route.get('segments', [])
                if not segments:
                    continue
                
                # Get truck's progress through route
                idx = getattr(truck, 'route_progress', 0)
                
                # Add current segment to active set
                if 0 <= idx < len(segments):
                    segment_id = segments[idx]
                    if segment_id:
                        active_segments.add(segment_id)
        
        return active_segments
    
    def _check_stochastic_events(self):
        """Check for probabilistic events (customer arrivals, accidents)."""
        # Generate accidents probabilistically
        if self.disruption_model and self.road_network and self.weather_model:
            # Get active truck segments to limit accident generation
            # Only check segments where trucks are currently traveling
            active_segments = self._get_active_truck_segments()
            
            new_accidents = self.disruption_model.generate_accidents(
                self.current_time,
                self.road_network,
                weather=self.weather_model.current_state,
                time_step_minutes=self.time_step,
                active_truck_segments=active_segments,
                active_trucks=self.all_trucks
            )
            
            # Clear expired accidents
            to_remove = []
            for accident in self.active_accidents:
                if self.current_time >= accident.end_time:
                    # Accident duration expired
                    segment = self.road_network.get_segment(accident.segment_id)
                    if segment:
                        segment.clear_accident()  # Use new method
                    accident.clear()
                    to_remove.append(accident)
                    logger.info(f"[OK] Accident {accident.accident_id} cleared on segment {accident.segment_id}")
            
            for accident in to_remove:
                self.active_accidents.remove(accident)

            # Schedule accident events
            for accident in new_accidents:
                # Activate new accidents (apply to road network immediately)
                segment = self.road_network.get_segment(accident.segment_id)
                if segment:
                    # Use new partial blockage method with severity
                    segment.set_accident(severity=accident.severity)
                    accident.activate()
                    self.active_accidents.append(accident)
                    
                    # Log accident
                    logger.warning(f"[ALERT] ACCIDENT on segment {accident.segment_id} "
                                 f"(severity={accident.severity}, duration={accident.duration_minutes:.0f}min, "
                                 f"speed_reduction={segment.accident_speed_reduction*100:.1f}%)")
                    
                    self.log_event('road_accident', {
                        'accident_id': accident.accident_id,
                        'segment_id': accident.segment_id,
                        'severity': accident.severity,
                        'duration_minutes': accident.duration_minutes,
                        'speed_reduction_percent': segment.accident_speed_reduction * 100
                    })
                    
                    # INTERNAL REACTIVE EVENT: Notify agents of road blockage
                    self.event_bus.publish('accident_alert', {
                        'segment_id': accident.segment_id,
                        'severity': accident.severity,
                        'location': segment.start_location
                    })

                # Track active accident (Already appended above if segment exists)
                if not segment:
                    self.active_accidents.append(accident)
                    accident.activate()
    
    def _feed_ai_manager(self, event: Dict[str, Any]):
        """
        Route simulation events to the AI manager for online learning.

        Called for every discrete event generated by agents so the
        prediction and optimization pods stay up-to-date.
        """
        if self.ai_manager is None:
            return

        etype = event.get('type', '')

        # Demand signal: every sale feeds the demand forecaster
        if etype == 'sale':
            self.ai_manager.record_sale(
                event.get('retailer_id', ''),
                event.get('time', self.current_time),
                event.get('quantity_kg', 0.0),
            )

        # Stockout signal: cross-agent sync
        elif etype == 'stockout':
            self.ai_manager.trigger_cross_agent_sync('stockout', event)

        # RSL alert: cross-agent sync when cargo is critical
        elif etype == 'cargo_spoiled':
            self.ai_manager.trigger_cross_agent_sync('rsl_alert', {
                'truck_id': event.get('truck_id'),
                'current_rsl': 0.0,
            })

        # Market update (if market data events are ever emitted)
        elif etype == 'market_update':
            self.ai_manager.trigger_cross_agent_sync('market_update', event)

        # Truck arrival: record actual travel time for ETA model training.
        # We use cargo_kg as a proxy signal; the real segment-level data is
        # recorded via record_segment_traversal() called from truck_agent when
        # a segment is completed.  Here we log the delivery completion signal.
        elif etype == 'truck_arrival':
            logger.debug(
                f"[AIManager] Truck {event.get('truck_id')} arrived at "
                f"node {event.get('destination')} with {event.get('cargo_kg', 0):.1f}kg"
            )

        # Delivery complete: log for reward signal / analytics
        elif etype == 'delivery_complete':
            logger.debug(
                f"[AIManager] Delivery complete: truck {event.get('truck_id')} "
                f"? retailer {event.get('retailer_id')}, {event.get('quantity_kg', 0):.1f}kg"
            )

        # Warehouse batch spoiled: treat as RSL alert for cross-agent sync
        elif etype == 'warehouse_batch_spoiled':
            self.ai_manager.trigger_cross_agent_sync('rsl_alert', {
                'truck_id': None,
                'warehouse_id': event.get('warehouse_id'),
                'current_rsl': 0.0,
            })

        # Truck destroyed: clear the retailer's pending_order so it can reorder
        elif etype == 'truck_destroyed_cleanup':
            order_id = event.get('order_id')
            if order_id:
                for retailer in self.retailers:
                    if (retailer.pending_order is not None
                            and retailer.pending_order.order_id == order_id):
                        logger.warning(
                            f"[CLEANUP] Clearing pending_order {order_id} "
                            f"from {retailer.retailer_id} (truck destroyed)"
                        )
                        # Capture old order quantity before clearing state
                        order_qty = retailer.pending_order.quantity_kg if retailer.pending_order else 0
                        
                        # Notify failure clears the state and tracks the metric
                        retailer.notify_delivery_failed('truck_destroyed', self.current_time)
                        
                        # Now trigger the emergency replacement
                        if order_qty > 0:
                            retailer.trigger_emergency_reorder(order_qty, self.current_time, self)
                        break

        # Truck departure: record as ETA training baseline
        elif etype == 'truck_departure':
            logger.debug(
                f"[AIManager] Truck {event.get('truck_id')} departed with "
                f"{event.get('cargo_kg', 0):.1f}kg, route_segments={event.get('route_segments', 0)}"
            )

        # Dispatch blocked due to low RSL: feed as RSL alert
        elif etype == 'dispatch_blocked_low_rsl':
            self.ai_manager.trigger_cross_agent_sync('rsl_alert', {
                'truck_id': None,
                'warehouse_id': event.get('warehouse_id'),
                'current_rsl': 0.0,
                'reason': 'dispatch_blocked',
            })

        # Delivery received at retailer: confirm demand was met
        elif etype == 'delivery_received':
            logger.debug(
                f"[AIManager] Delivery received at {event.get('retailer_id')}: "
                f"{event.get('quantity_kg', 0):.1f}kg"
            )

    def _handle_logging(self):
        """Handle 3-tier logging strategy."""
        if self.headless:
            # In headless mode, only CSV snapshot logging runs (no InfluxDB/MQTT)
            if self.headless_log_enabled:
                if self.current_time - self.last_snapshot_time >= self.time_step:
                    self._log_snapshots_csv()
                    self.last_snapshot_time = self.current_time
            return
            
        # Snapshots: every timestep (synchronized with simulation loop)
        if self.current_time - self.last_snapshot_time >= self.time_step:
            self._log_snapshots()
            self.last_snapshot_time = self.current_time
        
        # Telemetry: Flush buffer
        self._flush_telemetry()
        
        # Events: logged immediately when they occur
    
    def _log_startup_parameters(self):
        """Log simulation parameters at startup."""
        if self.mqtt_connected:
            payload = {
                'run_id': self.run_id,
                'timestamp': self.current_time,
                'event_type': 'simulation_start',
                'duration_days': self.max_time / 60 / 24,
                'time_step_minutes': self.time_step,
                'random_seed': self.random_seed,
                'num_warehouses': len(self.warehouses),
                'num_retailers': len(self.retailers),
                'num_trucks': len(self.all_trucks)
            }
            self.mqtt_client.publish('iot/simulation/events', payload)
        
        # Write directly to InfluxDB
        if self.influx_connected:
            start_date_str = self.sim_start_datetime.strftime("%Y-%m-%d")
            point = Point("simulation_metadata") \
                .tag("run_id", self.run_id) \
                .tag("event_type", "simulation_start") \
                .field("duration_days", self.max_time / 60 / 24) \
                .field("time_step_minutes", self.time_step) \
                .field("speed", float(self.speed_multiplier) if self.speed_multiplier else 1.0) \
                .field("random_seed", self.random_seed if self.random_seed else 0) \
                .field("num_warehouses", len(self.warehouses)) \
                .field("num_retailers", len(self.retailers)) \
                .field("num_trucks", len(self.all_trucks)) \
                .field("start_date", start_date_str)
                # No .time() - let InfluxDB use current wall-clock time
            try:
                self.influx_write_api.write(bucket=self.influx_bucket, record=point)
                logger.debug("InfluxDB: Wrote simulation_start event")
            except Exception as e:
                logger.warning(f"InfluxDB write failed: {e}")
    
    def _log_snapshots(self):
        """Log periodic state snapshots for all agents - writes to InfluxDB in batches."""
        logger.debug(f"Logging snapshots at t={self.current_time} min (synced with timestep={self.time_step})")
        
        # Collect all InfluxDB points for batch write
        influx_points = []
        
        # Warehouse snapshots
        for warehouse in self.warehouses:
            if hasattr(warehouse, 'get_state'):
                state = warehouse.get_state()
                
                # Publish to MQTT (for monitoring/debugging)
                if self.mqtt_connected:
                    mqtt_state = state.copy()
                    mqtt_state['run_id'] = self.run_id
                    mqtt_state['timestamp'] = self.current_time
                    self.mqtt_client.publish('iot/warehouse/state', mqtt_state)
                
                # Create InfluxDB point (don't write yet)
                if self.influx_connected:
                    try:
                        point = Point("warehouse_state") \
                            .tag("run_id", self.run_id) \
                            .tag("warehouse_id", state['warehouse_id'])
                        
                        # Add all fields from state (except location which is complex)
                        for key, value in state.items():
                            if key not in ['warehouse_id', 'location'] and isinstance(value, (int, float, bool)):
                                point = point.field(key, float(value))
                        
                        # Add timestamp field (simulation time in minutes)
                        point = point.field("timestamp", float(self.current_time))
                        
                        # Add location as separate fields
                        if 'location' in state:
                            point = point.field("latitude", float(state['location']['lat']))
                            point = point.field("longitude", float(state['location']['lon']))
                        
                        influx_points.append(point)
                    except Exception as e:
                        logger.warning(f"Failed to create warehouse point: {e}")
        
        # Retailer snapshots
        for retailer in self.retailers:
            if hasattr(retailer, 'get_state'):
                state = retailer.get_state()
                
                # Publish to MQTT
                if self.mqtt_connected:
                    mqtt_state = state.copy()
                    mqtt_state['run_id'] = self.run_id
                    mqtt_state['timestamp'] = self.current_time
                    self.mqtt_client.publish('iot/retailer/state', mqtt_state)
                
                # Create InfluxDB point (don't write yet)
                if self.influx_connected:
                    try:
                        point = Point("retailer_state") \
                            .tag("run_id", self.run_id) \
                            .tag("retailer_id", state['retailer_id'])
                        
                        # Tag the primary warehouse (first in subscription list)
                        warehouse_ids = state.get('warehouse_ids', [])
                        if warehouse_ids:
                            point = point.tag("primary_warehouse_id", warehouse_ids[0])
                        
                        # Add all numeric fields (exclude pending_order which is boolean)
                        for key, value in state.items():
                            if key not in ['retailer_id', 'warehouse_ids', 'location', 'pending_order'] and isinstance(value, (int, float)):
                                point = point.field(key, float(value))
                        
                        # Add pending_order as numeric (0 or 1)
                        if 'pending_order' in state:
                            point = point.field("pending_order", 1.0 if state['pending_order'] else 0.0)
                        
                        # Add timestamp field (simulation time in minutes  
                        point = point.field("timestamp", float(self.current_time))
                        
                        # Add location
                        if 'location' in state:
                            point = point.field("latitude", float(state['location']['lat']))
                            point = point.field("longitude", float(state['location']['lon']))
                        
                        influx_points.append(point)
                    except Exception as e:
                        logger.warning(f"Failed to create retailer point: {e}")
        
        # Truck snapshots - use TruckAgent.get_state() for richer sensor data
        for warehouse in self.warehouses:
            for agent in warehouse.truck_agents.values():
                truck = agent.truck
                state = agent.get_state()  # Use TruckAgent for richer sensor/driver data
                
                # Publish to MQTT
                if self.mqtt_connected:
                    mqtt_state = state.copy()
                    mqtt_state['run_id'] = self.run_id
                    mqtt_state['timestamp'] = self.current_time
                    self.mqtt_client.publish('iot/truck/state', mqtt_state)
                
                # Create InfluxDB point (don't write yet)
                if self.influx_connected:
                    try:
                        point = Point("truck_state") \
                            .tag("run_id", self.run_id) \
                            .tag("truck_id", state['truck_id']) \
                            .tag("warehouse_id", state.get('warehouse_id', '')) \
                            .tag("truck_type", state.get('truck_type', '')) \
                            .tag("current_zone", str(state.get('current_zone', 'unknown')))
                        
                        # Convert status to numeric enum for InfluxDB compatibility
                        # String fields cause type conflicts in pivot() operations
                        if 'status' in state:
                            status_numeric = self.status_codes.get(state['status'], -1.0)
                            point = point.field("status_code", status_numeric)
                        
                        # Add all numeric fields
                        for key, value in state.items():
                            if key not in ['truck_id', 'warehouse_id', 'truck_type', 'status',
                                           'location', 'latitude', 'longitude'] \
                               and isinstance(value, (int, float)):
                                point = point.field(key, float(value))
                        
                        # Add timestamp field (simulation time in minutes)
                        point = point.field("timestamp", float(self.current_time))
                        
                        # Add location
                        if 'location' in state and isinstance(state['location'], dict):
                            point = point.field("latitude", float(state['location'].get('lat', 0)))
                            point = point.field("longitude", float(state['location'].get('lon', 0)))
                        
                        influx_points.append(point)
                    except Exception as e:
                        logger.warning(f"Failed to create truck point: {e}")
        
        # Weather state snapshot (logged every timestep, not just on transitions)
        if self.influx_connected and self.weather_model:
            try:
                # Calculate time context for weather state
                current_dt = self.sim_start_datetime + timedelta(minutes=self.current_time)
                month = current_dt.month
                hour = current_dt.hour + (current_dt.minute / 60.0)
                
                # Get current weather state and environmental data
                weather_state = self.weather_model.current_state
                temperature = self.weather_model.get_temperature(month, hour, current_time=self.current_time)
                humidity = self.weather_model.get_humidity(month, hour, current_time=self.current_time)
                
                # Create weather snapshot point
                point = Point("weather_state") \
                    .tag("run_id", self.run_id) \
                    .tag("state", weather_state) \
                    .field("temperature_celsius", float(temperature)) \
                    .field("humidity_percent", float(humidity)) \
                    .field("timestamp", float(self.current_time)) \
                    .field("month", int(month)) \
                    .field("hour", float(hour))
                
                influx_points.append(point)
            except (ValueError, TypeError, AttributeError) as e:
                # Data conversion or field access errors
                logger.warning(f"Failed to create weather snapshot point (data error): {e}")
            except Exception as e:
                # Unexpected errors (likely InfluxDB client issues)
                logger.warning(f"Failed to create weather snapshot point (unexpected): {type(e).__name__}: {e}")
        
        # BATCH WRITE: Write all points asynchronously (non-blocking)
        if self.influx_connected and influx_points:
            try:
                # Async write - returns immediately, data buffered and sent in background
                self.influx_write_api.write(bucket=self.influx_bucket, record=influx_points)
                logger.debug(f"Queued {len(influx_points)} snapshot points for async InfluxDB write")
            except (ConnectionError, TimeoutError) as e:
                # Network/connection issues
                logger.warning(f"InfluxDB async write queue failed (connection): {e}")
            except (ValueError, TypeError) as e:
                # Data validation errors
                logger.warning(f"InfluxDB async write queue failed (invalid data): {e}")
            except Exception as e:
                # Unexpected errors
                logger.warning(f"InfluxDB async write queue failed (unexpected): {type(e).__name__}: {e}")
    
    def _log_snapshots_csv(self):
        """Write periodic state snapshots to CSV in headless mode."""
        import csv
        if not hasattr(self, 'telemetry_csv_path'):
            return
        try:
            with open(self.telemetry_csv_path, 'a', newline='') as f:
                writer = csv.writer(f)
                for warehouse in self.warehouses:
                    for agent in warehouse.truck_agents.values():
                        truck = agent.truck
                        sc = self.status_codes.get(truck.status, -1.0)
                        lat, lon = truck.current_location if truck.current_location else (0.0, 0.0)
                        avg_rsl = (sum(b.current_rsl for b in truck.cargo_batches) / len(truck.cargo_batches)
                                   if truck.cargo_batches else 100.0)
                        writer.writerow([
                            self.current_time, 'truck', truck.truck_id,
                            sc, round(truck.get_fuel_percentage(), 1),
                            round(avg_rsl, 1), round(lat, 6), round(lon, 6)
                        ])
        except Exception as e:
            logger.debug(f"CSV snapshot write failed: {e}")

    def _cleanup(self):
        # Shutdown AI manager (saves RL checkpoint)
        if self.ai_manager is not None:
            self.ai_manager.shutdown()
        # CRITICAL: Write simulation_end event BEFORE closing writer
        if self.mqtt_connected:
            # Log simulation end event
            payload = {
                'run_id': self.run_id,
                'timestamp': self.current_time,
                'event_type': 'simulation_end'
            }
            self.mqtt_client.publish('iot/simulation/events', payload)
            
        # CRITICAL: Write to InfluxDB BEFORE closing writer
        if self.influx_connected and hasattr(self, 'influx_write_api') and self.influx_write_api:
            try:
                point = Point("simulation_metadata") \
                    .tag("run_id", self.run_id) \
                    .tag("event_type", "simulation_end") \
                    .field("final_time_minutes", self.current_time) \
                    .field("completed", True)
                    # No .time() - let InfluxDB use current wall-clock time
                self.influx_write_api.write(bucket=self.influx_bucket, record=point)
                logger.info(f"InfluxDB: Wrote simulation_end event for {self.run_id}")
            except (ConnectionError, TimeoutError) as e:
                # Network/connection issues during cleanup
                logger.warning(f"InfluxDB: Failed to write simulation_end (connection): {e}")
            except Exception as e:
                # Unexpected errors during cleanup (don't fail simulation)
                logger.warning(f"InfluxDB: Failed to write simulation_end (unexpected): {type(e).__name__}: {e}")
        
        # NOW close async InfluxDB writer (after writing simulation_end)
        if hasattr(self, 'influx_write_api') and self.influx_write_api:
            try:
                logger.info("Flushing and closing async InfluxDB writer...")
                self.influx_write_api.close()  # Flushes buffer and shuts down cleanly
                logger.info("InfluxDB writer closed successfully")
            except (ConnectionError, TimeoutError) as e:
                # Network issues during cleanup (expected in some cases)
                logger.warning(f"Error closing InfluxDB writer (connection): {e}")
            except Exception as e:
                # Other errors during cleanup (don't fail simulation)
                logger.warning(f"Error closing InfluxDB writer (unexpected): {type(e).__name__}: {e}")
        
        if self.mqtt_connected:
            # Disconnect MQTT
            self.mqtt_client.disconnect()
    
    def _check_and_write_heartbeat(self) -> None:
        """Write heartbeat to InfluxDB based on real-time (not simulation time)."""
        import time
        current_real_time = time.time()
        if current_real_time - self.last_heartbeat_time >= self.heartbeat_interval:
            self.last_heartbeat_time = current_real_time
            if self.influx_connected:
                try:
                    from influxdb_client import Point
                    heartbeat_point = Point("simulation_metadata") \
                        .tag("run_id", self.run_id) \
                        .tag("event_type", "heartbeat") \
                        .field("simulation_time_minutes", self.current_time) \
                        .field("heartbeat_interval_seconds", self.heartbeat_interval)
                    self.influx_write_api.write(bucket=self.influx_bucket, record=heartbeat_point)
                except (ConnectionError, TimeoutError) as e:
                    # Network issues with heartbeat (suppress after first warning)
                    if not hasattr(self, '_hb_err_count'):
                        self._hb_err_count = 0
                        logger.warning(f"Heartbeat write failed (connection): {e}")
                    self._hb_err_count += 1
                except Exception as e:
                    # Other errors with heartbeat (suppress after first warning)
                    if not hasattr(self, '_hb_err_count'):
                        self._hb_err_count = 0
                        logger.warning(f"Heartbeat write failed (unexpected): {type(e).__name__}: {e}")
                    self._hb_err_count += 1

    def log_event(self, event_type: str, data: Dict[str, Any]):
        """
        Log an event immediately (Tier 1: Events).
        
        Events are discrete occurrences like orders, deliveries, accidents.
        They are logged immediately when they happen.
        
        Args:
            event_type: Type of event (e.g., 'truck_departure', 'order_placed')
            data: Event-specific data dictionary
        """
        # Publish to MQTT (for legacy monitoring)
        if self.mqtt_connected:
            payload = {
                'run_id': self.run_id,
                'timestamp': self.current_time,
                'event_type': event_type,
                **data
            }
            self.mqtt_client.publish('iot/events/all', payload)
        
        # Write to InfluxDB (for dashboard)
        if self.influx_connected:
            try:
                point = Point("events") \
                    .tag("run_id", self.run_id) \
                    .tag("event_type", event_type)
                
                # Add data as tags (strings) and fields (numbers)
                for key, value in data.items():
                    if value is None:
                        continue
                    elif isinstance(value, str):
                        point = point.tag(key, value)
                    elif isinstance(value, bool):
                        point = point.field(key, float(1 if value else 0))
                    elif isinstance(value, (int, float)):
                        point = point.field(key, float(value))
                
                # Add timestamp as field for querying
                point = point.field("sim_time", float(self.current_time))
                
                self.influx_write_api.write(bucket=self.influx_bucket, record=point)
                logger.debug(f"InfluxDB: Wrote event {event_type}")
            except (ConnectionError, TimeoutError) as e:
                # Network/connection issues
                logger.warning(f"InfluxDB event write failed (connection) for {event_type}: {e}")
            except (ValueError, TypeError) as e:
                # Data validation errors
                logger.warning(f"InfluxDB event write failed (invalid data) for {event_type}: {e}")
            except Exception as e:
                # Unexpected errors
                logger.warning(f"InfluxDB event write failed (unexpected) for {event_type}: {type(e).__name__}: {e}")
                
        # Headless CSV fallback
        if self.headless_log_enabled:
            import csv, json
            try:
                with open(self.event_csv_path, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([self.current_time, event_type, json.dumps(data)])
            except Exception as e:
                 logger.debug(f"CSV Event log failed: {e}")
    
    def log_truck_telemetry(self, truck_id: str, telemetry_data: Dict[str, Any]):
        """
        Log truck telemetry data (Tier 3: Telemetry).
        
        Telemetry is high-frequency data from active trucks only.
        Called by truck agents every 5 minutes when in transit.
        
        Args:
            truck_id: Unique truck identifier
            telemetry_data: Telemetry data (position, fuel, speed, cargo temp, etc.)
        """
        # Publish to MQTT (for legacy monitoring)
        if self.mqtt_connected:
            payload = {
                'run_id': self.run_id,
                'timestamp': self.current_time,
                'truck_id': truck_id,
                **telemetry_data
            }
            self.mqtt_client.publish('iot/truck/telemetry', payload)
        
        # Write to InfluxDB (Buffered)
        if self.influx_connected:
            try:
                # Map status to code using consolidated map
                status_code = self.status_codes.get(telemetry_data.get('status', 'idle'), -1.0)
                
                point = Point("truck_telemetry") \
                    .tag("run_id", self.run_id) \
                    .tag("truck_id", truck_id)
                
                # Add status as tag if present
                if 'status' in telemetry_data:
                    point = point.tag("status", str(telemetry_data['status']))
                
                # Add telemetry data as fields
                for key, value in telemetry_data.items():
                    if value is None or key in ['location', 'location_true']:
                        continue  # Skip None and complex types
                    elif isinstance(value, str):
                        # Skip string fields except status (already tagged)
                        if key not in ['status', 'driver_status', 'source']:
                            continue
                    elif isinstance(value, bool):
                        point = point.field(key, float(1 if value else 0))
                    elif isinstance(value, (int, float)):
                        point = point.field(key, float(value))
                
                # Handle location separately (lat/lon as fields)
                if 'location' in telemetry_data and telemetry_data['location']:
                    lat, lon = telemetry_data['location']
                    point = point.field("latitude", float(lat))
                    point = point.field("longitude", float(lon))
                
                if 'location_true' in telemetry_data and telemetry_data['location_true']:
                    lat, lon = telemetry_data['location_true']
                    point = point.field("latitude_true", float(lat))
                    point = point.field("longitude_true", float(lon))
                
                # Add timestamp
                point = point.field("timestamp", float(self.current_time))
                
                self._telemetry_buffer.append(point)
            except Exception as e:
                # Unexpected errors
                logger.warning(f"InfluxDB telemetry buffering failed for {truck_id}: {type(e).__name__}: {e}")

    def _flush_telemetry(self):
        """Flush buffered telemetry to InfluxDB in a single batch."""
        if self.influx_connected and self._telemetry_buffer:
            try:
                count = len(self._telemetry_buffer)
                self.influx_write_api.write(bucket=self.influx_bucket, record=self._telemetry_buffer)
                self._telemetry_buffer.clear()
                logger.debug(f"Flushed {count} telemetry points to InfluxDB")
            except Exception as e:
                logger.warning(f"Failed to flush telemetry buffer: {e}")
    
    def get_state(self) -> Dict[str, Any]:
        """
        Get current state of entire simulation.
        
        Returns:
            Dictionary containing complete simulation state
        """
        return {
            'run_id': self.run_id,
            'current_time': self.current_time,
            'current_time_formatted': self._format_time(self.current_time),
            'warehouses': [w.get_state() for w in self.warehouses if hasattr(w, 'get_state')],
            'retailers': [r.get_state() for r in self.retailers if hasattr(r, 'get_state')],
            'trucks': [
                agent.get_state()
                for wh in self.warehouses
                for agent in wh.truck_agents.values()
            ]
        }
    
    def _format_time(self, minutes: float) -> str:
        """Format simulation time as human-readable string."""
        days = int(minutes // (24 * 60))
        hours = int((minutes % (24 * 60)) // 60)
        mins = int(minutes % 60)
        return f"Day {days}, {hours:02d}:{mins:02d}"


def parse_arguments():
    """
    Parse command-line arguments for simulation configuration.
    
    Returns:
        Namespace with parsed arguments
    """
    parser = argparse.ArgumentParser(
        description='Digital Twin Supply Chain Simulation',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument(
        '--config',
        type=str,
        default='config/simulation_config.yaml',
        help='Path to configuration YAML file'
    )
    
    parser.add_argument(
        '--duration-days',
        type=int,
        default=None,
        help='Simulation duration in days (1-365), overrides config'
    )
    
    parser.add_argument(
        '--time-step',
        type=int,
        default=None,
        help='Time step in minutes (1-60), overrides config'
    )
    
    parser.add_argument(
        '--seed',
        type=int,
        default=None,
        help='Random seed for reproducibility'
    )
    
    parser.add_argument(
        '--start-date',
        type=str,
        default=None,
        help='Simulation start date (YYYY-MM-DD format, e.g. 2023-07-01), overrides config'
    )
    
    parser.add_argument(
        '--speed',
        type=float,
        default=None,
        help='Simulation speed multiplier (e.g. 1.0 = real-time, 10.0 = 10x speed). If not set, runs max speed.'
    )
    parser.add_argument('--steps', type=int, default=None, help='Exact number of simulation steps to run')
    parser.add_argument('--headless', action='store_true', help='Run without MQTT/Influx logging or speed limit')
    
    return parser.parse_args()