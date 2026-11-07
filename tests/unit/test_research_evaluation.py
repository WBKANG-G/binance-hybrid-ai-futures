from datetime import timedelta

import pytest

from backtest.data import MarketData
from backtest.evaluation import chronological_folds
from backtest.models import Instrument, ResearchProfile
from backtest.synthetic import synthetic_dataset
from tests.fixtures.research import START, bars


def test_forward_folds_have_past_only_training_and_holding_period_embargo():
    profile = ResearchProfile(ema_fast=2, ema_slow=3, adx_period=2, ema_pullback=2, atr_period=2)
    meta = Instrument(symbol="BTCUSDT", listed_at=START, tick_size="0.01", quantity_step="0.001")
    data = MarketData(bars(20 * 60), meta, profile)
    folds = chronological_folds(data, profile, count=2, test_hours=2)
    embargo = timedelta(
        seconds=max(profile.trend_hold_seconds, profile.breakout_hold_seconds)
        + profile.entry_ttl_seconds
    )
    assert len(folds) == 2
    for fold in folds:
        assert fold.train_start < fold.train_end < fold.test_start < fold.test_end <= data.end
        assert fold.test_start - fold.train_end == embargo
    assert folds[1].test_start > folds[0].test_end
    with pytest.raises(ValueError):
        chronological_folds(data, profile, count=20, test_hours=24)


def test_synthetic_data_and_profile_hash_are_repeatable_and_explicitly_labeled():
    profile = ResearchProfile()
    a, ledger = synthetic_dataset(profile, days=2)
    b, other = synthetic_dataset(profile, days=2)
    assert a.digest == b.digest and ledger == other
    assert a.instrument.provenance == "SYNTHETIC"
    assert ledger.covered_from <= a.start and ledger.covered_until >= a.end
