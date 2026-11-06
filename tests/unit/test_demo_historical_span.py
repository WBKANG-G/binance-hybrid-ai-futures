"""Retain oldest history without widening individual API requests."""

import pytest

from services.execution.demo_history_review import (
    HistoryBlocked,
    historical_trades_window,
    trades_window,
)
from tests.unit.test_demo_trade_history_window import Client, row

DAY = 86400000


def test_eleven_days_includes_oldest_newest_and_chunk_boundaries():
    points = [0, DAY - 1, DAY, 7 * DAY - 1, 7 * DAY, 11 * DAY]
    rows = [row(id=i, time=t) for i, t in enumerate(points)]
    client = Client(
        lambda p: [r for r in rows if int(p["startTime"]) <= r["time"] <= int(p["endTime"])]
    )
    result = historical_trades_window(client, 0, 11 * DAY)
    assert set(result) == {str(i) for i in range(len(rows))}
    intervals = sorted((int(p["startTime"]), int(p["endTime"])) for p in client.calls)
    assert intervals[0][0] == 0 and intervals[-1][1] == 11 * DAY
    assert all(right - left < DAY for left, right in intervals)
    assert all(b + 1 == c for (_, b), (c, _) in zip(intervals, intervals[1:], strict=False))


def test_existing_seven_day_contract_and_extended_31_day_ceiling():
    client = Client([])
    with pytest.raises(HistoryBlocked, match="INVALID_HISTORY_WINDOW"):
        trades_window(client, 0, 11 * DAY)
    with pytest.raises(HistoryBlocked, match="INVALID_HISTORY_WINDOW"):
        historical_trades_window(client, 0, 31 * DAY + 1)
    assert not client.calls
    assert historical_trades_window(client, 0, 31 * DAY) == {}
    assert len(client.calls) == 32


def test_global_40_call_budget_includes_all_days_and_splits():
    client = Client(lambda p: [row()] * 1000 if int(p["endTime"]) > int(p["startTime"]) else [])
    with pytest.raises(HistoryBlocked, match="HISTORY_QUERY_LIMIT"):
        historical_trades_window(client, 0, 11 * DAY)
    assert len(client.calls) == 40


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"symbol": "ETHUSDT"}, "INVALID_TRADE_IDENTITY"),
        ({"time": -1}, "INVALID_TRADE_TIME"),
        ({"price": "NaN"}, "INVALID_TRADE_NUMBER"),
    ],
)
def test_bad_record_in_oldest_day_cannot_be_hidden(updates, code):
    client = Client(
        lambda p: [row(**dict({"time": 150}, **updates))] if int(p["startTime"]) == 0 else []
    )
    with pytest.raises(HistoryBlocked, match=code):
        historical_trades_window(client, 0, 11 * DAY)
