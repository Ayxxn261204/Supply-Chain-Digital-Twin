"""
Main entry point for Digital Twin Supply Chain Simulation.

Usage:
    python main.py [--config CONFIG] [--duration-days DAYS] [--time-step MINUTES] [--seed SEED]

Examples:
    # Run 7-day simulation with default settings
    python main.py
    
    # Run 30-day simulation with 5-minute time steps
    python main.py --duration-days 30 --time-step 5
    
    # Run with specific random seed for reproducibility
    python main.py --seed 42
"""

from src.simulation.engine import SimulationEngine, parse_arguments
from src.data.config_loader import load_config
from src.simulation.agents.warehouse_agent import WarehouseAgent
from src.simulation.agents.retailer_agent import RetailerAgent
from src.simulation.entities import TruckType
from typing import Dict
import random
import logging

# Configuration loaded from simulation_config.yaml


from src.simulation.generator import populate_engine


def main():
    """Main entry point for the simulation."""
    # Parse command-line arguments
    args = parse_arguments()
    
    # Initialize logging FIRST (before any other operations)
    from src.utils.logger import setup_logging
    import logging
    from datetime import datetime
    
    # Create unique run ID
    run_id = f"sim-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    
    # Load config first to get log level
    config_path = args.config if args.config else 'config/simulation_config.yaml'
    try:
        from src.data.config_loader import load_config
        config = load_config(config_path)
        log_level_str = config.get('logging', {}).get('log_level', 'INFO')
        log_level = getattr(logging, log_level_str, logging.INFO)
    except Exception:
        log_level = logging.INFO  # Fallback
        config = None
    
    # Setup logging with config-driven level
    log_file = setup_logging(run_id, log_dir="logs", log_level=log_level)

    logger = logging.getLogger(__name__)
    
    logger.info("Digital Twin Supply Chain Simulation")
    logger.info("=" * 80)
    
    logger.info("Loading configuration...")
    config_path = args.config if args.config else 'config/simulation_config.yaml'
    
    try:
        config = load_config(config_path)
        logger.info(f"Configuration loaded from: {config_path}")
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_path}")
        print(f"[ERROR] Configuration file not found: {config_path}")
        return 1
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        print(f"[ERROR] Error loading configuration: {e}")
        return 1
    
    # Create simulation engine
    try:
        engine = SimulationEngine(
            config=config,
            duration_days=args.duration_days,
            time_step_minutes=args.time_step,
            random_seed=args.seed,
            speed_multiplier=args.speed,
            start_date=args.start_date,
            steps=args.steps,
            headless=args.headless
        )
    except Exception as e:
        print(f"[ERROR] Error initializing simulation: {e}")
        return 1
    
    # Initialize components
    try:
        print("\n" + "=" * 60)
        print("Initializing Simulation Components")
        print("=" * 60 + "\n")
        
        # Initialize road network (Phase 2)
        print("[ROAD] Initializing road network...")
        engine.initialize_road_network(use_cache=True)
        
        # Initialize weather and disruptions (Phase 4)
        print("[WEATHER] Initializing weather and disruptions...")
        engine.initialize_weather_and_disruptions()
        
        # Initialize agents using centralized generator
        print("[AGENTS] Initializing agents (Full Scale Digital Twin)...")
        populate_engine(engine, config)
        
        # Schedule warehouse restocking events (already handled in populate_engine technically, but let's be explicit if needed)
        # In generator.py, I added wh.restock_schedule, so we just need engine call
        if engine.warehouses:
            engine.schedule_warehouse_restocking()
        
        print("\n[OK] All components initialized")
        
    except Exception as e:
        print(f"[ERROR] Error initializing components: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # Run simulation
    try:
        engine.run()
        print("\n[OK] Simulation completed successfully")
        return 0
    except KeyboardInterrupt:
        print("\n[WARNING]  Simulation interrupted by user")
        return 130
    except Exception as e:
        print(f"\n[ERROR] Simulation error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit(main())
