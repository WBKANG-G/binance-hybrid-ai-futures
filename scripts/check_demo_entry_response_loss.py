"""Explicit capped DEMO entry ACK loss after real SDK success; same-ID recovery only."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import yaml
from sqlalchemy import create_engine

from integrations.binance_usdm.demo import DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials
from scripts.observe_demo_private import ACCOUNT
from services.execution.demo import DemoCoordinator
from services.execution.demo_api_restart_check import empty
from services.execution.demo_entry_response_loss import (
    EntryLossBlocked,
    EntryLossClient,
    code,
    run,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-demo", action="store_true", required=True)
    parser.add_argument("--enable-entry-response-loss", action="store_true", required=True)
    parser.add_argument("--operation-id", type=UUID, required=True)
    args = parser.parse_args()
    engine = store = lease = None
    report = {
        "entry_loss_check": "BLOCKED",
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
            raise EntryLossBlocked("LIVE_CONTEXT_FORBIDDEN")
        key = credentials(prompt=False)
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": True})
        client = EntryLossClient(cfg.model_copy(update={"demo_trading_enabled": False}), key)
        client.sync_clock()
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        state = store.read(ACCOUNT)
        op = str(args.operation_id)
        existing = state.orders.get(op)
        start = str(uuid5(args.operation_id, "entry-loss-start"))
        if existing and (
            state.operations.get(start) != "ENTRY_ACK_LOSS_CHECK"
            or existing.signal.purpose != "DEMO_VALIDATION"
            or existing.signal.direction != "LONG"
            or existing.signal.symbol != "BTCUSDT"
            or state.validation_entry_styles.get(op) != "OPPOSITE_QUOTE"
        ):
            raise EntryLossBlocked("ENTRY_SCOPE_INVALID")
        if any(k != op and (o.remaining or o.open_quantity) for k, o in state.orders.items()):
            raise EntryLossBlocked("ENTRY_SCOPE_INVALID")
        if existing is None and (
            start in state.operations
            or str(uuid5(args.operation_id, "entry-loss-result")) in state.operations
        ):
            raise EntryLossBlocked("OPERATION_ALREADY_EXISTS")
        lease = store.acquire(ACCOUNT, uuid4())
        coordinator = DemoCoordinator(store, lease, client)
        if existing is None:
            empty(coordinator)
        coordinator.refresh_metadata()
        coordinator.reconcile()
        client.settings = cfg
        report = asyncio.run(run(coordinator, args.operation_id))
        success = report["entry_loss_check"] == "ENTRY_ACK_LOSS_SAME_ID_NO_DUPLICATE_FLAT_VERIFIED"
    except Exception as error:
        report["failure"] = code(error)
    finally:
        try:
            if store is not None and lease is not None:
                store.release(lease)
        except Exception:
            success = False
            report["entry_loss_check"] = "BLOCKED"
            report["failure"] = "LEASE_RELEASE_FAILED"
        finally:
            if engine is not None:
                engine.dispose()
    print(json.dumps(report), flush=True)
    if not success:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
