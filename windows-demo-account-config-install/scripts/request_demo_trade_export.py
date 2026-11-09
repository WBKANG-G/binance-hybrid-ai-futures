"""Plan, explicitly request once, or inspect an existing DEMO trade export job."""

import argparse
import getpass
import json
import os
import warnings
from pathlib import Path

import yaml
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from integrations.binance_usdm.demo import DemoClient, DemoCredentials, DemoFailure, DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.review_demo_recovery import blocked
from services.execution.demo_trade_export import ExportBlocked, plan, request_export, status_export

ACCOUNT_ID = "DEMO-WINDOWS-ISOLATED-BTC-V1"
JOURNAL = Path("/app/demo-export-state/request.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--request-export", action="store_true")
    action.add_argument("--status-existing", action="store_true")
    args = parser.parse_args()
    if (
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
        or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
    ):
        blocked("EXPORT_CONTEXT", "LIVE_CONTEXT_FORBIDDEN")
    stage = "DATABASE_READ"
    engine = None
    try:
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        before = store.read(ACCOUNT_ID)
        if not args.request_export and not args.status_existing:
            report = {
                "export": "PLAN_ONLY_NO_EXPORT_REQUEST",
                **plan(before, store.now(), ACCOUNT_ID),
                "creation_limit": "FIVE_PER_MONTH_SHARED_WITH_UI",
                "journal_file": str(JOURNAL),
                "database_changed": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
                "recovery_eligible": False,
            }
        else:
            stage = "CREDENTIAL_INPUT"
            warnings.simplefilter("error", getpass.GetPassWarning)
            os.environ["BINANCE_DEMO_API_KEY"] = getpass.getpass("DEMO API Key (hidden): ")
            os.environ["BINANCE_DEMO_API_SECRET"] = getpass.getpass("DEMO Secret (hidden): ")
            stage = "CONFIGURATION"
            cfg = DemoSettings.model_validate(
                yaml.load(
                    Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
                )
            ).model_copy(update={"demo_trading_enabled": False})
            client = DemoClient(cfg, DemoCredentials.from_environment())
            stage = "CLOCK_CHECK"
            client.sync_clock()
            stage = "EXPORT_REQUEST" if args.request_export else "EXPORT_STATUS"
            report = (request_export if args.request_export else status_export)(
                store, client, before, ACCOUNT_ID, JOURNAL
            )
        if store.read(ACCOUNT_ID) != before:
            raise ExportBlocked("DATABASE_CHANGED_KEEP_EXPORT_JOURNAL")
        print(json.dumps(report), flush=True)
    except ExportBlocked as error:
        blocked(stage, str(error))
    except getpass.GetPassWarning:
        blocked(stage, "HIDDEN_INPUT_UNAVAILABLE")
    except DemoFailure:
        blocked(stage, "EXPORT_QUERY_FAILED_NO_AUTOMATIC_RETRY")
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
