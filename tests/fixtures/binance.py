"""Synthetic fixtures shaped from official docs; no account or live market data."""

from copy import deepcopy

BASE = 1_800_000_000_000  # minute-aligned UTC epoch milliseconds
INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "contractType": "PERPETUAL",
            "quoteAsset": "USDT",
            "marginAsset": "USDT",
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.1",
                    "maxPrice": "1000000",
                    "tickSize": "0.1",
                },
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.001",
                    "maxQty": "1000",
                    "stepSize": "0.001",
                },
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
                {"filterType": "PERCENT_PRICE", "multiplierUp": "1.1", "multiplierDown": "0.9"},
            ],
        }
    ]
}


def kline(open_ms=BASE, event_ms=None, closed=True, close="100.2"):
    return {
        "e": "kline",
        "E": event_ms or open_ms + 60_000,
        "s": "BTCUSDT",
        "k": {
            "t": open_ms,
            "T": open_ms + 59_999,
            "s": "BTCUSDT",
            "i": "1m",
            "o": "100",
            "h": "101",
            "l": "99",
            "c": close,
            "v": "2",
            "q": "200.1",
            "x": closed,
        },
    }


def depth(first=99, last=101, previous=98, event_ms=BASE):
    return {
        "e": "depthUpdate",
        "E": event_ms,
        "T": event_ms,
        "s": "BTCUSDT",
        "U": first,
        "u": last,
        "pu": previous,
        "b": [["100", "3"]],
        "a": [["101", "2"]],
    }


def snapshot():
    return {
        "lastUpdateId": 100,
        "E": BASE,
        "T": BASE,
        "bids": [["100", "1"]],
        "asks": [["101", "1"]],
    }


def exchange_info():
    return deepcopy(INFO)
