"""
Utility functions for API request validation
"""
import re
from fastapi import HTTPException

# Compiled regex for run_id validation — sim-YYYYMMDD-HHMMSS
_RUN_ID_PATTERN = re.compile(r'^sim-\d{8}-\d{6}$')


def validate_run_id(run_id: str) -> str:
    """
    Validate simulation run_id format.

    Expected format: sim-YYYYMMDD-HHMMSS
    Example: sim-20251217-143025

    Args:
        run_id: Run ID string to validate

    Returns:
        The validated run_id

    Raises:
        HTTPException 400: If run_id is missing or format is invalid
    """
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id is required")

    if not _RUN_ID_PATTERN.match(run_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid run_id format. Expected 'sim-YYYYMMDD-HHMMSS', got '{run_id}'"
        )

    return run_id
