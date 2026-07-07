"""
PredictionPod — Forecasting layer for the Digital Twin.

Two forecasters:

1. RSLForecaster
   Uses HybridRSLModel (Arrhenius physics) to predict RSL% at delivery
   given a route's estimated travel time, ambient temperature, and humidity.

2. ETAForecaster
   Online ridge-regression trained on actual segment traversal times.
   Falls back to segment.get_travel_time_minutes() until enough data.

Both are CPU-only and require no external ML packages beyond numpy/scipy.
"""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RSL Forecaster
# ---------------------------------------------------------------------------

class RSLForecaster:
    """
    Predicts Remaining Shelf Life at delivery time.

    Uses HybridRSLModel (Arrhenius physics) — no ML needed because the
    physics is already well-calibrated.  The value-add is integrating
    route travel time + weather into a single "will this cargo survive?" signal.
    """

    # Road-type vibration map (g-force proxy)
    _VIBE_MAP: Dict[str, float] = {
        'motorway': 0.8, 'trunk': 1.0, 'primary': 1.2,
        'secondary': 1.5, 'tertiary': 1.8, 'residential': 2.0,
    }

    def __init__(self, config: Dict):
        from .business_models import HybridRSLModel

        rsl_cfg  = config.get('ai', {}).get('prediction', {}).get('rsl', {})
        cargo_cfg = config.get('cargo', {})

        self._model = HybridRSLModel(cargo_cfg)
        self.safety_margin_hours: float = rsl_cfg.get('safety_margin_hours', 12.0)
        self.min_acceptable_rsl_hours: float = (
            config.get('quality_control', {}).get('min_acceptable_rsl_hours', 72.0)
        )
        self.total_shelf_life_hours: float = cargo_cfg.get('initial_rsl_hours', 336.0)

        logger.info(
            f"[PredictionPod] RSLForecaster ready "
            f"(safety_margin={self.safety_margin_hours}h, "
            f"min_acceptable={self.min_acceptable_rsl_hours}h)"
        )

    def predict(self, current_rsl_pct: float, travel_time_hours: float,
                ambient_temp_c: float, humidity_pct: float,
                vibration_g: float = 1.2) -> Dict:
        """
        Predict RSL state at end of a route.

        Returns dict with keys:
            predicted_rsl_pct, predicted_rsl_hours,
            will_spoil_in_transit, below_acceptance_threshold,
            decay_rate_per_hour, recommendation ("ok"|"warn"|"reject")
        """
        decay_rate = self._model.calculate_decay_rate(
            ambient_temp_c, humidity_pct, vibration_g
        )
        predicted_rsl_pct = max(0.0, current_rsl_pct - decay_rate * travel_time_hours)
        predicted_rsl_hours = (predicted_rsl_pct / 100.0) * self.total_shelf_life_hours
        threshold = self.min_acceptable_rsl_hours + self.safety_margin_hours

        will_spoil = predicted_rsl_pct <= 0.0
        below_threshold = predicted_rsl_hours < threshold

        if will_spoil:
            recommendation = "reject"
        elif below_threshold:
            recommendation = "warn"
        else:
            recommendation = "ok"

        return {
            "predicted_rsl_pct": round(predicted_rsl_pct, 2),
            "predicted_rsl_hours": round(predicted_rsl_hours, 1),
            "will_spoil_in_transit": will_spoil,
            "below_acceptance_threshold": below_threshold,
            "decay_rate_per_hour": round(decay_rate, 4),
            "recommendation": recommendation,
        }

    def predict_for_truck(self, truck, route: Dict, weather: str,
                          temp_c: float, humidity_pct: float) -> Dict:
        """Convenience wrapper: predict RSL for a truck's current cargo + route."""
        if not truck.cargo_batches:
            return {
                "recommendation": "ok",
                "predicted_rsl_pct": 100.0,
                "predicted_rsl_hours": self.total_shelf_life_hours,
                "will_spoil_in_transit": False,
                "below_acceptance_threshold": False,
                "decay_rate_per_hour": 0.0,
            }

        avg_rsl = (sum(b.current_rsl for b in truck.cargo_batches)
                   / len(truck.cargo_batches))

        segments = route.get("segment_objects", []) if route else []
        progress = getattr(truck, "route_progress", 0)
        current_seg = segments[progress] if progress < len(segments) else None
        vibration_g = self._VIBE_MAP.get(
            getattr(current_seg, 'road_type', 'secondary'), 1.2
        )

        remaining_segs = segments[progress:]
        travel_time_hours = (
            sum(s.get_travel_time_minutes() for s in remaining_segs) / 60.0
            if remaining_segs else 0.0
        )

        return self.predict(avg_rsl, travel_time_hours, temp_c, humidity_pct,
                            vibration_g)


