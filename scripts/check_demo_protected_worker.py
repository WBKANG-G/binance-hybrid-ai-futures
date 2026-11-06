"""Explicit one-entry protected-child kill; DB/API/alert services remain running."""

import argparse
import asyncio
import json
import os
import stat
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
from scripts.observe_demo_private import ACCOUNT
from services.execution.demo_protected_worker_check import (
    CappedCoordinator,
    CheckBlocked,
    ScopedClient,
    child_run,
    control_stop,
    fresh_start,
    require,
    run,
)


class Worker:
    def __init__(self, operation: UUID, owner: UUID):
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "scripts.check_demo_protected_worker",
                "--enable-demo",
                "--enable-protected-worker-kill",
                "--operation-id",
                str(operation),
                "--worker",
                "--owner-id",
                str(owner),
                "--parent-pid",
                str(os.getpid()),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.pid = self.process.pid

    def poll(self) -> int | None:
        return self.process.poll()

    def kill(self) -> None:
        self.process.kill()

    def wait(self, timeout: float | None = None) -> int:
        return self.process.wait(timeout=timeout)

    def stop(self) -> None:
        if self.process.stdin is not None:
            try:
                self.process.stdin.write(b"S")
                self.process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                self.process.stdin.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-demo", action="store_true", required=True)
    parser.add_argument("--enable-protected-worker-kill", action="store_true", required=True)
    parser.add_argument("--operation-id", type=UUID, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--owner-id", type=UUID, help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker != bool(args.owner_id and args.parent_pid) or (
        not args.worker and (args.owner_id or args.parent_pid)
    ):
        parser.error("Internal owner/parent scope invalid")
    engine = store = lease = None
    code = 2
    report: dict[str, object] = {
        "protected_worker_check": "BLOCKED_KEEP_PAUSED",
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
    try:
        require(
            os.name == "posix"
            and os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
            "DEMO_CONTEXT_REQUIRED",
        )
        if args.worker:
            require(
                args.parent_pid == os.getppid() and stat.S_ISFIFO(os.fstat(0).st_mode),
                "SUPERVISOR_PIPE_REQUIRED",
            )
        key = credentials(prompt=False)
        config = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": True})
        client = ScopedClient(
            config, key, account=ACCOUNT, operation=args.operation_id, entry_enabled=args.worker
        )
        client.sync_clock()
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        fresh_start(store.read(ACCOUNT), args.operation_id)
        if args.worker:
            lease = store.acquire(ACCOUNT, args.owner_id)
            coordinator = CappedCoordinator(store, lease, client)
            coordinator.refresh_metadata()
            asyncio.run(
                child_run(
                    coordinator, args.operation_id, stop=lambda: control_stop(args.parent_pid)
                )
            )
            code = 0
        else:
            result = asyncio.run(
                run(
                    store, client, args.operation_id, lambda owner: Worker(args.operation_id, owner)
                )
            )
            report = result
            code = (
                0
                if result["protected_worker_check"]
                == "PROTECTED_CHILD_SIGKILL_FENCED_RECOVERY_FLAT_VERIFIED"
                else 2
            )
    except Exception as error:
        report["failure"] = (
            str(error) if isinstance(error, CheckBlocked) else "CHECK_FAILED_DETAILS_REDACTED"
        )
    finally:
        if engine is not None:
            engine.dispose()
    if not args.worker:
        print(json.dumps(report), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
