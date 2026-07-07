"""Unit tests for DisruptionModel.

Tests accident generation logic including weather effects, time-of-day impacts,
road type base rates, severity distribution, and accident clearance.
"""
import pytest
from unittest.mock import Mock, MagicMock
from src.simulation.environment_models import DisruptionModel
from src.simulation.network.road_segment import RoadSegment


@pytest.fixture
def disruption_config():
    """Configuration for disruption model tests."""
    return {
        'disruptions': {
            'accidents': {
                'base_rate_per_million_vkm': {
                    'motorway': 0.001,
                    'trunk': 0.002,
                    'primary': 0.003,
                    'secondary': 0.004,
                    'tertiary': 0.005,
                    'residential': 0.001
                },
                'time_multipliers': {
                    'day': 1.0,
                    'evening': 1.3,
                    'night': 1.8
                },
                'weather_multipliers': {
                    'clear': 1.0,
                    'light_rain': 1.5,
                    'rain': 2.5,
                    'heavy_rain': 4.0,
                    'fog': 3.5
                },
                'severity_distribution': {
                    'minor': 0.70,
                    'moderate': 0.25,
                    'severe': 0.05
                },
                'duration_mean': 45,
                'duration_std': 20,
                'duration_min': 15,
                'duration_max': 180
            }
        }
    }


@pytest.fixture
def mock_segment():
    """Create a mock road segment."""
    segment = Mock(spec=RoadSegment)
    segment.segment_id = 'TEST_SEG_001'
    segment.start_node = 1
    segment.end_node = 2
    segment.length_km = 5.0
    segment.road_type = 'primary'
    segment.speed_limit_kmh = 60
    segment.is_blocked = False
    segment.current_traffic_density = 0.5  # vehicles per km per lane
    segment.current_speed_kmh = 50.0  # current speed
    segment.lanes = 2  # number of lanes
    return segment


@pytest.fixture
def mock_road_network(mock_segment):
    """Create a mock road network with test segments."""
    network = Mock()
    network.segments = {
        (1, 2, 0): mock_segment
    }
    return network


