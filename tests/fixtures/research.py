"""Synthetic research bars; exact Decimal arithmetic, never real Binance history."""

from datetime import timedelta
from decimal import Decimal

from services.market_data.normalization import Candle, utc_ms

START = utc_ms(1_788_220_800_000)  # UTC hour-aligned


def bars(count=120, start=START, price="100"):
    result = []
    for i in range(count):
        when = start + timedelta(minutes=i)
        value = Decimal(price) + Decimal(i % 7) / Decimal("100")
        result.append(
            Candle(
                symbol="BTCUSDT",
                interval="1m",
                open_time=when,
                close_time=when + timedelta(minutes=1) - timedelta(milliseconds=1),
                open=value,
                high=value + 1,
                low=value - 1,
                close=value,
                volume="10",
                quote_volume=value * 10,
                closed=True,
                as_of=when + timedelta(minutes=1) - timedelta(milliseconds=1),
                received_at=when + timedelta(minutes=1),
            )
        )
    return result


def bar(when, open="100", high="101", low="99", close="100"):
    return Candle(
        symbol="BTCUSDT",
        interval="1m",
        open_time=when,
        close_time=when + timedelta(minutes=1) - timedelta(milliseconds=1),
        open=open,
        high=high,
        low=low,
        close=close,
        volume="10",
        quote_volume="1000",
        closed=True,
        as_of=when + timedelta(minutes=1) - timedelta(milliseconds=1),
        received_at=when + timedelta(minutes=1),
    )


def directional_bars(count=480, short=False):
    result = []
    for i in range(count):
        value = Decimal("110") - Decimal(i) / 100 if short else Decimal("100") + Decimal(i) / 100
        when = START + timedelta(minutes=i)
        result.append(
            Candle(
                symbol="BTCUSDT",
                interval="1m",
                open_time=when,
                close_time=when + timedelta(minutes=1) - timedelta(milliseconds=1),
                open=value,
                high=value + Decimal("0.005"),
                low=value - Decimal("0.005"),
                close=value,
                volume="110" if i == 359 else "10",
                quote_volume=value * 10,
                closed=True,
                as_of=when + timedelta(minutes=1) - timedelta(milliseconds=1),
                received_at=when + timedelta(minutes=1),
            )
        )
    return result
