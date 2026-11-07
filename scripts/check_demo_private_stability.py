"""Fixed 30-minute empty DEMO private WSS/listen-key renewal check, no new orders."""

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
from services.execution.demo_empty_worker_recovery import check_empty
from services.execution.demo_private_stability import StabilityBlocked, StabilityClient, code, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe-30-minutes", action="store_true", required=True)
    parser.add_argument("--operation-id", type=UUID, required=True)
    args = parser.parse_args()
    engine = store = lease = None
    report = {
        "private_stability": "BLOCKED",
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
            raise StabilityBlocked("LIVE_CONTEXT_FORBIDDEN")
        key = credentials(prompt=False)
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": True})
        client = StabilityClient(cfg, key)
        client.sync_clock()
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        state = store.read(ACCOUNT)
        check_empty(state)
        if (
            str(args.operation_id) in state.operations
            or str(uuid5(args.operation_id, "stability-result")) in state.operations
        ):
            raise StabilityBlocked("OPERATION_ALREADY_EXISTS")
        lease = store.acquire(ACCOUNT, uuid4())
        coordinator = DemoCoordinator(store, lease, client)
        coordinator.refresh_metadata()
        coordinator.reconcile()
        report = asyncio.run(run(coordinator, args.operation_id))
        success = report["private_stability"] == "EMPTY_PRIVATE_SESSION_STABILITY_RENEWAL_VERIFIED"
    except Exception as error:
        report["failure"] = code(error)
    finally:
        try:
            if store is not None and lease is not None:
                store.release(lease)
        except Exception:
            success = False
            report = {
                "private_stability": "BLOCKED",
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
