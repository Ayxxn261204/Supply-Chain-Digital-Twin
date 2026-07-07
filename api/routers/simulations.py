"""
Simulations API Router
Endpoints for listing simulations and getting metadata
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List
from pathlib import Path
import yaml

from database import get_db
from schemas import SimulationRun, SimulationStatus, EntityList

router = APIRouter()


@router.get("/simulations", response_model=List[dict])
async def list_simulations(only_running: bool = True, lookback_hours: int = 720):  # 30 days
    """
    Get list of simulation runs
    
    Args:
        only_running: If True, only return simulations that are currently running (default)
        lookback_hours: Hours to look back for simulation discovery (default: 24)
    
    Returns:
        List of simulation metadata, deduplicated by run_id
    
    Design:
        A simulation is "running" if it has a simulation_start event but NO simulation_end event.
        We look back a configurable window (default 24h) to find recent simulations.
        This handles all cases:
        - New sims (just started, has start event)
        - Running sims (has start, no end)
        - Finished sims (has both start and end - excluded)
        - Old abandoned sims (start event outside window - excluded)
    """
    try:
        db = get_db()
        runs = db.get_simulation_runs()
        
        # Deduplicate by run_id (keep most recent _time)
        seen = {}
        for run in runs:
            run_id = run.get('run_id')
            if run_id and (run_id not in seen or run.get('_time', '') > seen[run_id].get('_time', '')):
                seen[run_id] = run
        
        unique_runs = list(seen.values())
        unique_runs.sort(key=lambda x: x.get('_time', ''), reverse=True)
        
        # Filter to only running simulations using clean metadata-based approach
        if only_running:
            running_runs = []
            
            for run in unique_runs:
                run_id = run.get('run_id')
                
                # Professional approach: Single query checking both start and end events
                # Uses simulation_metadata measurement only (single source of truth)
                
                # Check 1: Does simulation have an end event?
                end_query = f'''
                from(bucket: "{db.bucket}")
                    |> range(start: -{lookback_hours}h)
                    |> filter(fn: (r) => r["_measurement"] == "simulation_metadata")
                    |> filter(fn: (r) => r["run_id"] == "{run_id}")
                    |> filter(fn: (r) => r["event_type"] == "simulation_end")
                    |> limit(n: 1)
                '''
                end_events = db.query(end_query)
                
                if end_events:
                    # Has end event = completed, skip
                    continue
                
                # Check 2: Has recent heartbeat? (last 60 seconds)
                heartbeat_query = f'''
                from(bucket: "{db.bucket}")
                    |> range(start: -60s)
                    |> filter(fn: (r) => r["run_id"] == "{run_id}")
                    |> filter(fn: (r) => r["_measurement"] == "simulation_metadata")
                    |> filter(fn: (r) => r["event_type"] == "heartbeat")
                    |> limit(n: 1)
                '''
                recent_heartbeat = db.query(heartbeat_query)
                
                if recent_heartbeat:
                    # No end event + recent heartbeat = actively running
                    running_runs.append(run)
                # else: No end event + no heartbeat = force-stopped or dead, skip
            
            unique_runs = running_runs
        
        # Format response
        result = []
        for run in unique_runs:
            result.append({
                "run_id": run.get('run_id'),
                "start_time": run.get('_time'),
                "duration_days": run.get('duration_days'),
                "num_warehouses": run.get('num_warehouses'),
                "num_retailers": run.get('num_retailers'),
                "num_trucks": run.get('num_trucks'),
                "is_running": only_running  # If filtered, all are running
            })
        
        return result
    except (ValueError, TypeError) as e:
        # Data processing errors
        raise HTTPException(status_code=500, detail=f"Simulation list processing error: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Missing fields in metadata
        raise HTTPException(status_code=500, detail="Incomplete simulation metadata")
    except Exception as e:
        # Unexpected database/query errors
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/simulations/{run_id}", response_model=dict)
async def get_simulation(run_id: str):
    """
    Get simulation metadata and configuration
    
    Args:
        run_id: Simulation run ID
        
    Returns:
        Simulation metadata including entity counts
    """
    try:
        db = get_db()
        
        # Get simulation metadata using new method
        metadata = db.get_simulation_metadata(run_id)
        
        if not metadata:
            raise HTTPException(status_code=404, detail=f"Simulation {run_id} not found")
        
        # Get entity counts dynamically
        entities = db.get_entity_ids(run_id)
        
        return {
            "run_id": run_id,
            "start_time": metadata.get('_time'),
            "duration_days": float(metadata.get('duration_days', 7)),
            "time_step_minutes": int(metadata.get('time_step_minutes', 1)),
            "speed": float(metadata.get('speed', 1.0)),  # Simulation speed multiplier
            "num_warehouses": len(entities['warehouse_ids']),
            "num_retailers": len(entities['retailer_ids']),
            "num_trucks": len(entities['truck_ids']),
            "random_seed": int(metadata.get('random_seed', 0))
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/simulations/{run_id}/status", response_model=dict)
async def get_simulation_status(run_id: str):
    """
    Get current simulation status and time
    
    Args:
        run_id: Simulation run ID
        
    Returns:
        Current status including simulation time and progress
    """
    try:
        db = get_db()
        
        # Get latest timestamp from snapshot measurements (warehouse_state, retailer_state, or truck_state)
        # These measurements now all have the 'timestamp' field with simulation time
        flux_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["run_id"] == "{run_id}")  
            |> filter(fn: (r) => r["_measurement"] == "warehouse_state" or r["_measurement"] == "retailer_state" or r["_measurement"] == "truck_state")
            |> filter(fn: (r) => r["_field"] == "timestamp")
            |> last()
            |> limit(n: 1)
        '''
        results = db.query(flux_query)
        
        if not results:
            return {
                "run_id": run_id,
                "current_time_minutes": 0,
                "current_time_formatted": "Day 0, 00:00",
                "is_running": False,
                "progress_percent": 0
            }
        
        latest = results[0]
        current_time_minutes = latest.get('_value', 0)  # timestamp is in _value column
        
        # Format time as "Day X, HH:MM"
        days = int(current_time_minutes // (24 * 60))
        hours = int((current_time_minutes % (24 * 60)) // 60)
        minutes = int(current_time_minutes % 60)
        formatted_time = f"Day {days}, {hours:02d}:{minutes:02d}"
        
        # Get simulation duration from metadata
        metadata_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -30d)
            |> filter(fn: (r) => r["_measurement"] == "simulation_metadata" or r["_measurement"] == "events")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["event_type"] == "simulation_start")
            |> last()
        '''
        metadata = db.query(metadata_query)
        duration_days = metadata[0].get('duration_days', 7) if metadata else 7
        max_time_minutes = duration_days * 24 * 60
        
        progress_percent = min(100.0, (current_time_minutes / max_time_minutes) * 100)
        
        # Check if simulation ended
        end_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "simulation_metadata" or r["_measurement"] == "events")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["event_type"] == "simulation_end")
            |> last()
        '''
        end_results = db.query(end_query)
        is_running = len(end_results) == 0
        
        return {
            "run_id": run_id,
            "current_time_minutes": current_time_minutes,
            "current_time_formatted": formatted_time,
            "is_running": is_running,
            "progress_percent": round(progress_percent, 1)
        }
    except (ValueError, TypeError, KeyError) as e:
        # Data processing or field access errors
        raise HTTPException(status_code=500, detail=f"Status calculation error: {str(e)}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/simulations/{run_id}/entities", response_model=EntityList)
async def get_simulation_entities(run_id: str):
    """
    Get list of all entities (warehouses, retailers, trucks) in simulation
    CRITICAL: Dynamic discovery - no hardcoded counts!
    
    Args:
        run_id: Simulation run ID
        
    Returns:
        Lists of warehouse_ids, retailer_ids, truck_ids
    """
    try:
        db = get_db()
        entities = db.get_entity_ids(run_id)
        return EntityList(**entities)
    except (KeyError, ValueError) as e:
        # Missing or invalid entity data
        raise HTTPException(status_code=404, detail=f"No entity data found for run_id {run_id}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/simulations/{run_id}/coordinates", response_model=dict)
async def get_entity_coordinates(run_id: str):
    """
    Get GPS coordinates for all warehouses and retailers
    
    Uses real data from:
    - Warehouses: simulation config file
    - Retailers: InfluxDB state snapshots (where simulation stored them)
    
    Args:
        run_id: Simulation run ID
        
    Returns:
        Dictionary with warehouse and retailer coordinates
    """
    try:
        db = get_db()
        
        # Load config file to get ALL configured warehouses
        # Get absolute path: api/routers/simulations.py -> ../../config/simulation_config.yaml
        current_file = Path(__file__)  # api/routers/simulations.py
        api_dir = current_file.parent.parent  # api/ directory
        project_root = api_dir.parent  # project root
        config_path = project_root / "config" / "simulation_config.yaml"
        
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        # Get warehouse coordinates from InfluxDB (for active warehouses with data)
        wh_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "warehouse_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["_field"] == "latitude" or r["_field"] == "longitude" or r["_field"] == "current_inventory_kg" or r["_field"] == "pending_orders_count")
            |> last()
            |> pivot(rowKey: ["warehouse_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        wh_results = db.query(wh_query)
        
        # Create dict of InfluxDB warehouses
        wh_from_influx = {}
        for wh in wh_results:
            wh_id = wh.get('warehouse_id')
            lat = wh.get('latitude')
            lon = wh.get('longitude')
            if wh_id and lat is not None and lon is not None:
                wh_from_influx[wh_id] = {
                    'id': wh_id,
                    'name': wh.get('name', wh_id),
                    'lat': float(lat),
                    'lon': float(lon),
                    'current_inventory_kg': float(wh.get('current_inventory_kg', 0)) if wh.get('current_inventory_kg') is not None else None,
                    'active_orders_count': int(wh.get('pending_orders_count', 0)) if wh.get('pending_orders_count') is not None else None,
                    'has_data': True  # Mark as having real-time data
                }
        
        # Get ALL configured warehouses from config and merge
        warehouses = []
        for wh_config in config.get('warehouses', []):
            wh_id = wh_config['id']
            if wh_id in wh_from_influx:
                # Use InfluxDB data (real-time position)
                warehouses.append(wh_from_influx[wh_id])
            else:
                # Use config file position (static fallback)
                warehouses.append({
                    'id': wh_id,
                    'name': wh_config.get('name', wh_id),
                    'lat': float(wh_config['location']['lat']),
                    'lon': float(wh_config['location']['lon']),
                    'has_data': False  # Mark as configured but no real-time data
                })
        
        # Get retailer coordinates from InfluxDB
        retailer_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "retailer_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["_field"] == "latitude" or r["_field"] == "longitude" or r["_field"] == "current_inventory_kg")
            |> last()
            |> pivot(rowKey: ["retailer_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        retailer_results = db.query(retailer_query)
        
        retailers = []
        for ret in retailer_results:
            ret_id = ret.get('retailer_id')
            lat = ret.get('latitude')
            lon = ret.get('longitude')
            
            if ret_id and lat is not None and lon is not None:
                retailers.append({
                    'id': ret_id,
                    'lat': float(lat),
                    'lon': float(lon),
                    'current_inventory_kg': float(ret.get('current_inventory_kg', 0)) if ret.get('current_inventory_kg') is not None else None,
                    'has_data': True
                })
        
        # Note: Retailers are dynamically generated by simulation, so we don't have
        # static config positions. We only show retailers that have written data.
        
        return {
            'warehouses': warehouses,
            'retailers': retailers
        }
    except FileNotFoundError:
        # Fallback if config file not found
        raise HTTPException(status_code=500, detail="Configuration file not found")
    except (ValueError, TypeError) as e:
        # Data processing errors
        raise HTTPException(status_code=500, detail=f"Coordinate data processing error: {str(e)}")
    except (KeyError, IndexError, AttributeError) as e:
        # Missing data or structure errors
        raise HTTPException(status_code=404, detail=f"Incomplete coordinate data for run_id {run_id}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))

