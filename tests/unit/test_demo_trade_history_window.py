"""No cursor guessing: saturated time windows split or fail closed."""

from types import SimpleNamespace

import pytest

from services.execution.demo_history_review import HistoryBlocked, trades_window


def row(**updates):
    return dict(
        {
            "symbol": "BTCUSDT",
            "positionSide": "BOTH",
            "side": "BUY",
            "commissionAsset": "USDT",
            "id": 1,
            "orderId": 2,
            "time": 150,
            "qty": "0.001",
            "price": "100",
            "commission": "0.01",
            "realizedPnl": "0",
        },
        **updates,
    )


class Client:
    settings = SimpleNamespace(demo_trading_enabled=False)

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def request(self, method, path, params):
        assert method == "GET" and path == "/fapi/v1/userTrades"
        assert (
            params["symbol"] == "BTCUSDT" and params["limit"] == "1000" and "fromId" not in params
        )
        self.calls.append(params)
        return self.rows(params) if callable(self.rows) else self.rows


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"symbol": "ETHUSDT"}, "INVALID_TRADE_IDENTITY"),
        ({"positionSide": "LONG"}, "INVALID_TRADE_IDENTITY"),
        ({"commissionAsset": "BNB"}, "INVALID_TRADE_IDENTITY"),
        ({"id": True}, "INVALID_TRADE_IDENTITY"),
        ({"time": True}, "INVALID_TRADE_TIME"),
        ({"time": 201}, "INVALID_TRADE_TIME"),
        ({"qty": "0"}, "INVALID_TRADE_NUMBER"),
        ({"price": "NaN"}, "INVALID_TRADE_NUMBER"),
        ({"commission": "-1"}, "INVALID_TRADE_NUMBER"),
    ],
)
def test_invalid_trade_rows_block(updates, code):
    with pytest.raises(HistoryBlocked, match=code):
        trades_window(Client([row(**updates)]), 100, 200)


def test_saturated_window_is_split_without_skipping_equal_timestamps():
    data = [row(id=i, time=101 if i < 500 else 199) for i in range(1001)]

    def fetch(params):
        return [r for r in data if int(params["startTime"]) <= r["time"] <= int(params["endTime"])][
            :1000
        ]

    client = Client(fetch)
    assert len(trades_window(client, 100, 200)) == 1001
    assert len(client.calls) == 3


def test_single_millisecond_saturation_never_returns_partial_results():
    with pytest.raises(HistoryBlocked, match="TRADE_WINDOW_SATURATED"):
        trades_window(Client([row()] * 1000), 150, 150)


def test_duplicates_deduplicate_but_conflicting_identity_is_rejected():
    assert len(trades_window(Client([row(), row()]), 100, 200)) == 1
    with pytest.raises(HistoryBlocked, match="CONFLICTING_TRADE_ID"):
        trades_window(Client([row(), row(qty="0.002")]), 100, 200)


def test_history_span_and_request_budget_are_bounded():
    client = Client([])
    with pytest.raises(HistoryBlocked, match="INVALID_HISTORY_WINDOW"):
        trades_window(client, 0, 8 * 86400000)
    assert not client.calls
    full = [row()] * 1000

    def saturated(params):
        return full if int(params["endTime"]) > int(params["startTime"]) else []

    with pytest.raises(HistoryBlocked, match="HISTORY_QUERY_LIMIT"):
        trades_window(Client(saturated), 0, 86400000)


def test_unknown_private_error_text_is_not_exported():
    assert str(HistoryBlocked("private-key-url")) == "HISTORY_EVIDENCE_BLOCKED"
