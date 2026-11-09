"""Preview or run one capped DEMO open-order API restart test in an independent guard."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from sqlalchemy import create_engine

from integrations.binance_usdm.demo import DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials
from scripts.observe_demo_private import ACCOUNT, ObservationClient
from services.execution.demo import DemoCoordinator
from services.execution.demo_api_restart_check import (
    RestartBlocked,
    RestartClient,
    empty,
    preview,
    run,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preview", action="store_true")
    modes.add_argument("--run", action="store_true")
    parser.add_argument("--enable-demo", action="store_true")
    parser.add_argument("--operation-id", type=UUID)
    args = parser.parse_args()
    if (args.run and (not args.enable_demo or args.operation_id is None)) or (
        args.preview and (args.enable_demo or args.operation_id)
    ):
        parser.error(
            "Run requires explicit DEMO and operation UUID; preview has no execution flags"
        )
    engine = store = lease = None
    report = {
        "restart_check": "BLOCKED",
        "failure": "CHECK_FAILED",
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
    success = False
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
        ):
            raise RestartBlocked("ENTRY_SCOPE_INVALID")
        if args.run and os.getpid() != 1:
            raise RestartBlocked("WORKER_CONTAINER_REQUIRED")
        key = credentials(prompt=bool(args.run))
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": True})
        client = (ObservationClient if args.preview else RestartClient)(cfg, key)
        client.sync_clock()
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        lease = store.acquire(ACCOUNT, uuid4())
        coordinator = DemoCoordinator(store, lease, client)
        if args.preview or str(args.operation_id) not in store.read(ACCOUNT).orders:
            empty(coordinator)
        state = store.read(ACCOUNT)
        existing = state.orders.get(str(args.operation_id))
        if (
            args.run
            and existing is not None
            and (
                existing.signal.purpose != "DEMO_VALIDATION"
                or existing.signal.direction != "LONG"
                or any(
                    k != str(args.operation_id) and (o.remaining or o.open_quantity)
                    for k, o in state.orders.items()
                )
            )
        ):
            raise RestartBlocked("ENTRY_SCOPE_INVALID")
        coordinator.refresh_metadata()
        coordinator.reconcile()
        report = asyncio.run(
            preview(coordinator) if args.preview else run(coordinator, args.operation_id)
        )
        success = args.preview or report["restart_check"] == "OPEN_ORDER_API_RESTART_VERIFIED"
    except Exception as error:
        report["failure"] = error.code if isinstance(error, RestartBlocked) else "CHECK_FAILED"
    finally:
        try:
            if store is not None and lease is not None:
                store.release(lease)
        except Exception:
            success = False
            report = {
                "restart_check": "BLOCKED",
                "failure": "LEASE_RELEASE_FAILED",
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
