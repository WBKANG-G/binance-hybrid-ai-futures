from datetime import timedelta
from decimal import Decimal

import pytest

from backtest.data import MarketData
from backtest.models import Instrument, ResearchProfile
from services.strategies.indicators import adx, atr, confirmed_pivots, ema
from services.strategies.rules import RuleStrategy
from tests.fixtures.research import START, bar, bars


def instrument():
    return Instrument(symbol="BTCUSDT", listed_at=START, tick_size="0.01", quantity_step="0.001")


def test_ema_has_sma_seed_and_no_future_revision():
    values = list(map(Decimal, [1, 2, 3, 4, 5]))
    assert ema(values, 3) == (None, None, Decimal(2), Decimal(3), Decimal(4))
    assert ema(values + [Decimal(999)], 3)[:5] == ema(values, 3)


def test_atr_includes_gap_and_wilder_smoothing():
    sample = [
        bar(START, "100", "101", "99", "100"),
        bar(START + timedelta(minutes=1), "103", "104", "102", "103"),
        bar(START + timedelta(minutes=2), "105", "106", "104", "105"),
    ]
    assert atr(sample, 2) == (None, Decimal(3), Decimal(3))
    assert all(
        value == 0
        for value in adx([bar(START + timedelta(minutes=i)) for i in range(20)], 3)[5:]
        if value is not None
    )


def test_pivot_waits_for_two_right_confirmations():
    sample = [
        bar(START + timedelta(minutes=i), low=str(x)) for i, x in enumerate([99, 98, 95, 98, 99])
    ]
    assert confirmed_pivots(sample[:4], 2, 2)[-1][0] is None
    assert confirmed_pivots(sample, 2, 2)[-1][0] == Decimal(95)


def test_multi_timeframe_join_releases_only_complete_closed_groups():
    data = MarketData(bars(65), instrument(), ResearchProfile())
    assert data.index("5m", START + timedelta(minutes=4, seconds=59)) == -1
    assert data.frames["5m"][
        data.index("5m", START + timedelta(minutes=5))
    ].bar.close_time < START + timedelta(minutes=5)
    assert data.index("1h", START + timedelta(minutes=59)) == -1
    assert data.index("1h", START + timedelta(hours=1)) == 0
    assert len(data.frames["1h"]) == 1


def test_captured_policy_waits_for_actual_receive_timestamp():
    sample = bars(5)
    sample[-1] = sample[-1].model_copy(update={"received_at": START + timedelta(minutes=10)})
    profile = ResearchProfile(availability="captured")
    data = MarketData(sample, instrument(), profile)
    assert data.index("5m", START + timedelta(minutes=5)) == -1
    assert data.index("5m", START + timedelta(minutes=10)) == 0


@pytest.mark.parametrize("mutation", ["duplicate", "gap", "open", "prelisting", "reverse"])
def test_bad_research_input_is_rejected(mutation):
    sample = bars(10)
    meta = instrument()
    if mutation == "duplicate":
        sample.insert(3, sample[2])
    if mutation == "gap":
        sample.pop(3)
    if mutation == "open":
        sample[2] = sample[2].model_copy(update={"closed": False})
    if mutation == "prelisting":
        meta = meta.model_copy(update={"listed_at": START + timedelta(minutes=1)})
    if mutation == "reverse":
        sample.reverse()
    with pytest.raises(ValueError):
        MarketData(sample, meta, ResearchProfile())


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "LIVE"},
        {"entry_fee_bps": 0.1},
        {"quantity": "NaN"},
        {"slippage_bps": "-1"},
        {"ema_fast": 200},
        {"ai_can_set_risk": True},
    ],
)
def test_research_profile_is_not_a_live_or_unvalidated_configuration(change):
    with pytest.raises(ValueError):
        ResearchProfile(**change)


def test_strategy_warmup_and_future_prefix_invariance():
    profile = ResearchProfile(
        ema_fast=3, ema_slow=5, adx_period=2, ema_pullback=3, atr_period=2, breakout_lookback=3
    )
    early = bars(2 * 60)
    later = early + bars(60, start=START + timedelta(hours=2), price="1000")
    a = RuleStrategy(MarketData(early, instrument(), profile), profile, "BREAKOUT")
    b = RuleStrategy(MarketData(later, instrument(), profile), profile, "BREAKOUT")
    assert a.evaluate(START + timedelta(hours=1)) is None
    assert a.evaluate(START + timedelta(hours=2)) == b.evaluate(START + timedelta(hours=2))


@pytest.mark.parametrize("name", ["TREND_FOLLOWING", "BREAKOUT"])
@pytest.mark.parametrize("short", [False, True])
def test_positive_long_short_signals_are_prefix_stable_and_use_prior_ranges(name, short):
    from tests.fixtures.research import directional_bars

    profile = ResearchProfile(
        ema_fast=3, ema_slow=5, adx_period=2, ema_pullback=3, atr_period=2, breakout_lookback=3
    )
    sample = directional_bars(480, short=short)
    moment = START + timedelta(hours=6)
    prefix = MarketData(sample[:360], instrument(), profile)
    full = MarketData(sample, instrument(), profile)
    first = RuleStrategy(prefix, profile, name).evaluate(moment)
    second = RuleStrategy(full, profile, name).evaluate(moment)
    assert first is not None and first == second
    assert first.direction == ("SHORT" if short else "LONG")
    assert all(known_at < moment for _, known_at in first.based_on)
    altered = sample[:360] + bars(120, start=moment, price="1000")
    assert (
        RuleStrategy(MarketData(altered, instrument(), profile), profile, name).evaluate(moment)
        == first
    )


def test_hourly_adx_on_pure_rising_bars_is_one_hundred_after_seed():
    sample = [
        bar(
            START + timedelta(minutes=i),
            open=str(100 + i),
            high=str(101 + i),
            low=str(99 + i),
            close=str(100 + i),
        )
        for i in range(12)
    ]
    values = adx(sample, 3)
    assert all(value is None for value in values[:5])
    assert all(value == 100 for value in values[5:])


def test_entry_cost_filter_rejects_nonpositive_target_after_declared_fixed_costs():
    from tests.fixtures.research import directional_bars

    profile = ResearchProfile(
        ema_fast=3,
        ema_slow=5,
        adx_period=2,
        ema_pullback=3,
        atr_period=2,
        breakout_lookback=3,
        entry_fee_bps="1000",
        exit_fee_bps="1000",
    )
    data = MarketData(directional_bars(), instrument(), profile)
    assert RuleStrategy(data, profile, "BREAKOUT").evaluate(START + timedelta(hours=6)) is None
