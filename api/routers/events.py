"""
Events API Router
Endpoints for simulation events
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from database import get_db
from schemas import Event

router = APIRouter()


@router.get("/events", response_model=List[dict])
async def get_events(
    run_id: str = Query(..., description="Simulation run ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    limit: int = Query(50, description="Maximum number of events", le=200)
):
    """
    Get recent events for simulation
    
    Args:
        run_id: Simulation run ID
        event_type: Optional event type filter
        limit: Maximum number of events to return
        
    Returns:
        List of recent events
    """
    try:
        db = get_db()
        events = db.get_events(run_id, limit=limit, event_type=event_type)
        
        # Format for frontend
        result = []
        for event in events:
            result.append({
                "event_id": event.get('_time'),  # Use timestamp as ID
                "event_type": event.get('event_type', 'unknown'),
                "timestamp": event.get('timestamp', 0),
                "time": event.get('_time'),
                "run_id": run_id,
                "description": event.get('description', ''),
                "metadata": {
                    k: v for k, v in event.items()
                    if k not in ['_time', '_measurement', 'run_id', 'event_type', 'timestamp', 'description']
                }
            })
        
        return result
    except (ValueError, TypeError) as e:
        # Data processing errors
        raise HTTPException(status_code=500, detail=f"Event data processing error: {str(e)}")
    except (KeyError, AttributeError) as e:
        # Missing fields
        raise HTTPException(status_code=404, detail=f"No events found for run_id {run_id}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/events/accidents", response_model=List[dict])
async def get_active_accidents(
    run_id: str = Query(..., description="Simulation run ID")
):
    """
    Get currently active accidents for map overlay
    
    Args:
        run_id: Simulation run ID
        
    Returns:
        List of active accidents
    """
    try:
        db = get_db()
        
        # Get all accident events
        flux_query = f'''
        from(bucket: "{db.bucket}")
            |> range(start: -7d)
            |> filter(fn: (r) => r["_measurement"] == "events")
            |> filter(fn: (r) => r["run_id"] == "{run_id}")
            |> filter(fn: (r) => r["event_type"] == "accident_start" or r["event_type"] == "accident_end")
            |> sort(columns: ["_time"])
        '''
        events = db.query(flux_query)
        
        # Track active accidents (started but not ended)
        active_accidents = {}
        
        for event in events:
            accident_id = event.get('accident_id')
            event_type = event.get('event_type')
            
            if event_type == 'accident_start':
                active_accidents[accident_id] = {
                    "accident_id": accident_id,
                    "segment_id": event.get('segment_id'),
                    "severity": event.get('severity', 'minor'),
                    "start_time": event.get('timestamp', 0),
                    "duration_minutes": event.get('duration_minutes', 0)
                }
            elif event_type == 'accident_end' and accident_id in active_accidents:
                # Remove ended accidents
                del active_accidents[accident_id]
        
        return list(active_accidents.values())
    except (ValueError, TypeError, KeyError) as e:
        # Data processing or field access errors
        raise HTTPException(status_code=500, detail=f"Accident data processing error: {str(e)}")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=str(e))
