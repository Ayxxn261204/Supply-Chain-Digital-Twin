"""Unit tests for OrangeBatch spoilage model."""

import pytest
from datetime import datetime
from src.simulation.entities import OrangeBatch


class TestOrangeBatch:
    """Test OrangeBatch entity and spoilage modeling."""

    def _make_batch(self, batch_id="batch-001", quantity=100,
                    harvest_date=None, initial_quality=100.0, current_time=0.0):
        if harvest_date is None:
            harvest_date = datetime(2024, 1, 1)
        return OrangeBatch(batch_id, quantity, harvest_date, current_time, initial_quality)

    def test_orange_batch_initialization(self):
        """Test OrangeBatch is initialized with correct attributes."""
        harvest_date = datetime(2024, 1, 1)
        batch = self._make_batch(harvest_date=harvest_date)
        assert batch.batch_id == "batch-001"
        assert batch.quantity == 100
        assert batch.harvest_date == harvest_date
        assert batch.initial_quality == 100.0
        assert batch.current_rsl == 100.0
        assert batch.last_update_time == 0.0

    def test_rsl_degradation_over_time(self):
        """Test RSL degrades over time at constant temperature."""
        batch = self._make_batch()
        batch.update_rsl(4.0, 88.0, current_time=60.0)
        rsl_after_1h = batch.current_rsl
        batch.update_rsl(4.0, 88.0, current_time=120.0)
        rsl_after_2h = batch.current_rsl
        assert rsl_after_1h < 100.0
        assert rsl_after_2h < rsl_after_1h

    def test_higher_temperature_faster_degradation(self):
        """Test that higher temperature causes faster degradation."""
        batch_cold = self._make_batch("cold")
        batch_warm = self._make_batch("warm")
        batch_cold.update_rsl(4.0, 88.0, current_time=600.0)   # 10 hours
        batch_warm.update_rsl(30.0, 88.0, current_time=600.0)
        assert batch_warm.current_rsl < batch_cold.current_rsl

    def test_rsl_clamped_to_zero(self):
        """Test RSL is clamped to 0 (cannot go negative)."""
        batch = self._make_batch()
        batch.current_rsl = 0.5
        batch.last_update_time = 0.0
        batch.update_rsl(50.0, 95.0, current_time=100000.0)
        assert batch.current_rsl == 0.0

    def test_rsl_clamped_to_hundred(self):
        """Test RSL cannot exceed 100."""
        batch = self._make_batch()
        batch.current_rsl = 105.0
        batch.last_update_time = 0.0
        batch.update_rsl(-10.0, 88.0, current_time=1.0)
        assert batch.current_rsl <= 100.0

    def test_spoilage_detection_threshold(self):
        """Test spoilage detection when RSL reaches 0."""
        batch = self._make_batch()
        assert not batch.is_spoiled()
        batch.current_rsl = 0.0
        assert batch.is_spoiled()
        batch.current_rsl = 0.1
        assert not batch.is_spoiled()

    def test_multiple_updates_accumulate(self):
        """Multiple incremental updates should equal one large update."""
        batch1 = self._make_batch("b1")
        batch1.update_rsl(10.0, 88.0, current_time=60.0)
        batch1.update_rsl(10.0, 88.0, current_time=120.0)
        batch1.update_rsl(10.0, 88.0, current_time=180.0)

        batch2 = self._make_batch("b2")
        batch2.update_rsl(10.0, 88.0, current_time=180.0)

        assert abs(batch1.current_rsl - batch2.current_rsl) < 0.5

    def test_zero_time_elapsed_no_degradation(self):
        """No degradation when time elapsed is zero."""
        batch = self._make_batch()
        batch.update_rsl(4.0, 88.0, current_time=0.0)
        assert batch.current_rsl == 100.0
