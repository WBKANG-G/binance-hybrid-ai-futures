"""Explicit, bounded empty-account socket loss/reconnect check; never order writes."""

import argparse
import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import SecretStr
from sqlalchemy import create_engine

from integrations.binance_usdm.demo import DemoFailure, DemoSettings
from integrations.binance_usdm.demo_snapshot import operational_snapshot
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials
from scripts.observe_demo_private import ACCOUNT, ObservationClient
from services.execution.demo import DemoCoordinator
from services.execution.demo_models import DemoLedger
from services.execution.demo_stream import Connection, private_connect, session, settled_call
from services.risk.paper import RiskRejected

CODES = frozenset(
    {
        "LIVE_CONTEXT_FORBIDDEN",
        "READ_ONLY_CLIENT_REQUIRED",
        "PAUSED_EMPTY_NORMAL_REQUIRED",
        "EXCHANGE_NOT_EMPTY",
        "HISTORY_OR_BASELINES_CHANGED",
        "READY_STATE_INVALID",
        "EXPECTED_SOCKET_FAILURE_NOT_OBSERVED",
        "DISCONNECT_GATE_NOT_LOCKED",
        "RECONNECT_NOT_VERIFIED",
        "FINAL_GATE_NOT_LOCKED",
        "CHECK_FAILED",
        "LEASE_RELEASE_FAILED",
    }
)
PRESERVED = {
    "orders",
    "requests",
    "request_no_send_evidence",
    "entry_quantity_caps",
    "initial_fill_settlements",
    "validation_evidence",
    "recovery_receipts",
    "cancelled_exposure_corrections",
    "attested_day_baselines",
    "attested_period_baselines",
    "exit_anchored_day_baselines",
    "validation_entry_styles",
    "policy",
    "daily_key",
    "daily_base",
    "weekly_key",
    "weekly_base",
    "high_water",
    "net_transfers",
    "income_seen",
}


class CheckBlocked(DemoFailure):
    def __init__(self, code: str):
        self.code = code if code in CODES else "CHECK_FAILED"
        super().__init__(self.code)


def empty_state(coordinator: DemoCoordinator) -> DemoLedger:
    if not isinstance(coordinator.client, ObservationClient):
        raise CheckBlocked("READ_ONLY_CLIENT_REQUIRED")
    ledger = coordinator.store.read(coordinator.lease.account_id)
    if (
        ledger.risk_state.value != "NORMAL"
        or not ledger.paused
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or any(ledger.positions.values())
        or any(
            o.remaining or o.open_quantity or o.position_state.value != "CLOSED"
            for o in ledger.orders.values()
        )
    ):
        raise CheckBlocked("PAUSED_EMPTY_NORMAL_REQUIRED")
    return ledger


def remote_empty(coordinator: DemoCoordinator) -> None:
    ledger = empty_state(coordinator)
    coordinator.client.sync_clock()
    remote = operational_snapshot(coordinator.client, ledger, coordinator.store.now())
    if (
        any(remote.positions.values())
        or remote.margin_used != 0
        or any(
            r["status"] not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH"}
            for r in remote.orders.values()
        )
        or any(
            r["algoStatus"] not in {"CANCELED", "EXPIRED", "FINISHED", "REJECTED"}
            for r in remote.algos.values()
        )
    ):
        raise CheckBlocked("EXCHANGE_NOT_EMPTY")


def unchanged(coordinator: DemoCoordinator, before: dict[str, Any]) -> DemoLedger:
    ledger = empty_state(coordinator)
    if ledger.model_dump(mode="json", include=PRESERVED) != before:
        raise CheckBlocked("HISTORY_OR_BASELINES_CHANGED")
    return ledger


def gate_blocked(coordinator: DemoCoordinator, ledger: DemoLedger) -> bool:
    # Probe a deep copy with only paused removed, to verify readiness/failure
    # gates independently. Never unpause the durable account or submit a signal.
    probe = ledger.model_copy(deep=True)
    probe.paused = False
    try:
        coordinator._fresh(probe)
    except RiskRejected:
        return True
    return False


