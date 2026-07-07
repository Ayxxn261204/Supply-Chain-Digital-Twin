"""
Enhanced logging configuration for simulation runs.

Automatically names log files with timestamp and run ID to avoid confusion.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path


class SimulationLogger:
    """
    Manages simulation logging with unique file names.
    
    Format: logs/sim_{YYYYMMDD_HHMMSS}_{RUN_ID}.log
    Example: logs/sim_20251219_003500_sim-abc123.log
    """
    
    def __init__(self, run_id: str, log_dir: str = "logs"):
        """
        Initialize logger with timestamped filename.
        
        Args:
            run_id: Unique simulation run ID
            log_dir: Directory for log files (default: logs/)
        """
        self.run_id = run_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
        # Create timestamped log filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"sim_{timestamp}_{run_id}.log"
        
        # Configure logging
        self._setup_logging()
    
    def _setup_logging(self):
        """Configure logging to file and console."""
        # Create formatters
        file_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_formatter = logging.Formatter('%(message)s')
        
        # File handler
        file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(console_formatter)
        
        # Configure root logger
        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()  # Remove existing handlers
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        
        logging.info(f"Logging to: {self.log_file}")
    
    def get_log_path(self) -> Path:
        """Get the path to the current log file."""
        return self.log_file


# Usage example:
# logger = SimulationLogger(run_id="sim-abc123")
# logging.info("Simulation started")
