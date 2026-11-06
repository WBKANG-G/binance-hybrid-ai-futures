"""Prepare or verify one idle empty-account DB restart; no Docker or exchange mutations."""

import argparse
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from sqlalchemy import create_engine

from integrations.binance_usdm.demo import DemoClient, DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials
from scripts.observe_demo_private import ACCOUNT
from services.execution.demo_empty_db_restart import DBRestartBlocked, check, released
from services.execution.demo_validation import safe_failure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--operation-id", type=UUID, required=True)
    args = parser.parse_args()
    engine = store = lease = None
    report: dict[str, object] = {
        "db_restart_check": "BLOCKED",
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
    success = False
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
        ):
            raise DBRestartBlocked("LIVE_CONTEXT_FORBIDDEN")
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        client = DemoClient(cfg, credentials(prompt=False))
        client.sync_clock()
        engine = create_engine(
            database_url().get_secret_value(),
            connect_args={"connect_timeout": 2},
            pool_pre_ping=True,
        )
        store = DemoStore(engine)
        lease = store.acquire(ACCOUNT, uuid4())
        report = check(store, lease, client, args.operation_id, prepare=args.prepare)
        success = True
    except Exception as error:
        report = {
            "db_restart_check": "BLOCKED",
            "failure": error.code if isinstance(error, DBRestartBlocked) else safe_failure(error),
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    finally:
        try:
            if store is not None and lease is not None:
                store.release(lease)
                if not released(store, lease):
                    raise DBRestartBlocked("LEASE_RELEASE_NOT_VERIFIED")
                report["leader_released"] = True
        except Exception:
            success = False
            report["db_restart_check"] = "BLOCKED"
            report["failure"] = "LEASE_RELEASE_NOT_VERIFIED"
        finally:
            if engine is not None:
                engine.dispose()
    print(json.dumps(report), flush=True)
    if not success:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