@pytest.mark.unit
class TestDisruptionModel:
    """Test suite for DisruptionModel."""
    
    def test_model_initialization(self, disruption_config):
        """Test that DisruptionModel initializes with correct configuration."""
        model = DisruptionModel(disruption_config)
        
        # Check base rates loaded
        assert len(model.base_rates) == 6, "Should load all 6 road type base rates"
        assert model.base_rates['motorway'] == 0.001
        assert model.base_rates['primary'] == 0.003
        
        # Check multipliers loaded
        assert model.weather_multipliers['rain'] == 2.5
        assert model.weather_multipliers['fog'] == 3.5
        assert model.time_multipliers['night'] == 1.8
        
        # Check severity distribution
        assert model.severity_probs['minor'] == 0.70
        assert model.severity_probs['moderate'] == 0.25
        assert model.severity_probs['severe'] == 0.05
        assert sum(model.severity_probs.values()) == pytest.approx(1.0)
    
    def test_probability_calculation_base_case(self, disruption_config, mock_segment):
        """Test probability calculation with base parameters."""
        model = DisruptionModel(disruption_config)
        
        # Disable density effect for predictable calculation
        model.density_effect_enabled = False
        
        # Calculate probability (day, clear weather, 1 minute)
        # Formula: prob = (base_rate / 1M) * vehicle_km
        # vehicle_km = density * speed * lanes * length * time_hours
        # = 0.5 * 50 * 2 * 5.0 * (1/60) = 4.167 veh-km
        # prob = (0.003 / 1M) * 4.167 = 0.0000000125
        
        prob = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.0,  # day
            weather_mult=1.0,  # clear
            time_step_minutes=1.0
        )
        
        # Should be very small but non-zero
        assert prob > 0
        assert prob < 0.01  # Less than 1%
    
    def test_weather_multiplier_increases_probability(self, disruption_config, mock_segment):
        """Test that weather multipliers actually increase accident probability."""
        model = DisruptionModel(disruption_config)
        model.density_effect_enabled = False
        
        # Clear weather probability
        prob_clear = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.0,
            weather_mult=1.0,  # clear
            time_step_minutes=1.0
        )
        
        # Rain probability (2.5x multiplier)
        prob_rain = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.0,
            weather_mult=2.5,  # rain
            time_step_minutes=1.0
        )
        
        # Heavy rain probability (4.0x multiplier)
        prob_heavy_rain = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.0,
            weather_mult=4.0,  # heavy rain
            time_step_minutes=1.0
        )
        
        # Probabilities should scale with multipliers
        assert prob_rain == pytest.approx(prob_clear * 2.5, rel=0.01)
        assert prob_heavy_rain == pytest.approx(prob_clear * 4.0, rel=0.01)
    
    def test_time_multiplier_increases_probability(self, disruption_config, mock_segment):
        """Test that time-of-day multipliers increase accident probability."""
        model = DisruptionModel(disruption_config)
        model.density_effect_enabled = False
        
        # Day probability
        prob_day = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.0,  # day
            weather_mult=1.0,
            time_step_minutes=1.0
        )
        
        # Night probability (1.8x multiplier)
        prob_night = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.8,  # night
            weather_mult=1.0,
            time_step_minutes=1.0
        )
        
        # Night should be 1.8x more likely
        assert prob_night == pytest.approx(prob_day * 1.8, rel=0.01)
    
    def test_combined_multipliers_are_multiplicative(self, disruption_config, mock_segment):
        """Test that weather and time multipliers combine multiplicatively."""
        model = DisruptionModel(disruption_config)
        model.density_effect_enabled = False
        
        # Base case (day, clear)
        prob_base = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.0,
            weather_mult=1.0,
            time_step_minutes=1.0
        )
        
        # Night + heavy rain (1.8 * 4.0 = 7.2x)
        prob_combined = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.8,
            weather_mult=4.0,
            time_step_minutes=1.0
        )
        
        # Should be 7.2x the base probability
        assert prob_combined == pytest.approx(prob_base * 7.2, rel=0.01)
    
    def test_road_type_affects_base_rate(self, disruption_config):
        """Test that different road types have different base accident rates."""
        model = DisruptionModel(disruption_config)
        
        # Verify that residential roads are less safe than motorways
        # (Note: This is based on the configuration, not real-world necessarily)
        assert model.base_rates['tertiary'] > model.base_rates['motorway']
        assert model.base_rates['primary'] > model.base_rates['motorway']
    
    def test_probability_scales_with_time_step(self, disruption_config, mock_segment):
        """Test that probability increases linearly with time step duration."""
        model = DisruptionModel(disruption_config)
        model.density_effect_enabled = False
        
        # 1 minute time step
        prob_1min = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.0,
            weather_mult=1.0,
            time_step_minutes=1.0
        )
        
        # 10 minute time step
        prob_10min = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.0,
            weather_mult=1.0,
            time_step_minutes=10.0
        )
        
        # 10 minute step should have ~10x the probability
        assert prob_10min == pytest.approx(prob_1min * 10.0, rel=0.01)
    
    def test_probability_clamped_to_max(self, disruption_config, mock_segment):
        """Test that probability is clamped to maximum 1% per time step."""
        model = DisruptionModel(disruption_config)
        
        # Try to create absurdly high probability
        prob = model._calculate_accident_probability(
            mock_segment,
            time_mult=100.0,  # Extreme
            weather_mult=100.0,  # Extreme
            time_step_minutes=1000.0  # Long step
        )
        
        # Should be clamped to 0.01 (1%)
        assert prob <= 0.01
    
    def test_zero_speed_gives_zero_probability(self, disruption_config, mock_segment):
        """Test that stopped traffic has zero accident probability."""
        model = DisruptionModel(disruption_config)
        
        # Set speed to zero
        mock_segment.current_speed_kmh = 0.0
        
        prob = model._calculate_accident_probability(
            mock_segment,
            time_mult=1.0,
            weather_mult=1.0,
            time_step_minutes=1.0
        )
        
        # No movement = no accidents
        assert prob == 0.0
