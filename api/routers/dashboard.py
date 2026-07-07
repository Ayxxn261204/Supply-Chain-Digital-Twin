"""
Dashboard API Router
Single comprehensive endpoint for all dashboard data
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, List, Any
from database import get_db

router = APIRouter()


@router.get("/simulations/{run_id}/complete-state")
async def get_complete_dashboard_state(run_id: str) -> Dict[str, Any]:
    """
    Get complete dashboard state in ONE API call.
    
    Returns all data needed for dashboard:
    - Simulation metadata  
    - All warehouse states (with coordinates, inventory, etc.)
    - All retailer states (with coordinates, inventory, etc.)
    - All truck positions (with telemetry)
    - Entity counts
    
    This replaces multiple separate API calls with a single atomic request.
    
    Args:
        run_id: Simulation run ID
        
    Returns:
        Complete dashboard state with all entity data
    """
    try:
        db = get_db()
        
        # FIXED: Validate run_id exists BEFORE querying state (fail fast)
        # This avoids 3 unnecessary queries for non-existent simulations
        # and ensures clean 404 responses instead of 500 errors
        sim_metadata = db.get_simulation_metadata(run_id)
        
        if not sim_metadata:
            # If metadata query fails, simulation may not have started logging yet
            # Return minimal valid response
            raise HTTPException(
                status_code=404,
                detail=f"Simulation metadata not found for {run_id}. Simulation may not have started yet."
            )
        
        # NOW fetch complete state (after validation)
        complete_state = db.get_complete_state(run_id)
        
        # Calculate entity counts
        warehouses = complete_state.get('warehouses', [])
        retailers = complete_state.get('retailers', [])
        trucks = complete_state.get('trucks', [])
        
        # Build comprehensive response
        return {
            # Simulation metadata (dynamically fetched from InfluxDB)
            "simulation": {
                "run_id": run_id,
                "status": "running",  # Could be enhanced to check if simulation is complete
                "current_time": warehouses[0].get('timestamp', 0) if warehouses else (retailers[0].get('timestamp', 0) if retailers else (trucks[0].get('timestamp', 0) if trucks else 0)),
                "speed": float(sim_metadata.get('speed', 1.0)),
                "time_step_minutes": int(sim_metadata.get('time_step_minutes', 1)),
                "start_date": sim_metadata.get('start_date'),
                "duration_days": float(sim_metadata.get('duration_days', 7))
            },
            
            # Entity counts
            "counts": {
                "warehouses": len(warehouses),
                "retailers": len(retailers),
                "trucks": len(trucks)
            },
            
            # Full warehouse data
            "warehouses": warehouses,
            
            # Full retailer data
            "retailers": retailers,
            
            # Full truck data
            "trucks": trucks
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions (like 404) without modification
        raise
    except (ValueError, TypeError, KeyError) as e:
        # Data processing or aggregation errors
        raise HTTPException(status_code=500, detail=f"Dashboard state processing error: {str(e)}")
    except (AttributeError, IndexError) as e:
        # Missing data structures
        raise HTTPException(status_code=404, detail=f"Incomplete dashboard data")
    except Exception as e:
        # Unexpected errors
        raise HTTPException(status_code=500, detail=f"Error fetching complete state: {str(e)}")
