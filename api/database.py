"""
InfluxDB Database Connection and Query Utilities
"""

import os
import logging
from typing import List, Dict, Any, Optional
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.query_api import QueryApi
from fastapi import HTTPException
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get logger for this module
logger = logging.getLogger(__name__)


class InfluxDBManager:
    """Manager for InfluxDB connections and queries"""
    
    def __init__(self):
        """Initialize InfluxDB client from environment variables"""
        self.url = os.getenv('INFLUX_URL', 'http://localhost:8086')
        self.token = os.getenv('INFLUX_TOKEN')
        self.org = os.getenv('INFLUX_ORG', 'digital-twin')
        self.bucket = os.getenv('INFLUX_BUCKET', 'supply-chain')
        
        if not self.token:
            raise ValueError("INFLUX_TOKEN environment variable not set")
        
        # Initialize client
        self.client = InfluxDBClient(
            url=self.url,
            token=self.token,
            org=self.org
        )
        self.query_api: QueryApi = self.client.query_api()
    
    def query(self, flux_query: str) -> List[Dict[str, Any]]:
        """
        Execute Flux query and return results as list of dictionaries
        
        Args:
            flux_query: Flux query string
            
        Returns:
            List of records as dictionaries
        """
        try:
            tables = self.query_api.query(flux_query, org=self.org)
            results = []
            
            for table in tables:
                for record in table.records:
                    # Convert record to dictionary safely
                    # Don't try to get_time() if time isn't available (e.g., after pivot)
                    record_dict = {}
                    
                    # Safely add all values (fields and tags)
                    if hasattr(record, 'values') and record.values:
                        record_dict.update(record.values)
                    
                    # Try to add time if available
                    try:
                        record_dict['_time'] = record.get_time()
                    except (AttributeError, KeyError, TypeError):
                        pass  # Time not available after pivot, skip it
                    
                    results.append(record_dict)
            
            return results
        except Exception as e:
            # Log error and raise with details
            # FIXED: Provide descriptive error message even when exception is empty
            error_msg = str(e) if str(e) else f"InfluxDB query failed: {type(e).__name__}"
            logger.error(f"Query error: {error_msg}")
            raise HTTPException(status_code=500, detail=error_msg)
    
    
    def _calculate_query_range(self, run_id: str) -> str:
        """
        Calculate optimal time range for queries based on simulation start time.
        
        SMART STRATEGY:
        - For snapshot queries: Use simulation start time to ensure we capture ALL data
        - For real-time sims: Query from start (may be days ago for 1x speed)
        - For fast sims: Query from start (just minutes ago for 100x speed)
        
        This guarantees complete data regardless of simulation speed or duration!
        
        Args:
            run_id: Simulation run ID
            
        Returns:
            Flux range string calculated from simulation start time
        """
        try:
            # Get simulation metadata to find start time
            metadata = self.get_simulation_metadata(run_id)
            
            if metadata and metadata.get('_time'):
                # Calculate time since simulation started (real time)
                import datetime
                from dateutil import parser
                
                start_time = parser.parse(metadata['_time'])
                now = datetime.datetime.now(datetime.timezone.utc)
                elapsed = now - start_time
                
                # Convert to minutes and add safety buffer
                elapsed_minutes = int(elapsed.total_seconds() / 60)
                
                # Add 10% safety buffer + minimum 15 minutes
                range_minutes = max(15, int(elapsed_minutes * 1.1))
                
                # Return as InfluxDB range string
                if range_minutes >= 1440:  # >= 1 day
                    range_days = range_minutes / 1440
                    return f"-{int(range_days)}d"
                elif range_minutes >= 60:  # >= 1 hour
                    range_hours = range_minutes / 60
                    return f"-{int(range_hours)}h"
                else:
                    return f"-{range_minutes}m"
            else:
                # Metadata not found or no start time - use safe default
                return "-2h"
                
        except (ValueError, KeyError, AttributeError) as e:
            # Expected errors from metadata queries
            logger.warning(f"Could not calculate query range for {run_id}: {type(e).__name__}")
            return "-2h"
        except Exception as e:
            # Unexpected errors
            logger.error(f"UNEXPECTED error calculating query range: {type(e).__name__}: {e}")
            return "-2h"
    
    def get_latest_truck_positions(self, run_id: str) -> List[Dict[str, Any]]:
        """
        Get latest positions for all trucks in a simulation run
        
        Args:
            run_id: Simulation run ID
            
        Returns:
            List of truck positions with telemetry data
        """
        # Calculate optimal query range based on simulation speed
        query_range = self._calculate_query_range(run_id)
        
        flux_query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: {query_range})
            |> filter(fn: (r) => r["_measurement"] == "truck_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["_field"] == "latitude" or r["_field"] == "longitude" or r["_field"] == "speed_kmh" or r["_field"] == "fuel_percent" or r["_field"] == "cargo_kg")
            |> group(columns: ["truck_id"])
            |> last()
            |> pivot(rowKey:["truck_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        return self.query(flux_query)
    
    def get_warehouse_state(self, run_id: str, warehouse_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get latest warehouse state(s)
        
        Args:
            run_id: Simulation run ID
            warehouse_id: Optional specific warehouse ID
            
        Returns:
            List of warehouse states
        """
        extra_filter = f'|> filter(fn: (r) => r["warehouse_id"] == "{warehouse_id}")' if warehouse_id else ''
        
        # Calculate optimal query range based on simulation speed
        query_range = self._calculate_query_range(run_id)
        
        flux_query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: {query_range})
            |> filter(fn: (r) => r["_measurement"] == "warehouse_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            {extra_filter}
            |> last()
            |> pivot(rowKey:["warehouse_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        return self.query(flux_query)
    
    def get_retailer_state(self, run_id: str, retailer_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get latest retailer state(s)
        
        Args:
            run_id: Simulation run ID
            retailer_id: Optional specific retailer ID
            
        Returns:
            List of retailer states
        """
        extra_filter = f'|> filter(fn: (r) => r["retailer_id"] == "{retailer_id}")' if retailer_id else ''
        
        # Calculate optimal query range based on simulation speed
        query_range = self._calculate_query_range(run_id)
        
        flux_query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: {query_range})
            |> filter(fn: (r) => r["_measurement"] == "retailer_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            {extra_filter}
            |> last()
            |> pivot(rowKey:["retailer_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        return self.query(flux_query)
    
    def get_entity_ids(self, run_id: str) -> Dict[str, List[str]]:
        """
        Get all entity IDs (warehouses, retailers, trucks) for a simulation run
        Dynamic discovery - no hardcoding!
        
        Args:
            run_id: Simulation run ID
            
        Returns:
            Dictionary with lists of warehouse_ids, retailer_ids, truck_ids
        """
        try:
            # Calculate optimal query range based on simulation speed
            query_range = self._calculate_query_range(run_id)
            
            # Get warehouse IDs - IDs are stored as TAGS not fields
            wh_query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: {query_range})
                |> filter(fn: (r) => r["_measurement"] == "warehouse_state")
                |> filter(fn: (r) => r["run_id"] == "{run_id}")
                |> group(columns: ["warehouse_id"])
                |> first()
                |> keep(columns: ["warehouse_id"])
            '''
            result = self.query_api.query(wh_query, org=self.org)
            warehouse_ids = []
            for table in result:
                for record in table.records:
                    wh_id = record.values.get('warehouse_id')
                    if wh_id and wh_id not in warehouse_ids:
                        warehouse_ids.append(wh_id)

            # Get retailer IDs
            ret_query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: {query_range})
                |> filter(fn: (r) => r["_measurement"] == "retailer_state")
                |> filter(fn: (r) => r["run_id"] == "{run_id}")
                |> group(columns: ["retailer_id"])
                |> first()
                |> keep(columns: ["retailer_id"])
            '''
            result = self.query_api.query(ret_query, org=self.org)
            retailer_ids = []
            for table in result:
                for record in table.records:
                    ret_id = record.values.get('retailer_id')
                    if ret_id and ret_id not in retailer_ids:
                        retailer_ids.append(ret_id)
            
            # Get truck IDs - use truck_telemetry
            truck_query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: {query_range})
                |> filter(fn: (r) => r["_measurement"] == "truck_telemetry")
                |> filter(fn: (r) => r["run_id"] == "{run_id}")
                |> group(columns: ["truck_id"])
                |> first()
                |> keep(columns: ["truck_id"])
            '''
            result = self.query_api.query(truck_query, org=self.org)
            truck_ids = []
            for table in result:
                for record in table.records:
                    truck_id = record.values.get('truck_id')
                    if truck_id and truck_id not in truck_ids:
                        truck_ids.append(truck_id)
        
            logger.debug(f"get_entity_ids: Found {len(warehouse_ids)} warehouses, {len(retailer_ids)} retailers, {len(truck_ids)} trucks")
        
            return {
                'warehouse_ids': sorted(warehouse_ids),
                'retailer_ids': sorted(retailer_ids),
                'truck_ids': sorted(truck_ids)
            }
        except (ValueError, KeyError, AttributeError) as e:
            # Expected query errors (missing fields, empty results)
            logger.warning(f"Error in get_entity_ids (expected): {type(e).__name__}: {e}")
            # Return empty lists on error
            return {
                'warehouse_ids': [],
                'retailer_ids': [],
                'truck_ids': []
            }
        except Exception as e:
            # Unexpected errors
            logger.error(f"UNEXPECTED error in get_entity_ids: {type(e).__name__}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Return empty lists on error
            return {
                'warehouse_ids': [],
                'retailer_ids': [],
                'truck_ids': []
            }
    
    def get_simulation_runs(self) -> List[Dict[str, Any]]:
        """
        Get list of all simulation runs
        
        Returns:
            List of simulation run metadata
        """
        flux_query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "simulation_metadata")
            |> filter(fn: (r) => r["event_type"] == "simulation_start")
            |> unique(column: "run_id")
        '''
        results = self.query(flux_query)
        
        # CRITICAL: Sort in Python because unique() creates multiple tables
        # Sort doesn't work across tables, only within them
        return sorted(results, key=lambda x: x.get('_time', ''), reverse=True)
    
    def get_simulation_metadata(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Get complete metadata for a specific simulation run.
        
        Args:
            run_id: Simulation run ID
            
        Returns:
            Dictionary with simulation metadata (speed, time_step_minutes, etc) or None
        """
        flux_query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -30d)
            |> filter(fn: (r) => r["_measurement"] == "simulation_metadata")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["event_type"] == "simulation_start")
            |> pivot(rowKey: ["run_id"], columnKey: ["_field"], valueColumn: "_value")
            |> limit(n: 1)
        '''
        results = self.query(flux_query)
        
        if results and len(results) > 0:
            return results[0]
        return None
    
    def get_events(self, run_id: str, limit: int = 50, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get recent events for a simulation run
        
        Args:
            run_id: Simulation run ID
            limit: Maximum number of events to return
            event_type: Optional filter by event type
            
        Returns:
            List of events
        """
        event_filter = f'|> filter(fn: (r) => r["event_type"] == "{event_type}")' if event_type else ''
        
        flux_query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "events")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            {event_filter}
            |> sort(columns: ["_time"], desc: true)
            |> limit(n: {limit})
        '''
        return self.query(flux_query)
    
    def get_time_series(self, run_id: str, measurement: str, field: str, 
                       entity_id_field: Optional[str] = None, 
                       entity_id: Optional[str] = None,
                       window: str = "15m") -> List[Dict[str, Any]]:
        """
        Get time series data for charts
        
        Args:
            run_id: Simulation run ID
            measurement: Measurement name (e.g., 'warehouse_state', 'retailer_state')
            field: Field to query (e.g., 'inventory_kg', 'available_trucks')
            entity_id_field: Entity ID field name (e.g., 'warehouse_id', 'retailer_id')
            entity_id: Specific entity ID
            window: Aggregation window
            
        Returns:
            List of time series points
        """
        entity_filter = f'|> filter(fn: (r) => r["{entity_id_field}"] == "{entity_id}")' if entity_id_field and entity_id else ''
        
        flux_query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "{measurement}")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["_field"] == "{field}" or r["_field"] == "timestamp")
            {entity_filter}
            |> aggregateWindow(every: {window}, fn: last, createEmpty: false)
            |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
            |> sort(columns: ["_time"])
        '''
        return self.query(flux_query)
    
    def get_complete_state(self, run_id: str) -> Dict[str, Any]:
        """
        Get complete dashboard state for a simulation run.
        Fetches all warehouse, retailer, and truck data in 3 efficient queries.
        
        This method retrieves FULL state (not just coordinates) without using pivot(),
        which avoids the _time field errors and duplicate issues.
        
        Args:
            run_id: Simulation run ID
            
        Returns:
            Dictionary containing:
            - warehouses: List of warehouse states with all fields
            - retailers: List of retailer states with all fields
            - trucks: List of truck telemetry with all fields
        """
        
        # Query 1: Get ALL warehouse fields from latest snapshot
        # CRITICAL: Use pivot to convert fields to columns
        # FIXED: Sort by _time descending and take first record to get MOST RECENTLY WRITTEN data
        # (not just data with latest simulation time, since sim time may not have advanced much)
        wh_query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r["_measurement"] == "warehouse_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> group(columns: ["warehouse_id", "_field"])
            |> sort(columns: ["_time"], desc: true)
            |> limit(n: 1)
            |> group(columns: ["warehouse_id"])
            |> pivot(rowKey: ["warehouse_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        
        warehouses = []
        for record in self.query(wh_query):
            if record.get('warehouse_id'):
                wh_dict = {'id': record['warehouse_id'], 'name': record['warehouse_id']}
                # Add all numeric/scalar fields
                for key, value in record.items():
                    if key not in ['_time', 'result', 'table', '_start', '_stop', '_measurement', 'run_id', 'warehouse_id']:
                        wh_dict[key] = value
                warehouses.append(wh_dict)
        
        # Query 2: Get ALL retailer fields from latest snapshot
        ret_query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -1h)
            |> filter(fn: (r) => r["_measurement"] == "retailer_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> group(columns: ["retailer_id", "_field"])
            |> sort(columns: ["_time"], desc: true)
            |> limit(n: 1)
            |> group(columns: ["retailer_id"])
            |> pivot(rowKey: ["retailer_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        
        retailers = []
        for record in self.query(ret_query):
            if record.get('retailer_id'):
                ret_dict = {'id': record['retailer_id']}
                # Add all numeric/scalar fields
                for key, value in record.items():
                    if key not in ['_time', 'result', 'table', '_start', '_stop', '_measurement', 'run_id', 'retailer_id', 'warehouse_id']:
                        ret_dict[key] = value
                retailers.append(ret_dict)
        
        # Query 3: Get ALL truck state fields from latest data
        # Status is now logged as a numeric field (status_code) for InfluxDB compatibility
        # Use narrow time range to avoid schema conflicts with old simulations
        truck_query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -15m)
            |> filter(fn: (r) => r["_measurement"] == "truck_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> group(columns: ["truck_id", "_field"])
            |> sort(columns: ["_time"], desc: true)
            |> limit(n: 1)
            |> group(columns: ["truck_id"])
            |> pivot(rowKey: ["truck_id"], columnKey: ["_field"], valueColumn: "_value")
            |> keep(columns: ["truck_id", "status_code", "speed_kmh", "current_load_kg", "fuel_percent", "latitude", "longitude", 
                             "timestamp", "total_distance_km", "load_factor", "cargo_batches_count", 
                             "total_deliveries", "route_progress", "assigned_order_id", "total_fuel_consumed_liters", 
                             "current_fuel_liters", "cargo_rsl"])
        '''
        
        # Status code to string mapping — must match engine.py status_codes exactly:
        # 'idle':0.0, 'loading':1.0, 'in_transit':2.0, 'unloading':3.0,
        # 'unloading_complete':4.0, 'arrived':5.0, 'refueling':6.0, 'destroyed':7.0
        status_code_map = {
            0.0: 'idle',
            1.0: 'loading',
            2.0: 'in_transit',
            3.0: 'unloading',
            4.0: 'unloading_complete',
            5.0: 'arrived',
            6.0: 'refueling',
            7.0: 'destroyed',
            -1.0: 'unknown'
        }
        
        trucks = []
        for record in self.query(truck_query):
            if record.get('truck_id'):
                truck_dict = {'id': record['truck_id']}
                
                # Convert numeric status_code back to string status
                if 'status_code' in record:
                    truck_dict['status'] = status_code_map.get(record['status_code'], 'unknown')
                
                # Add all numeric/scalar fields
                for key, value in record.items():
                    if key not in ['_time', 'result', 'table', '_start', '_stop', '_measurement', 'run_id', 'truck_id', 'status_code']:
                        truck_dict[key] = value
                trucks.append(truck_dict)
        
        return {
            'warehouses': warehouses,
            'retailers': retailers,
            'trucks': trucks
        }
    
    def close(self):
        """Close InfluxDB client connection"""
        self.client.close()


# Global instance
_db_manager: Optional[InfluxDBManager] = None


def get_db() -> InfluxDBManager:
    """Get or create database manager instance"""
    global _db_manager
    if _db_manager is None:
        _db_manager = InfluxDBManager()
    return _db_manager
