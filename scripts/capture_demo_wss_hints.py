"""Bounded no-order observation and replay of allowlisted private WSS hints.

This records e/E and clock evidence only, never full private/account frames.
"""

import asyncio
import contextlib
import hashlib
import importlib
import io
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from integrations.binance_usdm.demo import DemoClockSample, DemoFailure
from services.execution.demo_event_clock import validate_event_time_async
from services.execution.demo_event_inbox import EventInbox
from services.execution.demo_session_diagnostics import EVENT_KINDS
from services.execution.demo_stream import private_connect, session

MAX_HINTS = 128
SOURCE_HASHES = {
    "scripts/check_demo_session_ready.py": "400d60be1e2c8c48115c74bb7c9c99918e626d4263a0c4eb879504d738101e60",
    "scripts/observe_demo_private.py": "842c9f03621ea1f98b4aa8a9628c36eb0582156823c7efbac89ae0c7dcc8e20c",
    "services/execution/demo_stream.py": "409098cb05abbfb97eb9b9f1db2070ca6bb5782bc6dd7542311e4eb703b6f456",
    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",
    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",
}


class Capture:
    def __init__(self) -> None:
        self.hints: list[dict[str, Any]] = []

    def receive(self, raw: str | bytes, client: Any) -> None:
        if len(raw) > 1048576 or len(self.hints) >= MAX_HINTS:
            raise DemoFailure("Capture bound exceeded")

        def reject(value: str) -> Any:
            raise ValueError("Non-finite frame")

        event = json.loads(raw, parse_constant=reject)
        sample, local = client.clock_sample, client.clock_ms()
        if (
            not isinstance(event, dict)
            or not isinstance(event.get("e"), str)
            or event.get("e") not in EVENT_KINDS
            or event.get("e") == "listenKeyExpired"
            or type(event.get("E")) is not int
            or not isinstance(sample, DemoClockSample)
            or type(local) is not int
            or any(type(v) is not int for v in asdict(sample).values())
        ):
            raise DemoFailure("Capture event or clock invalid")
        self.hints.append(
            {
                "e": event["e"],
                "E": event["E"],
                "received_local_ms": local,
                "clock_sample": asdict(sample),
            }
        )

    async def supervise(
        self,
        coordinator: Any,
        *,
        on_ready: Any = None,
        connector: Any = private_connect,
        max_messages: int | None = None,
    ) -> None:
        capture = self

        class Proxy:
            def __init__(self, socket: Any) -> None:
                self.socket = socket

            async def recv(self) -> str | bytes:
                raw: str | bytes = await self.socket.recv()
                capture.receive(raw, coordinator.client)
                return raw  # Existing parser still validates the original unmodified frame.

            async def ping(self) -> Any:
                return await self.socket.ping()

        @contextlib.asynccontextmanager
        async def wrapped(url: Any) -> AsyncIterator[Any]:
            async with connector(url) as socket:
                yield Proxy(socket)

        await session(coordinator, connector=wrapped, on_ready=on_ready, max_messages=max_messages)


