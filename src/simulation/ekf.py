"""
ekf.py — Extended Kalman Filter for per-truck IoT state reconstruction.

Each TruckAgent owns one TruckEKF instance.  Every simulation tick the agent
receives packet-lossy, Gaussian-noisy readings from three sensors:

    GPS  → (lat, lon)
    Temp → °C cargo area
    Stock→ kg payload

The EKF fuses these into a *clean* state estimate that is passed to the RL
observation builder instead of using raw ground-truth or raw noisy values.

State vector  x = [lat, lon, temp_c, stock_kg]   (4-D)
Measurement   z = [lat_noisy, lon_noisy, temp_noisy, stock_noisy]  (4-D)

Process model: constant (we predict the state stays the same across one
small time-step; the truck dynamics are dominated by sensor noise rather
than systematic drift, so a random-walk prior is appropriate here).

Measurement noise covariance R is diagonal, tuned to match sensor specs:
    GPS:   σ ≈ 0.00005 degrees  → R[0,0] = R[1,1] = (0.00005)²
    Temp:  σ ≈ 0.5 °C           → R[2,2] = 0.5²  = 0.25
    Stock: σ ≈ 2.0 kg           → R[3,3] = 2.0²  = 4.0

Process noise covariance Q is kept small to reflect that the true state
does not jump randomly between ticks (smooth truck motion / gradual cargo
decay). Q is 1/100th of R as a conservative prior.
"""

from __future__ import annotations

import logging
import math
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# TruckEKF                                                                    #
# --------------------------------------------------------------------------- #

