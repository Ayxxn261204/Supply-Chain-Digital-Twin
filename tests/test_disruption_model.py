import pytest
from unittest.mock import MagicMock, patch
from src.simulation.environment_models import DisruptionModel

class TestDisruptionModel:
    @pytest.fixture
    def config(self):
        return {
            'disruptions': {
                'accidents': {
                    'base_rate_per_million_vkm': {'motorway': 1.0},
                    'time_multipliers': {'day': 1.0},
                    'weather_multipliers': {'clear': 1.0},
                    'density_effect_enabled': False
                },
                'traffic': {
                    'jam_density_per_lane': {'motorway': 100}
                }
            }
        }

    @pytest.fixture
    def mock_segment(self):
        segment = MagicMock()
        segment.road_type = 'motorway'
        segment.length_km = 1.0
        segment.lanes = 2
        segment.current_traffic_density = 20
        segment.current_speed_kmh = 60
        segment.is_blocked = False
        segment.segment_id = "SEG001"
        return segment

    @pytest.fixture
    def mock_network(self, mock_segment):
        network = MagicMock()
        network.segments = {"SEG001": mock_segment}
        return network

    def test_initialization(self, config):
        """Test model initialization."""
        model = DisruptionModel(config)
        assert model.base_rates['motorway'] == 1.0

    def test_accident_probability_calculation(self, config, mock_segment):
        """Test probability calculation logic."""
        model = DisruptionModel(config)
        
        # Calculate manually:
        # Rate = 1.0 (base) * 1.0 (time) * 1.0 (weather) = 1.0
        # VKM = 20 (density) * 60 (speed) * 2 (lanes) * 1.0 (len) * (1/60) (time) = 40 vkm
        # Prob = (1.0 / 1,000,000) * 40 = 0.00004
        
        prob = model._calculate_accident_probability(
            mock_segment, time_mult=1.0, weather_mult=1.0, time_step_minutes=1.0
        )
        
        assert prob == pytest.approx(0.00004)

    def test_generate_accidents(self, config, mock_network):
        """Test accident generation loop."""
        model = DisruptionModel(config)
        
        # Force high probability to ensure accident generation
        with patch.object(model, '_calculate_accident_probability', return_value=0.99):
            accidents = model.generate_accidents(current_time=100, road_network=mock_network)
            
            assert len(accidents) > 0
            assert accidents[0].segment_id == "SEG001"
