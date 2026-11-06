"""One capped protected execution-child kill; independent natural-lease supervisor."""

import asyncio
import hashlib
import json
import os
import select
import signal
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from functools import partial
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4, uuid5

from sqlalchemy.exc import OperationalError

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number
from packages.domain.contracts import Signal
from packages.persistence.demo import DemoStore
from scripts.check_demo_disconnect import gate_blocked
from scripts.observe_demo_private import ACCOUNT
from services.execution.demo import DemoCoordinator
from services.execution.demo_empty_worker_recovery import ownership
from services.execution.demo_models import DemoLedger
from services.execution.demo_open_disconnect_check import drain_session
from services.execution.demo_stream import private_connect, session, settled_call
from services.execution.demo_tp_trigger_check import history_preserved
from services.execution.demo_validation import confirm_exit, prepare, safe_failure
from services.execution.models import Lease, PaperOrder
from services.risk.paper import GateSnapshot, RiskRejected

CAP = Decimal("0.0007")


class CheckBlocked(DemoFailure):
    pass


def require(value: bool, code: str) -> None:
    if not value:
        raise CheckBlocked(code)


def marker(operation: UUID, name: str) -> str:
    return str(uuid5(operation, "protected-worker-" + name))


class ScopedClient(DemoClient):
    """Original DEMO transport, with one own entry and first-generation exit only."""

    def __init__(
        self, *args: Any, account: str, operation: UUID, entry_enabled: bool, **kwargs: Any
    ):
        super().__init__(*args, **kwargs)
        self.entry_enabled = entry_enabled
        self.entry_id = "DE-" + hashlib.sha256((account + str(operation)).encode()).hexdigest()[:30]
        self.entry_attempts = self.exit_attempts = 0
        self.protection_attempts: dict[str, int] = {}

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        p = params or {}
        base = self.entry_id[3:]
        if "trade/asyn" in path:
            raise CheckBlocked("EXPORT_CREATION_FORBIDDEN")
        if method != "GET":
            quantity = Decimal(p.get("quantity", "0"))
            if path == "/fapi/v1/order" and method == "POST":
                require(
                    p.get("symbol") == "BTCUSDT" and quantity.is_finite() and 0 < quantity <= CAP,
                    "ORDER_SCOPE_INVALID",
                )
                if p.get("reduceOnly") == "true":
                    self.exit_attempts += 1
                    require(
                        self.exit_attempts == 1
                        and p.get("side") == "SELL"
                        and p.get("type") == "MARKET"
                        and p.get("newClientOrderId") == "DX-" + base,
                        "EXIT_SCOPE_OR_REPEAT_BLOCKED",
                    )
                else:
                    self.entry_attempts += 1
                    price = Decimal(p.get("price", "0"))
                    require(
                        self.entry_enabled
                        and self.entry_attempts == 1
                        and p.get("side") == "BUY"
                        and p.get("type") == "LIMIT"
                        and p.get("newClientOrderId") == self.entry_id
                        and price.is_finite()
                        and 0 < price * quantity <= 100,
                        "ENTRY_SCOPE_OR_REPEAT_BLOCKED",
                    )
            elif path == "/fapi/v1/algoOrder" and method == "POST":
                cid = p.get("clientAlgoId", "")
                expected = {"DS-" + base: "STOP_MARKET", "DT-" + base: "TAKE_PROFIT_MARKET"}
                self.protection_attempts[cid] = self.protection_attempts.get(cid, 0) + 1
                require(
                    self.entry_enabled
                    and cid in expected
                    and self.protection_attempts[cid] == 1
                    and p.get("type") == expected[cid]
                    and p.get("side") == "SELL"
                    and p.get("symbol") == "BTCUSDT"
                    and p.get("closePosition") == "true",
                    "PROTECTION_SCOPE_OR_REPEAT_BLOCKED",
                )
            elif path == "/fapi/v1/order" and method == "DELETE":
                require(
                    p.get("symbol") == "BTCUSDT" and p.get("origClientOrderId") == self.entry_id,
                    "CANCEL_SCOPE_INVALID",
                )
            elif path == "/fapi/v1/algoOrder" and method == "DELETE":
                require(
                    p.get("clientAlgoId") in {"DS-" + base, "DT-" + base}, "CLEANUP_SCOPE_INVALID"
                )
            else:
                raise CheckBlocked("MUTATION_FORBIDDEN")
        return super().request(method, path, params)


