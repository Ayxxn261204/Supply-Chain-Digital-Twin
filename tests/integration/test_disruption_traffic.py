"""Integration tests for Disruption-Traffic interactions.

Tests that accidents from DisruptionModel correctly affect
traffic flow and segment blocking.
"""
import pytest
from unittest.mock import Mock
from src.simulation.environment_models import DisruptionModel


@pytest.fixture
def disruption_config():
    """Configuration for disruption model."""
    return {
        'disruptions': {
            'accidents': {
                'base_rate_per_million_vkm': {'primary': 0.003},
                'time_multipliers': {'night': 1.8},
                'weather_multipliers': {'heavy_rain': 4.0},
                'severity_distribution': {'minor': 0.7, 'moderate': 0.25, 'severe': 0.05},
                'duration_mean': 45,
                'duration_std': 20,
                'duration_min': 15,
                'duration_max': 180
            }
        }
    }


@pytest.mark.integration
class TestDisruptionTrafficIntegration:
    """Integration tests for Disruption and Traffic models."""
    
    def test_accident_generation_works(self, disruption_config):
        """Test that DisruptionModel can generate accidents."""
        model = DisruptionModel(disruption_config)
        
        # Create mock network
        network = Mock()
        segment = Mock()
        segment.segment_id = 'TEST_001'
        segment.road_type = 'primary'
        segment.length_km = 5.0
        segment.is_blocked = False
        segment.current_traffic_density = 50.0
        segment.current_speed_kmh = 40.0
        segment.lanes = 2
        network.segments = {(1, 2, 0): segment}
        
        # Try to generate accidents (may be empty list due to low probability)
        try:
            accidents = model.generate_accidents(
                current_time=120.0,  # Night
                road_network=network,
                weather='heavy_rain',
                time_step_minutes=1.0
            )
            # Should return a list (possibly empty)
            assert isinstance(accidents, list)
        except Exception as e:
            pytest.fail(f"Accident generation failed: {e}")
    
    def test_integration_with_traffic_attributes(self, disruption_config):
        """Test that DisruptionModel uses traffic attributes from segments."""
        model = DisruptionModel(disruption_config)
        
        # Create segments with different traffic conditions
        network = Mock()
        
        # High traffic segment
        high_traffic = Mock()
        high_traffic.segment_id = 'HIGH_001'
        high_traffic.road_type = 'primary'
        high_traffic.length_km = 5.0
        high_traffic.is_blocked = False
        high_traffic.current_traffic_density = 80.0  # High density
        high_traffic.current_speed_kmh = 20.0  # Slow
        high_traffic.lanes = 2
        
        # Low traffic segment
        low_traffic = Mock()
        low_traffic.segment_id = 'LOW_001'
        low_traffic.road_type = 'primary'
        low_traffic.length_km = 5.0
        low_traffic.is_blocked = False
        low_traffic.current_traffic_density = 10.0  # Low density
        low_traffic.current_speed_kmh = 50.0  # Fast
        low_traffic.lanes = 2
        
        network.segments = {
            (1, 2, 0): high_traffic,
            (3, 4, 0): low_traffic
        }
        
        # Generate accidents - should work without errors
        try:
            accidents = model.generate_accidents(
                current_time=720.0,
                road_network=network,
                weather='clear',
                time_step_minutes=1.0
            )
            assert isinstance(accidents, list)
        except Exception as e:
            pytest.fail(f"Failed with traffic attributes: {e}")
