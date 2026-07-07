"""
edge_brain.py — Lightweight Edge AI for per-truck emergency routing decisions.

Architecture Overview (Hierarchical RL)
-----------------------------------------
The system uses a two-level hierarchy:

  CLOUD (Central Brain) — OptimizationPod (PPO, [64,64] MLP)
      Role: Macro strategy selection for the whole fleet.
            Runs offline-trained; selects 0=balanced, 1=speed, 2=fuel
            based on the full 25-dim observation stream.

  EDGE  (Per-truck Brain) — EdgeBrain (tiny [16,16] MLP, this file)
      Role: Immediate local emergency override.
            Fires BEFORE the Central Brain when a truck detects:
              - Critically low RSL (cargo decaying fast)
              - Near-empty fuel tank
              - Active accident blocking the planned route
            When an emergency is detected, the Edge Brain selects the
            appropriate override action and skips Central Brain consultation
            for that tick.

Why this split?
  - Central Brain = expensive, globally-optimal, trained offline
  - Edge Brain    = cheap, locally-reactive, zero network latency
  - Together they mirror real IoT Edge-to-Cloud architectures used in
    industrial logistics (Siemens MindSphere, AWS Greengrass, etc.)

Edge Observation Vector (5-D)
------------------------------
  [0] rsl_norm        — current cargo RSL as fraction 0-1
  [1] fuel_norm       — fuel level as fraction 0-1
  [2] accident_ahead  — 1.0 if next segment is blocked, else 0.0
  [3] ripple_level    — upstream back-pressure (0-1 normalised)
  [4] eta_norm        — estimated remaining travel time (0-1, 120min cap)

Edge Action Space (3 actions — same as Central)
-------------------------------------------------
  0 = balanced   (default cruise)
  1 = speed      (emergency freshness sprint — high RSL crisis)
  2 = fuel       (emergency fuel conservation — low fuel)

Emergency Thresholds (configurable via config yaml)
-----------------------------------------------------
  rsl_critical   = 0.70  (below this → RSL emergency)
  fuel_critical  = 0.20  (below this → fuel emergency)
  accident_ahead = 1.0   (any blocked segment ahead → reroute)

When no emergency is detected, EdgeBrain.select_action() returns None,
signalling TruckAgent to fall through to the Central Brain.

Learning
--------
The Edge Brain supports lightweight online Q-learning with experience
replay so it can improve from the simulation's own outcomes without
requiring an offline training loop.  The replay buffer is tiny (512
transitions) to keep memory footprint minimal on laptops.

This is entirely optional — even with zero training steps the rule-based
initialisation produces sensible emergency responses.
"""

from __future__ import annotations

import logging
import random
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EDGE_OBS_DIM  = 5   # [rsl, fuel, accident_ahead, ripple, eta]
EDGE_N_ACTIONS = 3  # 0=balanced, 1=speed, 2=fuel

# Emergency thresholds
RSL_CRISIS_THRESHOLD   = 0.70   # < 70 % RSL → freshness emergency
FUEL_CRISIS_THRESHOLD  = 0.20   # < 20 % fuel → fuel emergency

# How many ticks to suppress repeated Edge overrides (avoids thrashing)
OVERRIDE_COOLDOWN_TICKS = 3


# ---------------------------------------------------------------------------
# Tiny MLP (pure NumPy — no PyTorch dependency)
# ---------------------------------------------------------------------------

class _TinyMLP:
    """
    3-layer ReLU MLP implemented in pure NumPy.

    Architecture: EDGE_OBS_DIM → hidden → hidden → EDGE_N_ACTIONS
    Default hidden size: 16 neurons per layer.

    Uses Xavier initialisation.  Weights are intentionally small so the
    Q-values start near zero and the rule-based override logic dominates
    until enough experience has been collected.
    """

    def __init__(self, hidden: int = 16, seed: int = 0):
        rng = np.random.default_rng(seed)

        def xavier(fan_in: int, fan_out: int) -> np.ndarray:
            limit = np.sqrt(6.0 / (fan_in + fan_out))
            return rng.uniform(-limit, limit, (fan_in, fan_out)).astype(np.float32)

        self.W1 = xavier(EDGE_OBS_DIM,  hidden)
        self.b1 = np.zeros(hidden, dtype=np.float32)
        self.W2 = xavier(hidden,         hidden)
        self.b2 = np.zeros(hidden, dtype=np.float32)
        self.W3 = xavier(hidden,         EDGE_N_ACTIONS)
        self.b3 = np.zeros(EDGE_N_ACTIONS, dtype=np.float32)

    def forward(self, x: np.ndarray) -> np.ndarray:
        """Return Q-values for each action given observation x."""
        h1 = np.maximum(0.0, x @ self.W1 + self.b1)   # ReLU
        h2 = np.maximum(0.0, h1 @ self.W2 + self.b2)  # ReLU
        return h2 @ self.W3 + self.b3                  # linear output

    def parameters(self) -> List[np.ndarray]:
        return [self.W1, self.b1, self.W2, self.b2, self.W3, self.b3]