class CappedCoordinator(DemoCoordinator):
    def reserve(
        self,
        signal: Signal,
        gate: GateSnapshot,
        *,
        quantity_cap: Decimal | None = None,
        entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "OPPOSITE_QUOTE",
    ) -> PaperOrder:
        require(
            isinstance(self.client, ScopedClient)
            and self.client.entry_enabled
            and signal.symbol == "BTCUSDT"
            and signal.direction == "LONG"
            and signal.purpose == "DEMO_VALIDATION"
            and entry_style == "OPPOSITE_QUOTE"
            and quantity_cap is not None
            and 0 < quantity_cap <= CAP
            and quantity_cap * signal.suggested_limit_price <= 100,
            "ENTRY_SCOPE_INVALID",
        )
        return super().reserve(signal, gate, quantity_cap=quantity_cap, entry_style=entry_style)


def fresh_start(ledger: DemoLedger, operation: UUID) -> None:
    require(
        str(operation) not in ledger.orders and marker(operation, "start") not in ledger.operations,
        "OPERATION_ALREADY_USED_DO_NOT_REPEAT",
    )
    require(
        ledger.risk_state.value == "NORMAL"
        and ledger.paused
        and ledger.margin_mode == "ISOLATED"
        and ledger.symbols == ("BTCUSDT",)
        and ledger.verified_leverage.get("BTCUSDT") == 2
        and not any(ledger.positions.values())
        and all(
            not o.remaining and not o.open_quantity and o.position_state.value == "CLOSED"
            for o in ledger.orders.values()
        ),
        "EMPTY_NORMAL_PAUSED_REQUIRED",
    )


def protected(
    store: DemoStore, client: DemoClient, account: str, operation: UUID, *, stream: bool = False
) -> tuple[str, str, str, str]:
    state = store.read(account)
    order = state.orders.get(str(operation))
    require(order is not None, "OWN_ORDER_MISSING")
    assert order is not None
    require(
        state.paused
        and state.risk_state.value == "NORMAL"
        and (not stream or state.stream_ready and state.reconciled)
        and order.signal.symbol == "BTCUSDT"
        and order.signal.direction == "LONG"
        and order.signal.purpose == "DEMO_VALIDATION"
        and 0 < order.quantity <= CAP
        and order.filled == order.quantity == order.open_quantity
        and not order.remaining
        and order.protected
        and order.algo_id is not None
        and order.tp_algo_id is not None
        and not order.exit_id
        and all(
            k == str(operation) or not (o.remaining or o.open_quantity)
            for k, o in state.orders.items()
        )
        and state.validation_evidence.get(str(operation), {}).get("stop_verified", False)
        and state.validation_evidence.get(str(operation), {}).get("target_verified", False),
        "FULL_PROTECTED_PAUSED_REQUIRED",
    )
    client.sync_clock()
    entry = client.request(
        "GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "origClientOrderId": order.client_id}
    )
    require(
        isinstance(entry, dict)
        and entry.get("clientOrderId") == order.client_id
        and str(entry.get("orderId")) == str(order.server_order_id)
        and entry.get("status") == "FILLED"
        and entry.get("side") == "BUY"
        and entry.get("type") == "LIMIT"
        and signed_number(entry.get("executedQty")) == order.filled
        and signed_number(entry.get("origQty")) == order.quantity,
        "SIGNED_ENTRY_MISMATCH",
    )
    for cid, kind, price in [
        (order.algo_id, "STOP_MARKET", order.signal.initial_stop_price),
        (order.tp_algo_id, "TAKE_PROFIT_MARKET", order.signal.target_price),
    ]:
        row = client.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": cid or ""})
        require(
            isinstance(row, dict)
            and row.get("clientAlgoId") == cid
            and row.get("symbol") == "BTCUSDT"
            and row.get("side") == "SELL"
            and row.get("positionSide") == "BOTH"
            and row.get("algoType") == "CONDITIONAL"
            and row.get("orderType") == kind
            and row.get("algoStatus") == "NEW"
            and str(row.get("closePosition")).lower() == "true"
            and row.get("workingType") == "MARK_PRICE"
            and str(row.get("priceProtect")).lower() == "false"
            and signed_number(row.get("triggerPrice")) == price,
            "SIGNED_PROTECTION_CHANGED",
        )
    rows = client.request("GET", "/fapi/v3/positionRisk")
    require(isinstance(rows, list), "SIGNED_POSITION_INVALID")
    match = [
        r
        for r in rows
        if isinstance(r, dict) and r.get("symbol") == "BTCUSDT" and r.get("positionSide") == "BOTH"
    ]
    require(
        len(match) == 1
        and signed_number(match[0].get("positionAmt")) == order.filled
        and all(
            isinstance(r, dict) and (r in match or signed_number(r.get("positionAmt")) == 0)
            for r in rows
        ),
        "SIGNED_POSITION_CHANGED",
    )
    return order.client_id, str(order.server_order_id), order.algo_id or "", order.tp_algo_id or ""


