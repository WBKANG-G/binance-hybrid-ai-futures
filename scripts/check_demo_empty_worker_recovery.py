"""One empty-account observer child SIGKILL and natural-lease recovery; no order writes."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import yaml
from sqlalchemy import create_engine

from integrations.binance_usdm.demo import DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials
from scripts.observe_demo_private import ACCOUNT, ObservationClient
from services.execution.demo import DemoCoordinator
from services.execution.demo_empty_worker_recovery import (
    RecoveryCheckBlocked,
    check_empty,
    code,
    run,
    worker,
)


def spawn_worker(operation: UUID, owner: UUID) -> subprocess.Popen[bytes]:
    # No added key arguments/environment. Child reads the existing private RAM cache.
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.check_demo_empty_worker_recovery",
            "--enable-fault-injection",
            "--operation-id",
            str(operation),
            "--worker",
            "--owner-id",
            str(owner),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-fault-injection", action="store_true", required=True)
    parser.add_argument("--operation-id", type=UUID, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--owner-id", type=UUID, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker != bool(args.owner_id):
        parser.error("Internal worker and owner must be supplied together")
    engine = store = lease = None
    report = {
        "worker_recovery": "BLOCKED",
        "failure": "CHECK_FAILED",
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
    success = False
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
            or os.name != "posix"
        ):
            raise RecoveryCheckBlocked("LIVE_CONTEXT_FORBIDDEN")
        key = credentials(prompt=False)
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": True})
        client = ObservationClient(cfg, key)
        client.sync_clock()
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        if args.worker:
            check_empty(store.read(ACCOUNT))
            if str(args.operation_id) in store.read(ACCOUNT).operations:
                raise RecoveryCheckBlocked("OPERATION_ALREADY_EXISTS")
            lease = store.acquire(ACCOUNT, args.owner_id)
            coordinator = DemoCoordinator(store, lease, client)
            coordinator.refresh_metadata()
            coordinator.reconcile()
            asyncio.run(worker(coordinator, args.operation_id))
        else:
            report = asyncio.run(
                run(
                    store,
                    client,
                    args.operation_id,
                    lambda owner: spawn_worker(args.operation_id, owner),
                )
            )
            success = report["worker_recovery"] == "EMPTY_WORKER_SIGKILL_FENCED_RECOVERY_VERIFIED"
    except Exception as error:
        report["failure"] = code(error)
    finally:
        try:
            if lease is not None and store is not None:
                store.release(lease)
        except Exception:
            success = False
            report["failure"] = "LEASE_RELEASE_FAILED"
        finally:
            if engine is not None:
                engine.dispose()
    print(json.dumps(report), flush=True)
    if not success:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
