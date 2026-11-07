"""Empty-account observer process kill and natural-lease fenced takeover only."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select, text

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_snapshot import operational_snapshot
from packages.persistence.demo import DemoStore, accounts
from scripts.check_demo_disconnect import PRESERVED, gate_blocked
from scripts.observe_demo_private import ACCOUNT, ObservationClient
from services.execution.demo import DemoCoordinator
from services.execution.demo_models import DemoLedger
from services.execution.demo_open_disconnect_check import drain_session
from services.execution.demo_stream import private_connect, session, settled_call
from services.execution.demo_validation import safe_failure
from services.execution.models import Lease
from services.risk.paper import RiskRejected


class RecoveryCheckBlocked(DemoFailure):
    def __init__(self, code: str):
        self.code = (
            code
            if code
            in {
                "EMPTY_NORMAL_PAUSED_REQUIRED",
                "READ_ONLY_CLIENT_REQUIRED",
                "LIVE_CONTEXT_FORBIDDEN",
                "WORKER_READY_TIMEOUT",
                "WORKER_EXITED_BEFORE_READY",
                "WORKER_STATE_INVALID",
                "EARLY_TAKEOVER_SUCCEEDED",
                "PROCESS_KILL_NOT_VERIFIED",
                "LEASE_WAIT_TIMEOUT",
                "LEADER_CHANGED",
                "STALE_OWNER_NOT_REJECTED",
                "RECOVERY_GATE_NOT_LOCKED",
                "RECOVERY_NOT_READY",
                "RECOVERY_SESSION_FAILED",
                "HISTORY_CHANGED",
                "FINAL_STATE_NOT_VERIFIED",
                "RESULT_AUDIT_FAILED",
                "LEASE_RELEASE_FAILED",
                "OPERATION_ALREADY_EXISTS",
                "INTERNAL_OWNER_REQUIRED",
                "CHECK_FAILED",
            }
            else "CHECK_FAILED"
        )
        super().__init__(self.code)


class Child(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


def code(error: Exception) -> str:
    return error.code if isinstance(error, RecoveryCheckBlocked) else safe_failure(error)


def check_empty(ledger: DemoLedger) -> None:
    if (
        ledger.risk_state.value != "NORMAL"
        or not ledger.paused
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or ledger.verified_leverage.get("BTCUSDT") != 2
        or any(ledger.positions.values())
        or any(
            o.remaining or o.open_quantity or o.position_state.value != "CLOSED"
            for o in ledger.orders.values()
        )
    ):
        raise RecoveryCheckBlocked("EMPTY_NORMAL_PAUSED_REQUIRED")


def remote_empty(store: DemoStore, client: ObservationClient, account: str) -> None:
    ledger = store.read(account)
    check_empty(ledger)
    client.sync_clock()
    remote = operational_snapshot(client, ledger, store.now())
    if (
        any(remote.positions.values())
        or remote.margin_used
        or any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in remote.orders.values())
        or any(r["algoStatus"] == "NEW" for r in remote.algos.values())
    ):
        raise RecoveryCheckBlocked("EMPTY_NORMAL_PAUSED_REQUIRED")


def ownership(store: DemoStore, account: str) -> dict[str, Any]:
    with store.engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout=2000"))
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == account)).mappings().one()
        )
        return dict(row)


def progress(stage: str, **details: Any) -> None:
    print(
        json.dumps(
            {
                "worker_recovery_stage": stage,
                **details,
                "entry_signal_supplied": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        ),
        flush=True,
    )


async def worker(
    coordinator: DemoCoordinator, operation: UUID, *, connector: Any = private_connect
) -> None:
    """Read-only observer: bounded 180s if the supervising process disappears."""
    if not isinstance(coordinator.client, ObservationClient):
        raise RecoveryCheckBlocked("READ_ONLY_CLIENT_REQUIRED")
    check_empty(coordinator.store.read(coordinator.lease.account_id))

    def ready() -> None:
        ledger = coordinator.store.read(coordinator.lease.account_id)
        check_empty(ledger)
        if not ledger.stream_ready or not ledger.reconciled or coordinator.session_failed:
            raise RecoveryCheckBlocked("WORKER_STATE_INVALID")

        def marker(state: DemoLedger) -> None:
            state.operations[str(operation)] = "EMPTY_WORKER_READY"

        coordinator.store.change(coordinator.lease, "DEMO_EMPTY_WORKER_READY", marker)

    await asyncio.wait_for(session(coordinator, connector=connector, on_ready=ready), timeout=180)


async def recover_session(
    coordinator: DemoCoordinator, *, connector: Any = private_connect, timeout: float = 45
) -> None:
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    ready_error = "NONE"

    def ready() -> None:
        nonlocal ready_error
        try:
            ledger = coordinator.store.read(coordinator.lease.account_id)
            check_empty(ledger)
            if not ledger.reconciled or not ledger.stream_ready or coordinator.session_failed:
                raise RecoveryCheckBlocked("RECOVERY_NOT_READY")
        except Exception as error:
            ready_error = code(error)
            raise
        finally:
            loop.call_soon_threadsafe(event.set)

    task = asyncio.create_task(session(coordinator, connector=connector, on_ready=ready))
    waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait(
            {task, waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            await task
            raise RecoveryCheckBlocked("RECOVERY_SESSION_FAILED")
        if waiter not in done or ready_error != "NONE" or coordinator.session_failed:
            raise RecoveryCheckBlocked("RECOVERY_NOT_READY")
    finally:
        waiter.cancel()
        try:
            error = await drain_session(task, cancel=True)
        finally:
            await asyncio.gather(waiter, return_exceptions=True)
        if error is not None or coordinator.session_failed:
            raise RecoveryCheckBlocked("RECOVERY_SESSION_FAILED")


async def run(
    store: DemoStore,
    client: ObservationClient,
    operation: UUID,
    spawn: Callable[[UUID], Child],
    *,
    account: str = ACCOUNT,
    startup_timeout: float = 45,
    lease_timeout: float = 75,
    poll_seconds: float = 0.5,
    connector: Any = private_connect,
) -> dict[str, Any]:
    if not isinstance(client, ObservationClient):
        raise RecoveryCheckBlocked("READ_ONLY_CLIENT_REQUIRED")
    before = store.read(account)
    check_empty(before)
    if str(operation) in before.operations:
        raise RecoveryCheckBlocked("OPERATION_ALREADY_EXISTS")
    await settled_call(remote_empty, store, client, account)
    initial = ownership(store, account)
    if initial["lease_until"] and initial["lease_until"] > store.now():
        raise RecoveryCheckBlocked("LEADER_CHANGED")
    owner = uuid4()
    child: Child | None = None
    old: Lease | None = None
    lease: Lease | None = None
    coordinator: DemoCoordinator | None = None
    killed = early_blocked = stale_blocked = recovery_gate = restored = False
    stale_renew_blocked = False
    takeover_attempted = False
    failure = "NONE"
    cleanup_failure = "NONE"
    stage = "WORKER_START"
    failure_stage = "NONE"
    wait_ms = 0

    async def takeover() -> None:
        nonlocal lease, coordinator, takeover_attempted, wait_ms
        if old is None:
            return
        takeover_attempted = True
        started = time.monotonic()
        progress("WAIT_NATURAL_LEASE_EXPIRY")
        while True:
            row = await settled_call(ownership, store, account)
            if row["owner"] != str(owner) or row["fence"] != old.fence:
                raise RecoveryCheckBlocked("LEADER_CHANGED")
            if row["lease_until"] <= store.now():
                break
            if time.monotonic() - started >= lease_timeout:
                raise RecoveryCheckBlocked("LEASE_WAIT_TIMEOUT")
            await asyncio.sleep(poll_seconds)
        wait_ms = max(0, int((time.monotonic() - started) * 1000))
        takeover_attempted = True
        lease = await settled_call(store.acquire, account, uuid4())
        if lease.fence != old.fence + 1:
            raise RecoveryCheckBlocked("LEADER_CHANGED")
        coordinator = DemoCoordinator(store, lease, client)

    try:
        child = spawn(owner)
        progress("WORKER_STARTED", worker_pid=child.pid)
        deadline = time.monotonic() + startup_timeout
        stage = "WAIT_WORKER_READY"
        while True:
            row = await settled_call(ownership, store, account)
            if row["owner"] == str(owner):
                old = Lease(account_id=account, owner=owner, fence=row["fence"])
                current = DemoLedger.model_validate(row["state"])
                if current.operations.get(str(operation)) == "EMPTY_WORKER_READY":
                    check_empty(current)
                    if (
                        not current.reconciled
                        or not current.stream_ready
                        or row["lease_until"] <= store.now()
                    ):
                        raise RecoveryCheckBlocked("WORKER_STATE_INVALID")
                    break
            if child.poll() is not None:
                raise RecoveryCheckBlocked("WORKER_EXITED_BEFORE_READY")
            if time.monotonic() >= deadline:
                raise RecoveryCheckBlocked("WORKER_READY_TIMEOUT")
            await asyncio.sleep(poll_seconds)
        stage = "VERIFY_EXCLUSIVE_LEADER"
        try:
            competitor = await settled_call(store.acquire, account, uuid4())
        except RiskRejected:
            early_blocked = True
        else:
            await settled_call(store.release, competitor)
            raise RecoveryCheckBlocked("EARLY_TAKEOVER_SUCCEEDED")
        progress("WORKER_READY_KILLING", worker_pid=child.pid)
        stage = "KILL_WORKER"
        child.kill()
        exit_code = await settled_call(child.wait, 5)
        if exit_code != -9:
            raise RecoveryCheckBlocked("PROCESS_KILL_NOT_VERIFIED")
        killed = True
        progress("WORKER_SIGKILL_VERIFIED")
        stage = "WAIT_LEASE_EXPIRY"
        await takeover()
        assert coordinator is not None and lease is not None and old is not None
        stage = "VERIFY_RECOVERY_GATE"
        state = store.read(account)
        recovery_gate = (
            not state.reconciled and not state.stream_ready and gate_blocked(coordinator, state)
        )
        if not recovery_gate:
            raise RecoveryCheckBlocked("RECOVERY_GATE_NOT_LOCKED")
        stage = "VERIFY_STALE_OWNER_REJECTED"

        # This effect must never be called: the fence check occurs before apply.
        def forbidden(_: DemoLedger) -> None:
            raise RecoveryCheckBlocked("STALE_OWNER_NOT_REJECTED")

        try:
            await settled_call(store.change, old, "DEMO_EMPTY_WORKER_STALE_PROBE", forbidden)
        except RiskRejected:
            stale_blocked = True
        else:
            raise RecoveryCheckBlocked("STALE_OWNER_NOT_REJECTED")
        try:
            await settled_call(store.renew, old)
        except RiskRejected:
            stale_renew_blocked = True
        else:
            raise RecoveryCheckBlocked("STALE_OWNER_NOT_REJECTED")
        stage = "RESTORE_AND_RECONCILE"
        await settled_call(client.sync_clock)
        await settled_call(coordinator.refresh_metadata)
        await settled_call(coordinator.reconcile)
        await recover_session(coordinator, connector=connector)
        await settled_call(remote_empty, store, client, account)
        restored = True
        progress("NEW_LEADER_RECONCILED")
    except Exception as error:
        failure, failure_stage = code(error), stage
    finally:
        try:
            if child is not None and child.poll() is None:
                child.kill()
                await settled_call(child.wait, 5)
            if child is not None and old is None:
                row = await settled_call(ownership, store, account)
                if row["owner"] == str(owner):
                    old = Lease(account_id=account, owner=owner, fence=row["fence"])
            if old is not None and lease is None and not takeover_attempted:
                row = await settled_call(ownership, store, account)
                if row["owner"] == str(owner) and row["fence"] == old.fence:
                    await takeover()
            if coordinator is not None:
                await settled_call(coordinator.disconnect)
            if lease is not None:

                def checked(state: DemoLedger) -> None:
                    state.operations.setdefault(
                        str(uuid5(operation, "worker-recovery-result")),
                        "EMPTY_WORKER_CHECKED_RELEASE_PENDING",
                    )

                try:
                    await settled_call(
                        store.change, lease, "DEMO_EMPTY_WORKER_RECOVERY_CHECKED", checked
                    )
                finally:
                    await settled_call(store.release, lease)
        except Exception as error:
            cleanup_failure = code(error)
    final = store.read(account)
    history_ok = final.model_dump(mode="json", include=PRESERVED) == before.model_dump(
        mode="json", include=PRESERVED
    )
    row = ownership(store, account)
    accepted = bool(
        killed
        and early_blocked
        and stale_blocked
        and stale_renew_blocked
        and recovery_gate
        and restored
        and failure == cleanup_failure == "NONE"
        and history_ok
        and row["owner"] is None
        and final.paused
        and final.risk_state.value == "NORMAL"
        and not final.reconciled
        and not final.stream_ready
    )
    if not accepted and failure == "NONE":
        failure = "HISTORY_CHANGED" if not history_ok else "FINAL_STATE_NOT_VERIFIED"
        failure_stage = "FINAL_STATE"
    return {
        "worker_recovery": "EMPTY_WORKER_SIGKILL_FENCED_RECOVERY_VERIFIED"
        if accepted
        else "NOT_ACCEPTED",
        "scope": "EMPTY_ACCOUNT_OBSERVATION_WORKER_SIGKILL",
        "operation_id": str(operation),
        "worker_pid": child.pid if child else None,
        "worker_exit_code": child.poll() if child else None,
        "forced_kill_verified": killed,
        "competing_leader_blocked": early_blocked,
        "lease_fence_before": old.fence if old else None,
        "lease_fence_after": lease.fence if lease else None,
        "natural_lease_wait_ms": wait_ms,
        "stale_owner_effect_blocked": stale_blocked,
        "stale_owner_renew_blocked": stale_renew_blocked,
        "entry_gate_blocked_before_reconciliation": recovery_gate,
        "new_leader_reconciled": restored,
        "history_and_baselines_preserved": history_ok,
        "failure": failure,
        "failure_stage": failure_stage,
        "cleanup_failure": cleanup_failure,
        "risk_state": final.risk_state.value,
        "final_paused": final.paused,
        "final_stream_ready": final.stream_ready,
        "final_reconciled": final.reconciled,
        "leader_released": row["owner"] is None,
        "new_entry_post_count": 0,
        "entry_signal_supplied": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
