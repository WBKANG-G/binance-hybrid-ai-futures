"""Deterministic data integrity gates. A broken stream requires a new REST snapshot."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from integrations.binance_usdm.filters import number
from services.market_data.normalization import Candle, Freshness, interval_ms, utc_ms


class DataIntegrityError(ValueError):
    pass


class CandleTracker:
    def __init__(self, symbol: str, interval: str) -> None:
        interval_ms(interval)
        self.symbol = symbol
        self.interval = interval
        self.last: Candle | None = None
        self.reconciliation_required = False
        self.history: dict[datetime, Candle] = {}

    def disconnect(self) -> None:
        self.reconciliation_required = True

    def ingest(self, candle: Candle) -> Literal["OPEN", "ACCEPTED", "DUPLICATE"]:
        if candle.symbol != self.symbol or candle.interval != self.interval:
            self.disconnect()
            raise DataIntegrityError("Unexpected candle subscription")
        if not candle.closed:
            return "OPEN"
        previous = self.history.get(candle.open_time)
        if previous is not None:
            if previous.financial_content() == candle.financial_content():
                return "DUPLICATE"
            self.disconnect()
            raise DataIntegrityError("Closed candle correction requires reconciliation")
        if self.reconciliation_required:
            raise DataIntegrityError("Candle reconciliation required")
        if self.last is not None and candle.open_time - self.last.open_time != timedelta(
            milliseconds=interval_ms(self.interval)
        ):
            self.disconnect()
            raise DataIntegrityError("Candle gap or reverse ordering")
        self.history[candle.open_time] = candle
        if len(self.history) > 2000:
            del self.history[min(self.history)]
        self.last = candle
        return "ACCEPTED"

    def reconcile(self, candles: list[Candle]) -> None:
        self.disconnect()
        if not candles:
            raise DataIntegrityError("No closed bars for reconciliation")
        candidate = CandleTracker(self.symbol, self.interval)
        for candle in candles:
            if not candle.closed:
                raise DataIntegrityError("Reconciliation needs closed candles")
            candidate.ingest(candle)
        self.last = candidate.last
        self.history = candidate.history
        self.reconciliation_required = False

    def freshness(self, now: datetime, *, max_age: timedelta) -> Freshness:
        if max_age <= timedelta(0):
            raise ValueError("Freshness budget must be explicit and positive")
        last = self.last
        stale = (
            last is None
            or self.reconciliation_required
            or not timedelta(0) <= now - last.as_of <= max_age
            or not timedelta(0) <= now - last.received_at <= max_age
        )
        return Freshness(
            as_of=last.as_of if last else None,
            observed_at=now,
            is_stale=stale,
            reconciliation_required=self.reconciliation_required,
        )


class DepthBook:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.last_update_id: int | None = None
        self.synchronized = False
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.as_of: datetime | None = None
        self.received_at: datetime | None = None

    def disconnect(self) -> None:
        self.synchronized = False
        self.last_update_id = None
        self.bids.clear()
        self.asks.clear()
        self.as_of = None
        self.received_at = None

    @staticmethod
    def levels(rows: list[list[Any]]) -> dict[Decimal, Decimal]:
        if len(rows) > 10000:
            raise DataIntegrityError("Too many depth levels")
        levels = {}
        for row in rows:
            if len(row) != 2:
                raise DataIntegrityError("Malformed depth level")
            levels[number(row[0])] = number(row[1], allow_zero=True)
        return levels

    @staticmethod
    def validate(bids: dict[Decimal, Decimal], asks: dict[Decimal, Decimal]) -> None:
        if not bids or not asks or max(bids) >= min(asks):
            raise DataIntegrityError("Empty or crossed order book")

    def load_snapshot(self, raw: dict[str, Any]) -> None:
        self.disconnect()
        identifier = raw["lastUpdateId"]
        if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 0:
            raise DataIntegrityError("Invalid snapshot update ID")
        bids = {p: q for p, q in self.levels(raw["bids"]).items() if q > 0}
        asks = {p: q for p, q in self.levels(raw["asks"]).items() if q > 0}
        self.validate(bids, asks)
        self.bids = bids
        self.asks = asks
        self.last_update_id = identifier
        self.as_of = utc_ms(raw["E"])

    def apply(
        self, raw: dict[str, Any], *, received_at: datetime
    ) -> Literal["ACCEPTED", "DUPLICATE"]:
        try:
            if (
                raw.get("e") != "depthUpdate"
                or raw.get("s") != self.symbol
                or self.last_update_id is None
            ):
                raise DataIntegrityError("Snapshot/subscription required")
            first, last, previous = raw["U"], raw["u"], raw["pu"]
            if (
                any(
                    not isinstance(v, int) or isinstance(v, bool) or v < 0
                    for v in [first, last, previous]
                )
                or first > last
            ):
                raise DataIntegrityError("Invalid depth sequence")
            if last < self.last_update_id or (self.synchronized and last == self.last_update_id):
                return "DUPLICATE"
            if not self.synchronized and not first <= self.last_update_id <= last:
                raise DataIntegrityError("Depth snapshot bridge missing")
            if self.synchronized and previous != self.last_update_id:
                raise DataIntegrityError("Depth previous update ID mismatch")
            bids = self.bids.copy()
            asks = self.asks.copy()
            for target, key in [(bids, "b"), (asks, "a")]:
                for price, quantity in self.levels(raw[key]).items():
                    if quantity == 0:
                        target.pop(price, None)
                    else:
                        target[price] = quantity
            self.validate(bids, asks)
            event_time = utc_ms(raw["E"])
            if self.synchronized and self.as_of is not None and event_time < self.as_of:
                raise DataIntegrityError("Depth event time reversed")
            self.bids = bids
            self.asks = asks
            self.last_update_id = last
            self.synchronized = True
            self.as_of = event_time
            self.received_at = received_at
            return "ACCEPTED"
        except (KeyError, TypeError, ValueError):
            self.disconnect()
            raise DataIntegrityError("Depth integrity failed; REST snapshot required") from None

    @property
    def best_bid(self) -> tuple[Decimal, Decimal]:
        if not self.synchronized:
            raise DataIntegrityError("Book not synchronized")
        price = max(self.bids)
        return (price, self.bids[price])

    @property
    def best_ask(self) -> tuple[Decimal, Decimal]:
        if not self.synchronized:
            raise DataIntegrityError("Book not synchronized")
        price = min(self.asks)
        return (price, self.asks[price])

    def freshness(self, now: datetime, *, max_age: timedelta) -> Freshness:
        if max_age <= timedelta(0):
            raise ValueError("Freshness budget must be positive")
        stale = not self.synchronized or self.as_of is None or self.received_at is None
        if self.as_of is not None and self.received_at is not None:
            stale = (
                stale
                or not timedelta(0) <= now - self.as_of <= max_age
                or not timedelta(0) <= now - self.received_at <= max_age
            )
        return Freshness(
            as_of=self.as_of,
            observed_at=now,
            is_stale=stale,
            reconciliation_required=not self.synchronized,
        )
