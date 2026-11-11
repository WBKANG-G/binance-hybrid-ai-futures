"""Normalize public payloads into exact financial values and UTC timestamps."""

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field, model_validator

from packages.domain.config import ImmutableModel, NonNegativeDecimal, PositiveDecimal
from packages.domain.contracts import Symbol, UTCDateTime

INTERVALS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def interval_ms(interval: str) -> int:
    if interval not in INTERVALS:
        raise ValueError("Unsupported fixed interval")
    return INTERVALS[interval]


def utc_ms(value: int) -> datetime:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Expected integer timestamp milliseconds")
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)


def epoch_ms(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("Timezone required")
    return (value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)


class Candle(ImmutableModel):
    symbol: Symbol
    interval: str
    open_time: UTCDateTime
    close_time: UTCDateTime
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: NonNegativeDecimal
    quote_volume: NonNegativeDecimal
    closed: bool = Field(strict=True)
    as_of: UTCDateTime
    received_at: UTCDateTime

    @model_validator(mode="after")
    def valid_bar(self) -> "Candle":
        step = interval_ms(self.interval)
        if epoch_ms(self.open_time) % step or self.close_time - self.open_time != timedelta(
            milliseconds=step - 1
        ):
            raise ValueError("Candle time boundaries invalid")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("Invalid OHLC range")
        if self.closed and self.as_of < self.close_time:
            raise ValueError("Closed bar precedes close time")
        return self

    @property
    def key(self) -> tuple[str, str, datetime]:
        return (self.symbol, self.interval, self.open_time)

    def financial_content(self) -> tuple[object, ...]:
        return (
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.quote_volume,
            self.closed,
        )


def candle_from_ws(raw: dict[str, Any], *, received_at: datetime) -> Candle:
    if (
        raw.get("e") != "kline"
        or not isinstance(raw.get("k"), dict)
        or raw.get("s") != raw["k"].get("s")
    ):
        raise ValueError("Kline envelope mismatch")
    k = raw["k"]
    return Candle(
        symbol=raw["s"],
        interval=k["i"],
        open_time=utc_ms(k["t"]),
        close_time=utc_ms(k["T"]),
        open=k["o"],
        high=k["h"],
        low=k["l"],
        close=k["c"],
        volume=k["v"],
        quote_volume=k["q"],
        closed=k["x"],
        as_of=utc_ms(raw["E"]),
        received_at=received_at,
    )


def candles_from_rest(
    rows: list[list[Any]], *, symbol: str, interval: str, now: datetime
) -> list[Candle]:
    result = []
    for row in rows:
        if len(row) < 8:
            raise ValueError("Malformed historical bar")
        close_time = utc_ms(row[6])
        if close_time >= now:
            continue
        result.append(
            Candle(
                symbol=symbol,
                interval=interval,
                open_time=utc_ms(row[0]),
                close_time=close_time,
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
                quote_volume=row[7],
                closed=True,
                as_of=close_time,
                received_at=now,
            )
        )
    return result


class Freshness(ImmutableModel):
    as_of: UTCDateTime | None
    observed_at: UTCDateTime
    is_stale: bool
    reconciliation_required: bool


class BookTicker(ImmutableModel):
    symbol: Symbol
    update_id: int = Field(strict=True, ge=0)
    bid: PositiveDecimal
    bid_quantity: NonNegativeDecimal
    ask: PositiveDecimal
    ask_quantity: NonNegativeDecimal
    as_of: UTCDateTime
    received_at: UTCDateTime

    @model_validator(mode="after")
    def not_crossed(self) -> "BookTicker":
        if self.bid >= self.ask:
            raise ValueError("Crossed ticker")
        return self


def ticker_from_ws(raw: dict[str, Any], *, received_at: datetime) -> BookTicker:
    if raw.get("e") != "bookTicker":
        raise ValueError("Wrong ticker type")
    return BookTicker(
        symbol=raw["s"],
        update_id=raw["u"],
        bid=raw["b"],
        bid_quantity=raw["B"],
        ask=raw["a"],
        ask_quantity=raw["A"],
        as_of=utc_ms(raw["E"]),
        received_at=received_at,
    )