class TruckEKF:
    """
    Lightweight linear Kalman filter (technically KF, not extended, because our
    process and measurement models are both linear in the 4-D state vector we
    use).  We call it EKF in the codebase to reflect the *intent* — it is the
    standard Kalman framework that would extend naturally to non-linear dynamics
    (e.g. vehicle kinematic models) if needed later.

    Args:
        truck_id:     Identifier used only for logging.
        gps_noise:    1-σ GPS positional error in degrees (default 0.00005 ≈ 5 m).
        temp_noise:   1-σ temperature sensor error in °C (default 0.5).
        stock_noise:  1-σ load-cell error in kg (default 2.0).
        q_scale:      Process noise scale relative to R (default 0.01).
    """

    STATE_DIM = 4   # [lat, lon, temp, stock]
    MEAS_DIM  = 4   # same — full observation

    def __init__(
        self,
        truck_id: str,
        gps_noise:   float = 0.00005,
        temp_noise:  float = 0.5,
        stock_noise: float = 2.0,
        q_scale:     float = 0.01,
    ):
        self.truck_id = truck_id
        self._initialised = False

        # --- Measurement noise covariance R (sensor specs) ---
        r_diag = np.array([
            gps_noise  ** 2,   # lat variance
            gps_noise  ** 2,   # lon variance
            temp_noise ** 2,   # temp variance
            stock_noise** 2,   # stock variance
        ], dtype=np.float64)
        self.R = np.diag(r_diag)

        # --- Process noise covariance Q (how much the true state drifts) ---
        self.Q = np.diag(r_diag * q_scale)

        # --- State transition F = Identity (random-walk prior) ---
        self.F = np.eye(self.STATE_DIM, dtype=np.float64)

        # --- Observation matrix H = Identity (we observe all 4 states) ---
        self.H = np.eye(self.MEAS_DIM, self.STATE_DIM, dtype=np.float64)

        # --- State and covariance (uninitialised until first measurement) ---
        self.x = np.zeros(self.STATE_DIM, dtype=np.float64)
        self.P = np.eye(self.STATE_DIM, dtype=np.float64) * 1.0   # prior uncertainty

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def update(
        self,
        gps_reading:   Optional[Tuple[float, float]],
        temp_reading:  Optional[float],
        stock_reading: Optional[float],
        true_location: Tuple[float, float],
        true_temp:     float,
        true_stock:    float,
    ) -> Tuple[Tuple[float, float], float, float]:
        """
        Run one Kalman predict-update cycle.

        Sensor readings may be None (packet loss).  When a channel is lost we
        inflate that channel's measurement noise → the filter effectively
        ignores it and relies on the prior prediction instead.

        Args:
            gps_reading:   Noisy (lat, lon) from GPSSensor, or None.
            temp_reading:  Noisy °C from TemperatureSensor, or None.
            stock_reading: Noisy kg from StockSensor, or None.
            true_location: Ground-truth (lat, lon) used for bootstrap only.
            true_temp:     Ground-truth temperature for bootstrap.
            true_stock:    Ground-truth stock for bootstrap.

        Returns:
            (clean_location, clean_temp, clean_stock)
            where clean_location is a (lat, lon) tuple.
        """
        # --- Bootstrap: initialise state on first call ---
        if not self._initialised:
            lat = gps_reading[0] if gps_reading else true_location[0]
            lon = gps_reading[1] if gps_reading else true_location[1]
            tmp = temp_reading  if temp_reading  is not None else true_temp
            stk = stock_reading if stock_reading is not None else true_stock
            self.x = np.array([lat, lon, tmp, stk], dtype=np.float64)
            self._initialised = True
            logger.debug(f"[EKF] Truck {self.truck_id} bootstrapped at {lat:.5f},{lon:.5f}")
            return (lat, lon), tmp, stk

        # ---- (1) PREDICT ----
        x_pred = self.F @ self.x                          # same as self.x (F=I)
        P_pred = self.F @ self.P @ self.F.T + self.Q

        # ---- (2) BUILD MEASUREMENT & ADAPTIVE R ----
        # When a sensor packet is lost we set the measurement to the prior
        # prediction and inflate that channel's noise → effectively ignores it.
        R_adapt = self.R.copy()
        INF = 1e8   # effectively infinite noise for dropped channels

        lat_m  = gps_reading[0]   if gps_reading               else x_pred[0]
        lon_m  = gps_reading[1]   if gps_reading               else x_pred[1]
        temp_m = temp_reading     if temp_reading  is not None  else x_pred[2]
        stk_m  = stock_reading    if stock_reading is not None  else x_pred[3]

        if gps_reading is None:
            R_adapt[0, 0] = INF
            R_adapt[1, 1] = INF
        if temp_reading is None:
            R_adapt[2, 2] = INF
        if stock_reading is None:
            R_adapt[3, 3] = INF

        z = np.array([lat_m, lon_m, temp_m, stk_m], dtype=np.float64)

        # ---- (3) UPDATE ----
        S  = self.H @ P_pred @ self.H.T + R_adapt          # innovation covariance
        K  = P_pred @ self.H.T @ np.linalg.inv(S)          # Kalman gain
        y  = z - self.H @ x_pred                           # innovation
        self.x = x_pred + K @ y                            # posterior state
        self.P = (np.eye(self.STATE_DIM) - K @ self.H) @ P_pred  # posterior covariance

        # ---- (4) CLAMP to physically plausible ranges ----
        self.x[2] = max(-10.0, min(55.0,  self.x[2]))   # temp: -10°C to 55°C
        self.x[3] = max(0.0,              self.x[3])      # stock: non-negative

        clean_loc   = (float(self.x[0]), float(self.x[1]))
        clean_temp  = float(self.x[2])
        clean_stock = float(self.x[3])

        return clean_loc, clean_temp, clean_stock

    # ------------------------------------------------------------------ #
    # Introspection helpers (for telemetry / debugging)                   #
    # ------------------------------------------------------------------ #

    @property
    def position_uncertainty_m(self) -> float:
        """Estimated 1-σ positional uncertainty in metres (lat/lon average)."""
        deg_uncertainty = math.sqrt((self.P[0, 0] + self.P[1, 1]) / 2.0)
        return deg_uncertainty * 111_000   # 1° ≈ 111 km

    @property
    def temp_uncertainty_c(self) -> float:
        """Estimated 1-σ temperature uncertainty in °C."""
        return math.sqrt(max(0.0, self.P[2, 2]))

    @property
    def stock_uncertainty_kg(self) -> float:
        """Estimated 1-σ stock uncertainty in kg."""
        return math.sqrt(max(0.0, self.P[3, 3]))

    def get_diagnostics(self) -> dict:
        """Return a flat dict of EKF uncertainty metrics for telemetry."""
        return {
            "ekf_pos_uncertainty_m":   round(self.position_uncertainty_m, 2),
            "ekf_temp_uncertainty_c":  round(self.temp_uncertainty_c,     3),
            "ekf_stock_uncertainty_kg": round(self.stock_uncertainty_kg,  2),
        }
