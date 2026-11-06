from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from backtest.data import MarketData
from backtest.engine import simulate
from backtest.models import (
    FundingLedger,
    FundingPoint,
    Instrument,
    ResearchCandidate,
    ResearchProfile,
)
from tests.fixtures.research import START, bar


def candidate(**changes):
    base = dict(
        candidate_id=uuid4(),
        strategy="BREAKOUT",
        symbol="BTCUSDT",
        direction="LONG",
        decision_at=START + timedelta(minutes=1),
        expires_at=START + timedelta(minutes=6),
        limit_price="100",
        stop_price="95",
        target_price="105",
        max_hold_seconds=7200,
        profile_version="research-v1",
        based_on=(("5m", START),),
    )
    return ResearchCandidate(**(base | changes))


def setup(sample, **changes):
    profile = ResearchProfile(entry_fee_bps="0", exit_fee_bps="0", slippage_bps="0", **changes)
    meta = Instrument(symbol="BTCUSDT", listed_at=START, tick_size="0.01", quantity_step="0.001")
    data = MarketData(sample, meta, profile)
    ledger = FundingLedger(
        covered_from=START, covered_until=sample[-1].open_time + timedelta(minutes=1), events=()
    )
    return profile, data, ledger


def test_signal_bar_cannot_fill_and_touch_is_not_a_fill():
    sample = [
        bar(START, high="120", low="80"),
        bar(START + timedelta(minutes=1), open="101", high="102", low="100", close="101"),
    ]
    p, d, f = setup(sample)
    result = simulate(d, p, [candidate()], f, scenario="TRADE_THROUGH")
    assert not result.trades and result.filled_entries == 0


def test_ambiguous_fill_and_barriers_choose_stop_and_never_double_subtract_costs():
    sample = [
        bar(START),
        bar(START + timedelta(minutes=1), open="100", high="110", low="90", close="101"),
    ]
    p, d, f = setup(sample)
    result = simulate(d, p, [candidate()], f, scenario="TRADE_THROUGH")
    assert len(result.trades) == 1 and result.trades[0].reason == "STOP"
    assert result.trades[0].net_pnl == Decimal("-5")
    assert result.final_equity == p.initial_capital + result.trades[0].net_pnl


def test_fill_bar_target_alone_does_not_assume_favorable_ordering():
    sample = [
        bar(START),
        bar(START + timedelta(minutes=1), open="104", high="110", low="99", close="101"),
    ]
    p, d, f = setup(sample)
    result = simulate(d, p, [candidate()], f, scenario="TRADE_THROUGH")
    assert result.trades[0].reason == "END_OF_DATA"


def test_gap_stop_uses_worse_open_plus_adverse_slippage_and_both_fees():
    sample = [
        bar(START),
        bar(START + timedelta(minutes=1), open="100", high="101", low="99", close="100"),
        bar(START + timedelta(minutes=2), open="90", high="92", low="89", close="91"),
    ]
    p = ResearchProfile(entry_fee_bps="10", exit_fee_bps="10", slippage_bps="100")
    _, d, f = setup(sample)
    d = MarketData(sample, d.instrument, p)
    result = simulate(d, p, [candidate()], f, scenario="TRADE_THROUGH")
    trade = result.trades[0]
    assert trade.exit_price == Decimal("89.10")
    assert trade.fees == Decimal("0.18910")
    assert trade.net_pnl == Decimal("-11.08910")
    assert trade.slippage_cost == Decimal("0.90")
    assert result.final_equity == p.initial_capital + trade.net_pnl


def test_never_fill_scenario_and_limit_expiry_do_not_create_market_entries():
    sample = [bar(START + timedelta(minutes=i), low="99") for i in range(10)]
    p, d, f = setup(sample)
    result = simulate(d, p, [candidate()], f, scenario="NEVER")
    assert result.expired_entries == 1 and not result.trades
    assert result.final_equity == p.initial_capital


def test_short_direction_and_adverse_funding_accounting():
    sample = [
        bar(START),
        bar(START + timedelta(minutes=1), high="101"),
        bar(START + timedelta(minutes=2), open="100", high="101", low="94", close="96"),
    ]
    p, d, _ = setup(sample)
    ledger = FundingLedger(
        covered_from=START,
        covered_until=START + timedelta(minutes=3),
        events=(FundingPoint(at=START + timedelta(minutes=2), rate="-0.001", mark_price="100"),),
    )
    order = candidate(direction="SHORT", stop_price="105", target_price="95")
    result = simulate(d, p, [order], ledger, scenario="TRADE_THROUGH")
    assert result.trades[0].funding_cost == Decimal("0.1")
    assert result.trades[0].net_pnl == Decimal("4.9")


def test_unknown_funding_coverage_and_insufficient_capital_fail_closed():
    sample = [bar(START), bar(START + timedelta(minutes=1))]
    p, d, f = setup(sample)
    short = FundingLedger(covered_from=START, covered_until=START + timedelta(minutes=1), events=())
    with pytest.raises(ValueError):
        simulate(d, p, [candidate()], short, scenario="TRADE_THROUGH")
    poor = ResearchProfile(
        initial_capital="1", entry_fee_bps="0", exit_fee_bps="0", slippage_bps="0"
    )
    result = simulate(d, poor, [candidate()], f, scenario="TRADE_THROUGH")
    assert result.rejected_entries == 1 and not result.trades


def test_duplicate_candidates_are_idempotent():
    sample = [bar(START), bar(START + timedelta(minutes=1), low="90")]
    p, d, f = setup(sample)
    signal = candidate()
    result = simulate(d, p, [signal, signal], f, scenario="TRADE_THROUGH")
    assert len(result.trades) == 1 and result.candidate_count == 1


def test_favorable_funding_is_not_credited_on_unknown_entry_ordering():
    sample = [bar(START), bar(START + timedelta(minutes=1)), bar(START + timedelta(minutes=2))]
    p, d, _ = setup(sample)
    ledger = FundingLedger(
        covered_from=START,
        covered_until=START + timedelta(minutes=3),
        events=(
            FundingPoint(
                at=START + timedelta(minutes=1, seconds=30), rate="-0.001", mark_price="100"
            ),
            FundingPoint(
                at=START + timedelta(minutes=2, seconds=30), rate="-0.001", mark_price="100"
            ),
        ),
    )
    result = simulate(d, p, [candidate()], ledger, scenario="TRADE_THROUGH")
    assert result.trades[0].funding_cost == Decimal("-0.1")
    assert result.trades[0].net_pnl == Decimal("0.1")


def test_end_of_data_fill_bar_cannot_profit_beyond_an_ambiguous_target():
    sample = [
        bar(START),
        bar(START + timedelta(minutes=1), open="100", high="111", low="99", close="110"),
    ]
    p, d, f = setup(sample)
    result = simulate(d, p, [candidate()], f, scenario="TRADE_THROUGH")
    assert result.trades[0].exit_price == Decimal("105")
    assert result.trades[0].net_pnl == Decimal("5")


def test_timeout_gap_cannot_pick_a_better_fill_than_the_existing_target():
    sample = [
        bar(START),
        bar(START + timedelta(minutes=1)),
        bar(START + timedelta(minutes=2), open="110", high="111", low="109", close="110"),
    ]
    p, d, f = setup(sample)
    result = simulate(d, p, [candidate(max_hold_seconds=60)], f, scenario="TRADE_THROUGH")
    assert result.trades[0].reason == "TIMEOUT" and result.trades[0].exit_price == Decimal("105")
