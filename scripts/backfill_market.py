"""Explicit UTC-range closed-candle backfill, with bounded pagination and no order calls."""

import argparse
import json
import re
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from integrations.binance_usdm.filters import SymbolRules
from integrations.binance_usdm.rest import BinanceREST, ExchangeUnavailable
from packages.domain.config import database_url, load_config, validate_startup
from packages.persistence.market import MarketRepository
from services.market_data.normalization import INTERVALS, candles_from_rest, epoch_ms, utc_ms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", choices=tuple(INTERVALS), required=True)
    parser.add_argument("--start", required=True, help="ISO 8601 UTC/offset timestamp")
    parser.add_argument("--end", required=True, help="Exclusive ISO 8601 timestamp")
    args = parser.parse_args()
    engine = None
    try:
        validate_startup(load_config())
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or start >= end
            or not re.fullmatch(r"[A-Z0-9]{5,30}", args.symbol)
        ):
            raise ValueError("Explicit timezone and valid range/symbol required")
        client = BinanceREST()
        SymbolRules.from_exchange_info(client.get("/fapi/v1/exchangeInfo"), args.symbol)
        now = utc_ms(client.get("/fapi/v1/time")["serverTime"])
        if end > now:
            raise ValueError("Backfill cannot request future ranges")
        rows = client.historical_klines(
            args.symbol, args.interval, start_ms=epoch_ms(start), end_ms=epoch_ms(end)
        )
        bars = candles_from_rest(rows, symbol=args.symbol, interval=args.interval, now=now)
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        repository = MarketRepository(engine)
        # Archive individual pages/bars so every raw record stays below the transport frame budget.
        for row in rows:
            repository.archive(
                json.dumps(row, default=str),
                symbol=args.symbol,
                event_type="rest_klines",
                received_at=datetime.now(UTC),
            )
        repository.save_candles(bars)
        print(
            f"Stored {len(bars)} closed public candles; collection status is not changed to ready by backfill."
        )
        return 0
    except (ValueError, SQLAlchemyError, ExchangeUnavailable):
        print(
            "Public backfill failed; check UTC range, symbol eligibility, schema and public connectivity."
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
