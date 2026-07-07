"""
Time Series API Router
Endpoints for chart data (inventory trends, fleet utilization, etc.)
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List
from database import get_db

router = APIRouter()


@router.get("/timeseries/warehouse/inventory", response_model=List[dict])
async def get_warehouse_inventory_trend(
    run_id: str = Query(..., description="Simulation run ID"),
    warehouse_id: str = Query(..., description="Warehouse ID"),
    window: str = Query("1m", description="Aggregation window (e.g., '1m', '15m', '1h')")
):
    """
    Get warehouse inventory trend over time
    
    Args:
        run_id: Simulation run ID
        warehouse_id: Warehouse identifier
        window: Aggregation window
        
    Returns:
        Time series of inventory levels
    """
    try:
        db = get_db()
        data = db.get_time_series(
            run_id=run_id,
            measurement="warehouse_state",
            field="current_inventory_kg",  # Match warehouse get_state()
            entity_id_field="warehouse_id",
            entity_id=warehouse_id,
            window=window
        )
        
        # Format for frontend - include simulation timestamp
        result = []
        for point in data:
            result.append({
                "time": point.get('_time'),
                "timestamp": point.get('timestamp', 0),  # Simulation time in minutes
                "value": point.get('current_inventory_kg', 0)  # Use actual field name after pivot
            })
        
        return result
    except (ValueError, TypeError) as e:
        # Data processing errors
        raise HTTPException(status_code=500, detail=f"Data processing error: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Missing fields or structure errors
        raise HTTPException(status_code=404, detail=f"No inventory data found for warehouse {warehouse_id}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/timeseries/warehouse/fleet", response_model=List[dict])
async def get_warehouse_fleet_utilization(
    run_id: str = Query(..., description="Simulation run ID"),
    warehouse_id: str = Query(..., description="Warehouse ID"),
    window: str = Query("15m", description="Aggregation window")
):
    """
    Get warehouse fleet utilization over time
    
    Args:
        run_id: Simulation run ID
        warehouse_id: Warehouse identifier
        window: Aggregation window
        
    Returns:
        Time series of fleet availability
    """
    try:
        db = get_db()
        
        # Get available trucks time series
        available_data = db.get_time_series(
            run_id=run_id,
            measurement="warehouse_state",
            field="available_trucks",
            entity_id_field="warehouse_id",
            entity_id=warehouse_id,
            window=window
        )
        
        # Get in-transit trucks time series
        in_transit_data = db.get_time_series(
            run_id=run_id,
            measurement="warehouse_state",
            field="trucks_in_transit",
            entity_id_field="warehouse_id",
            entity_id=warehouse_id,
            window=window
        )
        
        # Query timestamp field for each data point
        timestamp_data = db.get_time_series(
            run_id=run_id,
            measurement="warehouse_state",
            field="timestamp",
            entity_id_field="warehouse_id",
            entity_id=warehouse_id,
            window=window
        )
        
        # Combine into stacked format with timestamp
        result = []
        for avail in available_data:
            time = avail.get('_time')
            # Find matching in_transit point
            in_transit_val = 0
            for transit in in_transit_data:
                if transit.get('_time') == time:
                    in_transit_val = transit.get('trucks_in_transit', transit.get('_value', 0))
                    break
            
            # Find matching timestamp
            timestamp_val = 0
            for ts in timestamp_data:
                if ts.get('_time') == time:
                    timestamp_val = ts.get('timestamp', ts.get('_value', 0))
                    break
            
            result.append({
                "time": time,
                "timestamp": timestamp_val,
                "available": avail.get('available_trucks', avail.get('_value', 0)),
                "in_transit": in_transit_val
            })
        
        return result
    except (ValueError, TypeError) as e:
        # Data aggregation/processing errors
        raise HTTPException(status_code=500, detail=f"Fleet data processing error: {str(e)}")
    except (KeyError, IndexError, AttributeError) as e:
        # Missing data fields
        raise HTTPException(status_code=404, detail=f"Incomplete fleet data for warehouse {warehouse_id}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e)) 


@router.get("/timeseries/retailer/stock", response_model=List[dict])
async def get_retailer_stock_trend(
    run_id: str = Query(..., description="Simulation run ID"),
    retailer_id: str = Query(..., description="Retailer ID"),
    window: str = Query("15m", description="Aggregation window")
):
    """
    Get retailer stock level trend over time
    
    Args:
        run_id: Simulation run ID
        retailer_id: Retailer identifier
        window: Aggregation window
        
    Returns:
        Time series of stock levels
    """
    try:
        db = get_db()
        data = db.get_time_series(
            run_id=run_id,
            measurement="retailer_state",
            field="current_inventory_kg",  # Match retailer get_state()
            entity_id_field="retailer_id",
            entity_id=retailer_id,
            window=window
        )
        
        # Format for frontend - include simulation timestamp
        result = []
        for point in data:
            result.append({
                "time": point.get('_time'),
                "timestamp": point.get('timestamp', 0),
                "value": point.get('current_inventory_kg', 0)
            })
        
        return result
    except (ValueError, TypeError) as e:
        # Data processing errors
        raise HTTPException(status_code=500, detail=f"Retailer stock data processing error: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Missing fields
        raise HTTPException(status_code=404, detail=f"No stock data found for retailer {retailer_id}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/timeseries/weather", response_model=List[dict])
async def get_weather_transitions(
    run_id: str = Query(..., description="Simulation run ID")
):
    """
    Get weather state over time (snapshot data logged every timestep)
    
    Args:
        run_id: Simulation run ID
        
    Returns:
        Time series of weather states with temperature and humidity
    """
    try:
        db = get_db()
        
        # Query weather snapshots (logged every timestep in _log_snapshots)
        flux_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "weather_state")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["_field"] == "temperature_celsius" or r["_field"] == "humidity_percent" or r["_field"] == "timestamp")
            |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
            |> sort(columns: ["_time"])
        '''
        data = db.query(flux_query)
        
        # Format for frontend
        result = []
        for point in data:
            result.append({
                "time": point.get('_time'),
                "state": point.get('state', 'clear'),  # state is a tag
                "temperature": point.get('temperature_celsius', 25.0),
                "humidity": point.get('humidity_percent', 50.0),
                "timestamp": point.get('timestamp', 0)
            })
        
        return result
    except (ValueError, TypeError) as e:
        # Data processing errors
        raise HTTPException(status_code=500, detail=f"Weather data processing error: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Missing fields
        raise HTTPException(status_code=404, detail=f"No weather data found for run_id {run_id}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/timeseries/truck/fuel", response_model=List[dict])
async def get_truck_fuel_trend(
    run_id: str = Query(..., description="Simulation run ID"),
    truck_id: str = Query(..., description="Truck ID"),
    window: str = Query("5m", description="Aggregation window")
):
    """
    Get truck fuel level over time
    
    Args:
        run_id: Simulation run ID
        truck_id: Truck identifier
        window: Aggregation window
        
    Returns:
        Time series of fuel percentage
    """
    try:
        db = get_db()
        data = db.get_time_series(
            run_id=run_id,
            measurement="truck_telemetry",  # FIXED: Use telemetry for real-time metrics
            field="fuel_percent",  # FIXED: Match actual field name from Truck.get_state()
            entity_id_field="truck_id",
            entity_id=truck_id,
            window=window
        )
        
        # Format for frontend - include simulation timestamp
        result = []
        for point in data:
            result.append({
                "time": point.get('_time'),
                "timestamp": point.get('timestamp', 0),
                "value": point.get('fuel_percent', 0)  # FIXED: Use correct field name
            })
        
        return result
    except (ValueError, TypeError) as e:
        # Data processing errors
        raise HTTPException(status_code=500, detail=f"Fuel data processing error: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Missing fields
        raise HTTPException(status_code=404, detail=f"No fuel data found for truck {truck_id}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/timeseries/truck/fatigue", response_model=List[dict])
async def get_truck_fatigue_trend(
    run_id: str = Query(..., description="Simulation run ID"),
    truck_id: str = Query(..., description="Truck ID"),
    window: str = Query("5m", description="Aggregation window")
):
    """
    Get truck driver fatigue over time
    
    Args:
        run_id: Simulation run ID
        truck_id: Truck identifier
        window: Aggregation window
        
    Returns:
        Time series of driver fatigue hours
    """
    try:
        db = get_db()
        data = db.get_time_series(
            run_id=run_id,
            measurement="truck_telemetry",
            field="driver_fatigue_hours",  # Now matches telemetry field
            entity_id_field="truck_id",
            entity_id=truck_id,
            window=window
        )
        
        # Format for frontend - include simulation timestamp
        result = []
        for point in data:
            result.append({
                "time": point.get('_time'),
                "timestamp": point.get('timestamp', 0),
                "value": point.get('driver_fatigue_hours', 0)
            })
        
        return result
    except (ValueError, TypeError) as e:
        # Data processing errors
        raise HTTPException(status_code=500, detail=f"Fatigue data processing error: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Missing fields
        raise HTTPException(status_code=404, detail=f"No fatigue data found for truck {truck_id}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))
