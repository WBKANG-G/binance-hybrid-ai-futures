from datetime import timedelta
from decimal import Decimal

import pytest

from services.market_data.integrity import CandleTracker, DataIntegrityError, DepthBook
from services.market_data.normalization import candle_from_ws, candles_from_rest, utc_ms
from tests.fixtures.binance import BASE, depth, kline, snapshot


def test_candle_is_decimal_utc_and_only_closed_bars_advance_tracker():
    candle = candle_from_ws(kline(), received_at=utc_ms(BASE + 60001))
    assert candle.close == Decimal("100.2")
    assert candle.as_of == utc_ms(BASE + 60000)
    tracker = CandleTracker("BTCUSDT", "1m")
    assert (
        tracker.ingest(candle_from_ws(kline(closed=False), received_at=utc_ms(BASE + 1))) == "OPEN"
    )
    assert tracker.last is None
    assert tracker.ingest(candle) == "ACCEPTED"
    assert tracker.ingest(candle) == "DUPLICATE"
    assert tracker.last == candle


def test_gap_reverse_and_closed_candle_correction_require_reconciliation():
    for message in [kline(BASE + 120000), kline(BASE - 60000), kline(close="100.3")]:
        tracker = CandleTracker("BTCUSDT", "1m")
        tracker.ingest(candle_from_ws(kline(), received_at=utc_ms(BASE + 60001)))
        with pytest.raises(DataIntegrityError):
            tracker.ingest(candle_from_ws(message, received_at=utc_ms(BASE + 300000)))
        assert tracker.reconciliation_required


def test_freshness_rejects_future_data_and_stale_receive_time():
    tracker = CandleTracker("BTCUSDT", "1m")
    tracker.ingest(candle_from_ws(kline(), received_at=utc_ms(BASE + 60001)))
    assert tracker.freshness(utc_ms(BASE + 60002), max_age=timedelta(seconds=5)).is_stale is False
    assert tracker.freshness(utc_ms(BASE + 70000), max_age=timedelta(seconds=5)).is_stale is True
    assert tracker.freshness(utc_ms(BASE), max_age=timedelta(seconds=5)).is_stale is True
    tracker.disconnect()
    assert tracker.freshness(utc_ms(BASE + 60002), max_age=timedelta(seconds=5)).is_stale


def test_rest_bars_exclude_open_bar_and_reconciliation_is_contiguous():
    rows = [
        [BASE, "100", "101", "99", "100.2", "2", BASE + 59999, "200.1", 1, "0", "0", "0"],
        [BASE + 60000, "100", "101", "99", "100.2", "2", BASE + 119999, "200.1", 1, "0", "0", "0"],
    ]
    bars = candles_from_rest(rows, symbol="BTCUSDT", interval="1m", now=utc_ms(BASE + 60001))
    assert len(bars) == 1 and bars[0].closed
    tracker = CandleTracker("BTCUSDT", "1m")
    tracker.disconnect()
    tracker.reconcile(bars)
    assert not tracker.reconciliation_required
    with pytest.raises(DataIntegrityError):
        tracker.reconcile([])


def test_depth_snapshot_needs_bridge_then_enforces_previous_update_id():
    book = DepthBook("BTCUSDT")
    book.load_snapshot(snapshot())
    assert not book.synchronized
    assert book.apply(depth(), received_at=utc_ms(BASE + 1)) == "ACCEPTED"
    assert book.synchronized and book.best_bid == (Decimal("100"), Decimal("3"))
    assert book.apply(depth(), received_at=utc_ms(BASE + 2)) == "DUPLICATE"
    assert book.apply(depth(102, 103, 101), received_at=utc_ms(BASE + 3)) == "ACCEPTED"
    with pytest.raises(DataIntegrityError):
        book.apply(depth(104, 105, 999), received_at=utc_ms(BASE + 4))
    assert not book.synchronized


def test_depth_absolute_quantities_zero_removal_and_crossed_book_fail_closed():
    book = DepthBook("BTCUSDT")
    book.load_snapshot(snapshot())
    book.apply(depth(), received_at=utc_ms(BASE + 1))
    event = depth(102, 103, 101)
    event["b"] = [["100", "0"], ["99", "7"]]
    book.apply(event, received_at=utc_ms(BASE + 2))
    assert book.best_bid == (Decimal("99"), Decimal("7"))
    bad = depth(104, 105, 103)
    bad["b"] = [["102", "1"]]
    with pytest.raises(DataIntegrityError):
        book.apply(bad, received_at=utc_ms(BASE + 3))
    assert not book.synchronized


@pytest.mark.parametrize("field,value", [("c", 100.2), ("h", "98"), ("x", "false")])
def test_malformed_financial_values_and_ohlc_are_rejected(field, value):
    raw = kline()
    raw["k"][field] = value
    with pytest.raises(ValueError):
        candle_from_ws(raw, received_at=utc_ms(BASE + 60001))


def test_snapshot_output_time_does_not_break_the_initial_sequence_bridge():
    book = DepthBook("BTCUSDT")
    raw = snapshot()
    raw["E"] = BASE + 10
    book.load_snapshot(raw)
    assert book.apply(depth(event_ms=BASE), received_at=utc_ms(BASE + 11)) == "ACCEPTED"
    assert book.synchronized
