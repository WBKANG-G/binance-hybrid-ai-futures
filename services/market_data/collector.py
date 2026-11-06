"""Public market ingestion; every reconnect replays REST before trusting streaming data."""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from integrations.binance_usdm.filters import SymbolRules
from integrations.binance_usdm.rest import BinanceREST
from integrations.binance_usdm.streams import (
    Connector,
    public_connect,
    run_reconnecting,
    stream_url,
)
from packages.persistence.market import MarketRepository
from services.market_data.integrity import CandleTracker, DataIntegrityError, DepthBook
from services.market_data.normalization import (
    BookTicker,
    candle_from_ws,
    candles_from_rest,
    interval_ms,
    ticker_from_ws,
    utc_ms,
)


class MarketCollector:
    def __init__(
        self,
        rest: BinanceREST,
        repository: MarketRepository,
        *,
        symbol: str,
        interval: str,
        book_age_ms: int = 5000,
        candle_age_ms: int | None = None,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.rest = rest
        self.repository = repository
        self.candles = CandleTracker(symbol, interval)
        self.candles.disconnect()
        self.book = DepthBook(symbol)
        self.ticker: BookTicker | None = None
        self.book_age_ms = book_age_ms
        self.candle_age_ms = candle_age_ms or interval_ms(interval) + 5000
        if min(self.book_age_ms, self.candle_age_ms) <= 0:
            raise ValueError("Freshness budgets must be positive")
        self.accepted = 0
        self.ready_observed = False
        self.rules: SymbolRules | None = None

    def persist_status(self, now: datetime, reason: str = "READY") -> None:
        candle = self.candles.last
        book_state = self.book.freshness(now, max_age=timedelta(milliseconds=self.book_age_ms))
        candle_state = self.candles.freshness(
            now, max_age=timedelta(milliseconds=self.candle_age_ms)
        )
        recovering = book_state.reconciliation_required or candle_state.reconciliation_required
        if not book_state.is_stale and not candle_state.is_stale:
            self.ready_observed = True
        elif reason == "READY":
            reason = "RECONCILING" if recovering else "STALE"
        self.repository.save_status(
            symbol=self.symbol,
            interval=self.interval,
            now=now,
            book_as_of=self.book.as_of,
            candle_as_of=candle.as_of if candle else None,
            book_received=self.book.received_at,
            candle_received=candle.received_at if candle else None,
            reconciliation_required=recovering,
            reason=reason,
            book_age_ms=self.book_age_ms,
            candle_age_ms=self.candle_age_ms,
        )

    def reconcile(self, route: Literal["public", "market"], *, now: datetime) -> None:
        self.disconnect(route, now=now, reason="RECONNECTING")
        if route == "market":
            info = self.rest.get("/fapi/v1/exchangeInfo")
            self.rules = SymbolRules.from_exchange_info(info, self.symbol)
            self.repository.archive(
                json.dumps(info, default=str),
                symbol=self.symbol,
                event_type="exchangeInfo",
                received_at=now,
            )
            server_now = utc_ms(self.rest.get("/fapi/v1/time")["serverTime"])
            rows = self.rest.get(
                "/fapi/v1/klines", {"symbol": self.symbol, "interval": self.interval, "limit": "3"}
            )
            self.repository.archive(
                json.dumps(rows, default=str),
                symbol=self.symbol,
                event_type="rest_klines",
                received_at=now,
            )
            bars = candles_from_rest(
                rows, symbol=self.symbol, interval=self.interval, now=server_now
            )
            self.repository.save_candles(bars)
            self.candles.reconcile(bars)
        else:
            snapshot = self.rest.get("/fapi/v1/depth", {"symbol": self.symbol, "limit": "1000"})
            self.repository.archive(
                json.dumps(snapshot, default=str),
                symbol=self.symbol,
                event_type="snapshot",
                received_at=now,
            )
            self.book.load_snapshot(snapshot)
        self.persist_status(now)

    def consume(self, raw: str, *, now: datetime) -> None:
        try:
            envelope = json.loads(raw)
            if not isinstance(envelope, dict):
                raise DataIntegrityError("Malformed market envelope")
            data = envelope.get("data", envelope)
            if not isinstance(data, dict) or data.get("s") != self.symbol:
                raise DataIntegrityError("Unexpected market subscription")
            event = data.get("e")
            if event not in {"kline", "depthUpdate", "bookTicker"}:
                raise DataIntegrityError("Unexpected public event type")
            self.repository.archive(raw, symbol=self.symbol, event_type=event, received_at=now)
            if event == "kline":
                bar = candle_from_ws(data, received_at=now)
                if self.candles.ingest(bar) == "ACCEPTED":
                    self.repository.save_candles([bar])
                    self.accepted += 1
            elif event == "depthUpdate":
                if self.book.apply(data, received_at=now) == "ACCEPTED":
                    self.repository.save_book(self.book)
                    self.accepted += 1
            else:
                ticker = ticker_from_ws(data, received_at=now)
                if self.ticker is None or ticker.update_id > self.ticker.update_id:
                    if self.ticker is not None and ticker.as_of < self.ticker.as_of:
                        raise DataIntegrityError("Ticker time reversed")
                    self.ticker = ticker
            self.persist_status(now)
        except (KeyError, TypeError, ValueError):
            self.candles.disconnect()
            self.book.disconnect()
            self.persist_status(now, "INTEGRITY_ERROR")
            raise DataIntegrityError(
                "Invalid public market event; resynchronization required"
            ) from None

    def disconnect(
        self, route: Literal["public", "market"], *, now: datetime, reason: str = "DISCONNECTED"
    ) -> None:
        if route == "market":
            self.candles.disconnect()
        else:
            self.book.disconnect()
            self.ticker = None
        self.persist_status(now, reason)


async def collect_for(
    collector: MarketCollector, seconds: int, *, connector: Connector = public_connect
) -> None:
    if not 1 <= seconds <= 86400:
        raise ValueError("Collector duration must be within 1..86400 seconds")
    lock = asyncio.Lock()

    async def serialized(operation: Callable[[], None]) -> None:
        async with lock:
            task = asyncio.create_task(asyncio.to_thread(operation))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Finish an in-flight DB/REST operation before final disconnect invalidates state.
                await task
                raise

    async def run_route(route: Literal["public", "market"], streams: list[str]) -> None:
        async def reconcile() -> None:
            await serialized(lambda: collector.reconcile(route, now=datetime.now(UTC)))

        async def consume(raw: str) -> None:
            await serialized(lambda: collector.consume(raw, now=datetime.now(UTC)))

        async def disconnect() -> None:
            await serialized(lambda: collector.disconnect(route, now=datetime.now(UTC)))

        url = stream_url(streams)
        if not isinstance(url, str):
            raise ValueError("Public URL expected")
        await run_reconnecting(
            url, reconcile=reconcile, consume=consume, disconnect=disconnect, connect=connector
        )

    tasks = [
        asyncio.create_task(
            run_route(
                "public",
                [
                    f"{collector.symbol.lower()}@depth@100ms",
                    f"{collector.symbol.lower()}@bookTicker",
                ],
            )
        ),
        asyncio.create_task(
            run_route("market", [f"{collector.symbol.lower()}@kline_{collector.interval}"])
        ),
    ]
    try:
        async with asyncio.timeout(seconds):
            await asyncio.gather(*tasks)
    except TimeoutError:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