async def finish(coordinator: DemoCoordinator, operation: UUID) -> str:
    await settled_call(coordinator.store.renew, coordinator.lease)
    await settled_call(coordinator.client.sync_clock)
    await settled_call(
        coordinator.command, UUID(marker(operation, "finish-pause")), "PAUSE_ENTRIES"
    )
    state = await settled_call(coordinator.store.read, coordinator.lease.account_id)
    if any(state.positions.values()) or any(o.open_quantity for o in state.orders.values()):
        await settled_call(coordinator.store.renew, coordinator.lease)
        await settled_call(
            coordinator.command, UUID(marker(operation, "finish-exit")), "EMERGENCY_FLATTEN"
        )
    return await confirm_exit(coordinator)


async def child_run(
    coordinator: CappedCoordinator,
    operation: UUID,
    *,
    stop: Callable[[], bool],
    connector: Any = private_connect,
    ready_timeout: float = 65,
    lifetime: float = 180,
) -> None:
    store, lease = coordinator.store, coordinator.lease
    client = coordinator.client
    require(
        isinstance(client, ScopedClient) and client.entry_enabled, "ENTRY_CHILD_CLIENT_REQUIRED"
    )
    assert isinstance(client, ScopedClient)
    fresh_start(store.read(lease.account_id), operation)
    store.change(
        lease,
        "DEMO_PROTECTED_WORKER_START",
        lambda s: s.operations.__setitem__(marker(operation, "start"), "STARTED"),
    )
    callback_done = asyncio.Event()
    loop = asyncio.get_running_loop()
    announced = False
    child_stage = "SESSION_CONNECT"
    child_failure = "NONE"

    def ready() -> None:
        nonlocal announced, child_stage
        try:
            child_stage = "RESUME_ENTRIES"
            coordinator.resume_entries(UUID(marker(operation, "resume")))
            child_stage = "PREPARE_ENTRY"
            sig, gate, cap = prepare(coordinator, operation, "LONG", entry_style="OPPOSITE_QUOTE")
            child_stage = "RESERVE_AND_SUBMIT"
            coordinator.reserve(sig, gate, quantity_cap=cap, entry_style="OPPOSITE_QUOTE")
            coordinator.submit(str(operation))
            state = store.read(lease.account_id)
            order = state.orders.get(str(operation))
            if order and order.filled == order.quantity and not order.remaining and order.protected:
                # The private receiver remains active, but session() does not dispatch
                # events/heartbeat writes until this ready callback has returned.
                # All child readiness mutations belong to this serialized callback.
                child_stage = "PAUSE_AND_VERIFY_PROTECTION"
                coordinator.command(UUID(marker(operation, "pause-new")), "PAUSE_ENTRIES")
                protected(store, client, lease.account_id, operation)

                def mark(s: DemoLedger) -> None:
                    s.operations[marker(operation, "counts")] = json.dumps(
                        {"entry": client.entry_attempts, "exit": client.exit_attempts}
                    )
                    s.operations[marker(operation, "ready")] = "FULL_PROTECTED_READY"

                store.change(lease, "DEMO_PROTECTED_WORKER_READY", mark)
                announced = True
            child_stage = "STREAM_OBSERVATION"
        finally:
            loop.call_soon_threadsafe(callback_done.set)

    task = asyncio.create_task(session(coordinator, connector=connector, on_ready=ready))
    started = time.monotonic()
    try:
        while time.monotonic() - started < lifetime and not stop():
            if task.done():
                await task
                raise CheckBlocked("CHILD_SESSION_ENDED")
            # This task only supervises time/control; it never mutates the account
            # alongside session's event/heartbeat consumer. Late/partial fills keep
            # their original protection/cleanup but do not arm this kill scenario.
            if not announced and time.monotonic() - started >= ready_timeout:
                child_failure = "PROTECTED_READY_NOT_OBSERVED_IN_CALLBACK"
                break
            await asyncio.sleep(0.2)
    except Exception as error:
        child_failure = str(error) if isinstance(error, CheckBlocked) else safe_failure(error)
        raise
    finally:
        drained_error = await drain_session(task, cancel=True)
        if child_failure == "NONE" and isinstance(drained_error, Exception):
            child_failure = (
                str(drained_error)
                if isinstance(drained_error, CheckBlocked)
                else safe_failure(drained_error)
            )
        try:
            await settled_call(
                store.change,
                lease,
                "DEMO_PROTECTED_WORKER_FINALLY",
                lambda s: s.operations.update(
                    {
                        marker(operation, "finally"): "FINALLY_RAN",
                        marker(operation, "failure"): json.dumps(
                            {"stage": child_stage, "code": child_failure}
                        ),
                    }
                ),
            )
            await finish(coordinator, operation)
        finally:
            await settled_call(store.release, lease)


