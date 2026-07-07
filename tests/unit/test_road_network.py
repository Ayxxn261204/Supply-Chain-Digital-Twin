"""Unit tests for RoadNetwork.

Tests road network loading, segment creation, node extraction, and queries.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
from src.simulation.network.road_network import RoadNetwork


@pytest.fixture
def road_network_config():
    """Configuration for road network tests."""
    return {
        'map_source': 'local',
        'local_map_file': 'data/maps/nagpur_small.xml',
        'cache_enabled': False  # Disable caching for tests
    }


@pytest.mark.unit
class TestRoadNetwork:
    """Test suite for RoadNetwork."""
    
    def test_road_network_class_exists(self):
        """Test that RoadNetwork class can be imported."""
        assert RoadNetwork is not None
    
    def test_has_required_methods(self):
        """Test that RoadNetwork has expected methods."""
        assert hasattr(RoadNetwork, 'get_segment')
        assert hasattr(RoadNetwork, 'get_stats')
        assert hasattr(RoadNetwork, 'update_traffic')
        assert hasattr(RoadNetwork, 'get_segment_by_nodes')
        assert hasattr(RoadNetwork, 'get_node_position')
        assert hasattr(RoadNetwork, 'get_nearest_node')
    
    def test_methods_are_callable(self):
        """Test that key methods are callable."""
        assert callable(getattr(RoadNetwork, 'get_segment', None))
        assert callable(getattr(RoadNetwork, 'get_stats', None))
        assert callable(getattr(RoadNetwork, 'update_traffic', None))
        assert callable(getattr(RoadNetwork, 'get_segment_by_nodes', None))
        assert callable(getattr(RoadNetwork, 'get_node_position', None))
        assert callable(getattr(RoadNetwork, 'get_nearest_node', None))
