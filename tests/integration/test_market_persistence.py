import json
import os
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, inspect, select, text

from packages.persistence.market import MarketRepository, candles, metadata, raw_events
from services.market_data.integrity import DataIntegrityError
from services.market_data.normalization import candle_from_ws, utc_ms
from tests.fixtures.binance import BASE, kline

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL TEST_DATABASE_URL required"
)


@pytest.fixture
def migrated_connection():
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    schema = "phase1_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        try:
            yield connection, config
        finally:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def test_migration_round_trip_and_model_drift(migrated_connection):
    connection, config = migrated_connection
    assert set(inspect(connection).get_table_names()) >= {
        "market_candles",
        "market_raw_events",
        "market_books",
        "market_stream_status",
    }
    assert compare_metadata(MigrationContext.configure(connection), metadata) == []
    command.downgrade(config, "base")
    assert "market_candles" not in inspect(connection).get_table_names()
    command.upgrade(config, "head")
    assert "market_candles" in inspect(connection).get_table_names()


def test_raw_and_candle_idempotency_exact_decimal_and_conflict(migrated_connection):
    connection, _ = migrated_connection
    repo = MarketRepository(connection)
    raw = json.dumps(kline())
    bar = candle_from_ws(
        kline(close="100.123456789012345678901234"), received_at=utc_ms(BASE + 60001)
    )
    assert repo.archive(raw, symbol="BTCUSDT", event_type="kline", received_at=bar.received_at)
    assert not repo.archive(raw, symbol="BTCUSDT", event_type="kline", received_at=bar.received_at)
    repo.save_candles([bar])
    repo.save_candles([bar])
    assert connection.execute(select(func.count()).select_from(raw_events)).scalar_one() == 1
    assert connection.execute(select(func.count()).select_from(candles)).scalar_one() == 1
    assert connection.execute(select(candles.c.close)).scalar_one() == bar.close
    changed = bar.model_copy(update={"close": bar.close + Decimal("0.1")})
    with pytest.raises(DataIntegrityError):
        repo.save_candles([changed])
    assert connection.execute(select(candles.c.close)).scalar_one() == bar.close


def test_status_ages_after_writer_stops_and_disconnect_is_explicit(migrated_connection):
    connection, _ = migrated_connection
    repo = MarketRepository(connection)
    now = utc_ms(BASE + 60001)
    repo.save_status(
        symbol="BTCUSDT",
        interval="1m",
        now=now,
        book_as_of=now,
        candle_as_of=utc_ms(BASE + 59999),
        book_received=now,
        candle_received=now,
        reconciliation_required=False,
        reason="READY",
        book_age_ms=5000,
        candle_age_ms=65000,
    )
    assert not repo.status("BTCUSDT", "1m", now=now)["is_stale"]
    assert repo.status("BTCUSDT", "1m", now=now + timedelta(seconds=6))["is_stale"]
    repo.save_status(
        symbol="BTCUSDT",
        interval="1m",
        now=now,
        book_as_of=None,
        candle_as_of=None,
        book_received=None,
        candle_received=None,
        reconciliation_required=True,
        reason="DISCONNECTED",
        book_age_ms=5000,
        candle_age_ms=65000,
    )
    assert repo.status("BTCUSDT", "1m", now=now)["reconciliation_required"]
    assert repo.status("ETHUSDT", "1m", now=now)["is_stale"]


def test_collector_pipeline_preserves_raw_and_requires_resync_after_gap(migrated_connection):
    from integrations.binance_usdm.rest import BinanceREST, RESTResponse
    from services.market_data.collector import MarketCollector
    from tests.fixtures.binance import depth, exchange_info, snapshot

    connection, _ = migrated_connection
    repository = MarketRepository(connection)
    now = utc_ms(BASE + 60001)

    def transport(path, params):
        payload = {
            "/fapi/v1/time": {"serverTime": BASE + 60001},
            "/fapi/v1/exchangeInfo": exchange_info(),
            "/fapi/v1/depth": snapshot(),
            "/fapi/v1/klines": [
                [BASE, "100", "101", "99", "100.2", "2", BASE + 59999, "200.1", 1, "0", "0", "0"]
            ],
        }[path]
        return RESTResponse(200, json.dumps(payload).encode(), {})

    collector = MarketCollector(BinanceREST(transport), repository, symbol="BTCUSDT", interval="1m")
    collector.reconcile("market", now=now)
    collector.reconcile("public", now=now)
    assert repository.status("BTCUSDT", "1m", now=now)["is_stale"]
    event = json.dumps(depth(event_ms=BASE + 60001))
    collector.consume(event, now=now)
    collector.consume(event, now=now)
    assert collector.ready_observed and collector.accepted == 1
    assert not repository.status("BTCUSDT", "1m", now=now)["is_stale"]
    bad = json.dumps(depth(102, 103, 999, event_ms=BASE + 60002))
    with pytest.raises(DataIntegrityError):
        collector.consume(bad, now=utc_ms(BASE + 60002))
    assert repository.status("BTCUSDT", "1m", now=now)["reconciliation_required"]
    assert (
        connection.execute(
            select(func.count()).select_from(raw_events).where(raw_events.c.payload == bad)
        ).scalar_one()
        == 1
    )
    collector.reconcile("market", now=now)
    collector.reconcile("public", now=now)
    collector.consume(event, now=now)
    assert not repository.status("BTCUSDT", "1m", now=now)["is_stale"]
    collector.disconnect("public", now=now)
    assert repository.status("BTCUSDT", "1m", now=now)["is_stale"]