class Child(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def stop(self) -> None: ...


def control_stop(parent: int) -> bool:
    if os.getppid() != parent:
        return True
    ready, _, _ = select.select([0], [], [], 0)
    return bool(ready and os.read(0, 1) in {b"", b"S"})


async def require_active_child(store: DemoStore, lease: Lease, child: Child) -> None:
    require(child.poll() is None, "CHILD_ALREADY_EXITED")
    row = await settled_call(ownership, store, lease.account_id)
    require(
        row["owner"] == str(lease.owner) and row["fence"] == lease.fence,
        "CHILD_LEASE_CHANGED_BEFORE_KILL",
    )
    require(
        row["lease_until"] is not None and row["lease_until"] > store.now(),
        "CHILD_LEASE_EXPIRED_BEFORE_KILL",
    )


async def reject_competing_leader(
    store: DemoStore,
    lease: Lease,
    child: Child,
    counts: dict[str, int],
    *,
    timeout: float = 15,
) -> None:
    """A row-lock timeout is inconclusive; only actual active-lease rejection passes.

    The original store's 2s lock timeout and 60s lease remain unchanged. Each
    timed-out acquire rolls back before another bounded probe is attempted.
    This never retries an order or starts another execution child.
    """
    deadline = time.monotonic() + timeout
    while True:
        require(time.monotonic() < deadline, "COMPETING_LEADER_PROBE_BUSY")
        await require_active_child(store, lease, child)
        counts["attempts"] += 1
        try:
            competitor = await settled_call(store.acquire, lease.account_id, uuid4())
        except RiskRejected:
            await require_active_child(store, lease, child)
            return
        except OperationalError as error:
            if getattr(error.orig, "sqlstate", None) != "55P03":
                raise
            counts["lock_timeouts"] += 1
            await asyncio.sleep(0.25)
        else:
            await settled_call(store.release, competitor)
            raise CheckBlocked("EARLY_TAKEOVER_SUCCEEDED")


async def restore(
    coordinator: DemoCoordinator,
    operation: UUID,
    identities: tuple[str, str, str, str] | None,
    *,
    connector: Any = private_connect,
) -> bool:
    """Drain a real private session; only callback evidence counts as restored."""
    ready = asyncio.Event()
    loop = asyncio.get_running_loop()
    valid = False

    def on_ready() -> None:
        nonlocal valid
        if identities is not None:
            require(
                protected(
                    coordinator.store,
                    coordinator.client,
                    coordinator.lease.account_id,
                    operation,
                    stream=True,
                )
                == identities,
                "RECOVERED_IDENTITIES_CHANGED",
            )
            valid = True
        loop.call_soon_threadsafe(ready.set)

    task = asyncio.create_task(session(coordinator, connector=connector, on_ready=on_ready))
    waiter = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait(
            {task, waiter}, timeout=45, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            await task
            raise CheckBlocked("RECOVERY_SESSION_ENDED")
        require(waiter in done, "RECOVERY_SESSION_TIMEOUT")
        return valid
    finally:
        waiter.cancel()
        error = await drain_session(task, cancel=True)
        await asyncio.gather(waiter, return_exceptions=True)
        if error is not None or coordinator.session_failed:
            raise CheckBlocked("RECOVERY_SESSION_FAILED")


async def run(
    store: DemoStore,
    client: ScopedClient,
    operation: UUID,
    spawn: Callable[[UUID], Child],
    *,
    account: str = ACCOUNT,
    connector: Any = private_connect,
    startup_timeout: float = 80,
    lease_timeout: float = 75,
) -> dict[str, Any]:
    require(not client.entry_enabled, "RECOVERY_CLIENT_MUST_FORBID_ENTRY")
    original = store.read(account)
    fresh_start(original, operation)
    row = ownership(store, account)
    require(
        row["lease_until"] is None or row["lease_until"] <= store.now(), "LEADER_ALREADY_ACTIVE"
    )
    owner = uuid4()
    child: Child | None = None
    old: Lease | None = None
    lease: Lease | None = None
    coordinator: DemoCoordinator | None = None
    identity: tuple[str, str, str, str] | None = None
    killed = early = stale = stale_renew = restored = recovery_gate = False
    continuity = True
    probes = wait_ms = kill_attempts = 0
    failure = cleanup_failure = "NONE"
    flat = "UNKNOWN"
    fixed_until = None
    remaining_ms = 0
    probe_counts = {"attempts": 0, "lock_timeouts": 0}
    stage = "CHILD_START"
    failure_stage = "NONE"

    def log(stage: str) -> None:
        print(
            json.dumps(
                {
                    "protected_worker_stage": stage,
                    "operation_id": str(operation),
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )

    try:
        child = spawn(owner)
        log("CHILD_STARTED")
        stage = "WAIT_PROTECTED_READY"
        deadline = time.monotonic() + startup_timeout
        while True:
            row = await settled_call(ownership, store, account)
            if row["owner"] == str(owner):
                old = Lease(account_id=account, owner=owner, fence=row["fence"])
                state = DemoLedger.model_validate(row["state"])
                if state.operations.get(marker(operation, "ready")) == "FULL_PROTECTED_READY":
                    counts = json.loads(state.operations[marker(operation, "counts")])
                    require(counts == {"entry": 1, "exit": 0}, "CHILD_MUTATION_COUNTS_INVALID")
                    stage = "SIGNED_PROTECTION_CHECK"
                    identity = await asyncio.to_thread(
                        protected, store, client, account, operation, stream=True
                    )
                    break
            require(child.poll() is None, "CHILD_EXITED_BEFORE_PROTECTED_READY")
            require(time.monotonic() < deadline, "PROTECTED_READY_NOT_OBSERVED")
            await asyncio.sleep(0.25)
        assert old is not None
        stage = "COMPETING_LEADER_PROBE"
        await reject_competing_leader(store, old, child, probe_counts)
        early = True
        # A busy row can delay the probe. Revalidate signed protection identities
        # and the still-live owner after that delay; never kill on stale proof.
        stage = "FINAL_SIGNED_PROTECTION_CHECK"
        require(
            await settled_call(partial(protected, store, client, account, operation, stream=True))
            == identity,
            "PROTECTION_IDENTITIES_CHANGED",
        )
        await require_active_child(store, old, child)
        require(child.poll() is None, "CHILD_ALREADY_EXITED")
        stage = "SIGKILL"
        kill_attempts += 1
        child.kill()
        require(await settled_call(child.wait, 5) == -signal.SIGKILL, "SIGKILL_NOT_VERIFIED")
        killed = True
        log("SIGKILL_VERIFIED_WAITING_NATURAL_LEASE")
    except Exception as error:
        failure_stage = stage
        failure = str(error) if isinstance(error, CheckBlocked) else safe_failure(error)
    finally:
        try:
            if child is not None and child.poll() is None:
                child.stop()  # Cooperative cleanup only; never an unprotected kill.
                await settled_call(child.wait, 120)
            row = await settled_call(ownership, store, account)
            if row["owner"] == str(owner):
                old = Lease(account_id=account, owner=owner, fence=row["fence"])
                fixed_until = row["lease_until"]
                remaining_ms = max(0, int((fixed_until - store.now()).total_seconds() * 1000))
                started = time.monotonic()
                last_probe = 0.0
                while store.now() < fixed_until:
                    current = await settled_call(ownership, store, account)
                    require(
                        current["owner"] == str(owner)
                        and current["fence"] == old.fence
                        and current["lease_until"] == fixed_until,
                        "LEASE_CHANGED_DURING_NATURAL_WAIT",
                    )
                    require(
                        time.monotonic() - started < lease_timeout, "NATURAL_LEASE_WAIT_TIMEOUT"
                    )
                    if killed and time.monotonic() - last_probe >= 2:
                        try:
                            require(
                                await settled_call(protected, store, client, account, operation)
                                == identity,
                                "PROTECTION_IDENTITIES_CHANGED",
                            )
                        except Exception:
                            continuity = (
                                False  # Trigger/query failure never prevents takeover cleanup.
                            )
                        probes += 1
                        last_probe = time.monotonic()
                    await asyncio.sleep(
                        min(0.5, max(0, (fixed_until - store.now()).total_seconds()))
                    )
                wait_ms = int((time.monotonic() - started) * 1000)
                require(wait_ms + 250 >= remaining_ms, "NATURAL_WAIT_SHORTENED")
            else:
                require(row["owner"] is None, "UNEXPECTED_LEADER_DO_NOT_INTERFERE")
            lease = await settled_call(store.acquire, account, uuid4())
            coordinator = DemoCoordinator(store, lease, client)
            if killed:
                require(old is not None and lease.fence == old.fence + 1, "FENCE_NOT_ADVANCED")
                require(
                    marker(operation, "finally") not in store.read(account).operations,
                    "CHILD_FINALLY_RAN",
                )
                state = await settled_call(store.read, account)
                recovery_gate = (
                    not state.reconciled
                    and not state.stream_ready
                    and gate_blocked(coordinator, state)
                )
                require(recovery_gate, "RECOVERY_GATE_NOT_LOCKED")

                def forbidden(_: DemoLedger) -> None:
                    raise CheckBlocked("STALE_EFFECT_EXECUTED")

                try:
                    await settled_call(store.change, old, "DEMO_PROTECTED_STALE_PROBE", forbidden)
                except RiskRejected:
                    stale = True
                try:
                    await settled_call(store.renew, old)
                except RiskRejected:
                    stale_renew = True
                require(stale and stale_renew, "STALE_OWNER_NOT_BLOCKED")
            await settled_call(client.sync_clock)
            await settled_call(coordinator.refresh_metadata)
            try:
                restored = await restore(
                    coordinator, operation, identity if continuity else None, connector=connector
                )
            except Exception as error:
                failure = (
                    failure
                    if failure != "NONE"
                    else str(error)
                    if isinstance(error, CheckBlocked)
                    else safe_failure(error)
                )
        except Exception as error:
            cleanup_failure = str(error) if isinstance(error, CheckBlocked) else safe_failure(error)
        finally:
            if coordinator is not None and lease is not None:
                try:
                    flat = await finish(coordinator, operation)
                except Exception as error:
                    cleanup_failure = (
                        str(error) if isinstance(error, CheckBlocked) else safe_failure(error)
                    )
                finally:
                    try:
                        await settled_call(coordinator.disconnect)
                        await settled_call(store.release, lease)
                    except Exception:
                        cleanup_failure = "LEASE_RELEASE_FAILED"
    final = store.read(account)
    preserved = history_preserved(original, final, str(operation))
    released = ownership(store, account)["owner"] is None
    accepted = (
        killed
        and early
        and stale
        and stale_renew
        and restored
        and recovery_gate
        and continuity
        and preserved
        and released
        and flat == "VERIFIED_FLAT"
        and final.paused
        and client.entry_attempts == 0
        and client.exit_attempts <= 1
        and not client.protection_attempts
        and failure == cleanup_failure == "NONE"
    )
    if not accepted and failure == "NONE":
        failure = (
            "PROTECTION_CHANGED_OR_QUERY_FAILED_DURING_WAIT"
            if not continuity
            else "PROTECTED_RECOVERY_NOT_VERIFIED"
        )
    return {
        "protected_worker_check": "PROTECTED_CHILD_SIGKILL_FENCED_RECOVERY_FLAT_VERIFIED"
        if accepted
        else "NOT_ACCEPTED_KEEP_PAUSED",
        "operation_id": str(operation),
        "worker_exit_code": child.poll() if child else None,
        "kill_count": kill_attempts,
        "forced_kill_verified": killed,
        "finally_marker_absent": marker(operation, "finally") not in final.operations,
        "competing_leader_blocked": early,
        "competing_leader_probe_attempts": probe_counts["attempts"],
        "competing_leader_lock_timeouts": probe_counts["lock_timeouts"],
        "prekill_failure_stage": failure_stage,
        "lease_fence_before": old.fence if old else None,
        "lease_fence_after": lease.fence if lease else None,
        "natural_lease_wait_ms": wait_ms,
        "remaining_at_wait_start_ms": remaining_ms,
        "protection_probe_count": probes,
        "protected_continuity_observed": continuity and killed,
        "stale_effect_blocked": stale,
        "stale_renew_blocked": stale_renew,
        "entry_gate_blocked_before_reconciliation": recovery_gate,
        "same_ids_reconciled_in_new_session": restored,
        "history_and_baselines_preserved": preserved,
        "parent_entry_post_attempts": client.entry_attempts,
        "parent_exit_post_attempts": client.exit_attempts,
        "parent_protection_post_attempts": sum(client.protection_attempts.values()),
        "flat_confirmation": flat,
        "leader_released": released,
        "failure": failure,
        "cleanup_failure": cleanup_failure,
        "risk_state": final.risk_state.value,
        "final_paused": final.paused,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
