"""
OptimizationPod - RL-based routing optimization for the Digital Twin.

Uses Stable-Baselines3 PPO with the existing MARLEnv Gym interface.
Designed to be laptop-friendly: small MLP policy, CPU-only, configurable
training budget.

Algorithm choice: PPO over DQN because
  - On-policy: no replay buffer memory overhead
  - Stable convergence on discrete action spaces
  - Handles the variable number of active trucks gracefully via
    independent per-truck policies sharing one network (parameter sharing)
  - SB3's PPO is battle-tested and requires zero custom training loop code

Config keys (under ai.optimization in simulation_config.yaml):
    enabled: true
    algorithm: "ppo"            # "ppo" | "none"
    policy_net: [64, 64]        # MLP hidden layer sizes
    learning_rate: 3e-4
    n_steps: 512                # Steps per PPO update (keep low for laptop)
    batch_size: 64
    n_epochs: 4
    gamma: 0.99
    ent_coef: 0.01              # Entropy bonus (encourages exploration)
    checkpoint_dir: "models/rl" # Where to save/load policy weights
    checkpoint_interval: 10000  # Save every N training steps
    inference_only: false       # If true, load weights and skip training
    epsilon_greedy: 0.1         # Random action probability during inference
                                # (keeps some exploration during live sim)

Integration
-----------
The OptimizationPod wraps MARLEnv in a single-agent SB3-compatible
environment adapter.  During live simulation, it is called in
inference mode only (no gradient updates) ? training happens in a
separate offline loop via `pod.train(n_steps)`.

The existing DQNAgent in business_models.py is kept as a fallback
when SB3 is not installed.
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

OBS_DIM = 25  # Must match RoutingEnv.OBS_DIM and TruckAgent._build_obs
N_ACTIONS = 3  # 0=balanced, 1=speed, 2=fuel


# --- Cached Spaces for Identity Persistence ---
_CACHED_OBS_SPACE = None
_CACHED_ACT_SPACE = None

def get_obs_space():
    global _CACHED_OBS_SPACE
    if _CACHED_OBS_SPACE is None:
        try:
            import gymnasium.spaces as spaces
            _CACHED_OBS_SPACE = spaces.Box(
                low=-1.0, high=1.0, shape=(OBS_DIM,), dtype=np.float32
            )
        except ImportError:
            _CACHED_OBS_SPACE = _Box(shape=(OBS_DIM,))
    return _CACHED_OBS_SPACE

def get_act_space():
    global _CACHED_ACT_SPACE
    if _CACHED_ACT_SPACE is None:
        try:
            import gymnasium.spaces as spaces
            _CACHED_ACT_SPACE = spaces.Discrete(N_ACTIONS)
        except ImportError:
            _CACHED_ACT_SPACE = _Discrete(n=N_ACTIONS)
    return _CACHED_ACT_SPACE

# ---------------------------------------------------------------------------
# SB3 availability check
# ---------------------------------------------------------------------------

def _sb3_available() -> bool:
    try:
        import stable_baselines3  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Single-agent Gym adapter for SB3
# ---------------------------------------------------------------------------

try:
    import gymnasium as gym
    _BaseEnv = gym.Env
except ImportError:
    _BaseEnv = object

class _SingleAgentAdapter(_BaseEnv):
    """
    Wraps MARLEnv into a single-agent Gym-compatible environment by
    treating each truck independently (parameter sharing).

    SB3 expects:
        reset() -> np.ndarray
        step(action) -> (obs, reward, done, info)
        observation_space.shape
        action_space.n
    """

    def __init__(self, marl_env):
        self._env = marl_env
        self._truck_ids: List[str] = []
        self._current_idx: int = 0
        self.render_mode = None

        # Gym spaces (use cached singletons for identity match)
        self.observation_space = get_obs_space()
        self.action_space = get_act_space()

    def reset(self, seed=None, options=None):
        if seed is not None:
            super().reset(seed=seed)
        obs_dict = self._env.reset()
        self._truck_ids = list(obs_dict.keys())
        self._current_idx = 0
        if not self._truck_ids:
            return np.zeros(OBS_DIM, dtype=np.float32), {}
        return obs_dict[self._truck_ids[0]], {}

    def step(self, action: int):
        if not self._truck_ids:
            return np.zeros(OBS_DIM, dtype=np.float32), 0.0, True, False, {}

        truck_id = self._truck_ids[self._current_idx % len(self._truck_ids)]
        obs_dict, rew_dict, done_dict, info = self._env.step({truck_id: action})

        obs = obs_dict.get(truck_id, np.zeros(OBS_DIM, dtype=np.float32))
        reward = rew_dict.get(truck_id, 0.0)
        done = done_dict.get("__all__", False)
        truncated = False

        self._current_idx += 1
        self._truck_ids = list(obs_dict.keys())

        return obs, reward, done, truncated, info


class _Box:
    """Minimal observation space duck-type."""
    def __init__(self, shape):
        self.shape = shape
        self.dtype = np.float32


class _Discrete:
    """Minimal action space duck-type."""
    def __init__(self, n):
        self.n = n


# ---------------------------------------------------------------------------
# OptimizationPod
# ---------------------------------------------------------------------------

class OptimizationPod:
    """
    RL routing policy manager.

    Lifecycle
    ---------
    1. Instantiated by AIManager at startup.
    2. During simulation: `select_action(truck_id, obs)` is called by
       TruckAgent._check_rerouting() when rl_enabled=true.
    3. Offline training: `train(marl_env, n_steps)` runs PPO updates.
    4. Checkpoints are saved/loaded automatically.

    Fallback
    --------
    If SB3 is not installed, falls back to the existing DQNAgent from
    business_models.py (epsilon-greedy, no training loop needed).
    """

    def __init__(self, config: Dict):
        self.config = config
        opt_cfg = config.get("ai", {}).get("optimization", {})

        self.enabled: bool = opt_cfg.get("enabled", False)
        self.algorithm: str = opt_cfg.get("algorithm", "ppo").lower()
        self.inference_only: bool = opt_cfg.get("inference_only", False)
        self.epsilon: float = opt_cfg.get("epsilon_greedy", 0.1)
        self._epsilon_start: float = opt_cfg.get("epsilon_greedy", 0.1)
        self._epsilon_end: float = opt_cfg.get("epsilon_min", 0.02)
        
        # Enforce highly exploitative policy during evaluation/benchmarking
        if self.inference_only:
            self.epsilon = 0.01

        self.checkpoint_dir = Path(opt_cfg.get("checkpoint_dir", "models/rl"))
        self.checkpoint_interval: int = opt_cfg.get("checkpoint_interval", 10000)

        self._policy_net_arch: List[int] = opt_cfg.get("policy_net", [64, 64])
        self._lr: float = opt_cfg.get("learning_rate", 3e-4)
        self._n_steps: int = opt_cfg.get("n_steps", 512)
        self._batch_size: int = opt_cfg.get("batch_size", 64)
        self._n_epochs: int = opt_cfg.get("n_epochs", 4)
        self._gamma: float = opt_cfg.get("gamma", 0.99)
        self._ent_coef: float = opt_cfg.get("ent_coef", 0.01)

        self._model = None          # SB3 PPO model
        self._dqn_fallback = None   # DQNAgent fallback
        self._total_steps = 0

        if not self.enabled:
            logger.info("[OptimizationPod] Disabled via config (ai.optimization.enabled=false)")
            return

        if _sb3_available() and self.algorithm == "ppo":
            self._init_ppo()
        else:
            self._init_dqn_fallback()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_action(self, truck_id: str, obs: np.ndarray) -> int:
        """
        Select a routing action for a truck.

        Parameters
        ----------
        truck_id : used for logging only
        obs      : observation vector (OBS_DIM floats)

        Returns
        -------
        int: strategy index 0=balanced, 1=speed, 2=fuel
        """
        if not self.enabled:
            return 0

        # Epsilon-greedy exploration
        if random.random() < self.epsilon:
            return random.randint(0, 2)

        if self._model is not None:
            action, _ = self._model.predict(obs, deterministic=True)
            return int(action)

        if self._dqn_fallback is not None:
            return self._dqn_fallback.select_action(obs)

        return 0

    def train(self, marl_env, n_steps: int = 10000, callbacks=None, reset_timesteps: bool = True):
        """
        Run PPO training for `n_steps` environment steps.

        Accepts either a MARLEnv (legacy) or a RoutingEnv (fast).
        RoutingEnv is preferred — it resets in ~5ms vs ~50s for MARLEnv.

        Parameters
        ----------
        marl_env         : MARLEnv | RoutingEnv — environment to train on
        n_steps          : total environment steps to train for
        callbacks        : optional list of SB3 callbacks
        reset_timesteps  : if True (default), reset SB3's internal step counter
                           so ep_len_mean and ep_rew_mean start fresh each run
        """
        if not self.enabled or self._model is None:
            logger.warning("[OptimizationPod] train() called but PPO not available.")
            return

        if self.inference_only:
            logger.info("[OptimizationPod] inference_only=true, skipping training.")
            return

        # RoutingEnv is already Gym-compatible (Gymnasium API).
        # MARLEnv needs the legacy single-agent adapter.
        from .routing_env import RoutingEnv
        if isinstance(marl_env, RoutingEnv):
            env = marl_env
        else:
            env = _SingleAgentAdapter(marl_env)

        # (Re)Initialize model with the REAL environment to ensure space matching
        self._init_ppo(env=env)

        logger.info(f"[OptimizationPod] Starting PPO training ({n_steps} steps, reset_timesteps={reset_timesteps})...")

        all_callbacks = []
        chkpt_cb = self._make_checkpoint_callback()
        if chkpt_cb:
            all_callbacks.append(chkpt_cb)
        if callbacks:
            if isinstance(callbacks, list):
                all_callbacks.extend(callbacks)
            else:
                all_callbacks.append(callbacks)

        self._model.learn(
            total_timesteps=n_steps,
            reset_num_timesteps=reset_timesteps,
            callback=all_callbacks,
        )
        self._total_steps += n_steps
        self._save_checkpoint()
        # Decay epsilon linearly toward epsilon_min over training
        decay = (self._epsilon_start - self._epsilon_end) * (n_steps / max(n_steps, 100000))
        self.epsilon = max(self._epsilon_end, self.epsilon - decay)
        logger.info(f"[OptimizationPod] Training complete. Total steps: {self._total_steps}, epsilon: {self.epsilon:.3f}")

    def save(self, path: Optional[str] = None):
        """Save policy weights."""
        if self._model is None:
            return
        save_path = path or str(self.checkpoint_dir / "best_model")
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._model.save(save_path)
        logger.info(f"[OptimizationPod] Policy saved to {save_path}")

    def load(self, path: Optional[str] = None):
        """Load policy weights."""
        if not _sb3_available():
            return
        load_path = path or str(self.checkpoint_dir / "best_model")
        if not Path(load_path + ".zip").exists():
            logger.info(f"[OptimizationPod] No checkpoint found at {load_path}, starting fresh.")
            return
        try:
            from stable_baselines3 import PPO
            self._model = PPO.load(load_path, device="cpu")
            logger.info(f"[OptimizationPod] Policy loaded from {load_path}")
        except Exception as e:
            logger.warning(f"[OptimizationPod] Failed to load checkpoint: {e}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_ppo(self, env=None):
        """Initialize SB3 PPO with an environment."""
        try:
            from stable_baselines3 import PPO
            import gymnasium as gym
            import gymnasium.spaces as spaces

            if env is None:
                # Build a proper Gymnasium env for SB3 initialisation fallback
                class _GymDummyEnv(gym.Env):
                    def __init__(self):
                        super().__init__()
                        self.observation_space = get_obs_space()
                        self.action_space = get_act_space()

                    def reset(self, seed=None, options=None):
                        return np.zeros(OBS_DIM, dtype=np.float32), {}

                    def step(self, action):
                        return np.zeros(OBS_DIM, dtype=np.float32), 0.0, True, False, {}

                active_env = _GymDummyEnv()
            else:
                active_env = env

            # Try to load existing checkpoint first (if it exists)
            load_path = str(self.checkpoint_dir / "best_model")
            if Path(load_path + ".zip").exists():
                try:
                    self._model = PPO.load(load_path, env=active_env, device="cpu")
                    logger.info(f"[OptimizationPod] Policy loaded from {load_path}")
                except Exception as e:
                    logger.warning(f"[OptimizationPod] Failed to load checkpoint: {e}. Starting fresh.")
                    self._model = None
            
            if self._model is None:
                # Create fresh model
                self._model = PPO(
                    policy="MlpPolicy",
                    env=active_env,
                    learning_rate=self._lr,
                    n_steps=self._n_steps,
                    batch_size=self._batch_size,
                    n_epochs=self._n_epochs,
                    gamma=self._gamma,
                    ent_coef=self._ent_coef,
                    policy_kwargs={"net_arch": self._policy_net_arch},
                    device="cpu",
                    verbose=1,
                    tensorboard_log="logs/ppo_tensorboard"
                )

            logger.info(
                f"[OptimizationPod] PPO initialized "
                f"(arch={self._policy_net_arch}, lr={self._lr}, device=cpu)"
            )
        except Exception as e:
            logger.warning(f"[OptimizationPod] PPO init failed: {e}. Using DQN fallback.")
            self._init_dqn_fallback()

    def _init_dqn_fallback(self):
        """Fall back to the existing DQNAgent."""
        try:
            from .business_models import DQNAgent
            self._dqn_fallback = DQNAgent(state_dim=OBS_DIM, action_dim=6)
            logger.info("[OptimizationPod] Using DQNAgent fallback (SB3 not available).")
        except Exception as e:
            logger.warning(f"[OptimizationPod] DQN fallback also failed: {e}. No RL policy.")

    def _save_checkpoint(self):
        self.save()

    def _make_checkpoint_callback(self):
        """Create a SB3 callback that saves every checkpoint_interval steps."""
        try:
            from stable_baselines3.common.callbacks import CheckpointCallback
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            return CheckpointCallback(
                save_freq=self.checkpoint_interval,
                save_path=str(self.checkpoint_dir),
                name_prefix="ppo_routing",
                verbose=0,
            )
        except Exception:
            return None

