"""
Trucks API Router
Endpoints for truck positions and telemetry
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List
from database import get_db
from schemas import TruckPosition, TruckTelemetry

router = APIRouter()


@router.get("/trucks/live", response_model=List[dict])
async def get_live_truck_positions(run_id: str = Query(..., description="Simulation run ID")):
    """
    Get latest positions of all trucks for map display
    
    Args:
        run_id: Simulation run ID
        
    Returns:
        List of truck positions with key telemetry
    """
    try:
        db = get_db()
        
        # Get latest truck telemetry
        positions = db.get_latest_truck_positions(run_id)
        
        # Format for frontend
        result = []
        for pos in positions:
            # Get latitude and longitude from separate fields (as stored in InfluxDB)
            lat = pos.get('latitude')
            lon = pos.get('longitude')
            
            if lat is not None and lon is not None:
                result.append({
                    "truck_id": pos.get('truck_id'),
                    "location": (float(lat), float(lon)),  # Create tuple from separate fields
                    "status": pos.get('status', 'unknown'),
                    "speed_kmh": pos.get('speed_kmh', 0),
                    "fuel_percent": pos.get('fuel_percent', 0),
                    "cargo_kg": pos.get('cargo_kg', 0),
                    "timestamp": pos.get('timestamp', 0)
                })
        
        return result
    except (ValueError, TypeError) as e:
        # Data conversion errors (lat/lon parsing, etc.)
        raise HTTPException(status_code=500, detail=f"Data processing error: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Missing expected fields in database response
        raise HTTPException(status_code=404, detail=f"Incomplete truck data for run_id {run_id}")
    except Exception as e:
        # Unexpected errors (likely database/connection issues)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trucks/{truck_id}/telemetry", response_model=dict)
async def get_truck_telemetry(
    truck_id: str,
    run_id: str = Query(..., description="Simulation run ID")
):
    """
    Get latest telemetry data for a specific truck
    
    Args:
        truck_id: Truck identifier
        run_id: Simulation run ID
        
    Returns:
        Detailed telemetry data
    """
    try:
        db = get_db()
        
        # Query for specific truck's latest telemetry
        flux_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "truck_telemetry")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["truck_id"] == "{truck_id}")
            |> last()
        '''
        results = db.query(flux_query)
        
        if not results:
            raise HTTPException(status_code=404, detail=f"Truck {truck_id} not found")
        
        # Consolidate fields from multiple records
        telemetry = {}
        for record in results:
            field = record.get('_field')
            value = record.get('_value')
            if field:
                telemetry[field] = value
            
            # Also capture common fields
            if 'truck_id' not in telemetry:
                telemetry['truck_id'] = record.get('truck_id')
            if 'timestamp' not in telemetry:
                telemetry['timestamp'] = record.get('timestamp', 0)
        
        return telemetry
    except HTTPException:
        # Re-raise HTTP exceptions (like 404)
        raise
    except (ValueError, TypeError) as e:
        # Data type conversion errors
        raise HTTPException(status_code=500, detail=f"Telemetry data processing error: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Missing fields in database response
        raise HTTPException(status_code=500, detail=f"Incomplete telemetry data structure: {str(e)}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trucks/{truck_id}/history", response_model=List[dict])
async def get_truck_history(
    truck_id: str,
    run_id: str = Query(..., description="Simulation run ID"),
    start_time: float = Query(0, description="Start time in minutes"),
    end_time: float = Query(None, description="End time in minutes (optional)")
):
    """
    Get historical route/telemetry for a truck
    
    Args:
        truck_id: Truck identifier
        run_id: Simulation run ID
        start_time: Start time in simulation minutes
        end_time: End time in simulation minutes (if None, use latest)
        
    Returns:
        List of historical telemetry points
    """
    try:
        db = get_db()
        
        # Build time filter
        time_filter = f'''
        |> filter(fn: (r) => r["timestamp"] >= {start_time})
        '''
        if end_time:
            time_filter += f'''
        |> filter(fn: (r) => r["timestamp"] <= {end_time})
            '''
        
        flux_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "truck_telemetry")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["truck_id"] == "{truck_id}")
            {time_filter}
            |> filter(fn: (r) => r["_field"] == "location" or r["_field"] == "status" or r["_field"] == "speed_kmh")
            |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
        '''
        results = db.query(flux_query)
        
        return results
    except (ValueError, TypeError) as e:
        # Query parameter errors (invalid times, etc.)
        raise HTTPException(status_code=400, detail=f"Invalid query parameters: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Data structure errors
        raise HTTPException(status_code=500, detail=f"Data access error: {str(e)}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))
