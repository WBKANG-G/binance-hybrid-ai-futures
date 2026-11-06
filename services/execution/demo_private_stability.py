"""Thirty minutes of one empty-account WSS session and successful listen-key renewal."""

import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoFailure
from scripts.check_demo_disconnect import PRESERVED
from scripts.observe_demo_private import ObservationClient
from services.execution.demo import DemoCoordinator
from services.execution.demo_empty_worker_recovery import (
    RecoveryCheckBlocked,
    check_empty,
    remote_empty,
)
from services.execution.demo_open_disconnect_check import drain_session
from services.execution.demo_stream import private_connect, session, settled_call
from services.execution.demo_validation import safe_failure

OBSERVATION_SECONDS = 1800


class StabilityBlocked(DemoFailure):
    def __init__(self, code: str):
        self.code = (
            code
            if code
            in {
                "LIVE_CONTEXT_FORBIDDEN",
                "READ_ONLY_CLIENT_REQUIRED",
                "OPERATION_ALREADY_EXISTS",
                "READY_TIMEOUT",
                "SESSION_ENDED_EARLY",
                "SESSION_NOT_READY",
                "SESSION_FAILED",
                "HISTORY_CHANGED",
                "DURATION_NOT_COMPLETED",
                "KEY_RENEWAL_NOT_VERIFIED",
                "KEY_CLOSE_NOT_VERIFIED",
                "FINAL_STATE_NOT_VERIFIED",
                "RESULT_AUDIT_FAILED",
                "LEASE_RELEASE_FAILED",
                "CHECK_FAILED",
            }
            else "CHECK_FAILED"
        )
        super().__init__(self.code)


class StabilityClient(ObservationClient):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.key_creates = self.key_renewals = self.key_closes = self.read_queries = 0

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        result = super().request(method, path, params)
        self.read_queries += 1
        return result

    def listen_key(self, method: Literal["POST", "PUT", "DELETE"]) -> SecretStr | None:
        result = super().listen_key(method)
        if method == "POST":
            self.key_creates += 1
        elif method == "PUT":
            self.key_renewals += 1
        elif method == "DELETE":
            self.key_closes += 1
        return result


def code(error: Exception) -> str:
    return (
        error.code
        if isinstance(error, (StabilityBlocked, RecoveryCheckBlocked))
        else safe_failure(error)
    )


