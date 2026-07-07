"""Unit tests for OrangeBatch entity."""

import pytest
from datetime import datetime
from src.simulation.entities import OrangeBatch


def _batch(batch_id='TEST', quantity=1000.0, current_time=0.0):
    return OrangeBatch(batch_id, quantity, datetime(2024, 11, 15), current_time, 100.0)


@pytest.mark.unit
class TestOrangeBatch:

    def test_batch_initialization(self):
        batch = _batch()
        assert batch.batch_id == 'TEST'
        assert batch.quantity == 1000.0
        assert batch.current_rsl == 100.0
        assert not batch.is_spoiled()

    def test_rsl_degradation_at_optimal_conditions(self):
        batch = _batch()
        batch.update_rsl(4.0, 88.0, current_time=7 * 24 * 60)  # 7 days
        assert batch.current_rsl < 100.0
        assert batch.current_rsl > 40.0
        assert not batch.is_spoiled()

    def test_rsl_degradation_at_high_temp(self):
        cold = _batch('cold')
        hot  = _batch('hot')
        cold.update_rsl(4.0,  88.0, current_time=3 * 24 * 60)
        hot.update_rsl( 30.0, 88.0, current_time=3 * 24 * 60)
        assert hot.current_rsl < cold.current_rsl
        ratio = (100.0 - hot.current_rsl) / max(0.001, 100.0 - cold.current_rsl)
        assert ratio > 2.0

    def test_rsl_degradation_low_humidity(self):
        dry = _batch('dry')
        opt = _batch('opt')
        dry.update_rsl(20.0, 60.0, current_time=5 * 24 * 60)
        opt.update_rsl(20.0, 88.0, current_time=5 * 24 * 60)
        assert dry.current_rsl < opt.current_rsl

    def test_rsl_degradation_high_humidity(self):
        wet = _batch('wet')
        opt = _batch('opt')
        wet.update_rsl(20.0, 98.0, current_time=5 * 24 * 60)
        opt.update_rsl(20.0, 88.0, current_time=5 * 24 * 60)
        assert wet.current_rsl < opt.current_rsl

    def test_spoilage_detection(self):
        batch = _batch()
        assert not batch.is_spoiled()
        batch.update_rsl(35.0, 95.0, current_time=40 * 24 * 60)
        assert batch.is_spoiled()
        assert batch.current_rsl <= 0

    def test_rsl_never_goes_negative(self):
        batch = _batch()
        batch.update_rsl(40.0, 98.0, current_time=60 * 24 * 60)
        assert batch.current_rsl >= 0.0
        assert batch.current_rsl <= 100.0

    def test_rsl_decreases_monotonically(self):
        batch = _batch()
        prev = batch.current_rsl
        for day in range(10):
            t = 10.0 if day % 2 == 0 else 25.0
            h = 88.0 if day % 2 == 0 else 70.0
            batch.update_rsl(t, h, current_time=(day + 1) * 24 * 60)
            assert batch.current_rsl <= prev + 1e-9
            prev = batch.current_rsl

    def test_extreme_temperature_handling(self):
        cold = _batch('freeze')
        hot  = _batch('scorch')
        cold.update_rsl(0.0,  88.0, current_time=3 * 24 * 60)
        hot.update_rsl( 50.0, 88.0, current_time=3 * 24 * 60)
        assert cold.current_rsl < 100.0
        assert hot.current_rsl < 100.0
        assert hot.current_rsl < cold.current_rsl

    def test_harvest_date_tracking(self):
        harvest = datetime(2024, 11, 1, 8, 30)
        batch = OrangeBatch('DATE', 600.0, harvest, 0.0, 100.0)
        assert batch.harvest_date == harvest
