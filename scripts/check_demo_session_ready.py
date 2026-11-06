"""Read-only post-rollover session readiness; never resumes entries or modifies the ledger."""

import json
import os
from pathlib import Path

import yaml
from sqlalchemy import create_engine

from integrations.binance_usdm.demo import DemoClient, DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials
from scripts.observe_demo_private import ACCOUNT
from services.execution.demo_empty_db_restart import DBRestartBlocked, empty, exchange_digest
from services.execution.demo_recovery import local_period_status
from services.execution.demo_validation import safe_failure


def main() -> None:
    engine = None
    report: dict[str, object]
    success = False
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
        ):
            raise DBRestartBlocked("LIVE_CONTEXT_FORBIDDEN")
        key = credentials(prompt=False)
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        engine = create_engine(
            database_url().get_secret_value(),
            connect_args={"connect_timeout": 2},
            pool_pre_ping=True,
        )
        store = DemoStore(engine)
        before = store.read(ACCOUNT)
        empty(before)
        periods = local_period_status(store, before)
        if not periods["period_keys_match"]:
            report = {"session_ready": "BLOCKED", "failure": "PERIOD_BASELINE_MISSING", **periods}
        else:
            client = DemoClient(cfg, key)
            client.sync_clock()
            _, metrics = exchange_digest(store, client, before)
            if store.read(ACCOUNT) != before:
                raise DBRestartBlocked("HISTORY_CHANGED")
            report = {
                "session_ready": "READ_ONLY_READY_ENTRIES_STILL_PAUSED",
                "period_keys_match": True,
                "periods": periods["periods"],
                "risk_state": before.risk_state.value,
                "paused": before.paused,
                "credential_source": "RUNTIME_ENV"
                if (os.getenv("BINANCE_DEMO_API_KEY") or os.getenv("BINANCE_DEMO_API_SECRET"))
                else "PRIVATE_TMPFS_SESSION",
                "exchange_empty_verified": True,
                "database_changed": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
                "read_diagnostic": metrics,
            }
            success = True
    except Exception as error:
        report = {
            "session_ready": "BLOCKED",
            "failure": error.code if isinstance(error, DBRestartBlocked) else safe_failure(error),
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)
    if not success:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