# ---------------------------------------------------------------------------
# ETA Forecaster
# ---------------------------------------------------------------------------

class ETAForecaster:
    """
    Lightweight online ridge-regression ETA model.

    Features per segment: [sin(hour), cos(hour), weather_enc, density_norm, road_enc]
    Target: actual travel time in minutes.

    Falls back to segment.get_travel_time_minutes() until min_samples reached.
    """

    _WEATHER_ENC: Dict[str, float] = {
        "clear": 0.0, "partly_cloudy": 0.1, "cloudy": 0.2,
        "light_rain": 0.5, "rain": 0.7, "heavy_rain": 1.0, "fog": 0.8,
    }
    _ROAD_ENC: Dict[str, float] = {
        "motorway": 0.0, "trunk": 0.1, "primary": 0.3,
        "secondary": 0.5, "tertiary": 0.7, "residential": 0.9,
    }

    def __init__(self, config: Dict):
        eta_cfg = config.get("ai", {}).get("prediction", {}).get("eta", {})
        self.min_samples: int = eta_cfg.get("min_samples_to_train", 50)
        self._n_features = 5
        self._checkpoint_path: str = eta_cfg.get(
            "checkpoint_path", "models/eta/eta_forecaster.npz"
        )

        lam = eta_cfg.get("ridge_lambda", 1.0)
        self._A = lam * np.eye(self._n_features)
        self._b = np.zeros(self._n_features)
        self._n_samples = 0
        self._weights: Optional[np.ndarray] = None

        logger.info(f"[PredictionPod] ETAForecaster ready (min_samples={self.min_samples})")

    def update(self, segment, actual_travel_time_min: float,
               hour: float, weather: str):
        """Record one completed segment traversal."""
        x = self._featurize(segment, hour, weather)
        self._A += np.outer(x, x)
        self._b += x * actual_travel_time_min
        self._n_samples += 1
        self._weights = None  # invalidate cached solution

    def predict_segment(self, segment, hour: float, weather: str) -> float:
        """Predict travel time (minutes) for one segment."""
        if self._n_samples < self.min_samples:
            return segment.get_travel_time_minutes()
        if self._weights is None:
            try:
                self._weights = np.linalg.solve(self._A, self._b)
            except np.linalg.LinAlgError:
                return segment.get_travel_time_minutes()
        x = self._featurize(segment, hour, weather)
        pred = float(np.dot(self._weights, x))
        physics = segment.get_travel_time_minutes()
        return max(0.1, min(pred, physics * 5.0))

    def predict_route(self, route: Dict, hour: float, weather: str,
                      route_progress: int = 0) -> float:
        """Predict total remaining travel time for a route."""
        segments = route.get("segment_objects", [])
        return sum(
            self.predict_segment(s, hour, weather)
            for s in segments[route_progress:]
        )

    def _featurize(self, segment, hour: float, weather: str) -> np.ndarray:
        hour_rad = hour * (2.0 * math.pi / 24.0)
        weather_enc = self._WEATHER_ENC.get(weather, 0.5)
        road_enc = self._ROAD_ENC.get(getattr(segment, "road_type", "secondary"), 0.5)
        density = getattr(segment, "current_traffic_density", 0.0) / 100.0
        return np.array([
            math.sin(hour_rad), math.cos(hour_rad),
            weather_enc, min(1.0, density), road_enc,
        ], dtype=np.float64)

    # ------------------------------------------------------------------ #
    # Persistence                                                         #
    # ------------------------------------------------------------------ #

    def save(self) -> None:
        """
        Persist the Ridge Regression state (A matrix, b vector, sample count)
        to a compressed NumPy archive so the model survives simulation restarts.

        File: models/eta/eta_forecaster.npz
        """
        import os
        os.makedirs(os.path.dirname(self._checkpoint_path), exist_ok=True)
        np.savez_compressed(
            self._checkpoint_path,
            A=self._A,
            b=self._b,
            n_samples=np.array([self._n_samples]),
        )
        logger.info(
            f"[ETAForecaster] Saved checkpoint: {self._n_samples} samples "
            f"→ {self._checkpoint_path}"
        )

    def load(self) -> bool:
        """
        Load a previously saved Ridge Regression state.

        Returns True if a checkpoint was found and loaded, False otherwise.
        """
        import os
        path = self._checkpoint_path
        # np.savez_compressed adds .npz automatically
        if not path.endswith(".npz"):
            path += ".npz"
        if not os.path.exists(path):
            logger.info(f"[ETAForecaster] No checkpoint found at {path} — starting fresh.")
            return False
        try:
            data = np.load(path)
            self._A = data["A"]
            self._b = data["b"]
            self._n_samples = int(data["n_samples"][0])
            self._weights = None   # invalidate cached solution
            logger.info(
                f"[ETAForecaster] Loaded checkpoint: {self._n_samples} samples "
                f"from {path}"
            )
            return True
        except Exception as e:
            logger.warning(f"[ETAForecaster] Failed to load checkpoint: {e} — starting fresh.")
            return False


