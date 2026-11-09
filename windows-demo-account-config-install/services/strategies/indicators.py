"""Prefix-stable Decimal indicators with explicit seeds and confirmed pivots."""

from collections.abc import Sequence
from decimal import Decimal, localcontext

from services.market_data.normalization import Candle


def ema(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    if period < 2:
        raise ValueError("Period must be >=2")
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    with localcontext() as context:
        context.prec = 50
        previous = sum(values[:period], Decimal(0)) / period
        result[period - 1] = previous
        alpha = Decimal(2) / (period + 1)
        for i in range(period, len(values)):
            previous = alpha * values[i] + (1 - alpha) * previous
            result[i] = previous
    return tuple(result)


def wilder(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    if period < 2:
        raise ValueError("Period must be >=2")
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    with localcontext() as context:
        context.prec = 50
        previous = sum(values[:period], Decimal(0)) / period
        result[period - 1] = previous
        for i in range(period, len(values)):
            previous = (previous * (period - 1) + values[i]) / period
            result[i] = previous
    return tuple(result)


def true_ranges(bars: Sequence[Candle]) -> tuple[Decimal, ...]:
    return tuple(
        max(bar.high - bar.low, abs(bar.high - bars[i - 1].close), abs(bar.low - bars[i - 1].close))
        if i
        else bar.high - bar.low
        for i, bar in enumerate(bars)
    )


def atr(bars: Sequence[Candle], period: int) -> tuple[Decimal | None, ...]:
    return wilder(true_ranges(bars), period)


def adx(bars: Sequence[Candle], period: int) -> tuple[Decimal | None, ...]:
    if period < 2:
        raise ValueError("Period must be >=2")
    result: list[Decimal | None] = [None] * len(bars)
    if len(bars) < 2:
        return tuple(result)
    plus = []
    minus = []
    for before, current in zip(bars, bars[1:], strict=False):
        up = current.high - before.high
        down = before.low - current.low
        plus.append(up if up > down and up > 0 else Decimal(0))
        minus.append(down if down > up and down > 0 else Decimal(0))
    ranges = wilder(true_ranges(bars)[1:], period)
    plus_s = wilder(plus, period)
    minus_s = wilder(minus, period)
    values = []
    with localcontext() as context:
        context.prec = 50
        for i in range(period - 1, len(ranges)):
            tr, p, m = ranges[i], plus_s[i], minus_s[i]
            if tr is None or p is None or m is None:
                continue
            total = p + m
            values.append(Decimal(0) if total == 0 else 100 * abs(p - m) / total)
        smooth = wilder(values, period)
    for i, value in enumerate(smooth):
        if period + i < len(result):
            result[period + i] = value
    return tuple(result)


def confirmed_pivots(
    bars: Sequence[Candle], left: int = 2, right: int = 2
) -> tuple[tuple[Decimal | None, Decimal | None], ...]:
    if min(left, right) < 1:
        raise ValueError("Pivot confirmations required")
    result = []
    low = None
    high = None
    for known in range(len(bars)):
        candidate = known - right
        if candidate >= left:
            neighbors = [
                bars[i] for i in range(candidate - left, candidate + right + 1) if i != candidate
            ]
            if all(bars[candidate].low < b.low for b in neighbors):
                low = bars[candidate].low
            if all(bars[candidate].high > b.high for b in neighbors):
                high = bars[candidate].high
        result.append((low, high))
    return tuple(result)