def progress(stage: str, elapsed: float, client: StabilityClient, target: float) -> None:
    print(
        json.dumps(
            {
                "private_stability_stage": stage,
                "elapsed_seconds": int(elapsed),
                "target_seconds": target,
                "successful_key_renewals": client.key_renewals,
                "entry_signal_supplied": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        ),
        flush=True,
    )


async def run(
    coordinator: DemoCoordinator,
    operation: UUID,
    *,
    connector: Any = private_connect,
    observation_seconds: float = OBSERVATION_SECONDS,
    progress_seconds: float = 60,
    ready_timeout: float = 45,
) -> dict[str, Any]:
    store, lease, client = coordinator.store, coordinator.lease, coordinator.client
    if not isinstance(client, StabilityClient):
        raise StabilityBlocked("READ_ONLY_CLIENT_REQUIRED")
    original = store.read(lease.account_id)
    check_empty(original)
    key = str(operation)
    if (
        key in original.operations
        or str(uuid5(operation, "stability-result")) in original.operations
    ):
        raise StabilityBlocked("OPERATION_ALREADY_EXISTS")
    await settled_call(remote_empty, store, client, lease.account_id)
    baseline = original.model_dump(mode="json", include=PRESERVED)
    ready_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    started: float | None = None
    ready_at: str | None = None
    finished_at: str | None = None
    callback_failure = "NONE"
    failure = "NONE"
    completion_failure = "NONE"
    failure_stage = "NONE"
    stage = "SESSION_CONNECT"
    duration_complete = False
    elapsed = 0.0

    def ready() -> None:
        nonlocal started, ready_at, callback_failure
        try:
            current = store.read(lease.account_id)
            check_empty(current)
            if coordinator.session_failed or not current.stream_ready or not current.reconciled:
                raise StabilityBlocked("SESSION_NOT_READY")
            if current.model_dump(mode="json", include=PRESERVED) != baseline:
                raise StabilityBlocked("HISTORY_CHANGED")

            def marker(ledger: Any) -> None:
                ledger.operations[key] = "EMPTY_PRIVATE_STABILITY_STARTED"

            store.change(lease, "DEMO_PRIVATE_STABILITY_READY", marker)
            ready_at = store.now().isoformat()
            started = time.monotonic()
            progress("OBSERVING", 0, client, observation_seconds)
        except Exception as error:
            callback_failure = code(error)
            raise
        finally:
            loop.call_soon_threadsafe(ready_event.set)

    task = asyncio.create_task(session(coordinator, connector=connector, on_ready=ready))
    waiter = asyncio.create_task(ready_event.wait())
    sleeper: asyncio.Task[None] | None = None
    try:
        done, _ = await asyncio.wait(
            {task, waiter}, timeout=ready_timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            await task
            raise StabilityBlocked("SESSION_ENDED_EARLY")
        if waiter not in done:
            raise StabilityBlocked("READY_TIMEOUT")
        if callback_failure != "NONE" or started is None:
            raise StabilityBlocked("SESSION_NOT_READY")
        stage = "OBSERVATION"
        while True:
            elapsed = time.monotonic() - started
            remaining = observation_seconds - elapsed
            if remaining <= 0:
                duration_complete = True
                finished_at = store.now().isoformat()
                break
            sleeper = asyncio.create_task(asyncio.sleep(min(progress_seconds, remaining)))
            done, _ = await asyncio.wait({task, sleeper}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                await task
                raise StabilityBlocked("SESSION_ENDED_EARLY")
            if coordinator.session_failed:
                raise StabilityBlocked("SESSION_FAILED")
            current = await settled_call(store.read, lease.account_id)
            check_empty(current)
            if current.model_dump(mode="json", include=PRESERVED) != baseline:
                raise StabilityBlocked("HISTORY_CHANGED")
            elapsed = time.monotonic() - started
            progress("OBSERVING", elapsed, client, observation_seconds)
    except Exception as error:
        failure = callback_failure if callback_failure != "NONE" else code(error)
        failure_stage = stage
    finally:
        waiter.cancel()
        if sleeper is not None:
            sleeper.cancel()
        try:
            session_error = await drain_session(task, cancel=True)
            if (session_error is not None or coordinator.session_failed) and failure == "NONE":
                failure, failure_stage = "SESSION_FAILED", "SESSION_CLEANUP"
        finally:
            await asyncio.gather(
                waiter, *([sleeper] if sleeper is not None else []), return_exceptions=True
            )
        if callback_failure != "NONE":
            failure = callback_failure
        try:
            await settled_call(store.renew, lease)
            await settled_call(remote_empty, store, client, lease.account_id)
            await settled_call(coordinator.disconnect)
        except Exception as error:
            completion_failure = code(error)
    final = store.read(lease.account_id)
    history_ok = final.model_dump(mode="json", include=PRESERVED) == baseline
    accepted = bool(
        duration_complete
        and elapsed >= observation_seconds
        and started is not None
        and failure == completion_failure == "NONE"
        and history_ok
        and client.key_creates == client.key_closes == 1
        and client.key_renewals >= 1
        and final.paused
        and final.risk_state.value == "NORMAL"
        and not final.stream_ready
        and not final.reconciled
    )
    if not accepted and failure == "NONE":
        failure_stage = "FINAL_STATE"
        failure = (
            "HISTORY_CHANGED"
            if not history_ok
            else "DURATION_NOT_COMPLETED"
            if not duration_complete
            else "KEY_RENEWAL_NOT_VERIFIED"
            if client.key_renewals < 1
            else "KEY_CLOSE_NOT_VERIFIED"
            if client.key_closes != 1
            else "FINAL_STATE_NOT_VERIFIED"
        )
    try:

        def audit(ledger: Any) -> None:
            ledger.operations.setdefault(
                str(uuid5(operation, "stability-result")),
                "PRIVATE_STABILITY:" + ("VERIFIED" if accepted else failure),
            )

        store.change(lease, "DEMO_PRIVATE_STABILITY_RESULT", audit)
    except Exception:
        accepted = False
        if failure == "NONE":
            failure, failure_stage = "RESULT_AUDIT_FAILED", "RESULT_AUDIT"
    return {
        "private_stability": "EMPTY_PRIVATE_SESSION_STABILITY_RENEWAL_VERIFIED"
        if accepted
        else "NOT_ACCEPTED",
        "scope": "EMPTY_ACCOUNT_SINGLE_WSS_SESSION",
        "operation_id": key,
        "target_seconds": observation_seconds,
        "observed_elapsed_ms": max(0, int(elapsed * 1000)),
        "ready_at_utc": ready_at,
        "observation_finished_at_utc": finished_at,
        "key_create_success_count": client.key_creates,
        "successful_key_renewals": client.key_renewals,
        "key_close_success_count": client.key_closes,
        "successful_read_queries": client.read_queries,
        "received_events": coordinator.session_diagnostic.received_events,
        "private_session_diagnostic": coordinator.session_diagnostic.model_dump(mode="json"),
        "failure": failure,
        "failure_stage": failure_stage,
        "completion_failure": completion_failure,
        "history_and_baselines_preserved": history_ok,
        "risk_state": final.risk_state.value,
        "final_paused": final.paused,
        "final_stream_ready": final.stream_ready,
        "final_reconciled": final.reconciled,
        "new_entry_post_count": 0,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
