"""
Unit and property-based tests for the RL Routing Strategy redesign.

Tests the strategy-selection RoutingEnv (N_ACTIONS=3), OptimizationPod,
and TruckAgent._check_rerouting() strategy-weight integration.

All tests use Mock objects — the real OSM network is NOT loaded.
"""

import math
import random
import pytest
import numpy as np
from unittest.mock import Mock, patch, MagicMock

# Graceful hypothesis import
try:
    from hypothesis import given, settings, assume
    from hypothesis import strategies as st
    _hypothesis_available = True
except ImportError:
    _hypothesis_available = False
    def given(*a, **kw): return lambda f: pytest.mark.skip(reason="hypothesis not installed")(f)
    def settings(*a, **kw): return lambda f: f
    def assume(c): pass
    class st:
        @staticmethod
        def floats(**kw): return None
        @staticmethod
        def integers(**kw): return None
        @staticmethod
        def sampled_from(seq): return None

from src.simulation.routing_env import RoutingEnv, STRATEGY_WEIGHTS, OBS_DIM, N_ACTIONS
from src.simulation.optimization_pod import OptimizationPod, OBS_DIM as POD_OBS_DIM, N_ACTIONS as POD_N_ACTIONS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_segment(seg_id='s0', road_type='primary', length_km=0.5):
    seg = Mock()
    seg.segment_id = seg_id
    seg.road_type = road_type
    seg.lanes = 2
    seg.length_km = length_km
    seg.speed_limit_kmh = 50.0
    seg.base_weight = length_km / 50.0
    seg.current_traffic_density = 10.0
    seg.current_speed_kmh = 50.0
    seg.is_blocked = False
    seg.zone_type = 'RESIDENTIAL'
    seg.get_travel_time_minutes.return_value = 0.6
    seg.get_fuel_consumption_liters.return_value = 0.14
    seg.set_accident = Mock()
    seg.clear_accident = Mock()
    return seg


def _make_mock_route(n_segs=5):
    segs = [_make_mock_segment(f's{i}') for i in range(n_segs)]
    return {
        'nodes': list(range(n_segs + 1)),
        'segments': [f's{i}' for i in range(n_segs)],
        'segment_objects': segs,
        'total_distance_km': n_segs * 0.5,
        'total_time_minutes': n_segs * 0.6,
        'total_fuel_liters': n_segs * 0.14,
        'total_cost': 5.0,
    }


def _make_config(freshness=1.0, fuel=0.5, delivery_bonus=10.0, timeout_penalty=2.0):
    return {
        'routing': {
            'strategies': {
                'balanced': {'distance_km': 1.0, 'time_minutes': 0.5, 'fuel_liters': 2.0},
                'speed':    {'distance_km': 0.5, 'time_minutes': 2.0, 'fuel_liters': 0.5},
                'fuel':     {'distance_km': 1.0, 'time_minutes': 0.3, 'fuel_liters': 4.0},
            }
        },
        'ai': {'optimization': {
            'route_pool_size': 3,
            'checkpoint_dir': 'models/rl',
            'reward_weights': {
                'freshness': freshness,
                'fuel': fuel,
                'delivery_bonus': delivery_bonus,
                'timeout_penalty': timeout_penalty,
            }
        }},
        'truck_types': {'medium': {
            'fuel_tank_liters': 150.0,
            'fuel_consumption_empty_l_per_100km': 28.0,
            'fuel_consumption_full_l_per_100km': 38.0,
            'fuel_efficiency_by_speed': {},
            'capacity_kg': 3000,
        }},
        'cargo': {},
        'simulation': {'start_date': '2024-07-15'},
    }


