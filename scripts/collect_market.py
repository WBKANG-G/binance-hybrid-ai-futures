"""Bounded public-data collection. Never loads exchange keys or transmits account/order requests."""

import argparse
import asyncio
import re

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from integrations.binance_usdm.rest import BinanceREST, ExchangeUnavailable
from packages.domain.config import database_url, load_config, validate_startup
from packages.persistence.market import MarketRepository
from services.market_data.collector import MarketCollector, collect_for
from services.market_data.normalization import INTERVALS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", choices=tuple(INTERVALS), default="1m")
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    engine = None
    try:
        validate_startup(load_config())
        if not re.fullmatch(r"[A-Z0-9]{5,30}", args.symbol) or not 1 <= args.seconds <= 86400:
            raise ValueError("Invalid collection arguments")
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        collector = MarketCollector(
            BinanceREST(), MarketRepository(engine), symbol=args.symbol, interval=args.interval
        )
        asyncio.run(collect_for(collector, args.seconds))
        if not collector.ready_observed or collector.accepted == 0:
            print("Collection did not obtain synchronized public data; status remains untrusted.")
            return 2
        print(
            f"Collected {collector.accepted} accepted public updates; synchronized data was observed. Collector stopped; status is now untrusted."
        )
        return 0
    except (ValueError, SQLAlchemyError, ExchangeUnavailable):
        print(
            "Public-data collection failed. Check schema, configuration and public network access; orders remain disabled."
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
