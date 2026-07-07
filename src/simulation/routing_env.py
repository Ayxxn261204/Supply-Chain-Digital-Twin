"""
RoutingEnv - Lightweight Gymnasium-compatible environment for PPO routing training.

Design
------
The RL problem is: at each decision point (dispatch, accident, RSL threshold),
pick one of 3 routing strategies. A* executes the chosen strategy. Terminal-only
reward tied to RSL at delivery and fuel consumed.

Observation vector (OBS_DIM = 25)  # FROZEN — must match TruckAgent._build_obs
Action space (Discrete 3):
  0 = balanced   (distance=1.0, time=0.5, fuel=2.0)
  1 = speed      (distance=0.5, time=2.0, fuel=0.5)
  2 = fuel       (distance=1.0, time=0.3, fuel=4.0)

Reward (terminal only):
  R = (RSL_at_delivery / 100) * w_freshness
    - (fuel_consumed / fuel_at_dispatch) * w_fuel
    + delivery_bonus
"""

from __future__ import annotations

import logging
import math
import random
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import numpy as np
import networkx as nx

try:
    import gymnasium as gym
    import gymnasium.spaces as spaces
    _GYM_AVAILABLE = True
except ImportError:
    _GYM_AVAILABLE = False

logger = logging.getLogger(__name__)

OBS_DIM = 25   # FROZEN — must match TruckAgent._build_obs
N_ACTIONS = 3  # 0=balanced, 1=speed, 2=fuel

# Default strategy weights — overridden by config['routing']['strategies'] if present
STRATEGY_WEIGHTS = [
    {'distance_km': 1.0, 'time_minutes': 0.5, 'fuel_liters': 2.0},  # 0: balanced
    {'distance_km': 0.5, 'time_minutes': 2.0, 'fuel_liters': 0.5},  # 1: speed
    {'distance_km': 1.0, 'time_minutes': 0.3, 'fuel_liters': 4.0},  # 2: fuel
]


class _Box:
    def __init__(self, low, high, shape, dtype=np.float32):
        self.low = np.full(shape, low, dtype=dtype)
        self.high = np.full(shape, high, dtype=dtype)
        self.shape = shape
        self.dtype = dtype


class _Discrete:
    def __init__(self, n):
        self.n = n
        self.shape = ()
        self.dtype = np.int64


if _GYM_AVAILABLE:
    _EnvBase = gym.Env
else:
    _EnvBase = object


