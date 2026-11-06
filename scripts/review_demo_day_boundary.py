"""Collect read-only DEMO income evidence. Never writes a daily loss baseline."""

import getpass
import json
import os
import warnings
from pathlib import Path

import yaml
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from integrations.binance_usdm.demo import DemoClient, DemoFailure, DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials as session_credentials
from scripts.review_demo_recovery import blocked
from services.execution.demo_day_review import DayReviewBlocked, review_day


def main() -> None:
    if (
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
        or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
    ):
        blocked("DAY_EVIDENCE", "LIVE_CONTEXT_FORBIDDEN")
    stage = "CREDENTIAL_INPUT"
    engine = None
    try:
        warnings.simplefilter("error", getpass.GetPassWarning)
        credentials = session_credentials(prompt=True)
        stage = "CONFIGURATION"
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        client = DemoClient(cfg, credentials)
        stage = "CLOCK_CHECK"
        client.sync_clock()
        stage = "DATABASE_READ"
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        account = "DEMO-WINDOWS-ISOLATED-BTC-V1"
        before = store.read(account)
        stage = "DAY_EVIDENCE"
        report = review_day(store, client, before, account_id=account)
        if store.read(account) != before:
            raise DayReviewBlocked("DATABASE_CHANGED_DURING_REVIEW")
        print(json.dumps(report))
    except DayReviewBlocked as error:
        blocked(stage, str(error))
    except getpass.GetPassWarning:
        blocked(stage, "HIDDEN_INPUT_UNAVAILABLE")
    except DemoFailure:
        blocked(stage, "DEMO_QUERY_OR_SNAPSHOT_FAILED")
    except SQLAlchemyError:
        blocked(stage, "DATABASE_ACCESS_FAILED")
    except (ValueError, EOFError):
        blocked(stage, "INPUT_OR_CONFIGURATION_INVALID")
    finally:
        os.environ.pop("BINANCE_DEMO_API_KEY", None)
        os.environ.pop("BINANCE_DEMO_API_SECRET", None)
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