def progress(stage: str) -> None:
    print(
        json.dumps(
            {
                "disconnect_check_stage": stage,
                "entry_signal_supplied": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        ),
        flush=True,
    )


async def check(
    coordinator: DemoCoordinator,
    *,
    connector: Callable[[SecretStr], AbstractAsyncContextManager[Connection]] = private_connect,
    drop_delay: float = 1,
    observe_seconds: float = 5,
    phase_timeout: float = 45,
) -> dict[str, Any]:
    await settled_call(remote_empty, coordinator)
    before = empty_state(coordinator).model_dump(mode="json", include=PRESERVED)
    loop = asyncio.get_running_loop()
    first_ready = asyncio.Event()
    control = {"requested": False, "completed": False}

    def ready(marker: str, event: asyncio.Event) -> None:
        ledger = unchanged(coordinator, before)
        if coordinator.session_failed or not ledger.reconciled or not ledger.stream_ready:
            raise CheckBlocked("READY_STATE_INVALID")
        coordinator.store.change(coordinator.lease, marker, lambda _: None)
        progress(marker)
        loop.call_soon_threadsafe(event.set)

    @asynccontextmanager
    async def interrupted(url: SecretStr) -> AsyncIterator[Connection]:
        async with connector(url) as socket:

            async def close_once() -> None:
                await first_ready.wait()
                await asyncio.sleep(drop_delay)
                control["requested"] = True
                await socket.close(code=1011)  # type: ignore[attr-defined]
                control["completed"] = True

            closer = asyncio.create_task(close_once())
            try:
                yield socket
            finally:
                if not control["requested"]:
                    closer.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(closer), timeout=3)
                except (asyncio.CancelledError, TimeoutError):
                    closer.cancel()
                    await asyncio.gather(closer, return_exceptions=True)

    expected_failure = False
    try:
        await asyncio.wait_for(
            session(
                coordinator,
                connector=interrupted,
                on_ready=lambda: ready("DEMO_EMPTY_WS_FAULT_READY", first_ready),
            ),
            timeout=phase_timeout,
        )
    except DemoFailure:
        expected_failure = True
    failed = unchanged(coordinator, before)
    failure = failed.last_private_session_failure
    if not (
        expected_failure
        and first_ready.is_set()
        and control["requested"]
        and control["completed"]
        and coordinator.session_failed
        and failure is not None
        and failure.code == "SOCKET_CLOSED"
    ):
        raise CheckBlocked("EXPECTED_SOCKET_FAILURE_NOT_OBSERVED")
    if failed.stream_ready or failed.reconciled or not gate_blocked(coordinator, failed):
        raise CheckBlocked("DISCONNECT_GATE_NOT_LOCKED")
    await settled_call(
        coordinator.store.change, coordinator.lease, "DEMO_EMPTY_WS_FAULT_BLOCKED", lambda _: None
    )
    progress("DEMO_EMPTY_WS_FAULT_BLOCKED")
    # One deliberate reconnect, no retry loop. Every connection runs the
    # existing REST reconciliation before ready(). No entry callback exists.
    await asyncio.sleep(1)
    await settled_call(coordinator.store.renew, coordinator.lease)
    second_ready = asyncio.Event()
    task = asyncio.create_task(
        session(
            coordinator,
            connector=connector,
            on_ready=lambda: ready("DEMO_EMPTY_WS_FAULT_RECONNECTED", second_ready),
        )
    )
    try:

        async def observe() -> None:
            await second_ready.wait()
            await asyncio.sleep(observe_seconds)

        observation = asyncio.create_task(observe())
        try:
            done, _ = await asyncio.wait(
                {task, observation}, timeout=phase_timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                await task
                raise CheckBlocked("RECONNECT_NOT_VERIFIED")
            if observation not in done or not second_ready.is_set() or coordinator.session_failed:
                raise CheckBlocked("RECONNECT_NOT_VERIFIED")
            live = unchanged(coordinator, before)
            if not live.stream_ready or not live.reconciled:
                raise CheckBlocked("RECONNECT_NOT_VERIFIED")
        finally:
            observation.cancel()
            await asyncio.gather(observation, return_exceptions=True)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    final = unchanged(coordinator, before)
    if (
        coordinator.session_failed
        or final.stream_ready
        or final.reconciled
        or not gate_blocked(coordinator, final)
    ):
        raise CheckBlocked("FINAL_GATE_NOT_LOCKED")
    await settled_call(remote_empty, coordinator)
    await settled_call(
        coordinator.store.change, coordinator.lease, "DEMO_EMPTY_WS_FAULT_COMPLETE", lambda _: None
    )
    return {
        "disconnect_check": "EMPTY_SOCKET_LOSS_RECONNECT_VERIFIED",
        "scope": "EMPTY_ACCOUNT_LOCAL_SOCKET_CLOSE",
        "injected_close_count": 1,
        "reconnect_count": 1,
        "disconnect_code": failure.code,
        "entry_gate_blocked_after_disconnect": True,
        "reconnect_reconciled": True,
        "history_and_baselines_preserved": True,
        "final_paused": final.paused,
        "final_stream_ready": final.stream_ready,
        "final_reconciled": final.reconciled,
        "risk_state": final.risk_state.value,
        "entry_signal_supplied": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-fault-injection", action="store_true", required=True)
    parser.parse_args()
    engine = store = lease = coordinator = None
    report: dict[str, Any] = {
        "disconnect_check": "BLOCKED",
        "code": "CHECK_FAILED",
        "entry_signal_supplied": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
    success = False
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
        ):
            raise CheckBlocked("LIVE_CONTEXT_FORBIDDEN")
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
        lease = store.acquire(ACCOUNT, uuid4())
        coordinator = DemoCoordinator(store, lease, client)
        empty_state(coordinator)
        remote_empty(coordinator)
        coordinator.refresh_metadata()
        coordinator.reconcile()
        report = asyncio.run(check(coordinator))
        success = True
    except Exception as error:
        report["code"] = error.code if isinstance(error, CheckBlocked) else "CHECK_FAILED"
    finally:
        try:
            if store is not None and lease is not None:
                store.release(lease)
        except Exception:
            success = False
            report = {
                "disconnect_check": "BLOCKED",
                "code": "LEASE_RELEASE_FAILED",
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
