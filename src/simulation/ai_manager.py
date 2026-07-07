"""
AIManager — Central orchestration hub for the AI/ML ecosystem.

Owns and exposes:
  - PredictionPod  (RSL-at-delivery, ETA forecasting)
  - OptimizationPod (PPO routing policy)

Public interface used by agents / engine:
    ai_manager.record_sale(retailer_id, sim_time, demand_kg)
    ai_manager.predict_rsl_at_delivery(truck, route, weather, temp, humidity) -> dict
    ai_manager.predict_route_eta(truck, route, hour, weather, progress) -> float
    ai_manager.record_segment_traversal(segment, actual_min, hour, weather)
    ai_manager.select_routing_action(truck_id, obs) -> int
    ai_manager.trigger_cross_agent_sync(source, event_data)
    ai_manager.shutdown()
"""

import logging
from typing import Dict, Optional

import numpy as np

from .prediction_pod import PredictionPod
from .optimization_pod import OptimizationPod

logger = logging.getLogger(__name__)


class AIManager:
    """
    Central hub for AI model lifecycle and inter-agent communication.

    Instantiated once by SimulationEngine and passed to all agents.
    """

    def __init__(self, config: Dict):
        self.config = config

        self.prediction_pod = PredictionPod(config)
        self.optimization_pod = OptimizationPod(config)

        self._warehouses: list = []

        logger.info("[AIManager] Initialized (prediction_pod + optimization_pod)")

    def register_warehouses(self, warehouses: list):
        """Register warehouse agents for cross-agent coordination."""
        self._warehouses = warehouses

    # ------------------------------------------------------------------
    # Prediction interface
    # ------------------------------------------------------------------

    def record_sale(self, retailer_id: str, sim_time: float, demand_kg: float):
        """Feed a sale observation into the demand forecaster (no-op — EMA is internal)."""
        pass  # Demand forecasting is handled inside InventoryModel per-retailer

    def predict_rsl_at_delivery(self, truck, route: Dict,
                                 weather: str, temp_c: float,
                                 humidity_pct: float) -> Dict:
        """Predict RSL state at end of a truck's current route."""
        if self.prediction_pod.rsl is None:
            return {
                "recommendation": "ok", "predicted_rsl_pct": 100.0,
                "predicted_rsl_hours": 336.0, "will_spoil_in_transit": False,
                "below_acceptance_threshold": False, "decay_rate_per_hour": 0.0,
            }
        return self.prediction_pod.rsl.predict_for_truck(
            truck, route, weather, temp_c, humidity_pct
        )

    def predict_route_eta(self, truck, route: Dict,
                          hour: float, weather: str,
                          route_progress: int = 0) -> float:
        """Predict remaining travel time (minutes) for a route."""
        if self.prediction_pod.eta is None:
            segs = route.get("segment_objects", [])
            return sum(s.get_travel_time_minutes() for s in segs[route_progress:])
        return self.prediction_pod.eta.predict_route(route, hour, weather, route_progress)

    def record_segment_traversal(self, segment, actual_minutes: float,
                                  hour: float, weather: str):
        """Feed a completed segment traversal into the ETA model."""
        if self.prediction_pod.eta is not None:
            self.prediction_pod.eta.update(segment, actual_minutes, hour, weather)

    # ------------------------------------------------------------------
    # Optimization interface
    # ------------------------------------------------------------------

    def select_routing_action(self, truck_id: str, obs: np.ndarray) -> int:
        """Select a routing strategy for a truck. Returns 0=balanced, 1=speed, 2=fuel."""
        return self.optimization_pod.select_action(truck_id, obs)

    def train_routing_policy(self, marl_env, n_steps: int = 10000, callbacks=None):
        """Run offline PPO training. Call from a training script, not the sim loop."""
        self.optimization_pod.train(marl_env, n_steps, callbacks=callbacks)

    # ------------------------------------------------------------------
    # Cross-agent sync
    # ------------------------------------------------------------------

    def trigger_cross_agent_sync(self, source: str, event_data: Dict):
        """
        Handle inter-agent intelligence sharing.

        rsl_alert  → log critical RSL and reduce epsilon for more exploitation
        stockout   → boost order priority for the affected retailer
        """
        if source == "rsl_alert":
            current_rsl = event_data.get("current_rsl", 100.0)
            truck_id = event_data.get("truck_id")
            if current_rsl < 70.0:
                logger.info(
                    f"[AIManager] Critical RSL ({current_rsl:.1f}%) for truck {truck_id}. "
                    f"Boosting exploitation."
                )
                if self.optimization_pod.enabled:
                    self.optimization_pod.epsilon = max(
                        0.0, self.optimization_pod.epsilon - 0.01
                    )

        elif source == "stockout":
            retailer_id = event_data.get("retailer_id")
            logger.info(f"[AIManager] Stockout at {retailer_id} — boosting order priority.")
            for warehouse in self._warehouses:
                boosted = 0
                for order in warehouse.pending_orders:
                    if order.retailer_id == retailer_id:
                        order.priority = min(10.0, order.priority + 1.0)
                        boosted += 1
                if boosted:
                    warehouse.pending_orders.sort(key=lambda o: o.priority, reverse=True)
                    warehouse.pending_orders_changed = True

    def shutdown(self):
        """Save all learnable model state on simulation end."""
        logger.info("[AIManager] Shutting down — saving model checkpoints...")
        if self.optimization_pod.enabled:
            self.optimization_pod.save()
        self.prediction_pod.save_all()   # persist ETA Ridge Regression weights
        logger.info("[AIManager] Shutdown complete.")