# ---------------------------------------------------------------------------
# PredictionPod — container
# ---------------------------------------------------------------------------

class PredictionPod:
    """
    Central container for RSL and ETA forecasters.

    Usage:
        pod = PredictionPod(config)
        result = pod.rsl.predict(current_rsl, travel_hours, temp, humidity)
        pod.eta.update(segment, actual_minutes, hour, weather)
        eta_min = pod.eta.predict_route(route, hour, weather, progress)
    """

    def __init__(self, config: Dict):
        self.config = config
        pred_cfg = config.get("ai", {}).get("prediction", {})
        self.enabled: bool = pred_cfg.get("enabled", True)

        if not self.enabled:
            logger.info("[PredictionPod] Disabled via config")
            self.demand = None
            self.rsl = None
            self.eta = None
            return

        # demand attribute kept for backward compatibility with retailer_agent
        self.demand = None  # Demand forecasting handled per-retailer via InventoryModel EMA

        rsl_enabled = pred_cfg.get("rsl", {}).get("enabled", True)
        self.rsl = RSLForecaster(config) if rsl_enabled else None

        eta_enabled = pred_cfg.get("eta", {}).get("enabled", True)
        self.eta = ETAForecaster(config) if eta_enabled else None

        # Auto-load ETA checkpoint if one exists from a previous run
        if self.eta is not None:
            self.eta.load()

        logger.info("[PredictionPod] Initialized (rsl + eta)")

    # Convenience methods called by engine/agents

    def record_segment_traversal(self, segment, actual_minutes: float,
                                  hour: float, weather: str):
        """Feed a completed segment traversal into the ETA model."""
        if self.eta is not None:
            self.eta.update(segment, actual_minutes, hour, weather)

    def save_all(self) -> None:
        """Persist all learnable model state to disk (call on simulation shutdown)."""
        if self.eta is not None:
            self.eta.save()


# ---------------------------------------------------------------------------
# ForecastingAgentAdapter
# ---------------------------------------------------------------------------

class ForecastingAgentAdapter:
    """
    Adapts the PredictionPod's demand forecasting to the ForecastingAgent
    interface expected by InventoryModel.

    InventoryModel calls:
        predict(history: List[Dict]) -> float   (kg/hour rate)
        update(history: List[Dict], actual: float)

    This adapter bridges the two by delegating to the simple EMA forecaster
    inside InventoryModel itself (since PredictionPod no longer has a
    per-retailer demand forecaster after the Chronos/LSTM removal).
    """

    def __init__(self, demand_forecaster, retailer_id: str):
        # demand_forecaster is None after simplification — use internal EMA
        self._retailer_id = retailer_id
        self._smoothed: float = 0.0
        self._alpha: float = 0.3
        self._n_samples: int = 0

    def predict(self, history) -> float:
        """Return smoothed demand rate (kg/hour)."""
        return max(0.0, self._smoothed)

    def update(self, history, actual_demand: float):
        """Update EMA with latest demand observation."""
        if self._n_samples == 0:
            self._smoothed = actual_demand
        else:
            self._smoothed = self._alpha * actual_demand + (1 - self._alpha) * self._smoothed
        self._n_samples += 1
