"""
Centralized logging setup for supply chain simulation.

Provides consistent logging across all modules with:
- Timestamped log files (unique per run)
- Proper log levels (DEBUG/INFO/WARNING/ERROR/CRITICAL)
- Structured format
- Both file and console output
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def setup_logging(run_id: str, log_dir: str = "logs", log_level: int = logging.INFO) -> Path:
    """
    Setup centralized logging for the simulation.
    
    Creates a timestamped log file and configures all loggers.
    
    Args:
        run_id: Unique simulation run identifier
        log_dir: Directory for log files (default: 'logs/')
        log_level: Minimum log level (default: INFO)
    
    Returns:
        Path to the created log file
    
    Example:
        >>> log_file = setup_logging("sim-20251219-004421")
        >>> logging.info("Simulation started")
    """
    # Ensure log directory exists
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)
    
    # Create timestamped log filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_path / f"sim_{timestamp}_{run_id}.log"
    
    # Remove any existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Set root logger level
    root_logger.setLevel(logging.DEBUG)
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)-8s - [%(name)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)-8s - %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # File handler (DEBUG level - everything)
    file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='w')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    root_logger.addHandler(file_handler)
    
    # Console handler (specified level - user-facing)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # Log initialization
    logging.info("=" * 80)
    logging.info("LOGGING INITIALIZED")
    logging.info("=" * 80)
    logging.info(f"Log file: {log_file}")
    logging.info(f"Run ID: {run_id}")
    logging.info(f"File log level: DEBUG")
    logging.info(f"Console log level: {logging.getLevelName(log_level)}")
    logging.info("=" * 80)
    
    return log_file


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific module.
    
    Args:
        name: Logger name (usually __name__)
    
    Returns:
        Logger instance
    
    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Module initialized")
    """
    return logging.getLogger(name)


# Convenience functions for common log patterns
def log_event(category: str, message: str, level: int = logging.INFO, **kwargs):
    """
    Log a structured event with category tag.
    
    Args:
        category: Event category (e.g., 'REFUEL', 'ORDER', 'DELIVERY')
        message: Event message
        level: Log level
        **kwargs: Additional key-value pairs to log
    
    Example:
        >>> log_event('REFUEL', 'TRK001 refueling', fuel=150, duration=4.2)
    """
    logger = logging.getLogger('SimulationEvent')
    
    # Format message with kwargs
    if kwargs:
        details = ', '.join(f"{k}={v}" for k, v in kwargs.items())
        full_message = f"[{category}] {message} ({details})"
    else:
        full_message = f"[{category}] {message}"
    
    logger.log(level, full_message)


# Example usage patterns
if __name__ == "__main__":
    # Initialize logging
    log_file = setup_logging("test-run")
    
    # Get module-specific logger
    logger = get_logger(__name__)
    
    # Log different levels
    logger.debug("Debug message - detailed diagnostic")
    logger.info("Info message - normal operation")
    logger.warning("Warning message - unexpected but handled")
    logger.error("Error message - something went wrong")
    logger.critical("Critical message - severe problem")
    
    # Log structured events
    log_event('ORDER', 'Created order ORD001', retailer='RET001', quantity_kg=500)
    log_event('REFUEL', 'TRK001 refueling', fuel_liters=150, duration_min=4.2)
    log_event('QUALITY', 'Batch rejected', level=logging.ERROR, batch_id='B001', rsl_hours=45)