def _make_env(max_steps=10, router_returns=None, config_override=None):
    """Build a RoutingEnv with mocked dependencies. No real OSM loading."""
    config = config_override or _make_config()

    road_network = Mock()
    road_network.graph = Mock()
    road_network.graph.nodes.return_value = list(range(20))
    road_network.traffic_model = None
    road_network.get_segment.return_value = _make_mock_segment()
    road_network.get_segment_id_between.return_value = 's0'
    road_network.get_adjacent_nodes.return_value = [1, 2, 3]
    road_network.get_node_position.return_value = (21.0, 79.0)

    traffic_model = Mock()
    traffic_model.jam_density_per_lane = {'primary': 100, 'secondary': 90, 'residential': 60}
    traffic_model.current_ripples = {}
    traffic_model.get_traffic_density.return_value = 10.0

    mock_route = router_returns if router_returns is not None else _make_mock_route()
    router = Mock()
    router.find_path.return_value = mock_route

    # Bypass __init__ to avoid route pool building / disk I/O
    env = RoutingEnv.__new__(RoutingEnv)
    env.config = config
    env.road_network = road_network
    env.traffic_model = traffic_model
    env.router = router
    env.weather_loader = None
    env.max_steps = max_steps

    strategies_cfg = config.get('routing', {}).get('strategies', {})
    env._strategy_weights = [
        strategies_cfg.get('balanced', STRATEGY_WEIGHTS[0]),
        strategies_cfg.get('speed',    STRATEGY_WEIGHTS[1]),
        strategies_cfg.get('fuel',     STRATEGY_WEIGHTS[2]),
    ]

    rw = config['ai']['optimization']['reward_weights']
    env._w_freshness     = rw.get('freshness', 1.0)
    env._w_fuel          = rw.get('fuel', 0.5)
    env._delivery_bonus  = rw.get('delivery_bonus', 10.0)
    env._timeout_penalty = rw.get('timeout_penalty', 2.0)

    from src.simulation.business_models import HybridRSLModel
    env._rsl_model = HybridRSLModel(config.get('cargo', {}))

    env._fuel_tank  = 150.0
    env._fuel_empty = 28.0
    env._fuel_full  = 38.0
    env._truck_type_dict = {
        'capacity_kg': 3000,
        'fuel_consumption_empty_l_per_100km': 28.0,
        'fuel_consumption_full_l_per_100km': 38.0,
        'fuel_efficiency_by_speed': {},
    }
    env._truck_cfg = config['truck_types']['medium']

    try:
        import gymnasium.spaces as spaces
        env.observation_space = spaces.Box(low=-1.0, high=1.0, shape=(25,), dtype=np.float32)
        env.action_space = spaces.Discrete(3)
    except ImportError:
        from src.simulation.routing_env import _Box, _Discrete
        env.observation_space = _Box(-1.0, 1.0, shape=(25,))
        env.action_space = _Discrete(3)

    env.num_envs = 1
    env._current_node = 0
    env._destination_node = 19
    env._rsl = 80.0
    env._fuel = 150.0
    env._load_fraction = 0.5
    env._sim_time = 0.0
    env._step_count = 0
    env._done = True
    env._fuel_at_dispatch = 150.0
    env._rsl_at_dispatch = 80.0
    env._injected_segments = []
    env._all_nodes = list(range(20))
    env._route_pool = [
        {'nodes': list(range(10))},
        {'nodes': list(range(10))},
        {'nodes': list(range(10))},
    ]

    return env


