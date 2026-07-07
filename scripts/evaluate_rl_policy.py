"""
Evaluate the trained PPO routing policy against a random baseline.

Runs N test episodes with the trained policy and N with random actions,
then reports:
  - Mean episode reward
  - Delivery rate (% of episodes where truck reached destination)
  - Mean RSL at delivery
  - Mean steps to delivery
  - Mean fuel remaining at delivery

Usage
-----
    python scripts/evaluate_rl_policy.py
    python scripts/evaluate_rl_policy.py --episodes 200
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.config_loader import load_config
from src.simulation.network.road_network import RoadNetwork
from src.simulation.network.traffic_model import TrafficModel
from src.simulation.routing_env import RoutingEnv

logging.basicConfig(level=logging.WARNING)  # Suppress info noise during eval
logger = logging.getLogger("eval_rl")
logger.setLevel(logging.INFO)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config/simulation_config.yaml")
    p.add_argument("--episodes", type=int, default=100)
    p.add_argument("--checkpoint", default="models/rl/best_model.zip")
    return p.parse_args()


def run_episodes(env, policy, n_episodes: int, label: str) -> dict:
    """Run n_episodes and collect metrics."""
    rewards, deliveries, rsls, steps, fuels = [], [], [], [], []

    for ep in range(n_episodes):
        obs, _ = env.reset()
        ep_reward = 0.0
        done = False

        while not done:
            if policy is not None:
                action, _ = policy.predict(obs, deterministic=True)
                action = int(action)
            else:
                action = env.action_space.sample()

            obs, reward, terminated, truncated, info = env.step(action)
            ep_reward += reward
            done = terminated or truncated

        rewards.append(ep_reward)
        deliveries.append(1 if info.get("arrived", False) else 0)
        rsls.append(info.get("rsl", 0.0))
        steps.append(env._step_count)
        fuels.append(info.get("fuel_pct", 0.0))

    return {
        "label": label,
        "mean_reward": np.mean(rewards),
        "std_reward": np.std(rewards),
        "delivery_rate": np.mean(deliveries) * 100,
        "mean_rsl_at_end": np.mean(rsls),
        "mean_steps": np.mean(steps),
        "mean_fuel_pct": np.mean(fuels),
    }


def print_results(r: dict):
    logger.info(f"\n{'='*50}")
    logger.info(f"  {r['label']}")
    logger.info(f"{'='*50}")
    logger.info(f"  Mean reward:       {r['mean_reward']:+.3f} ± {r['std_reward']:.3f}")
    logger.info(f"  Delivery rate:     {r['delivery_rate']:.1f}%")
    logger.info(f"  Mean RSL at end:   {r['mean_rsl_at_end']:.1f}%")
    logger.info(f"  Mean steps:        {r['mean_steps']:.1f}")
    logger.info(f"  Mean fuel remain:  {r['mean_fuel_pct']:.1f}%")


def main():
    args = parse_args()

    config = load_config(args.config)
    config.setdefault("ai", {}).setdefault("optimization", {})["enabled"] = True

    logger.info("Loading road network...")
    road_network = RoadNetwork(config)
    road_network.load(use_cache=True)

    traffic_model = TrafficModel(config)
    traffic_model.initialize_network(road_network, 0.0)

    from src.simulation.network.router import Router
    router = Router(road_network, config)

    weather_loader = None
    weather_cfg = config.get("weather", {})
    if weather_cfg.get("use_real_data", False):
        dataset_path = weather_cfg.get("dataset_path", "data/processed/weather/nagpur_2023.parquet")
        if Path(dataset_path).exists():
            from src.data.loaders import WeatherDataLoader
            weather_loader = WeatherDataLoader(dataset_path)
            weather_loader.load()

    env = RoutingEnv(
        config=config,
        road_network=road_network,
        traffic_model=traffic_model,
        router=router,
        weather_loader=weather_loader,
        max_steps=10,
    )

    # Load trained policy
    checkpoint = Path(args.checkpoint)
    if not checkpoint.exists():
        logger.error(f"Checkpoint not found: {checkpoint}")
        sys.exit(1)

    try:
        from stable_baselines3 import PPO
        policy = PPO.load(str(checkpoint), device="cpu")
        logger.info(f"Loaded policy from {checkpoint}")
    except Exception as e:
        logger.error(f"Failed to load policy: {e}")
        sys.exit(1)

    logger.info(f"Running {args.episodes} episodes each for trained policy and random baseline...")

    trained = run_episodes(env, policy, args.episodes, "TRAINED PPO POLICY")
    random_b = run_episodes(env, None,   args.episodes, "RANDOM BASELINE")

    print_results(trained)
    print_results(random_b)

    # Summary comparison
    logger.info(f"\n{'='*50}")
    logger.info("  IMPROVEMENT OVER RANDOM")
    logger.info(f"{'='*50}")
    reward_delta = trained["mean_reward"] - random_b["mean_reward"]
    delivery_delta = trained["delivery_rate"] - random_b["delivery_rate"]
    rsl_delta = trained["mean_rsl_at_end"] - random_b["mean_rsl_at_end"]
    logger.info(f"  Reward:        {reward_delta:+.3f}")
    logger.info(f"  Delivery rate: {delivery_delta:+.1f}pp")
    logger.info(f"  RSL at end:    {rsl_delta:+.1f}pp")

    if delivery_delta > 5:
        logger.info("\n  ✓ Policy is meaningfully better than random.")
    elif delivery_delta > 0:
        logger.info("\n  ~ Policy is marginally better than random. Consider more training steps.")
    else:
        logger.info("\n  ✗ Policy is not better than random. Needs more training.")


if __name__ == "__main__":
    main()