class RoutingEnv(_EnvBase):
    """Strategy-selection RL environment backed by the real OSM network."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        config: Dict,
        road_network,
        traffic_model,
        router,
        weather_loader=None,
        max_steps: int = 10,
    ):
        if _GYM_AVAILABLE:
            super().__init__()

        self.config = config
        self.road_network = road_network
        self.traffic_model = traffic_model
        self.router = router
        self.weather_loader = weather_loader
        self.max_steps = max_steps

        # Load strategy weights from config if present, else use defaults
        strategies_cfg = config.get('routing', {}).get('strategies', {})
        if strategies_cfg:
            self._strategy_weights = [
                strategies_cfg.get('balanced', STRATEGY_WEIGHTS[0]),
                strategies_cfg.get('speed',    STRATEGY_WEIGHTS[1]),
                strategies_cfg.get('fuel',     STRATEGY_WEIGHTS[2]),
            ]
        else:
            self._strategy_weights = list(STRATEGY_WEIGHTS)

        # Load reward weights from config
        rw = config.get('ai', {}).get('optimization', {}).get('reward_weights', {})
        self._w_freshness     = rw.get('freshness',       1.0)
        self._w_fuel          = rw.get('fuel',            0.5)
        self._delivery_bonus  = rw.get('delivery_bonus', 10.0)
        self._timeout_penalty = rw.get('timeout_penalty', 2.0)

        # RSL physics
        from .business_models import HybridRSLModel
        self._rsl_model = HybridRSLModel(config.get('cargo', {}))

        # Truck type config
        truck_types = config.get('truck_types', {})
        self._truck_cfg = truck_types.get(
            'medium', next(iter(truck_types.values())) if truck_types else {}
        )
        self._fuel_tank  = float(self._truck_cfg.get('fuel_tank_liters', 150.0))
        self._fuel_empty = float(self._truck_cfg.get('fuel_consumption_empty_l_per_100km', 28.0))
        self._fuel_full  = float(self._truck_cfg.get('fuel_consumption_full_l_per_100km', 38.0))
        self._truck_type_dict = {
            'capacity_kg': self._truck_cfg.get('capacity_kg', 3000),
            'fuel_consumption_empty_l_per_100km': self._fuel_empty,
            'fuel_consumption_full_l_per_100km': self._fuel_full,
            'fuel_efficiency_by_speed': self._truck_cfg.get('fuel_efficiency_by_speed', {}),
        }

        # Gym spaces
        if _GYM_AVAILABLE:
            self.observation_space = spaces.Box(
                low=-1.0, high=1.0, shape=(OBS_DIM,), dtype=np.float32
            )
            self.action_space = spaces.Discrete(N_ACTIONS)
        else:
            self.observation_space = _Box(-1.0, 1.0, shape=(OBS_DIM,))
            self.action_space = _Discrete(N_ACTIONS)

        self.num_envs = 1

        # Episode state
        self._current_node: Optional[int] = None
        self._destination_node: Optional[int] = None
        self._rsl: float = 100.0
        self._fuel: float = self._fuel_tank
        self._load_fraction: float = 1.0
        self._sim_time: float = 0.0
        self._step_count: int = 0
        self._done: bool = True
        self._fuel_at_dispatch: float = self._fuel_tank
        self._rsl_at_dispatch: float = 100.0
        self._injected_segments: List = []

        self._all_nodes: List[int] = list(road_network.graph.nodes())

        # Route pool — cached to disk
        pool_size = config.get('ai', {}).get('optimization', {}).get('route_pool_size', 50)
        pool_cache_path = Path(
            config.get('ai', {}).get('optimization', {}).get('checkpoint_dir', 'models/rl')
        ) / f'route_pool_{pool_size}.pkl'

        if pool_cache_path.exists():
            import pickle
            try:
                with open(pool_cache_path, 'rb') as f:
                    cached = pickle.load(f)
                if len(cached) >= pool_size:
                    self._route_pool: List[Dict] = cached
                    logger.info(f'[RoutingEnv] Loaded {len(self._route_pool)} routes from cache')
                else:
                    raise ValueError('Cache too small')
            except Exception:
                try:
                    pool_cache_path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._route_pool = self._build_route_pool(pool_size)
                pool_cache_path.parent.mkdir(parents=True, exist_ok=True)
                import pickle
                try:
                    with open(pool_cache_path, 'wb') as f:
                        pickle.dump(self._route_pool, f)
                except OSError:
                    pass
        else:
            logger.info(f'[RoutingEnv] Pre-computing {pool_size} routes for training pool...')
            self._route_pool = self._build_route_pool(pool_size)
            pool_cache_path.parent.mkdir(parents=True, exist_ok=True)
            import pickle
            try:
                with open(pool_cache_path, 'wb') as f:
                    pickle.dump(self._route_pool, f)
                logger.info(f'[RoutingEnv] Route pool saved ({len(self._route_pool)} routes)')
            except OSError:
                logger.warning('[RoutingEnv] Could not save route pool cache. Continuing without cache.')

        logger.info(
            f'[RoutingEnv] Initialised — {len(self._all_nodes):,} nodes, '
            f'{len(self._route_pool)} routes in pool, OBS_DIM={OBS_DIM}, max_steps={max_steps}'
        )

    # ------------------------------------------------------------------
    # Gym API
    # ------------------------------------------------------------------

    def reset(self, seed: Optional[int] = None, options: Optional[Dict] = None):
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

        entry = random.choice(self._route_pool)
        nodes = entry['nodes']
        self._current_node = nodes[0]
        self._destination_node = nodes[-1]

        self._rsl = random.uniform(60.0, 100.0)
        self._fuel = random.uniform(0.4, 1.0) * self._fuel_tank
        self._load_fraction = random.uniform(0.3, 1.0)
        self._sim_time = random.randint(0, 364) * 24 * 60 + random.uniform(0, 24) * 60

        self._fuel_at_dispatch = self._fuel
        self._rsl_at_dispatch = self._rsl
        self._step_count = 0
        self._done = False
        self._injected_segments = []

        return self._build_obs(), {}

    def step(self, action: int):
        assert not self._done, 'Call reset() before step()'
        assert action in {0, 1, 2}, f'Invalid action {action}'

        weights = self._strategy_weights[action]

        # Call router with strategy weights
        route = self.router.find_path(
            self._current_node, self._destination_node,
            truck_type_config=self._truck_type_dict,
            load_fraction=self._load_fraction,
            avoid_blocked=True,
            current_time=self._sim_time,
            cost_weights=weights,
        )

        self._step_count += 1

        # No path found — truncate
        if route is None:
            self._done = True
            self._cleanup_accidents()
            return (
                self._build_obs(),
                -self._timeout_penalty,
                False,
                True,
                {'arrived': False, 'rsl': self._rsl, 'fuel_pct': self._fuel / self._fuel_tank * 100},
            )

        # Stochastic accident injection (0-2 accidents, ~50% chance of 0)
        n_accidents = random.choice([0, 0, 0, 1, 1, 2])
        segs = route.get('segment_objects', [])

        for _ in range(n_accidents):
            if not segs:
                break
            seg = random.choice(segs)
            if not seg.is_blocked:
                seg.set_accident(severity='moderate')
                self._injected_segments.append(seg)

                # Advance state to ~halfway point (approximate accident location)
                partial_fraction = random.uniform(0.2, 0.8)
                partial_time = route['total_time_minutes'] * partial_fraction
                partial_fuel = route['total_fuel_liters'] * partial_fraction

                self._sim_time += partial_time
                self._fuel = max(0.0, self._fuel - partial_fuel)
                _, temp_c, humidity = self._get_weather()
                decay = self._rsl_model.calculate_decay_rate(temp_c, humidity)
                self._rsl = max(0.0, self._rsl - decay * (partial_time / 60.0))

                # Agent must re-decide
                if self._step_count >= self.max_steps:
                    self._done = True
                    self._cleanup_accidents()
                    return (
                        self._build_obs(),
                        -self._timeout_penalty,
                        False,
                        True,
                        {'arrived': False, 'rsl': self._rsl, 'fuel_pct': self._fuel / self._fuel_tank * 100},
                    )

                return (
                    self._build_obs(),
                    0.0,
                    False,
                    False,
                    {'accident_reroute': True, 'rsl': self._rsl, 'fuel_pct': self._fuel / self._fuel_tank * 100},
                )

        # No accident — complete the full journey
        self._sim_time += route['total_time_minutes']
        self._fuel = max(0.0, self._fuel - route['total_fuel_liters'])
        _, temp_c, humidity = self._get_weather()
        decay = self._rsl_model.calculate_decay_rate(temp_c, humidity)
        self._rsl = max(0.0, self._rsl - decay * (route['total_time_minutes'] / 60.0))
        self._current_node = self._destination_node

        self._done = True
        self._cleanup_accidents()

        fuel_consumed = self._fuel_at_dispatch - self._fuel
        rsl_lost = self._rsl_at_dispatch - self._rsl
        reward = (
            self._delivery_bonus
            - (rsl_lost / max(self._rsl_at_dispatch, 1.0)) * self._w_freshness
            - (fuel_consumed / max(self._fuel_at_dispatch, 1.0)) * self._w_fuel
        )
        if math.isnan(reward) or math.isinf(reward):
            reward = -10.0

        return (
            self._build_obs(),
            reward,
            True,
            False,
            {
                'arrived': True,
                'rsl': float(self._rsl),
                'fuel_pct': float(self._fuel / self._fuel_tank * 100.0),
            },
        )

    def render(self):
        pass

    def close(self):
        self._cleanup_accidents()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cleanup_accidents(self):
        for seg in self._injected_segments:
            seg.clear_accident()
        self._injected_segments.clear()

    def _build_route_pool(self, pool_size: int) -> List[Dict]:
        pool = []
        attempts = 0
        max_attempts = pool_size * 8

        while len(pool) < pool_size and attempts < max_attempts:
            attempts += 1
            src = random.choice(self._all_nodes)
            dst = random.choice(self._all_nodes)
            if src == dst:
                continue
            try:
                route = self.router.find_path(
                    src, dst,
                    truck_type_config=self._truck_type_dict,
                    load_fraction=0.5,
                    avoid_blocked=True,
                    current_time=0.0,
                )
                if route and len(route.get('nodes', [])) >= 5:
                    n_nodes = len(route['nodes'])
                    if n_nodes >= 5 and n_nodes <= 400:
                        pool.append({'nodes': route['nodes']})
            except Exception:
                pass

        if len(pool) < pool_size:
            logger.warning(f'[RoutingEnv] Built {len(pool)}/{pool_size} routes after {attempts} attempts')
        return pool

    def _build_obs(self) -> np.ndarray:
        rsl_norm = self._rsl / 100.0
        critical_flag = 1.0 if self._rsl < 70.0 else 0.0
        fuel_norm = self._fuel / self._fuel_tank
        load_norm = self._load_fraction

        # Placeholders: no planned route tracked in strategy env
        has_planned = 1.0          # always have a destination
        dist_to_dest_norm = 0.5    # placeholder — no hop tracking
        accident_ahead = 0.0       # no planned route to check

        neighbors = self.road_network.get_adjacent_nodes(self._current_node) \
            if self._current_node is not None else []

        traffic_obs = []
        accident_obs = []
        for i in range(5):
            if i < len(neighbors):
                seg_id = self.road_network.get_segment_id_between(self._current_node, neighbors[i])
                seg = self.road_network.get_segment(seg_id) if seg_id else None
                road_type = getattr(seg, 'road_type', 'secondary') if seg else 'secondary'
                jam_d = self.traffic_model.jam_density_per_lane.get(road_type, 100)
                density = float(np.clip(seg.current_traffic_density / jam_d if seg else 0.0, 0.0, 1.0))
                is_acc = 1.0 if (seg and getattr(seg, 'is_blocked', False)) else 0.0
                traffic_obs.append(density)
                accident_obs.append(is_acc)
            else:
                traffic_obs.append(0.0)
                accident_obs.append(0.0)

        # Current Zone awareness
        current_seg_id = None
        if neighbors:
            current_seg_id = self.road_network.get_segment_id_between(
                self._current_node, neighbors[0]
            )
        current_seg = self.road_network.get_segment(current_seg_id) if current_seg_id else None
        zone_map = {'OFFICE': 0.1, 'SHOPPING': 0.4, 'RESIDENTIAL': 0.7, 'HIGHWAY': 1.0}
        zone_val = zone_map.get(getattr(current_seg, 'zone_type', 'HIGHWAY'), 0.0)

        # Ripple Radar (Backpressure on 4 neighbors)
        ripple_obs = []
        for i in range(4):
            if i < len(neighbors):
                seg_id = self.road_network.get_segment_id_between(self._current_node, neighbors[i])
                ripple = self.traffic_model.current_ripples.get(seg_id, 0.0)
                n_seg = self.road_network.get_segment(seg_id) if seg_id else None
                n_road_type = getattr(n_seg, 'road_type', 'secondary') if n_seg else 'secondary'
                jam_d = self.traffic_model.jam_density_per_lane.get(n_road_type, 100)
                ripple_obs.append(min(1.0, ripple / jam_d))
            else:
                ripple_obs.append(0.0)

        hour_frac = (self._sim_time / 60.0) % 24.0
        hour_rad = hour_frac * (2.0 * math.pi / 24.0)
        time_norm = hour_frac / 24.0

        obs = np.array(
            [rsl_norm, critical_flag, fuel_norm, load_norm, dist_to_dest_norm,
             *traffic_obs, *accident_obs, accident_ahead, has_planned,
             zone_val, *ripple_obs,
             time_norm, math.sin(hour_rad), math.cos(hour_rad)],
            dtype=np.float32,
        )
        obs = np.nan_to_num(obs, nan=0.0, posinf=1.0, neginf=-1.0)
        return np.clip(obs, -1.0, 1.0)

    def _get_weather(self) -> Tuple[str, float, float]:
        if self.weather_loader is not None:
            try:
                data = self.weather_loader.get_weather_at_time(self._sim_time)
                return data['state'], data['temperature'], data['humidity']
            except Exception:
                pass
        month = int((self._sim_time / (30 * 24 * 60)) % 12) + 1
        hour = (self._sim_time / 60.0) % 24.0
        if month in [6, 7, 8, 9]:
            temp = 26.0 + 3.0 * math.cos((hour - 14) * 2 * math.pi / 24)
            humidity = 80.0
            weather = random.choices(
                ['clear', 'light_rain', 'rain', 'heavy_rain'],
                weights=[0.1, 0.3, 0.35, 0.25]
            )[0]
        else:
            temp = 28.0 + 8.0 * math.cos((hour - 14) * 2 * math.pi / 24)
            humidity = 45.0
            weather = 'clear'
        return weather, temp, humidity