async def replay(hints: list[dict[str, Any]]) -> dict[str, Any]:
    if not hints:
        return {"hint_replay": "NO_EVENTS_NOT_ACCEPTED", "received": 0}
    if len(hints) > MAX_HINTS:
        raise ValueError("Replay bound exceeded")
    index, validated = 0, 0
    replay_client: Any = None
    completed = asyncio.Event()

    class Source:
        async def recv(self) -> str:
            nonlocal index, replay_client
            if index == len(hints):
                await asyncio.Future()
            row = hints[index]
            if set(row) != {"e", "E", "received_local_ms", "clock_sample"}:
                raise ValueError("Allowlisted replay schema required")
            if type(row["received_local_ms"]) is not int:
                raise ValueError("Replay clock required")
            origin = time.monotonic()
            local = row["received_local_ms"]
            replay_client = SimpleNamespace(
                clock_sample=DemoClockSample(**row["clock_sample"]),
                cooldown_until=0,
                synced_at=row["clock_sample"]["after_ms"],
                clock_ms=lambda: local + int((time.monotonic() - origin) * 1000),
            )
            index += 1
            return json.dumps({"e": row["e"], "E": row["E"]})

    async def validate(stamp: object, detail: Any) -> int:
        nonlocal validated
        age = await validate_event_time_async(replay_client, stamp, detail)
        validated += 1
        if validated == len(hints):
            completed.set()
        return age

    inbox = EventInbox(lambda: replay_client.clock_ms(), completed.set, validate_time=validate)
    task = asyncio.create_task(inbox.run(Source()))
    try:
        await asyncio.wait_for(completed.wait(), timeout=5)
        inbox.raise_if_failed()
        hint = await inbox.take(timeout=1)
        if (
            inbox.received != len(hints)
            or validated != len(hints)
            or hint.stamp != max(r["E"] for r in hints)
        ):
            raise ValueError("Replay evidence incomplete")
        return {
            "hint_replay": "CAPTURED_HINT_ADMISSION_REPLAY_PASSED",
            "received": inbox.received,
            "coalesced": inbox.coalesced,
            "timestamps_rewritten": False,
            "full_private_frames_replayed": False,
        }
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def financial_hash(engine: Any) -> str:
    from sqlalchemy import select

    from packages.persistence.demo import accounts

    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='2s'")
        ledger = conn.execute(
            select(accounts.c.state).where(accounts.c.account_id == "DEMO-WINDOWS-ISOLATED-BTC-V1")
        ).scalar_one()
    fields = (
        "daily_key",
        "weekly_key",
        "daily_base",
        "weekly_base",
        "high_water",
        "risk_state",
        "paused",
        "orders",
        "requests",
        "income_seen",
        "net_transfers",
        "recovery_receipts",
        "cancelled_exposure_corrections",
        "exit_anchored_day_baselines",
        "request_no_send_evidence",
        "validation_evidence",
        "entry_quantity_caps",
        "available_balance",
        "margin_used",
        "positions",
        "entry_prices",
        "isolated_wallets",
        "isolated_margins",
        "attested_day_baselines",
        "attested_period_baselines",
    )
    selected = {k: ledger.get(k) for k in fields}
    selected["value"] = {k: v for k, v in ledger.get("value", {}).items() if k != "as_of"}
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def check_context() -> None:
    if (
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
        or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
    ):
        raise ValueError("DEMO context required")
    for name, expected in SOURCE_HASHES.items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
            raise ValueError("Deployed source mismatch")


def ready_check() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_demo_session_ready"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Read-only readiness blocked")
    report = json.loads(result.stdout)
    if (
        report.get("session_ready") != "READ_ONLY_READY_ENTRIES_STILL_PAUSED"
        or report.get("risk_state") != "NORMAL"
        or report.get("paused") is not True
        or report.get("period_keys_match") is not True
        or report.get("exchange_empty_verified") is not True
        or report.get("database_changed") is not False
        or report.get("orders_enabled") is not False
        or report.get("live_trading_enabled") is not False
    ):
        raise ValueError("Read-only readiness incomplete")


def main() -> None:
    capture, engine = Capture(), None
    stage = "CONTEXT"
    try:
        check_context()
        from sqlalchemy import create_engine

        from packages.domain.config import database_url

        observer: Any = importlib.import_module("scripts.observe_demo_private")

        stage = "READ_ONLY_READY"
        ready_check()
        stage = "FINANCIAL_SNAPSHOT"
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        before = financial_hash(engine)
        stage = "NO_ORDER_OBSERVATION"
        original = observer.session
        observer.session = capture.supervise
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                observer.main()
        finally:
            observer.session = original
        observation = json.loads(output.getvalue())
        if observation.get("private_observation") != "OBSERVATION_FINISHED_NO_ENTRY":
            raise ValueError("Observation not accepted")
        stage = "FINANCIAL_PRESERVATION"
        if financial_hash(engine) != before:
            raise ValueError("Financial state changed")
        stage = "HINT_REPLAY"
        replayed = asyncio.run(replay(capture.hints))
        report = {
            "capture_check": "NO_ORDER_OBSERVATION_FINISHED",
            "observation": observation,
            "captured_hint_count": len(capture.hints),
            "captured_hints": capture.hints,
            "replay": replayed,
            "full_private_frames_recorded": False,
            "financial_fields_unchanged": True,
            "database_metadata_may_change": True,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    except (Exception, SystemExit):  # noqa: BLE001 -- never print raw private frame/URL/error
        report = {
            "capture_check": "BLOCKED_OR_FAILED_NO_AUTOMATIC_RETRY",
            "stage": stage,
            "captured_hint_count": len(capture.hints),
            "private_data_logged": False,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)
    if report["capture_check"] != "NO_ORDER_OBSERVATION_FINISHED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
