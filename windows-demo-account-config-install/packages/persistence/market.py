"""PostgreSQL public data storage; raw records retained, closed bars never silently overwritten."""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    Engine,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    select,
)
from sqlalchemy.dialects.postgresql import insert

from services.market_data.integrity import DataIntegrityError, DepthBook
from services.market_data.normalization import Candle

metadata = MetaData()
raw_events = Table(
    "market_raw_events",
    metadata,
    Column("source", String(40), primary_key=True),
    Column("digest", String(64), primary_key=True),
    Column("symbol", String(30), nullable=False),
    Column("event_type", String(40), nullable=False),
    Column("payload", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)
candles = Table(
    "market_candles",
    metadata,
    Column("source", String(40), primary_key=True),
    Column("symbol", String(30), primary_key=True),
    Column("interval", String(10), primary_key=True),
    Column("open_time", DateTime(timezone=True), primary_key=True),
    Column("close_time", DateTime(timezone=True), nullable=False),
    Column("open", Numeric, nullable=False),
    Column("high", Numeric, nullable=False),
    Column("low", Numeric, nullable=False),
    Column("close", Numeric, nullable=False),
    Column("volume", Numeric, nullable=False),
    Column("quote_volume", Numeric, nullable=False),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "low <= open AND low <= close AND high >= open AND high >= close AND low > 0",
        name="ck_market_candle_ohlc",
    ),
    CheckConstraint("volume >= 0 AND quote_volume >= 0", name="ck_market_candle_volume"),
)
books = Table(
    "market_books",
    metadata,
    Column("source", String(40), primary_key=True),
    Column("symbol", String(30), primary_key=True),
    Column("update_id", BigInteger, primary_key=True),
    Column("bid", Numeric, nullable=False),
    Column("ask", Numeric, nullable=False),
    Column("bid_quantity", Numeric, nullable=False),
    Column("ask_quantity", Numeric, nullable=False),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "bid > 0 AND ask > bid AND bid_quantity > 0 AND ask_quantity > 0",
        name="ck_market_book_spread",
    ),
)
stream_status = Table(
    "market_stream_status",
    metadata,
    Column("source", String(40), primary_key=True),
    Column("symbol", String(30), primary_key=True),
    Column("interval", String(10), primary_key=True),
    Column("checked_at", DateTime(timezone=True), nullable=False),
    Column("book_as_of", DateTime(timezone=True)),
    Column("candle_as_of", DateTime(timezone=True)),
    Column("book_received", DateTime(timezone=True)),
    Column("candle_received", DateTime(timezone=True)),
    Column("reconciliation_required", Boolean, nullable=False),
    Column("reason", String(40), nullable=False),
    Column("book_age_ms", Integer, nullable=False),
    Column("candle_age_ms", Integer, nullable=False),
    CheckConstraint("book_age_ms > 0 AND candle_age_ms > 0", name="ck_market_freshness_budget"),
)

SOURCE = "BINANCE_USDM_PUBLIC"