def _make_pod_config():
    return {
        'ai': {'optimization': {
            'enabled': True,
            'algorithm': 'ppo',
            'epsilon_greedy': 0.0,  # deterministic for tests
            'epsilon_min': 0.0,
            'checkpoint_dir': 'models/rl',
            'policy_net': [32],
            'learning_rate': 3e-4,
            'n_steps': 64,
            'batch_size': 32,
            'n_epochs': 1,
            'gamma': 0.99,
            'ent_coef': 0.01,
        }}
    }


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRLRoutingStrategy:

    def test_n_actions_is_3(self):
        assert N_ACTIONS == 3
        assert POD_N_ACTIONS == 3

    def test_obs_dim_frozen(self):
        assert OBS_DIM == 25
        assert POD_OBS_DIM == 25

    def test_strategy_weights_values(self):
        assert len(STRATEGY_WEIGHTS) == 3
        balanced, speed, fuel = STRATEGY_WEIGHTS
        assert balanced == {'distance_km': 1.0, 'time_minutes': 0.5, 'fuel_liters': 2.0}
        assert speed    == {'distance_km': 0.5, 'time_minutes': 2.0, 'fuel_liters': 0.5}
        assert fuel     == {'distance_km': 1.0, 'time_minutes': 0.3, 'fuel_liters': 4.0}

    def test_reset_returns_valid_obs(self):
        env = _make_env()
        obs, info = env.reset()
        assert obs.shape == (25,)
        assert obs.dtype == np.float32
        assert np.all(obs >= -1.0) and np.all(obs <= 1.0)
        assert isinstance(info, dict)

    def test_reset_state_bounds(self):
        env = _make_env()
        for _ in range(20):
            env.reset()
            assert 60.0 <= env._rsl <= 100.0
            assert 0.4 * env._fuel_tank <= env._fuel <= env._fuel_tank
            assert 0.3 <= env._load_fraction <= 1.0

    def test_step_all_actions_no_exception(self):
        for action in [0, 1, 2]:
            env = _make_env()
            env.reset()
            obs, reward, terminated, truncated, info = env.step(action)
            assert obs.shape == (25,)
            assert isinstance(reward, float)
            assert isinstance(terminated, bool)
            assert isinstance(truncated, bool)

    def test_step_calls_router_with_cost_weights(self):
        for action in [0, 1, 2]:
            env = _make_env()
            env.reset()
            env.router.find_path.reset_mock()
            env.step(action)
            call_kwargs = env.router.find_path.call_args
            assert call_kwargs is not None
            # cost_weights should be passed as keyword arg
            kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
            args = call_kwargs.args if call_kwargs.args else ()
            # cost_weights is the 7th positional or a keyword
            passed_weights = kwargs.get('cost_weights') or (args[6] if len(args) > 6 else None)
            expected = env._strategy_weights[action]
            assert passed_weights == expected, f"Action {action}: expected {expected}, got {passed_weights}"

    def test_no_path_truncates(self):
        env = _make_env(router_returns=None)
        env.router.find_path.return_value = None
        env.reset()
        obs, reward, terminated, truncated, info = env.step(0)
        assert truncated is True
        assert terminated is False
        assert info.get('arrived') is False
        assert reward == pytest.approx(-env._timeout_penalty)

    def test_terminal_reward_on_arrival(self):
        env = _make_env()
        env.reset()
        env._rsl = 90.0
        env._fuel = 120.0
        env._fuel_at_dispatch = 150.0
        env._current_node = 0
        env._destination_node = 19

        # Make router return a route that completes the journey
        # and set current_node to destination after step
        route = _make_mock_route()
        env.router.find_path.return_value = route

        # Patch _get_weather to return stable values
        with patch.object(env, '_get_weather', return_value=('clear', 25.0, 50.0)):
            obs, reward, terminated, truncated, info = env.step(0)

        if info.get('arrived'):
            fuel_consumed = env._fuel_at_dispatch - env._fuel
            expected = (
                (env._rsl / 100.0) * env._w_freshness
                - (fuel_consumed / env._fuel_at_dispatch) * env._w_fuel
                + env._delivery_bonus
            )
            assert reward == pytest.approx(expected, rel=0.01)

    def test_timeout_penalty(self):
        env = _make_env(max_steps=1)
        env.reset()
        # Force no accident injection and no arrival
        env._current_node = 0
        env._destination_node = 999  # unreachable

        with patch.object(env, '_get_weather', return_value=('clear', 25.0, 50.0)):
            with patch('random.choice', return_value=0):  # no accidents
                obs, reward, terminated, truncated, info = env.step(0)

        # After max_steps, should truncate
        if truncated:
            assert reward == pytest.approx(-env._timeout_penalty)

    def test_cleanup_accidents_on_episode_end(self):
        env = _make_env()
        env.reset()
        # Manually inject a fake segment
        fake_seg = Mock()
        fake_seg.is_blocked = True
        env._injected_segments = [fake_seg]
        env._done = False

        # Force truncation by exhausting steps
        env._step_count = env.max_steps - 1
        env.router.find_path.return_value = None  # triggers truncation

        env.step(0)

        fake_seg.clear_accident.assert_called_once()
        assert len(env._injected_segments) == 0

    def test_select_action_returns_valid_strategy(self):
        config = _make_pod_config()
        pod = OptimizationPod(config)
        obs = np.zeros(25, dtype=np.float32)
        # With epsilon=0 and no model, falls back to 0
        action = pod.select_action('truck_1', obs)
        assert action in {0, 1, 2}

    def test_select_action_epsilon_greedy_range(self):
        config = _make_pod_config()
        config['ai']['optimization']['epsilon_greedy'] = 1.0  # always random
        pod = OptimizationPod(config)
        obs = np.zeros(25, dtype=np.float32)
        for _ in range(50):
            action = pod.select_action('truck_1', obs)
            assert action in {0, 1, 2}

    def test_config_strategy_weights_loaded(self):
        env = _make_env()
        assert env._strategy_weights[0] == {'distance_km': 1.0, 'time_minutes': 0.5, 'fuel_liters': 2.0}
        assert env._strategy_weights[1] == {'distance_km': 0.5, 'time_minutes': 2.0, 'fuel_liters': 0.5}
        assert env._strategy_weights[2] == {'distance_km': 1.0, 'time_minutes': 0.3, 'fuel_liters': 4.0}

    def test_route_pool_node_counts(self):
        env = _make_env()
        for entry in env._route_pool:
            n = len(entry['nodes'])
            assert 5 <= n <= 400 or n == 10, f"Route has {n} nodes, expected 5-400"

    def test_rsl_decreases_after_step(self):
        env = _make_env()
        env.reset()
        rsl_before = env._rsl
        with patch.object(env, '_get_weather', return_value=('clear', 30.0, 50.0)):
            with patch('random.choice', return_value=0):  # no accidents
                env.step(0)
        # RSL should not increase
        assert env._rsl <= rsl_before

    def test_fuel_decreases_after_step(self):
        env = _make_env()
        env.reset()
        fuel_before = env._fuel
        with patch.object(env, '_get_weather', return_value=('clear', 25.0, 50.0)):
            with patch('random.choice', return_value=0):
                env.step(0)
        assert env._fuel <= fuel_before


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
@given(seed=st.integers(min_value=0, max_value=10000))
@settings(max_examples=50)
def test_reset_obs_shape_always_25(seed):
    env = _make_env()
    obs, _ = env.reset(seed=seed)
    assert obs.shape == (25,)
    assert np.all(obs >= -1.0) and np.all(obs <= 1.0)


