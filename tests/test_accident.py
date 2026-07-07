import pytest
from src.simulation.entities import Accident

class TestAccident:
    def test_initialization(self):
        """Test accident initialization."""
        accident = Accident(
            accident_id="ACC001",
            segment_id="SEG123",
            start_time=100.0,
            duration_minutes=45.0,
            severity="moderate"
        )
        
        assert accident.accident_id == "ACC001"
        assert accident.segment_id == "SEG123"
        assert accident.start_time == 100.0
        assert accident.duration_minutes == 45.0
        assert accident.severity == "moderate"
        assert accident.end_time == 145.0
        assert accident.is_active is False

    def test_activation(self):
        """Test activation state."""
        accident = Accident("ACC001", "SEG123", 100, 45, "minor")
        accident.activate()
        assert accident.is_active is True

    def test_clearing(self):
        """Test clearing state."""
        accident = Accident("ACC001", "SEG123", 100, 45, "minor")
        accident.activate()
        accident.clear()
        assert accident.is_active is False