# ---------------------------------------------------------------------------
# EdgeBrain
# ---------------------------------------------------------------------------

class EdgeBrain:
    """
    Lightweight per-truck Edge AI for immediate emergency routing.

    Designed to be instantiated once per TruckAgent and called every
    rerouting tick (every ~15 sim-minutes by default).

    Usage
    -----
        brain = EdgeBrain(truck_id, config)

        # In TruckAgent._check_rerouting():
        edge_action = brain.select_action(obs_5d, current_time)
        if edge_action is not None:
            apply(edge_action)          # override — skip Central Brain
        else:
            action = central.select()   # fall through to Cloud
            apply(action)

    Args:
        truck_id:   Identifier for logging.
        config:     Simulation config dict (reads ai.edge section).
        hidden:     Hidden layer width (default 16 — keeps params tiny).
    """

    def __init__(self, truck_id: str, config: Dict, hidden: int = 16):
        self.truck_id  = truck_id
        self._hidden   = hidden

        # Load edge-specific config (falls back to defaults if absent)
        edge_cfg = config.get("ai", {}).get("edge", {})
        self._rsl_thresh  = edge_cfg.get("rsl_critical_threshold",  RSL_CRISIS_THRESHOLD)
        self._fuel_thresh = edge_cfg.get("fuel_critical_threshold", FUEL_CRISIS_THRESHOLD)
        self._lr          = edge_cfg.get("learning_rate", 3e-3)
        self._gamma       = edge_cfg.get("gamma", 0.95)
        self._epsilon     = edge_cfg.get("epsilon", 0.05)   # very low — mostly exploit
        self._buf_size    = edge_cfg.get("replay_buffer_size", 512)
        self._batch_size  = edge_cfg.get("batch_size", 32)
        self._min_samples = edge_cfg.get("min_samples_to_train", 64)

        # MLP Q-network (tiny — ~600 parameters total)
        self._net = _TinyMLP(hidden=hidden)

        # Online learning state
        self._replay: deque = deque(maxlen=self._buf_size)
        self._step_count: int = 0

        # Override cooldown (prevents thrashing on same emergency)
        self._cooldown: int = 0          # ticks remaining on cooldown
        self._last_action: Optional[int] = None

        # Training statistics (for telemetry)
        self.n_overrides: int  = 0
        self.n_updates: int    = 0
        self.last_loss: float  = 0.0

        logger.debug(f"[EdgeBrain] {truck_id} initialised (hidden={hidden}, params≈{self._count_params()})")

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def build_observation(
        self,
        rsl_pct:        float,
        fuel_pct:       float,
        accident_ahead: bool,
        ripple_level:   float,
        eta_minutes:    float,
    ) -> np.ndarray:
        """
        Build the 5-dimensional Edge observation vector.

        All values are normalised to [0, 1]:
            rsl_norm  = rsl_pct / 100
            fuel_norm = fuel_pct / 100
            acc_ahead = 1.0 if accident_ahead else 0.0
            ripple    = clipped ripple / 100 (density units)
            eta_norm  = min(eta_minutes, 120) / 120
        """
        return np.array([
            max(0.0, min(1.0, rsl_pct  / 100.0)),
            max(0.0, min(1.0, fuel_pct / 100.0)),
            1.0 if accident_ahead else 0.0,
            max(0.0, min(1.0, ripple_level / 100.0)),
            max(0.0, min(1.0, eta_minutes  / 120.0)),
        ], dtype=np.float32)

    def select_action(
        self,
        obs: np.ndarray,
        current_time: float = 0.0,
    ) -> Optional[int]:
        """
        Select an emergency override action, or return None.

        Returns
        -------
        int   — override action (0/1/2) if an emergency is detected
        None  — no emergency; let the Central Brain decide

        Logic
        -----
        1. Check for rule-based emergencies (deterministic guards).
        2. If emergency and not on cooldown:
               → use Q-network (or epsilon-greedy) to pick action
               → arm cooldown
               → return action
        3. Else: return None
        """
        # Tick down cooldown
        if self._cooldown > 0:
            self._cooldown -= 1
            return None

        rsl_norm  = float(obs[0])
        fuel_norm = float(obs[1])
        acc_ahead  = float(obs[2]) > 0.5

        # --- Emergency detection ---
        rsl_emergency  = rsl_norm  < self._rsl_thresh
        fuel_emergency = fuel_norm < self._fuel_thresh
        acc_emergency  = acc_ahead

        emergency = rsl_emergency or fuel_emergency or acc_emergency

        if not emergency:
            return None

        # --- Action selection via Q-network (or epsilon-greedy) ---
        if random.random() < self._epsilon:
            action = random.randint(0, EDGE_N_ACTIONS - 1)
        else:
            # Rule-based prior FIRST, then let Q-network refine over time
            action = self._rule_prior(rsl_emergency, fuel_emergency, acc_emergency)

            # If Q-network has learned enough, let it override the rule prior
            if self._step_count >= self._min_samples:
                q_vals = self._net.forward(obs)
                # Blend: prefer Q-network if it strongly disagrees with rule
                q_action = int(np.argmax(q_vals))
                rule_q   = q_vals[action]
                best_q   = q_vals[q_action]
                if best_q - rule_q > 0.3:      # Q-net is significantly more confident
                    action = q_action

        self._cooldown    = OVERRIDE_COOLDOWN_TICKS
        self._last_action = action
        self.n_overrides += 1

        logger.debug(
            f"[EdgeBrain] {self.truck_id} OVERRIDE action={action} "
            f"(rsl={rsl_norm:.2f}, fuel={fuel_norm:.2f}, acc={acc_ahead})"
        )
        return action

    def record_outcome(
        self,
        obs: np.ndarray,
        action: int,
        reward: float,
        next_obs: np.ndarray,
        done: bool = False,
    ) -> None:
        """
        Store a transition and optionally run one gradient update.

        Call this after a delivery completes or after a reroute resolves.
        The reward signal should be:
            +RSL_delivered  (e.g. +0.8 if 80% RSL at delivery)
            -1.0 if cargo spoiled
            -0.5 if truck ran out of fuel
        """
        self._replay.append((obs, action, reward, next_obs, done))
        self._step_count += 1

        if len(self._replay) >= self._min_samples:
            self._update()

    def get_diagnostics(self) -> Dict:
        """Return flat dict of Edge Brain stats for telemetry."""
        return {
            "edge_n_overrides":   self.n_overrides,
            "edge_n_updates":     self.n_updates,
            "edge_last_loss":     round(self.last_loss, 4),
        }

    # ------------------------------------------------------------------ #
    # Internal                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _rule_prior(rsl: bool, fuel: bool, acc: bool) -> int:
        """
        Deterministic rule-based action prior.

        These rules encode domain knowledge:
          - RSL emergency  → speed strategy (minimise transit time)
          - Fuel emergency → fuel strategy (maximise range)
          - Accident ahead → speed strategy (fastest detour)
          - Both RSL+fuel  → speed (freshness takes priority)
        """
        if rsl:
            return 1   # speed — minimise transit time to save cargo
        if fuel:
            return 2   # fuel  — conserve fuel to avoid stranding
        if acc:
            return 1   # speed — fastest detour around the blockage
        return 0       # balanced (fallback, shouldn't reach here)

    def _update(self) -> None:
        """
        One mini-batch Q-learning update (pure NumPy SGD).

        Uses a simplified DQN update rule:
            target = r + γ * max_a Q(s', a)    (if not done)
            target = r                          (if done)
            loss   = MSE(Q(s, a) - target)
            ΔW     = -lr * ∂loss/∂W            (numerical gradient)

        Numerical gradient is fast enough at this tiny scale and avoids
        a PyTorch dependency for what is essentially a toy network.
        """
        batch = random.sample(self._replay, min(self._batch_size, len(self._replay)))

        total_loss = 0.0
        eps = 1e-5   # finite difference step

        for obs, action, reward, next_obs, done in batch:
            # Compute TD target
            q_next   = self._net.forward(next_obs)
            td_target = reward + (0.0 if done else self._gamma * float(np.max(q_next)))

            # Compute current Q-value
            q_curr   = self._net.forward(obs)
            td_error = float(q_curr[action]) - td_target
            total_loss += td_error ** 2

            # Numerical gradient update for each parameter array
            for param in self._net.parameters():
                flat = param.ravel()
                grad = np.zeros_like(flat)
                for i in range(len(flat)):
                    orig = flat[i]
                    flat[i] = orig + eps
                    q_plus = self._net.forward(obs)[action]
                    flat[i] = orig - eps
                    q_minus = self._net.forward(obs)[action]
                    flat[i] = orig
                    grad[i] = (q_plus - q_minus) / (2 * eps) * td_error
                flat -= self._lr * grad

        self.last_loss = total_loss / len(batch)
        self.n_updates += 1

    def _count_params(self) -> int:
        return sum(p.size for p in self._net.parameters())
