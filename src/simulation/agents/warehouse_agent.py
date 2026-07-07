"""WarehouseAgent - Autonomous agent for warehouse operations."""

import logging
import datetime
from typing import List, Dict, Optional, Tuple
from ..entities import Truck, TruckType
from ..entities import Order
from ..entities import OrangeBatch
from ..network.road_network import RoadNetwork
from ..sensor import GPSSensor, TemperatureSensor, StockSensor
import numpy as np
from .truck_agent import TruckAgent
from ..business_models import InventoryModel

# Module-level logger
logger = logging.getLogger(__name__)


class WarehouseAgent:
    """
    Autonomous agent managing warehouse operations.
    
    Responsibilities:
    - Manage inventory of oranges
    - Own and allocate heterogeneous truck fleet
    - Prioritize and fulfill orders
    - Handle periodic restocking
    - Track warehouse metrics
    """
    
    def __init__(self, warehouse_id: str, location: Tuple[float, float],
                 initial_inventory_kg: float, fleet_config: List[Dict],
                 truck_types: Dict[str, TruckType], road_network, router, config: Dict,
                 restock_schedule: Dict = None, event_bus=None, ai_manager=None):
        """
        Initialize WarehouseAgent.
        
        Args:
            warehouse_id: Unique identifier
            location: (lat, lon) position
            initial_inventory_kg: Starting inventory
            fleet_config: List of {type: str, count: int} for fleet composition
            truck_types: Dict mapping type name to TruckType
            road_network: RoadNetwork instance
            router: Router instance
            config: Configuration dict
            restock_schedule: Optional dict with restock schedule (day_of_week, time_of_day, quantity_kg)
            event_bus: Optional EventBus for reactive internal communication
            ai_manager: Optional AIManager for AI/ML integration
        """
        self.warehouse_id = warehouse_id
        self.location = location
        self.current_inventory_kg = initial_inventory_kg
        self.restock_schedule = restock_schedule  # Store restock schedule
        self.config = config  # Store config for later use
        self.event_bus = event_bus      # Reactive pub/sub bus
        self.ai_manager = ai_manager    # AI/ML ecosystem manager
        
        # Find warehouse node in road network
        self.node_id = road_network.get_nearest_node(location[0], location[1])
        
        # Create heterogeneous truck fleet
        self.trucks: List[Truck] = []
        self.truck_agents: Dict[str, TruckAgent] = {}
        
        truck_counter = 0
        for fleet_spec in fleet_config:
            truck_type_name = fleet_spec['type']
            count = fleet_spec['count']
            truck_type = truck_types[truck_type_name]
            
            for i in range(count):
                truck_id = f"{warehouse_id}_truck_{truck_type_name}_{i:02d}"
                truck = Truck(truck_id, truck_type, warehouse_id, location)
                truck.current_node = self.node_id
                
                # Create agent for this truck
                agent = TruckAgent(truck, road_network, router, config,
                                   ai_manager=ai_manager, event_bus=event_bus)
                
                self.trucks.append(truck)
                self.truck_agents[truck_id] = agent
                truck_counter += 1
        
        # Order management
        self.pending_orders: List[Order] = []
        self.active_orders: Dict[str, Order] = {}  # order_id -> Order
        self.completed_orders: List[Order] = []
        
        # Batch management
        self.batch_counter = 0
        self.inventory_batches: List[OrangeBatch] = []
        self._initialize_inventory_batches(initial_inventory_kg)
        
        # Metrics
        self.total_orders_fulfilled = 0
        self.total_kg_shipped = 0.0
        self.inventory_shortages = 0

        # AI-Driven Inventory Management
        # Reads from config['warehouse']['inventory_management'] — all keys must match
        # InventoryModel.__init__ exactly.  Falls back to safe defaults if section missing.
        inv_config = config.get('warehouse', {}).get('inventory_management', {
            'service_level': 0.95,
            'lead_time_days_mean': 1.0,
            'lead_time_days_std': 0.2,
            'review_period_hours': 24.0,
            'order_quantity_method': 'EOQ',
            'holding_cost_per_kg_per_day': 0.05,
            'ordering_cost_per_order': 50,
            'order_up_to_days': 14,
            'demand_forecast_window_days': 7,
            'demand_smoothing_alpha': 0.3,
            'min_order_quantity_kg': 5000,
            'max_order_quantity_kg': 50000,
        })
        self.inventory_model = InventoryModel(inv_config)
        self.pending_restock = False
        
        # IoT Sensors (NEW - Phase 3)
        sensor_cfg = config.get('sensors', {})
        self.temp_sensor = TemperatureSensor(f"{warehouse_id}_temp", sensor_cfg)
        self.stock_sensor = StockSensor(f"{warehouse_id}_stock", sensor_cfg)
        self._perceived_inventory_kg = initial_inventory_kg
        
        # Capacity enforcement - set after construction via warehouse.max_capacity_kg = wh_config['max_capacity_kg']
        # Defaults to 2x initial inventory as a safe fallback
        self.max_capacity_kg = initial_inventory_kg * 2.0
        
        # OPTIMIZATION 8: Event-driven order processing
        self.pending_orders_changed = False  # Flag: new orders added
        self.trucks_changed = False  # Flag: truck returned/became available
        
        # Environmental state (NEW - for dispatch prediction)
        self._current_temperature = 25.0
        self._current_humidity = 50.0
        self._current_weather = 'clear'

        # Subscribe to EventBus for reactive responses
        if event_bus is not None:
            event_bus.subscribe('accident_alert', self._on_accident_alert)

    def _on_accident_alert(self, data: dict):
        """
        React to an accident alert.

        If any of our in-transit trucks are on the affected segment,
        flag trucks_changed so the allocation loop re-evaluates on the
        next update cycle.  The individual TruckAgent will handle the
        actual reroute via its own EventBus subscription.
        """
        affected_segment = data.get('segment_id')
        if not affected_segment:
            return
        for truck in self.trucks:
            if truck.status != 'in_transit' or not truck.current_route:
                continue
            segments = truck.current_route.get('segments', [])
            remaining = segments[truck.route_progress:]
            if affected_segment in remaining:
                logger.info(
                    f"[EVENTBUS] Warehouse {self.warehouse_id}: truck {truck.truck_id} "
                    f"affected by accident on segment {affected_segment}."
                )
                # Flag re-evaluation so the allocation loop can reassign
                # any idle trucks to cover for the delayed one
                self.trucks_changed = True
                break
        
    def _initialize_inventory_batches(self, total_kg: float):
        """Create initial inventory as batches.

        Harvest dates are set to the simulation epoch (datetime(2000, 1, 1)) so
        that RSL tracking is consistent with simulation time rather than
        wall-clock time.  FIFO ordering still works correctly because all
        initial batches share the same epoch date and are therefore treated as
        equally 'old'.
        """
        # Get batch size from config (moved from hardcoded 1000)
        batch_size = self.config.get('warehouse_operations', {}).get('batch_size_kg', 1000)
        num_batches = int(total_kg / batch_size)
        remainder = total_kg % batch_size

        # Simulation-epoch sentinel: consistent, sortable, not wall-clock time
        epoch_date = datetime.datetime(2000, 1, 1)

        for i in range(num_batches):
            batch = OrangeBatch(
                f"{self.warehouse_id}_batch_{self.batch_counter:04d}",
                batch_size,
                epoch_date,
                0.0,
                100.0
            )
            self.inventory_batches.append(batch)
            self.batch_counter += 1

        if remainder > 0:
            batch = OrangeBatch(
                f"{self.warehouse_id}_batch_{self.batch_counter:04d}",
                remainder,
                epoch_date,
                0.0,
                100.0
            )
            self.inventory_batches.append(batch)
            self.batch_counter += 1
    
    def _recalculate_inventory(self):
        """Recalculate current_inventory_kg from inventory_batches to stay consistent."""
        self.current_inventory_kg = sum(b.quantity for b in self.inventory_batches)

    def update(self, current_time: float, time_step: float, ambient_temperature: float = 25.0, ambient_humidity: float = 50.0, current_weather: str = 'clear', engine=None) -> List[Dict]:
        """
        Update warehouse state for one time step.
        """
        events = []

        # Store current environmental conditions for use in dispatch RSL prediction
        self._current_temperature = ambient_temperature
        self._current_humidity = ambient_humidity
        self._current_weather = current_weather
        
        # CRITICAL FIX: Update RSL for warehouse inventory in cold storage
        # Standard citrus cold storage: 6°C, 85-90% RH
        # Phase 3 FIX: Use noisy sensor data for physics input
        GROUND_TRUTH_TEMP = 6.0  # Celsius
        WAREHOUSE_STORAGE_HUMIDITY = 88.0  # Percent
        
        reading = self.temp_sensor.read(GROUND_TRUTH_TEMP, current_time)
        # Use sensor reading if available, else last known good value (handling packet loss)
        perceived_temp = reading if reading is not None else getattr(self.temp_sensor, 'last_reading', GROUND_TRUTH_TEMP)
        if perceived_temp is None: perceived_temp = GROUND_TRUTH_TEMP

        for batch in self.inventory_batches:
            # SCIENTIFIC FIX: Reality spoilage depends on actual physics (Ground Truth).
            # Sensor noise is handled separately for model perception.
            # Stationary warehouse inventory has 0.0 vibration.
            batch.update_rsl(GROUND_TRUTH_TEMP, WAREHOUSE_STORAGE_HUMIDITY, current_time, vibration_g=0.0)

            
            # Check for spoiled batches
            if batch.is_spoiled():
                events.append({
                    'type': 'warehouse_batch_spoiled',
                    'warehouse_id': self.warehouse_id,
                    'batch_id': batch.batch_id,
                    'time': current_time,
                    'quantity_kg': batch.quantity
                })
        
        # Calculate spoilage BEFORE removing from list
        spoiled_kg = sum(b.quantity for b in self.inventory_batches if b.is_spoiled())
        
        # Then remove spoiled batches from inventory
        self.inventory_batches = [b for b in self.inventory_batches if not b.is_spoiled()]
        
        # Recalculate inventory from batch list (single source of truth — no manual subtraction)
        self._recalculate_inventory()
        
        # Sync perceived inventory after spoilage so dispatch logic stays accurate
        if spoiled_kg > 0:
            self._perceived_inventory_kg = max(0.0, self._perceived_inventory_kg - spoiled_kg)

        # Update perceived inventory from IoT weight sensors
        stock_reading = self.stock_sensor.read(self.current_inventory_kg, current_time)
        if stock_reading is not None:
            self._perceived_inventory_kg = stock_reading
        
        # Update all truck agents
        for agent in self.truck_agents.values():
            truck_events = agent.update(current_time, time_step, ambient_temperature, ambient_humidity, current_weather)
            events.extend(truck_events)

            # Handle truck destruction cleanup
            for ev in truck_events:
                if ev.get('type') == 'truck_destroyed_cleanup':
                    order_id = ev.get('order_id')
                    if order_id and order_id in self.active_orders:
                        order = self.active_orders[order_id]
                        order.status = 'failed_truck_destroyed'
                        self.completed_orders.append(order)
                        del self.active_orders[order_id]
                        logger.warning(
                            f"[CLEANUP] Order {order_id} removed from active_orders "
                            f"(truck {ev.get('truck_id')} destroyed)"
                        )
                    # Also flag trucks_changed so pending orders get re-allocated
                    self.trucks_changed = True

            # Handle truck arrivals
            if agent.truck.status == "arrived":
                if agent.truck.destination_node == self.node_id:
                    # Truck returned to warehouse
                    self._handle_truck_return(agent.truck, current_time)
                else:
                    # Truck arrived at retailer - START UNLOADING (realistic delay)
                    unloading_event = agent.start_unloading(current_time)
                    if unloading_event:
                        events.append(unloading_event)
            
            # Handle completed unloading
            elif agent.truck.status == "unloading_complete":
                 # Unloading finished - deliver cargo and return
                 delivery_event = self._handle_retailer_delivery(agent.truck, current_time, engine)
                 if delivery_event:
                     events.append(delivery_event)
        
        # OPTIMIZATION 8: Only process pending orders if something changed
        # (new order arrived OR truck became available)
        if self.pending_orders and (self.pending_orders_changed or self.trucks_changed):
            allocation_events = self._allocate_orders(current_time)
            events.extend(allocation_events)
            # Reset flags
            self.pending_orders_changed = False
            self.trucks_changed = False
            
        # SCIENTIFIC FIX: Check for reorder using AI-driven InventoryModel
        # Phase 3 FIX: Use perceived inventory (noisy) instead of ground truth
        if self.inventory_model.should_reorder(self._perceived_inventory_kg) and not self.pending_restock:
            self.pending_restock = True
            
            # Calculate dynamic order quantity based on predicted demand
            restock_amount = self.inventory_model.calculate_order_quantity(self._perceived_inventory_kg)

            # CAP to physical shelf space — never order more than the building can hold
            available_space = self.max_capacity_kg - self.current_inventory_kg
            restock_amount = min(restock_amount, available_space)

            if restock_amount <= 0:
                self.pending_restock = False  # Already full, no point ordering
            elif engine:
                from ..events import WarehouseRestockEvent
                # Lead time from model (minutes)
                lead_time = self.inventory_model.lead_time_mean
                restock_time = current_time + lead_time
                
                event = WarehouseRestockEvent(
                    time=restock_time,
                    warehouse_id=self.warehouse_id,
                    product_type="oranges",
                    quantity_kg=restock_amount
                )
                engine.event_queue.schedule(event)
                
                events.append({
                    'type': 'warehouse_reorder_triggered',
                    'warehouse_id': self.warehouse_id,
                    'time': current_time,
                    'current_inventory': self.current_inventory_kg,
                    'restock_amount': restock_amount,
                    'eta': restock_time
                })
        
        return events
    
    def receive_order(self, order: Order, current_time: float = 0.0):
        """
        Receive a new order from a retailer.
        Gracefully clamps massive incoming orders to the fleet logistics limit.
        """
        # Determine max truck capacity we currently own
        max_truck_cap = max((t.truck_type.capacity_kg for t in self.trucks), default=8000.0) if self.trucks else 8000.0
        
        # Clamp the incoming order quantity so we never choke the single-truck dispatch loop
        if order.quantity_kg > max_truck_cap:
            logger.debug(f"[WAREHOUSE] {self.warehouse_id} clamping massive order {order.order_id} "
                         f"from {order.quantity_kg:.1f}kg down to {max_truck_cap:.1f}kg max bound.")
            order.quantity_kg = max_truck_cap
        else:
            logger.debug(f"[WAREHOUSE] {self.warehouse_id} received standard order {order.order_id}: {order.quantity_kg:.1f}kg")

        # Record ONLY the physically accepted workload as our true localized demand
        context = {
            'hour': int((current_time / 60.0) % 24),
            'day': int((current_time / 1440.0) % 7)
        }
        self.inventory_model.record_demand(current_time, order.quantity_kg, context)

        self.pending_orders.append(order)
        self.pending_orders_changed = True
    
    def _allocate_orders(self, current_time: float) -> List[Dict]:
        """
        Allocate available trucks to pending orders.
        
        Uses priority-based allocation:
        1. Sort orders by priority (urgency)
        2. For each order, find best available truck
        3. Assign truck and prepare delivery
        
        Args:
            current_time: Current simulation time
            
        Returns:
            List of events generated
        """
        events = []
        
        # Find available trucks (idle status, at warehouse)
        available_trucks = [
            truck for truck in self.trucks
            if truck.status == "idle" and truck.current_node == self.node_id
        ]
        
        if not available_trucks:
            return events
        
        # Sort orders by priority (highest first) so urgent orders are dispatched first
        self.pending_orders.sort(key=lambda o: o.priority, reverse=True)
        
        # Process orders in priority order
        orders_to_remove = []
        
        for order in self.pending_orders:
            if not available_trucks:
                break
            
            # Check if we have enough inventory to fulfil this order.
            # Use actual inventory for batch allocation decisions (perceived is
            # for reorder trigger only — dispatch needs ground truth).
            if self.current_inventory_kg < order.quantity_kg:
                self.inventory_shortages += 1
                events.append({
                    'type': 'inventory_shortage',
                    'warehouse_id': self.warehouse_id,
                    'order_id': order.order_id,
                    'time': current_time,
                    'required_kg': order.quantity_kg,
                    'available_kg': self._perceived_inventory_kg
                })
                continue
            
            # Find best truck for this order (smallest truck that can fit the load)
            best_truck = None
            for truck in sorted(available_trucks, key=lambda t: t.truck_type.capacity_kg):
                if truck.truck_type.capacity_kg >= order.quantity_kg:
                    best_truck = truck
                    break
            
            if not best_truck:
                # No truck large enough
                continue
            
            # Allocate batches for this order
            batches = self._allocate_batches(order.quantity_kg, current_time)
            if not batches:
                continue

            # PRE-DISPATCH RSL CHECK: Verify allocated batches meet quality threshold
            # before loading onto a truck. This prevents dispatching cargo that will
            # fail the quality check at delivery, wasting a truck trip.
            min_rsl = self.config.get('quality_control', {}).get('min_acceptable_rsl_hours', 72.0)
            total_shelf_life = self.config.get('cargo', {}).get('initial_rsl_hours', 336.0)
            # Convert min_rsl_hours to RSL percentage
            min_rsl_pct = (min_rsl / total_shelf_life) * 100.0

            acceptable_batches = [b for b in batches if b.current_rsl >= min_rsl_pct]
            rejected_batches = [b for b in batches if b.current_rsl < min_rsl_pct]

            if rejected_batches:
                rejected_kg = sum(b.quantity for b in rejected_batches)
                logger.warning(
                    f"[PRE-DISPATCH] {self.warehouse_id}: Rejected {len(rejected_batches)} batches "
                    f"({rejected_kg:.1f}kg) for order {order.order_id} ? RSL below {min_rsl_pct:.1f}% "
                    f"(min {min_rsl:.0f}h). Disposing spoiled stock."
                )
                # Remove spoiled batches from inventory permanently
                for b in rejected_batches:
                    if b in self.inventory_batches:
                        self.inventory_batches.remove(b)
                self._recalculate_inventory()

            if not acceptable_batches:
                # All batches failed ? cannot fulfil this order right now
                # Return nothing to inventory (batches were already removed above)
                events.append({
                    'type': 'dispatch_blocked_low_rsl',
                    'warehouse_id': self.warehouse_id,
                    'order_id': order.order_id,
                    'retailer_id': order.retailer_id,
                    'time': current_time,
                    'reason': 'all_batches_below_rsl_threshold',
                })
                logger.warning(
                    f"[PRE-DISPATCH] {self.warehouse_id}: Cannot dispatch order {order.order_id} "
                    f"? no acceptable batches available. Waiting for restock."
                )
                continue

            # Use only acceptable batches; adjust order quantity if partial
            batches = acceptable_batches
            actual_qty = sum(b.quantity for b in batches)

            # RSL-AT-DELIVERY PREDICTION: Warn if cargo will spoil before reaching retailer.
            # This uses the AIManager's RSLForecaster to predict RSL at delivery time
            # given current temperature and the estimated route duration.
            if self.ai_manager is not None and batches:
                avg_rsl = sum(b.current_rsl for b in batches) / len(batches)
                estimated_travel_hours = 0.5  # conservative default (30 min)
                # Use actual ambient conditions stored from the last engine update tick
                temp_c = self._current_temperature
                humidity = self._current_humidity
                rsl_prediction = self.ai_manager.prediction_pod.rsl.predict(
                    avg_rsl, estimated_travel_hours, temp_c, humidity
                ) if self.ai_manager.prediction_pod.rsl is not None else None

                if rsl_prediction and rsl_prediction.get("recommendation") == "reject":
                    logger.warning(
                        f"[RSL-PREDICT] {self.warehouse_id}: Order {order.order_id} ? "
                        f"cargo predicted to spoil in transit "
                        f"(predicted RSL at delivery: {rsl_prediction.get('predicted_rsl_pct', 0):.1f}%). "
                        f"Dispatching anyway ? retailer needs stock."
                    )
                elif rsl_prediction and rsl_prediction.get("recommendation") == "warn":
                    logger.warning(
                        f"[RSL-PREDICT] {self.warehouse_id}: Order {order.order_id} ? "
                        f"cargo RSL marginal at delivery "
                        f"(predicted: {rsl_prediction.get('predicted_rsl_pct', 0):.1f}%). Dispatching."
                    )
            
            # Get retailer node from order
            retailer_node = order.retailer_node_id
            
            if retailer_node is None:
                # Skip order if no node ID (shouldn't happen)
                continue
            
            # Assign delivery to truck agent
            agent = self.truck_agents[best_truck.truck_id]
            success = agent.assign_delivery(
                retailer_node, order.order_id, batches, current_time
            )
            
            if success:
                # Update order status
                order.assign_truck(best_truck.truck_id, current_time)
                order.mark_departed(current_time)

                # Move order to active
                self.active_orders[order.order_id] = order
                orders_to_remove.append(order)

                # Remove truck from available list
                available_trucks.remove(best_truck)

                # Update metrics (use actual dispatched quantity, may differ from order qty)
                self.total_kg_shipped += actual_qty

                logger.info(
                    f"[DISPATCH] {self.warehouse_id} assigned order {order.order_id} to truck "
                    f"{best_truck.truck_id}: {actual_qty:.1f}kg ? {order.retailer_id} "
                    f"(Perceived Inv: {self._perceived_inventory_kg:.1f}kg)"
                )
                
                events.append({
                    'type': 'order_assigned',
                    'warehouse_id': self.warehouse_id,
                    'order_id': order.order_id,
                    'truck_id': best_truck.truck_id,
                    'time': current_time,
                    'quantity_kg': order.quantity_kg
                })
            else:
                # Assignment failed, return batches to inventory
                self.inventory_batches.extend(batches)
        
        # Remove allocated orders from pending
        for order in orders_to_remove:
            self.pending_orders.remove(order)
        
        # SCIENTIFIC FIX (H2): Ensure inventory count is perfectly synced after allocation
        self._recalculate_inventory()
        
        return events
    
    def _allocate_batches(self, quantity_kg: float, current_time: float) -> List[OrangeBatch]:
        """
        Allocate batches from inventory for an order.
        
        Uses FIFO (First In First Out) - oldest batches first.
        
        Args:
            quantity_kg: Quantity needed
            
        Returns:
            List of OrangeBatch objects, or empty list if insufficient inventory
        """
        if self.current_inventory_kg < quantity_kg:
            return []
        
        allocated = []
        remaining = quantity_kg
        
        # Sort batches by harvest date (oldest first)
        self.inventory_batches.sort(key=lambda b: b.harvest_date)
        
        batches_to_remove = []
        for batch in self.inventory_batches:
            if remaining <= 0:
                break
            
            if batch.quantity <= remaining:
                # Take entire batch
                allocated.append(batch)
                if batch not in batches_to_remove:
                    batches_to_remove.append(batch)
                remaining -= batch.quantity
            else:
                # Split batch
                # Create new batch with needed quantity
                new_batch = OrangeBatch(
                    f"{self.warehouse_id}_batch_{self.batch_counter:04d}",
                    remaining,
                    batch.harvest_date,
                    current_time,
                    batch.current_rsl
                )
                
                self.batch_counter += 1
                allocated.append(new_batch)
                
                # Reduce original batch quantity
                batch.quantity -= remaining
                remaining = 0
        
        # Remove fully allocated batches from inventory
        for batch in batches_to_remove:
            try:
                self.inventory_batches.remove(batch)
            except ValueError:
                # Batch already removed (shouldn't happen but be safe)
                pass
        
        return allocated
    
    def _handle_retailer_delivery(self, truck: Truck, current_time: float, engine) -> Optional[Dict]:
        """
        Handle truck delivering cargo to retailer.
        
        Args:
            truck: Truck that arrived at retailer
            current_time: Current simulation time
            engine: SimulationEngine instance
            
        Returns:
            Delivery event dict
        """
        if not truck.assigned_order_id or not engine:
            return None
        
        # Find the order
        order = self.active_orders.get(truck.assigned_order_id)
        if not order:
            return None
        
        # Find the retailer
        retailer = None
        for r in engine.retailers:
            if r.retailer_id == order.retailer_id:
                retailer = r
                break
        
        if not retailer:
            return None
        
        # NEW: Quality check before delivery (configurable RSL threshold)
        cargo_batches = truck.cargo_batches
        accepted_batches = []
        rejected_batches = []
        
        # Get quality threshold from config
        min_rsl = self.config.get('quality_control', {}).get('min_acceptable_rsl_hours', 72.0)
        
        for batch in cargo_batches:
            if batch.is_acceptable_quality(min_rsl_hours=min_rsl):
                accepted_batches.append(batch)
            else:
                rejected_batches.append(batch)
        
        # If ALL batches rejected, delivery fails
        if rejected_batches and not accepted_batches:
            # Complete delivery failure - reorder needed
            truck.unload_cargo()  # Dispose of rejected cargo
            order.status = 'failed_quality_rejection'
            
            # CLEANUP: Remove failed order from active tracking
            if order.order_id in self.active_orders:
                self.completed_orders.append(order)  # Track as completed (failed)
                del self.active_orders[order.order_id]
            
            # Trigger automatic reorder from retailer
            # Trigger automatic reorder from retailer
            retailer.notify_delivery_failed('quality_rejection', current_time)
            retailer.trigger_emergency_reorder(order.quantity_kg, current_time, engine)
            
            # Set truck to return to warehouse
            try:
                agent = self.truck_agents[truck.truck_id]
                
                # Convert TruckType to dict for router
                truck_type_dict = {
                    'capacity_kg': truck.truck_type.capacity_kg,
                    'fuel_consumption_empty_l_per_100km': truck.truck_type.fuel_consumption_empty_l_per_100km,
                    'fuel_consumption_full_l_per_100km': truck.truck_type.fuel_consumption_full_l_per_100km,
                    'fuel_efficiency_by_speed': truck.truck_type.fuel_efficiency_by_speed
                }
                
                return_route = agent.router.find_path(
                    truck.current_node,
                    self.node_id,
                    truck_type_config=truck_type_dict,
                    load_fraction=0.0,  # Empty truck returning
                    avoid_blocked=True,
                    current_time=current_time
                )
                
                if return_route:
                    truck.current_route = return_route
                    truck.route_progress = 0
                    truck.segment_distance_traveled = 0.0
                    truck.destination_node = self.node_id
                    truck.status = "in_transit"
                    
                    logger.info(f"[DELIVERY FAILED] Truck {truck.truck_id} - all batches rejected at {retailer.retailer_id}, returning to {self.warehouse_id}")
                else:
                    # No return route — snap truck back to warehouse node so it
                    # re-enters the available fleet on the next allocation cycle.
                    truck.status = "idle"
                    truck.current_route = None
                    truck.destination_node = None
                    truck.current_node = self.node_id  # recover to warehouse
                    truck.current_location = self.location
                    truck.route_progress = 0
                    truck.segment_distance_traveled = 0.0
                    self.trucks_changed = True
                    logger.warning(f"[RETURN ROUTE FAILED] Truck {truck.truck_id} has no return route after rejection — recovered to warehouse {self.warehouse_id}")
            except Exception as e:
                logger.warning(f"Failed to route truck {truck.truck_id} back after rejection: {type(e).__name__}: {e}")
            
            return {
                'type': 'delivery_failed_quality_rejection',
                'truck_id': truck.truck_id,
                'warehouse_id': self.warehouse_id,
                'retailer_id': retailer.retailer_id,
                'order_id': order.order_id,
                'time': current_time,
                'rejected_batches': len(rejected_batches),
                'total_quantity_rejected_kg': sum(b.quantity for b in rejected_batches),
                'reason': 'insufficient_RSL'
            }
        
        # Partial or full acceptance
        if rejected_batches:
            # Log partial rejection
            logger.warning(f"[QUALITY] Partial rejection at {retailer.retailer_id}: "
                          f"{rejected_batches} batches rejected (RSL < 72hrs), {accepted_batches} accepted")
        
        # Deliver only accepted cargo to retailer
        delivery_qty = sum(b.quantity for b in accepted_batches) if accepted_batches else 0
        
        if delivery_qty > 0:
            retailer.receive_delivery(delivery_qty, current_time)
        
        # Unload all cargo (accepted delivered, rejected disposed)
        truck.unload_cargo()
        
        # Mark order status
        if rejected_batches:
            order.status = 'partially_delivered'
            if accepted_batches:
                order.delivered_rsl_pct = sum(b.current_rsl for b in accepted_batches) / len(accepted_batches)
            
            # Retailer may need to reorder shortfall
            shortfall_kg = sum(b.quantity for b in rejected_batches)
            if shortfall_kg > 0:
                retailer.notify_delivery_failed('partial_rejection', current_time)
                retailer.trigger_emergency_reorder(shortfall_kg, current_time, engine)
            if order.order_id in self.active_orders:
                self.completed_orders.append(order)
                del self.active_orders[order.order_id]
                self.total_orders_fulfilled += 1
        else:
            order.mark_delivered(current_time)
            if accepted_batches:
                order.delivered_rsl_pct = sum(b.current_rsl for b in accepted_batches) / len(accepted_batches)
            
            if order.order_id in self.active_orders:
                self.completed_orders.append(order)
                del self.active_orders[order.order_id]
                self.total_orders_fulfilled += 1
        
        # Set truck to return to warehouse
        # Calculate route back to warehouse
        try:
            agent = self.truck_agents[truck.truck_id]
            
            # Convert TruckType to dict for router
            truck_type_dict = {
                'capacity_kg': truck.truck_type.capacity_kg,
                'fuel_consumption_empty_l_per_100km': truck.truck_type.fuel_consumption_empty_l_per_100km,
                'fuel_consumption_full_l_per_100km': truck.truck_type.fuel_consumption_full_l_per_100km,
                'fuel_efficiency_by_speed': truck.truck_type.fuel_efficiency_by_speed
            }
            
            return_route = agent.router.find_path(
                truck.current_node,
                self.node_id,
                truck_type_config=truck_type_dict,
                load_fraction=0.0,  # Empty truck returning
                avoid_blocked=True,
                current_time=current_time
            )
            
            if return_route:
                truck.current_route = return_route
                truck.route_progress = 0
                truck.segment_distance_traveled = 0.0
                truck.destination_node = self.node_id
                truck.status = "in_transit"
                
                logger.info(f"[DELIVERY] Truck {truck.truck_id} delivered {delivery_qty:.1f}kg to {retailer.retailer_id} "
                           f"(Order: {order.order_id}, Status: {'partial' if rejected_batches else 'complete'})")
                
                return {
                    'type': 'delivery_complete',
                    'truck_id': truck.truck_id,
                    'order_id': order.order_id,
                    'retailer_id': retailer.retailer_id,
                    'time': current_time,
                    'quantity_kg': delivery_qty,
                    'avg_rsl_at_delivery': round(order.delivered_rsl_pct, 2) if order.delivered_rsl_pct is not None else None,
                }
            else:
                # No return route — snap truck back to warehouse node so it
                # re-enters the available fleet on the next allocation cycle.
                truck.status = "idle"
                truck.current_route = None
                truck.destination_node = None
                truck.current_node = self.node_id  # recover to warehouse
                truck.current_location = self.location
                truck.route_progress = 0
                truck.segment_distance_traveled = 0.0
                self.trucks_changed = True
                logger.warning(f"[RETURN ROUTE FAILED] Truck {truck.truck_id} has no return route after delivery — recovered to warehouse {self.warehouse_id}")
                return {
                    'type': 'delivery_complete',
                    'truck_id': truck.truck_id,
                    'order_id': order.order_id,
                    'retailer_id': retailer.retailer_id,
                    'time': current_time,
                    'quantity_kg': delivery_qty,
                    'avg_rsl_at_delivery': round(order.delivered_rsl_pct, 2) if order.delivered_rsl_pct is not None else None,
                }
        except Exception as e:
            # Routing failed — snap truck back to warehouse so it re-enters the fleet
            logger.warning(f"[RETURN ROUTE FAILED] Truck {truck.truck_id}: Cannot calculate return route from "
                          f"retailer (Order: {order.order_id}) - {type(e).__name__}: {str(e)}")
            truck.status = "idle"
            truck.current_route = None
            truck.destination_node = None
            truck.current_node = self.node_id  # recover to warehouse
            truck.current_location = self.location
            self.trucks_changed = True
        
        return None
    
    def _handle_truck_return(self, truck: Truck, current_time: float):
        """
        Handle truck returning to warehouse after delivery.
        
        Args:
            truck: Truck that returned
            current_time: Current simulation time
        """
        # Unload any remaining cargo (shouldn't be any after delivery)
        truck.unload_cargo()
        
        # Mark order as completed
        if truck.assigned_order_id and truck.assigned_order_id in self.active_orders:
            order = self.active_orders[truck.assigned_order_id]
            if order.status != 'delivered' and order.status != 'partially_delivered':
                order.mark_delivered(current_time)
                self.total_orders_fulfilled += 1
            self.completed_orders.append(order)
            del self.active_orders[truck.assigned_order_id]
            
            # Increment truck delivery counter
            truck.total_deliveries += 1
            
            logger.info(f"[RETURN] Truck {truck.truck_id} returned to {self.warehouse_id} "
                       f"(Order {truck.assigned_order_id} fulfilled, Total fulfilled: {self.total_orders_fulfilled})")
        
        # Reset truck status
        truck.status = "idle"
        truck.assigned_order_id = None
        truck.current_route = None
        truck.destination_node = None
        truck.route_progress = 0
        truck.segment_distance_traveled = 0.0
        
        # Refuel if needed
        if truck.is_low_fuel():
            truck.refuel()
        
        # OPTIMIZATION 8: Set flag for event-driven processing
        self.trucks_changed = True
    
    def restock(self, quantity_kg: float, current_time: float) -> Dict:
        """
        Receive restocking shipment.

        Args:
            quantity_kg: Quantity received
            current_time: Current simulation time in minutes

        Returns:
            Event dict
        """
        # Check capacity before restocking
        available_capacity = self.max_capacity_kg - self.current_inventory_kg
        if quantity_kg > available_capacity:
            # Can only accept partial shipment
            accepted_qty = available_capacity
            rejected_qty = quantity_kg - available_capacity

            logger.warning(f"[CAPACITY] Warehouse {self.warehouse_id} at capacity! "
                          f"Rejecting {rejected_qty:.1f}kg (current: {self.current_inventory_kg:.0f}kg, "
                          f"max: {self.max_capacity_kg:.0f}kg)")

            quantity_kg = accepted_qty

            if quantity_kg <= 0:
                self.pending_restock = False  # Reset so future restocks can be triggered
                return {
                    'type': 'restock_rejected',
                    'warehouse_id': self.warehouse_id,
                    'time': current_time,
                    'reason': 'at_max_capacity',
                    'current_inventory_kg': round(self.current_inventory_kg, 2),
                    'perceived_inventory_kg': round(self._perceived_inventory_kg, 2),
                    'max_capacity_kg': self.max_capacity_kg,
                }

        # Represent the restock harvest date as a datetime derived from
        # simulation time so FIFO ordering is consistent with simulation time.
        # Using a fixed epoch (2000-01-01) + current_time minutes keeps
        # harvest_date comparable to the epoch dates used in
        # _initialize_inventory_batches.
        sim_epoch = datetime.datetime(2000, 1, 1)
        # SCIENTIFIC FIX (V1-Harvest): Add realistic Farm-to-Warehouse lead time (2 days)
        # Fresh oranges don't appear instantly; they are harvested and transported.
        lead_time_minutes = 2.0 * 24.0 * 60.0 # 2 days
        harvest_date = sim_epoch + datetime.timedelta(minutes=current_time - lead_time_minutes)

        # Create batches for the restock (get from config)
        batch_size_kg = self.config.get('warehouse_operations', {}).get('batch_size_kg', 1000)
        num_batches = int(quantity_kg / batch_size_kg)
        remainder = quantity_kg % batch_size_kg

        for i in range(num_batches):
            batch = OrangeBatch(
                f"{self.warehouse_id}_batch_{self.batch_counter:04d}",
                batch_size_kg,
                harvest_date,
                current_time,
                100.0
            )
            self.inventory_batches.append(batch)
            self.batch_counter += 1

        if remainder > 0:
            batch = OrangeBatch(
                f"{self.warehouse_id}_batch_{self.batch_counter:04d}",
                remainder,
                harvest_date,
                current_time,
                100.0
            )
            self.inventory_batches.append(batch)
            self.batch_counter += 1

        self.current_inventory_kg += quantity_kg
        self._perceived_inventory_kg += quantity_kg
        self.pending_restock = False  # Reset pending flag
        
        return {
            'type': 'warehouse_restock',
            'warehouse_id': self.warehouse_id,
            'time': current_time,
            'quantity_kg': quantity_kg,
            'new_inventory_kg': self.current_inventory_kg
        }
    
    def get_available_truck_count(self) -> int:
        """Get number of trucks currently available for assignment."""
        return sum(1 for truck in self.trucks 
                  if truck.status == "idle" and truck.current_node == self.node_id)
    
    def get_state(self) -> Dict:
        """
        Get current warehouse state for snapshot logging.
        
        Returns:
            Dictionary with warehouse state data
        """
        return {
            'warehouse_id': self.warehouse_id,
            'location': {'lat': self.location[0], 'lon': self.location[1]},
            'current_inventory_kg': round(self.current_inventory_kg, 2),
            'inventory_batches_count': len(self.inventory_batches),
            'total_trucks': len(self.trucks),
            'available_trucks': self.get_available_truck_count(),
            'trucks_in_transit': sum(1 for t in self.trucks if t.status == "in_transit"),
            'trucks_unloading': sum(1 for t in self.trucks if t.status in ["unloading", "unloading_complete"]),
            'pending_orders_count': len(self.pending_orders),
            'active_orders_count': len(self.active_orders),
            'total_orders_fulfilled': self.total_orders_fulfilled,
            'total_kg_shipped': round(self.total_kg_shipped, 2),
            'inventory_shortages': self.inventory_shortages,
            'pending_restock': self.pending_restock
        }
    
    def __repr__(self) -> str:
        return (f"WarehouseAgent(id={self.warehouse_id}, "
                f"inventory={self.current_inventory_kg:.0f}kg, "
                f"trucks={len(self.trucks)}, "
                f"available={self.get_available_truck_count()})")