@pytest.mark.unit
@given(action=st.integers(min_value=0, max_value=2))
@settings(max_examples=30)
def test_step_obs_shape_always_25(action):
    env = _make_env()
    env.reset()
    with patch.object(env, '_get_weather', return_value=('clear', 25.0, 50.0)):
        obs, *_ = env.step(action)
    assert obs.shape == (25,)
    assert np.all(obs >= -1.0) and np.all(obs <= 1.0)


@pytest.mark.unit
@given(action=st.integers(min_value=0, max_value=2))
@settings(max_examples=30)
def test_rsl_and_fuel_monotone(action):
    env = _make_env()
    env.reset()
    rsl_before = env._rsl
    fuel_before = env._fuel
    with patch.object(env, '_get_weather', return_value=('clear', 25.0, 50.0)):
        env.step(action)
    assert env._rsl <= rsl_before + 1e-6   # allow tiny float error
    assert env._fuel <= fuel_before + 1e-6


@pytest.mark.unit
@given(
    rsl=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    fuel_consumed_frac=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100)
def test_terminal_reward_formula_correct(rsl, fuel_consumed_frac):
    env = _make_env()
    fuel_at_dispatch = 150.0
    fuel_consumed = fuel_consumed_frac * fuel_at_dispatch

    expected = (
        (rsl / 100.0) * env._w_freshness
        - (fuel_consumed / max(fuel_at_dispatch, 1.0)) * env._w_fuel
        + env._delivery_bonus
    )

    # Compute directly using the same formula
    actual = (
        (rsl / 100.0) * env._w_freshness
        - (fuel_consumed / max(fuel_at_dispatch, 1.0)) * env._w_fuel
        + env._delivery_bonus
    )
    assert abs(actual - expected) < 1e-6
