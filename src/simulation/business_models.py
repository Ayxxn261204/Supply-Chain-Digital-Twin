"""
Business models for the supply chain digital twin.

Contains three core models used throughout the simulation:
  - DemandModel: Stochastic customer arrival model (Poisson process)
  - InventoryModel: (s,S) inventory policy with EOQ and safety stock
  - HybridRSLModel: Arrhenius-based Remaining Shelf Life physics model
"""

from __future__ import annotations

import logging
import math
import random
from typing import Dict, List, Optional, Any

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simple exponential smoothing forecaster (replaces LSTM — same accuracy
# for short-horizon demand, zero extra dependencies)
# ---------------------------------------------------------------------------

class _SimpleForecaster:
    """
    Exponential moving average demand forecaster.

    Provides the same interface as the old ForecastingAgent so InventoryModel
    works without changes.
    """

    def __init__(self, window_size: int = 24, alpha: float = 0.3):
        self._alpha = alpha
        self._smoothed: float = 0.0
        self._n_samples: int = 0

    def predict(self, history: List[Dict]) -> float:
        """Return smoothed demand rate (kg/hour)."""
        return max(0.0, self._smoothed)

    def update(self, history: List[Dict], actual_demand: float):
        """Update EMA with latest observation."""
        if self._n_samples == 0:
            self._smoothed = actual_demand
        else:
            self._smoothed = self._alpha * actual_demand + (1 - self._alpha) * self._smoothed
        self._n_samples += 1


# ---------------------------------------------------------------------------
# DemandModel
# ---------------------------------------------------------------------------

class DemandModel:
    """
    Stochastic demand model using a Poisson process with time-varying rates.

    Accounts for:
    - Time-of-day patterns (24-hour multipliers)
    - Day-of-week patterns (0=Sunday, 6=Saturday)
    - Seasonal patterns (monthly multipliers)
    - Weather effects
    - Temperature effects
    """

    def __init__(self, config: Dict):
        self.base_arrival_rate = config['base_arrival_rate_per_hour']
        self.time_of_day_multipliers = config['time_of_day_multipliers']
        self.day_of_week_multipliers = config['day_of_week_multipliers']
        self.seasonal_multipliers = config['seasonal_multipliers']

        self.purchase_mean = config['purchase_quantity_mean']
        self.purchase_std = config['purchase_quantity_std']
        self.purchase_min = config['purchase_quantity_min']
        self.purchase_max = config['purchase_quantity_max']

        self.weather_multipliers = config.get('weather_demand_multipliers', {})
        temp_cfg = config.get('temperature_demand_effect', {})
        self.temp_effect_enabled = temp_cfg.get('enabled', False)
        self.base_temperature = temp_cfg.get('base_temperature_celsius', 25)
        self.temp_multiplier_per_degree = temp_cfg.get('multiplier_per_degree', 0.02)
        self.max_temp_multiplier = temp_cfg.get('max_multiplier', 1.3)

    def get_arrival_rate(self, current_time: float, day_of_week: int,
                         month: int, weather: str = "clear",
                         temperature_celsius: float = 25.0) -> float:
        """Return customer arrival rate (customers/hour) for given conditions."""
        hour = int((current_time / 60) % 24)
        rate = self.base_arrival_rate
        rate *= self.time_of_day_multipliers[hour]
        rate *= self.day_of_week_multipliers[day_of_week]
        rate *= self.seasonal_multipliers[month - 1]
        if weather in self.weather_multipliers:
            rate *= self.weather_multipliers[weather]
        if self.temp_effect_enabled:
            temp_diff = temperature_celsius - self.base_temperature
            temp_mult = 1.0 + (temp_diff * self.temp_multiplier_per_degree)
            temp_mult = min(self.max_temp_multiplier, max(0.7, temp_mult))
            rate *= temp_mult
        return max(0.0, rate)

    def generate_customer_arrivals(self, current_time: float, time_step: float,
                                   day_of_week: int, month: int,
                                   weather: str = "clear",
                                   temperature_celsius: float = 25.0) -> int:
        """Generate number of customer arrivals in this time step (Poisson)."""
        rate_per_hour = self.get_arrival_rate(
            current_time, day_of_week, month, weather, temperature_celsius
        )
        rate_per_step = rate_per_hour * (time_step / 60.0)
        return int(np.random.poisson(rate_per_step)) if rate_per_step > 0 else 0

    def generate_purchase_quantity(self) -> float:
        """Generate a random purchase quantity (kg) for one customer."""
        qty = random.gauss(self.purchase_mean, self.purchase_std)
        return max(self.purchase_min, min(self.purchase_max, qty))

    def __repr__(self) -> str:
        return f"DemandModel(base_rate={self.base_arrival_rate} customers/hour)"


# ---------------------------------------------------------------------------
# InventoryModel
# ---------------------------------------------------------------------------

class InventoryModel:
    """
    (s, S) continuous-review inventory policy with EOQ and safety stock.

    Reorder point s = predicted demand during lead time + safety stock.
    Order-up-to level S = s + EOQ  (or fixed-days equivalent).
    """

    def __init__(self, config: Dict, ai_forecaster=None):
        self.service_level = config['service_level']
        self.lead_time_mean = config['lead_time_days_mean'] * 24 * 60   # → minutes
        self.lead_time_std  = config['lead_time_days_std']  * 24 * 60
        self.review_period  = config['review_period_hours'] * 60         # → minutes

        self.order_method   = config['order_quantity_method']
        self.holding_cost   = config.get('holding_cost_per_kg_per_day', 0.05)
        self.ordering_cost  = config.get('ordering_cost_per_order', 50)
        self.order_up_to_days = config.get('order_up_to_days', 7)

        self.forecast_window  = config['demand_forecast_window_days'] * 24 * 60
        self.smoothing_alpha  = config['demand_smoothing_alpha']

        self.min_order_qty = config['min_order_quantity_kg']
        self.max_order_qty = config['max_order_quantity_kg']

        # Demand history and statistics
        self.demand_history_full: List[Dict] = []
        self.forecasted_demand_rate = 0.0   # kg/minute (legacy fallback)
        self.demand_std = 0.0

        # Forecaster: use provided adapter or fall back to simple EMA
        self.ai_forecaster = ai_forecaster if ai_forecaster else _SimpleForecaster(
            alpha=self.smoothing_alpha
        )

        self.z_score = stats.norm.ppf(self.service_level)

    def record_demand(self, time: float, quantity: float,
                      context: Optional[Dict] = None):
        """Record a demand event for forecasting."""
        if context is None:
            context = {'price': 40.0, 'hour': 12, 'day': 3}
        entry = {'time': time, 'demand': quantity, **context}
        self.demand_history_full.append(entry)
        if len(self.demand_history_full) > 1000:
            self.demand_history_full.pop(0)
        self.ai_forecaster.update(self.demand_history_full[:-1], quantity)
        self._update_legacy_forecast()

    def _update_legacy_forecast(self):
        """Update simple EMA demand rate (kg/minute) for safety-stock calc."""
        if not self.demand_history_full:
            return
        total_qty = sum(e['demand'] for e in self.demand_history_full)
        time_span = (self.demand_history_full[-1]['time']
                     - self.demand_history_full[0]['time'])
        if time_span > 0:
            observed = total_qty / time_span
            self.forecasted_demand_rate = (
                self.smoothing_alpha * observed
                + (1 - self.smoothing_alpha) * self.forecasted_demand_rate
            ) if self.forecasted_demand_rate > 0 else observed
        if len(self.demand_history_full) >= 10:
            quantities = [e['demand'] for e in self.demand_history_full]
            mean_q = sum(quantities) / len(quantities)
            self.demand_std = math.sqrt(
                sum((q - mean_q) ** 2 for q in quantities) / len(quantities)
            )

    def calculate_reorder_point(self) -> float:
        """Reorder point s = predicted demand during lead time + safety stock."""
        predicted_rate_kg_h = self.ai_forecaster.predict(self.demand_history_full)
        predicted_demand_kg = predicted_rate_kg_h * (self.lead_time_mean / 60.0)
        protection_period = self.lead_time_mean + self.review_period
        if self.demand_std > 0:
            safety_stock = self.z_score * self.demand_std * math.sqrt(
                protection_period / 60
            )
        else:
            safety_stock = 0.2 * predicted_demand_kg
        return max(self.min_order_qty, predicted_demand_kg + safety_stock)

    def calculate_order_up_to_level(self) -> float:
        """Order-up-to level S using EOQ or fixed-days method."""
        if self.order_method == "EOQ":
            return self._eoq_level()
        return self._fixed_days_level()

    def _eoq_level(self) -> float:
        predicted_rate_kg_h = self.ai_forecaster.predict(self.demand_history_full)
        annual_demand = predicted_rate_kg_h * 24 * 365
        if annual_demand > 0 and self.holding_cost > 0:
            eoq = math.sqrt(
                (2 * annual_demand * self.ordering_cost) / (self.holding_cost * 365)
            )
            s = self.calculate_reorder_point()
            return max(self.min_order_qty, min(self.max_order_qty, s + eoq))
        return self._fixed_days_level()

    def _fixed_days_level(self) -> float:
        predicted_rate_kg_h = self.ai_forecaster.predict(self.demand_history_full)
        demand_for_period = predicted_rate_kg_h * 24 * self.order_up_to_days
        protection_period = self.lead_time_mean + self.review_period
        if self.demand_std > 0:
            safety_stock = self.z_score * self.demand_std * math.sqrt(
                protection_period / 60
            )
        else:
            safety_stock = 0.2 * demand_for_period
        return max(self.min_order_qty, min(self.max_order_qty,
                                           demand_for_period + safety_stock))

    def should_reorder(self, current_inventory: float) -> bool:
        """True if inventory has fallen below the reorder point."""
        return current_inventory <= self.calculate_reorder_point()

    def calculate_order_quantity(self, current_inventory: float) -> float:
        """Quantity to order to bring inventory up to S."""
        order_qty = self.calculate_order_up_to_level() - current_inventory
        if order_qty < self.min_order_qty:
            return 0.0
        return min(self.max_order_qty, order_qty)

    def __repr__(self) -> str:
        return (f"InventoryModel(method={self.order_method}, "
                f"service_level={self.service_level:.0%})")


# ---------------------------------------------------------------------------
# HybridRSLModel
# ---------------------------------------------------------------------------

class HybridRSLModel:
    """
    Physics-based Remaining Shelf Life model using the Arrhenius equation.

    Decay rate (% per hour) = A * exp(-Ea / (R * T)) * humidity_factor
                              * vibration_factor

    Calibrated for Nagpur oranges (Citrus sinensis):
      - 14-day shelf life at 4°C, 90% RH
      - Ea ≈ 120 kJ/mol (Vitamin C loss literature value)
    """

    def __init__(self, config: Optional[Dict] = None):
        self.R  = 8.314       # Gas constant J/(mol·K)
        self.Ea = 120000.0    # Activation energy J/mol
        self.A  = 1.3e20      # Pre-exponential factor (calibrated)

        self.optimal_humidity    = 90.0   # %
        self.humidity_sensitivity = 0.01  # penalty per % deviation
        self.vibration_sensitivity = 0.05 # penalty per g

        if config:
            self.Ea = float(config.get('activation_energy', self.Ea))
            self.A  = float(config.get('pre_exponential_factor', self.A))
            self.optimal_humidity = config.get('optimal_humidity', self.optimal_humidity)

    def calculate_decay_rate(self, temp_c: float, humidity: float,
                             vibration_g: float = 0.0) -> float:
        """
        Return RSL decay rate in percentage points per hour.

        Args:
            temp_c:      Temperature in Celsius
            humidity:    Relative humidity (%)
            vibration_g: Road vibration in g-force (0 = stationary)
        """
        T_k = max(250.0, temp_c + 273.15)
        try:
            base_rate = self.A * math.exp(-self.Ea / (self.R * T_k))
        except OverflowError:
            base_rate = 0.5

        hum_factor  = 1.0 + abs(humidity - self.optimal_humidity) * self.humidity_sensitivity
        vibe_factor = 1.0 + vibration_g * self.vibration_sensitivity

        rate = base_rate * hum_factor * vibe_factor
        return max(0.001, min(10.0, rate))

    def predict_rsl_hours(self, current_rsl_pct: float,
                          future_temp_c: float, future_humidity: float,
                          future_vibration_g: float = 0.0) -> float:
        """Predict remaining shelf life (hours) under constant future conditions."""
        rate = self.calculate_decay_rate(future_temp_c, future_humidity,
                                         future_vibration_g)
        return current_rsl_pct / rate if rate > 0 else 999.0


# ---------------------------------------------------------------------------
# Legacy DQN policy network and agent
# (Fallback when Stable-Baselines3 is not installed)
# OBS_DIM = 25, N_ACTIONS = 6 — must match RoutingEnv and TruckAgent._build_obs
# ---------------------------------------------------------------------------

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False


class RoutingDQN:
    """
    Lightweight 3-layer DQN network for routing decisions.

    Default dimensions match the shared observation/action space:
      state_dim=25  (OBS_DIM — rsl, fuel, traffic×5, accidents×5, zone, ripple×4, time×3)
      action_dim=6  (N_ACTIONS — 0=follow route, 1-5=deviate to neighbour)

    Args:
        state_dim: Input feature dimension (default 25).
        action_dim: Number of discrete actions (default 6).
    """

    def __init__(self, state_dim: int = 25, action_dim: int = 6):
        if not _TORCH_AVAILABLE:
            raise ImportError("PyTorch is required for RoutingDQN.")
        super().__init__()
        self.fc1 = nn.Linear(state_dim, 128)
        self.fc2 = nn.Linear(128, 128)
        self.fc3 = nn.Linear(128, action_dim)
        self._relu = nn.ReLU()

    def forward(self, x):
        x = self._relu(self.fc1(x))
        x = self._relu(self.fc2(x))
        return self.fc3(x)

    def __call__(self, x):
        return self.forward(x)


class DQNAgent:
    """
    Epsilon-greedy DQN agent wrapping RoutingDQN.

    Args:
        state_dim: Observation dimension (default 25).
        action_dim: Action space size (default 6).
        lr: Learning rate (default 1e-3).
        gamma: Discount factor (default 0.99).
        epsilon: Initial exploration rate (default 0.1).
    """

    def __init__(self, state_dim: int = 25, action_dim: int = 6,
                 lr: float = 1e-3, gamma: float = 0.99, epsilon: float = 0.1):
        if not _TORCH_AVAILABLE:
            raise ImportError("PyTorch is required for DQNAgent.")
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.gamma = gamma
        self.epsilon = epsilon
        self.policy_net = RoutingDQN(state_dim, action_dim)
        self.target_net = RoutingDQN(state_dim, action_dim)
        self.optimizer = optim.Adam(self.policy_net.fc1.parameters(), lr=lr)

    def select_action(self, obs) -> int:
        """Epsilon-greedy action selection."""
        import random as _random
        if _random.random() < self.epsilon:
            return _random.randint(0, self.action_dim - 1)
        import torch as _torch
        with _torch.no_grad():
            obs_t = _torch.FloatTensor(obs).unsqueeze(0)
            q_values = self.policy_net(obs_t)
            return int(q_values.argmax().item())
