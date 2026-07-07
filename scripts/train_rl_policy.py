"""
Offline RL training script using RoutingEnv.

RoutingEnv loads the OSM road network once and resets in ~5ms per episode,
making 50k-step training feasible on a laptop in a few minutes.

The trained PPO weights are saved to models/rl/best_model.zip and
loaded automatically by OptimizationPod when the full simulation runs with
ai.optimization.enabled: true.

Usage
-----
    # From FYP/ directory:
    python scripts/train_rl_policy.py
    python scripts/train_rl_policy.py --steps 50000
    python scripts/train_rl_policy.py --steps 100000 --max-episode-steps 300

Requirements
------------
    pip install stable-baselines3
"""

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.config_loader import load_config
from src.simulation.network.road_network import RoadNetwork
from src.simulation.network.traffic_model import TrafficModel
from src.simulation.network.router import Router
from src.simulation.routing_env import RoutingEnv
from src.simulation.optimization_pod import OptimizationPod, _sb3_available

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("train_rl")


def parse_args():
    p = argparse.ArgumentParser(description="Train PPO routing policy with RoutingEnv")
    p.add_argument("--config", default="config/simulation_config.yaml")
    p.add_argument("--steps", type=int, default=50000,
                   help="Total environment steps to train for (default: 50000)")
    p.add_argument("--max-episode-steps", type=int, default=10,
                   help="Max steps per episode before timeout (default: 10)")
    return p.parse_args()


def main():
    args = parse_args()

    if not _sb3_available():
        logger.error("stable-baselines3 not installed. Run: pip install stable-baselines3")
        sys.exit(1)

    logger.info(f"Loading config from {args.config}")
    config = load_config(args.config)

    # Force optimization pod on for training
    config.setdefault("ai", {}).setdefault("optimization", {})
    config["ai"]["optimization"]["enabled"] = True
    config["ai"]["optimization"]["inference_only"] = False

    # --- Load road network ONCE (shared across all episodes) ---
    logger.info("Loading road network (cached)...")
    t0 = time.time()
    road_network = RoadNetwork(config)
    road_network.load(use_cache=True)
    logger.info(f"Road network loaded in {time.time()-t0:.1f}s "
                f"({len(list(road_network.graph.nodes())):,} nodes)")

    # --- Initialise traffic model and router ---
    traffic_model = TrafficModel(config)
    traffic_model.initialize_network(road_network, 0.0)
    router = Router(road_network, config)

    # --- Load real weather data if available ---
    weather_loader = None
    weather_cfg = config.get("weather", {})
    if weather_cfg.get("use_real_data", False):
        dataset_path = weather_cfg.get("dataset_path", "data/processed/weather/nagpur_2023.parquet")
        if Path(dataset_path).exists():
            try:
                from src.data.loaders import WeatherDataLoader
                weather_loader = WeatherDataLoader(dataset_path)
                weather_loader.load()
                logger.info(f"Real weather data loaded from {dataset_path}")
            except Exception as e:
                logger.warning(f"Could not load weather data: {e}. Using fallback.")

    # --- Delete stale route pool cache ---
    pool_cache = Path(config["ai"]["optimization"].get("checkpoint_dir", "models/rl")) / f"route_pool_{config['ai']['optimization'].get('route_pool_size', 50)}.pkl"
    if pool_cache.exists():
        pool_cache.unlink()
        logger.info(f"Deleted stale route pool cache: {pool_cache}")

    # --- Build RoutingEnv ---
    env = RoutingEnv(
        config=config,
        road_network=road_network,
        traffic_model=traffic_model,
        router=router,
        weather_loader=weather_loader,
        max_steps=args.max_episode_steps,
    )

    # Smoke-test the environment
    logger.info("Smoke-testing environment...")
    obs, _ = env.reset()
    assert obs.shape == (25,), f"Unexpected obs shape: {obs.shape}"
    obs, reward, term, trunc, info = env.step(0)
    assert obs.shape == (25,)
    logger.info(f"Env OK — obs shape={obs.shape}, reward={reward:.3f}")
    # --- Build OptimizationPod (initialises PPO) ---
    pod = OptimizationPod(config)
    if pod._model is None:
        logger.error("PPO model failed to initialise. Check stable-baselines3 installation.")
        sys.exit(1)

    # --- Train ---
    logger.info(f"Starting PPO training for {args.steps:,} steps...")
    t0 = time.time()
    pod.train(env, n_steps=args.steps)
    elapsed = time.time() - t0
    logger.info(f"Training complete in {elapsed:.1f}s ({args.steps/elapsed:.0f} steps/sec)")
    logger.info("Checkpoint saved to models/rl/best_model.zip")
    logger.info("Set ai.optimization.enabled: true in config to use the policy in simulation.")


if __name__ == "__main__":
    main()