class MarketRepository:
    def __init__(self, bind: Engine | Connection) -> None:
        self.bind = bind

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        if isinstance(self.bind, Connection):
            with self.bind.begin_nested():
                yield self.bind
        else:
            with self.bind.begin() as connection:
                yield connection

    def archive(self, raw: str, *, symbol: str, event_type: str, received_at: datetime) -> bool:
        byte_budget = 4_194_304 if event_type == "exchangeInfo" else 1_048_576
        if len(raw.encode()) > byte_budget or event_type not in {
            "kline",
            "depthUpdate",
            "bookTicker",
            "rest_klines",
            "snapshot",
            "exchangeInfo",
        }:
            raise ValueError("Only bounded public market payloads can be archived")
        json.loads(raw)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.connection() as connection:
            result = connection.execute(
                insert(raw_events)
                .values(
                    source=SOURCE,
                    digest=digest,
                    symbol=symbol,
                    event_type=event_type,
                    payload=raw,
                    received_at=received_at,
                )
                .on_conflict_do_nothing()
                .returning(raw_events.c.digest)
            )
            return result.scalar_one_or_none() is not None

    def save_candles(self, bars: list[Candle]) -> None:
        with self.connection() as connection:
            for bar in bars:
                bar = Candle.model_validate(bar.model_dump())
                if not bar.closed:
                    raise ValueError("Only closed bars may be stored as canonical candles")
                values = bar.model_dump(exclude={"closed"}) | {"source": SOURCE}
                connection.execute(insert(candles).values(**values).on_conflict_do_nothing())
                existing = (
                    connection.execute(
                        select(candles).where(
                            candles.c.source == SOURCE,
                            candles.c.symbol == bar.symbol,
                            candles.c.interval == bar.interval,
                            candles.c.open_time == bar.open_time,
                        )
                    )
                    .mappings()
                    .one()
                )
                if any(
                    existing[name] != getattr(bar, name)
                    for name in [
                        "close_time",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "quote_volume",
                    ]
                ):
                    raise DataIntegrityError(
                        "Conflicting closed candle; explicit reconciliation review required"
                    )

    def save_book(self, book: DepthBook) -> None:
        if not book.synchronized or book.as_of is None or book.received_at is None:
            raise DataIntegrityError("Unsynchronized book must not be persisted as valid")
        bid, bid_qty = book.best_bid
        ask, ask_qty = book.best_ask
        with self.connection() as connection:
            connection.execute(
                insert(books)
                .values(
                    source=SOURCE,
                    symbol=book.symbol,
                    update_id=book.last_update_id,
                    bid=bid,
                    ask=ask,
                    bid_quantity=bid_qty,
                    ask_quantity=ask_qty,
                    as_of=book.as_of,
                    received_at=book.received_at,
                )
                .on_conflict_do_nothing()
            )

    def save_status(
        self,
        *,
        symbol: str,
        interval: str,
        now: datetime,
        book_as_of: datetime | None,
        candle_as_of: datetime | None,
        book_received: datetime | None,
        candle_received: datetime | None,
        reconciliation_required: bool,
        reason: str,
        book_age_ms: int,
        candle_age_ms: int,
    ) -> None:
        values = dict(
            source=SOURCE,
            symbol=symbol,
            interval=interval,
            checked_at=now,
            book_as_of=book_as_of,
            candle_as_of=candle_as_of,
            book_received=book_received,
            candle_received=candle_received,
            reconciliation_required=reconciliation_required,
            reason=reason,
            book_age_ms=book_age_ms,
            candle_age_ms=candle_age_ms,
        )
        with self.connection() as connection:
            statement = insert(stream_status).values(**values)
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["source", "symbol", "interval"],
                    set_={
                        k: v for k, v in values.items() if k not in {"source", "symbol", "interval"}
                    },
                )
            )

    def status(self, symbol: str, interval: str, *, now: datetime) -> dict[str, Any]:
        with self.connection() as connection:
            row = (
                connection.execute(
                    select(stream_status).where(
                        stream_status.c.source == SOURCE,
                        stream_status.c.symbol == symbol,
                        stream_status.c.interval == interval,
                    )
                )
                .mappings()
                .one_or_none()
            )
        result: dict[str, Any] = dict(
            symbol=symbol,
            interval=interval,
            as_of=None,
            observed_at=now,
            is_stale=True,
            reconciliation_required=True,
            reason="NOT_STARTED",
            orders_enabled=False,
        )
        if row is None:
            return result
        stale = bool(row["reconciliation_required"])
        for field, budget in [
            ("book_as_of", "book_age_ms"),
            ("book_received", "book_age_ms"),
            ("candle_as_of", "candle_age_ms"),
            ("candle_received", "candle_age_ms"),
        ]:
            value = row[field]
            stale = (
                stale
                or value is None
                or not timedelta(0) <= now - value <= timedelta(milliseconds=row[budget])
            )
        result.update(
            as_of=row["book_as_of"],
            candle_as_of=row["candle_as_of"],
            is_stale=stale,
            reconciliation_required=bool(row["reconciliation_required"]),
            reason=row["reason"]
            if not stale
            else (row["reason"] if row["reconciliation_required"] else "STALE"),
        )
        return result
