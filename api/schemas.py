"""
Pydantic Models for API Request/Response Validation
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime


# ===== Simulation Models =====

class SimulationRun(BaseModel):
    """Simulation run metadata"""
    run_id: str
    start_time: datetime
    duration_days: float
    num_warehouses: int
    num_retailers: int
    num_trucks: int


class SimulationStatus(BaseModel):
    """Current simulation status"""
    run_id: str
    current_time_minutes: float
    current_time_formatted: str  # "Day X, HH:MM"
    is_running: bool
    progress_percent: float


class EntityList(BaseModel):
    """List of all entities in a simulation"""
    warehouse_ids: List[str]
    retailer_ids: List[str]
    truck_ids: List[str]


# ===== Truck Models =====

class TruckPosition(BaseModel):
    """Real-time truck position for map"""
    truck_id: str
    location: Tuple[float, float]  # (lat, lon)
    status: str  # idle, in_transit, loading, unloading
    speed_kmh: float
    fuel_percent: float
    cargo_kg: float
    timestamp: float


class TruckTelemetry(BaseModel):
    """Detailed truck telemetry data"""
    truck_id: str
    location: Tuple[float, float]
    location_true: Optional[Tuple[float, float]]  # Ground truth for comparison
    speed_kmh: float
    fuel_liters: float
    fuel_percent: float
    cargo_kg: float
    cargo_rsl: float  # Remaining shelf life
    temperature_celsius: float
    driver_fatigue: float
    driver_status: str  # "Driving" or "Break"
    status: str
    timestamp: float


# ===== Warehouse Models =====

class WarehouseState(BaseModel):
    """Warehouse state snapshot"""
    warehouse_id: str
    location: Tuple[float, float]
    inventory_kg: float
    available_trucks: int
    trucks_in_transit: int
    total_fleet: int
    active_orders: int
    timestamp: float


class WarehouseTrends(BaseModel):
    """Time series data for warehouse"""
    warehouse_id: str
    inventory_series: List[Dict[str, Any]]  # [{time, value}, ...]
    fleet_utilization_series: List[Dict[str, Any]]


# ===== Retailer Models =====

class RetailerState(BaseModel):
    """Retailer state snapshot"""
    retailer_id: str
    location: Tuple[float, float]
    inventory_kg: float
    reorder_point: float
    demand_rate: float
    timestamp: float


class RetailerTrends(BaseModel):
    """Time series data for retailer"""
    retailer_id: str
    stock_series: List[Dict[str, Any]]  # [{time, value}, ...]


# ===== KPI Models =====

class KPISummary(BaseModel):
    """Summary KPI metrics for dashboard cards"""
    total_deliveries: int
    active_trucks: int  # In-transit
    avg_delivery_time_minutes: float
    current_accidents: int
    total_inventory_kg: float


class FleetStatus(BaseModel):
    """Fleet status across all warehouses"""
    warehouse_id: str
    trucks_idle: int
    trucks_in_transit: int
    trucks_loading: int
    total_fleet: int


# ===== Event Models =====

class Event(BaseModel):
    """Simulation event"""
    event_id: Optional[str] = None
    event_type: str  # order_placed, delivery_complete, accident_start, etc.
    timestamp: float
    run_id: str
    description: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ===== Weather Models =====

class WeatherState(BaseModel):
    """Weather state at a point in time"""
    state: str  # clear, light_rain, rain, heavy_rain, fog
    timestamp: float


class WeatherSeries(BaseModel):
    """Time series of weather transitions"""
    transitions: List[WeatherState]


# ===== Time Series Models =====

class TimeSeriesPoint(BaseModel):
    """Generic time series data point"""
    time: datetime
    value: float


class TimeSeries(BaseModel):
    """Generic time series data"""
    entity_id: str
    field_name: str
    data: List[TimeSeriesPoint]
