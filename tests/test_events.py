"""
Unit tests for EventQueue and event types.
"""

import pytest
from src.simulation.events import EventQueue
from src.simulation.events import (
    Event, TruckDepartureEvent, TruckArrivalEvent, OrderPlacedEvent,
    AccidentStartEvent, AccidentEndEvent, WarehouseRestockEvent,
    CustomerArrivalEvent, RouteChangedEvent, LowFuelWarningEvent,
    CargoSpoiledEvent
)


def test_event_queue_initialization():
    """Test that event queue initializes empty."""
    queue = EventQueue()
    assert len(queue) == 0
    assert queue.is_empty()


def test_event_queue_schedule_single_event():
    """Test scheduling a single event."""
    queue = EventQueue()
    event = TruckDepartureEvent(
        time=100.0,
        truck_id="T001",
        warehouse_id="WH001",
        destination_id="R001",
        cargo_batch_id="B001"
    )
    
    queue.schedule(event)
    assert len(queue) == 1
    assert not queue.is_empty()


def test_event_queue_schedule_multiple_events():
    """Test scheduling multiple events."""
    queue = EventQueue()
    
    event1 = TruckDepartureEvent(100.0, "T001", "WH001", "R001", "B001")
    event2 = TruckArrivalEvent(200.0, "T001", "R001", "B001")
    event3 = OrderPlacedEvent(50.0, "O001", "R001", "WH001", 1000, None)
    
    queue.schedule(event1)
    queue.schedule(event2)
    queue.schedule(event3)
    
    assert len(queue) == 3


def test_event_queue_priority_ordering():
    """Test that events are retrieved in time order."""
    queue = EventQueue()
    
    # Schedule events out of order
    event1 = TruckDepartureEvent(200.0, "T001", "WH001", "R001", "B001")
    event2 = TruckArrivalEvent(100.0, "T001", "R001", "B001")
    event3 = OrderPlacedEvent(300.0, "O001", "R001", "WH001", 1000, None)
    
    queue.schedule(event1)
    queue.schedule(event2)
    queue.schedule(event3)
    
    # Get events at specific times
    events_at_100 = queue.get_events_at(100.0)
    assert len(events_at_100) == 1
    assert events_at_100[0].time == 100.0
    
    events_at_200 = queue.get_events_at(200.0)
    assert len(events_at_200) == 1
    assert events_at_200[0].time == 200.0


def test_event_queue_get_events_at_time():
    """Test getting events at specific time."""
    queue = EventQueue()
    
    event1 = TruckDepartureEvent(100.0, "T001", "WH001", "R001", "B001")
    event2 = TruckDepartureEvent(100.0, "T002", "WH001", "R002", "B002")
    event3 = TruckArrivalEvent(200.0, "T001", "R001", "B001")
    
    queue.schedule(event1)
    queue.schedule(event2)
    queue.schedule(event3)
    
    # Get events at time 100
    events = queue.get_events_at(100.0)
    assert len(events) == 2
    
    # Queue should now have 1 event left
    assert len(queue) == 1


def test_event_queue_peek():
    """Test peeking at next event without removing it."""
    queue = EventQueue()
    
    event1 = TruckDepartureEvent(100.0, "T001", "WH001", "R001", "B001")
    event2 = TruckArrivalEvent(200.0, "T001", "R001", "B001")
    
    queue.schedule(event1)
    queue.schedule(event2)
    
    # Peek should return earliest event
    next_event = queue.peek()
    assert next_event is not None
    assert next_event.time == 100.0
    
    # Queue should still have both events
    assert len(queue) == 2


def test_event_queue_peek_empty():
    """Test peeking at empty queue."""
    queue = EventQueue()
    assert queue.peek() is None


def test_truck_departure_event():
    """Test TruckDepartureEvent creation and data."""
    event = TruckDepartureEvent(
        time=100.0,
        truck_id="T001",
        warehouse_id="WH001",
        destination_id="R001",
        cargo_batch_id="B001"
    )
    
    assert event.time == 100.0
    assert event.event_type == "truck_departure"
    assert event.truck_id == "T001"
    
    data = event.to_dict()
    assert data['truck_id'] == "T001"
    assert data['warehouse_id'] == "WH001"
    assert data['destination_id'] == "R001"


def test_truck_arrival_event():
    """Test TruckArrivalEvent creation."""
    event = TruckArrivalEvent(
        time=200.0,
        truck_id="T001",
        destination_id="R001",
        cargo_batch_id="B001"
    )
    
    assert event.time == 200.0
    assert event.event_type == "truck_arrival"
    assert event.truck_id == "T001"


def test_order_placed_event():
    """Test OrderPlacedEvent creation."""
    event = OrderPlacedEvent(
        time=50.0,
        order_id="O001",
        retailer_id="R001",
        warehouse_id="WH001",
        quantity_kg=1000,
        order_obj=None
    )
    
    assert event.time == 50.0
    assert event.event_type == "order_placed"
    assert event.quantity_kg == 1000


def test_accident_start_event():
    """Test AccidentStartEvent creation."""
    event = AccidentStartEvent(
        time=150.0,
        accident_id="A001",
        segment_id="S001",
        duration_minutes=45.0,
        severity="moderate"
    )
    
    assert event.time == 150.0
    assert event.event_type == "accident_start"
    assert event.severity == "moderate"
    assert event.duration_minutes == 45.0


def test_accident_end_event():
    """Test AccidentEndEvent creation."""
    event = AccidentEndEvent(
        time=195.0,
        accident_id="A001",
        segment_id="S001"
    )
    
    assert event.time == 195.0
    assert event.event_type == "accident_end"


def test_warehouse_restock_event():
    """Test WarehouseRestockEvent creation."""
    event = WarehouseRestockEvent(
        time=10080.0,  # 1 week
        warehouse_id="WH001",
        product_type="oranges",
        quantity_kg=30000
    )
    
    assert event.time == 10080.0
    assert event.event_type == "warehouse_restock"
    assert event.quantity_kg == 30000


def test_route_changed_event():
    """Test RouteChangedEvent creation."""
    event = RouteChangedEvent(
        time=120.0,
        truck_id="T001",
        old_route_length=50.0,
        new_route_length=45.0,
        reason="accident_avoidance"
    )
    
    assert event.time == 120.0
    assert event.event_type == "route_changed"
    assert event.reason == "accident_avoidance"


def test_low_fuel_warning_event():
    """Test LowFuelWarningEvent creation."""
    event = LowFuelWarningEvent(
        time=180.0,
        truck_id="T001",
        fuel_liters=15.0,
        fuel_percent=18.75
    )
    
    assert event.time == 180.0
    assert event.event_type == "low_fuel_warning"
    assert event.fuel_percent == 18.75


def test_cargo_spoiled_event():
    """Test CargoSpoiledEvent creation."""
    event = CargoSpoiledEvent(
        time=500.0,
        truck_id="T001",
        cargo_batch_id="B001",
        final_rsl=0.0
    )
    
    assert event.time == 500.0
    assert event.event_type == "cargo_spoiled"
    assert event.final_rsl == 0.0


def test_event_comparison():
    """Test that events are compared by time."""
    event1 = TruckDepartureEvent(100.0, "T001", "WH001", "R001", "B001")
    event2 = TruckArrivalEvent(200.0, "T001", "R001", "B001")
    
    assert event1 < event2
    assert not (event2 < event1)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
