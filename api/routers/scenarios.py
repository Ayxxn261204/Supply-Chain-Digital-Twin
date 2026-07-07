"""
Scenario Control API Router

Allows the dashboard to inject real-time scenarios into the running simulation.
Commands are written to a shared JSON file that the simulation engine polls
each tick and applies immediately.

Supported scenarios:
  - inject_accident:  Block a road segment for N minutes
  - adjust_demand:    Multiply a retailer's demand rate by a factor
  - trigger_stockout: Set a warehouse's inventory to near-zero
  - clear_commands:   Remove all pending/applied commands (reset)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()

# Shared command file — same machine as simulation, so file I/O is fast and safe
_CMD_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "scenario_commands.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_commands() -> Dict[str, Any]:
    """Read the command file, returning an empty structure if missing or corrupt."""
    try:
        if _CMD_FILE.exists():
            with open(_CMD_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[Scenarios] Could not read command file: {e}")
    return {"commands": []}


def _write_commands(data: Dict[str, Any]) -> None:
    """Atomically write the command file."""
    _CMD_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CMD_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(_CMD_FILE)


def _append_command(cmd: Dict[str, Any]) -> None:
    """Append a new command to the queue."""
    data = _read_commands()
    # Keep only the last 50 commands to prevent unbounded growth
    data["commands"] = data["commands"][-49:] + [cmd]
    _write_commands(data)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class InjectAccidentRequest(BaseModel):
    segment_id:       str   = Field(..., description="OSM segment ID to block")
    severity:         str   = Field("moderate", description="minor | moderate | severe")
    duration_minutes: int   = Field(30, ge=5, le=180, description="How long the accident lasts")


class AdjustDemandRequest(BaseModel):
    retailer_id: str   = Field(..., description="Retailer ID (e.g. RET001)")
    multiplier:  float = Field(..., ge=0.1, le=5.0, description="Demand rate multiplier (1.0 = no change)")


class TriggerStockoutRequest(BaseModel):
    warehouse_id: str = Field(..., description="Warehouse ID (e.g. WH001)")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/scenarios/inject-accident", summary="Inject a road accident")
async def inject_accident(body: InjectAccidentRequest) -> Dict[str, Any]:
    """
    Block a road segment to simulate an accident.

    The simulation engine will apply this on its next tick and clear it
    automatically after `duration_minutes` of simulated time.
    """
    if body.severity not in ("minor", "moderate", "severe"):
        raise HTTPException(status_code=422, detail="severity must be minor, moderate, or severe")

    cmd = {
        "type":             "inject_accident",
        "segment_id":       body.segment_id,
        "severity":         body.severity,
        "duration_minutes": body.duration_minutes,
        "applied":          False,
    }
    _append_command(cmd)
    logger.info(f"[Scenarios] Queued inject_accident on {body.segment_id} ({body.severity}, {body.duration_minutes}min)")
    return {"status": "queued", "command": cmd}


@router.post("/scenarios/adjust-demand", summary="Adjust retailer demand rate")
async def adjust_demand(body: AdjustDemandRequest) -> Dict[str, Any]:
    """
    Multiply a retailer's base demand arrival rate.

    multiplier=2.0 doubles demand; multiplier=0.5 halves it.
    The change persists for the remainder of the simulation run.
    """
    cmd = {
        "type":         "adjust_demand",
        "retailer_id":  body.retailer_id,
        "multiplier":   body.multiplier,
        "applied":      False,
    }
    _append_command(cmd)
    logger.info(f"[Scenarios] Queued adjust_demand for {body.retailer_id} ×{body.multiplier}")
    return {"status": "queued", "command": cmd}


@router.post("/scenarios/trigger-stockout", summary="Trigger a warehouse stockout")
async def trigger_stockout(body: TriggerStockoutRequest) -> Dict[str, Any]:
    """
    Set a warehouse's inventory to zero, forcing emergency reorders.

    Useful for testing the system's resilience and cross-agent coordination.
    """
    cmd = {
        "type":         "trigger_stockout",
        "warehouse_id": body.warehouse_id,
        "applied":      False,
    }
    _append_command(cmd)
    logger.info(f"[Scenarios] Queued trigger_stockout for {body.warehouse_id}")
    return {"status": "queued", "command": cmd}


@router.delete("/scenarios/clear", summary="Clear all scenario commands")
async def clear_commands() -> Dict[str, Any]:
    """Remove all pending and applied scenario commands."""
    _write_commands({"commands": []})
    logger.info("[Scenarios] All commands cleared")
    return {"status": "cleared"}


@router.get("/scenarios/status", summary="List all scenario commands")
async def get_status() -> Dict[str, Any]:
    """Return the current command queue with applied/pending status."""
    data = _read_commands()
    commands = data.get("commands", [])
    pending  = [c for c in commands if not c.get("applied")]
    applied  = [c for c in commands if c.get("applied")]
    return {
        "total":   len(commands),
        "pending": len(pending),
        "applied": len(applied),
        "commands": commands[-20:],  # Return last 20 for display
    }
