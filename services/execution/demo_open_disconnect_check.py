"""One capped DEMO resting order, one local WSS close, cancel and reconciled reconnect."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from pydantic import SecretStr
from websockets.exceptions import ConnectionClosed

from integrations.binance_usdm.demo import DemoFailure
from packages.domain.contracts import PositionState
from scripts.check_demo_disconnect import PRESERVED, gate_blocked
from services.execution.demo import DemoCoordinator
from services.execution.demo_api_restart_check import (
    RestartBlocked,
    RestartClient,
    empty,
    open_order,
    terms,
)
from services.execution.demo_stream import Connection, private_connect, session, settled_call
from services.execution.demo_validation import confirm_exit, safe_failure


class SocketCheckBlocked(DemoFailure):
    def __init__(self, code: str):
        # All callers use fixed local codes; raw exceptions never reach this field.
        self.code = (
            code
            if code
            in {
                "RECOVERY_ONLY",
                "ENTRY_SCOPE_INVALID",
                "READY_STATE_INVALID",
                "SOCKET_LOSS_NOT_VERIFIED",
                "DISCONNECT_GATE_NOT_LOCKED",
                "RECONNECT_NOT_VERIFIED",
                "ORDER_IDENTITY_CHANGED",
                "CANCELLATION_NOT_VERIFIED",
                "DUPLICATE_ENTRY_ATTEMPT",
                "CALLBACK_NOT_COMPLETED",
                "PHASE_TIMEOUT",
                "HISTORY_CHANGED",
                "FLAT_NOT_CONFIRMED",
                "FINAL_STATE_NOT_VERIFIED",
                "RESULT_AUDIT_FAILED",
                "SECOND_SESSION_FAILED",
                "LIVE_CONTEXT_FORBIDDEN",
                "LEASE_RELEASE_FAILED",
            }
            else "CHECK_FAILED"
        )
        super().__init__(self.code)


def failure_code(error: Exception) -> str:
    if isinstance(error, (SocketCheckBlocked, RestartBlocked)):
        return error.code
    return safe_failure(error)


async def drain_session(task: asyncio.Task[None], *, cancel: bool = False) -> BaseException | None:
    """Consume all session cleanup, including repeated caller cancellation.

    A fault-observation deadline does not cancel cleanup after the receiver has
    already observed the closed socket. SDK and fencing limits remain unchanged.
    """
    current = asyncio.current_task()
    cancellations = current.cancelling() if current is not None else 0
    if cancel:
        task.cancel()
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    error = None if task.cancelled() else task.exception()
    if current is not None and current.cancelling() > cancellations:
        raise asyncio.CancelledError
    return error


async def run(
    coordinator: DemoCoordinator,
    operation: UUID,
    *,
    connector: Callable[[SecretStr], AbstractAsyncContextManager[Connection]] = private_connect,
    phase_timeout: float = 45,
) -> dict[str, Any]:
    store, lease, client = coordinator.store, coordinator.lease, coordinator.client
    if not isinstance(client, RestartClient):
        raise SocketCheckBlocked("ENTRY_SCOPE_INVALID")
    key = str(operation)
    original = store.read(lease.account_id)
    existed = key in original.orders
    if any(k != key and (o.remaining or o.open_quantity) for k, o in original.orders.items()):
        raise SocketCheckBlocked("ENTRY_SCOPE_INVALID")
    if existed and (
        original.orders[key].signal.purpose != "DEMO_VALIDATION"
        or original.orders[key].signal.direction != "LONG"
        or original.orders[key].signal.symbol != "BTCUSDT"
    ):
        raise SocketCheckBlocked("ENTRY_SCOPE_INVALID")
    if not existed:
        empty(coordinator)
    own_collections = {
        "entry_quantity_caps",
        "validation_entry_styles",
        "initial_fill_settlements",
        "validation_evidence",
    }
    preserved = (PRESERVED - {"orders", "requests"} - own_collections) | {
        "margin_mode",
        "symbols",
        "verified_leverage",
        "margin_types",
    }
    baseline = original.model_dump(mode="json", include=preserved)
    old_orders = {k: v.model_dump(mode="json") for k, v in original.orders.items() if k != key}
    # Existing request states must not be rewritten, including unresolved historical requests.
    old_requests = dict(original.requests)
    loop = asyncio.get_running_loop()
    ready_event = asyncio.Event()
    second_event = asyncio.Event()
    socket_observed = asyncio.Event()
    first_started_at = store.now().isoformat()
    first_started = time.monotonic()
    requested_at: str | None = None
    observed_at: str | None = None
    drained_at: str | None = None
    observed_elapsed_ms: int | None = None
    injected = 0
    close_completed = False
    reconnect_count = 0
    blocked = False
    verified = False
    before_row: dict[str, Any] = {}
    callback_failure = "NONE"
    stage = "SESSION_CONNECT"
    failure_stage = "NONE"
    failure = "NONE"
    completion = "UNKNOWN"
    completion_failure = "NONE"

    def progress(marker: str) -> None:
        store.change(lease, marker, lambda _: None)
        print(
            json.dumps(
                {
                    "disconnect_check_stage": marker,
                    "operation_id": key,
                    "new_entry_post_count": client.entry_post_sent,
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )

    def first_ready() -> None:
        nonlocal before_row, callback_failure, failure_stage, stage
        try:
            stage = "RESUME_ENTRIES"
            coordinator.resume_entries(uuid5(operation, "socket-resume"))
            stage = "PREPARE_ENTRY"
            signal, gate, cap = terms(coordinator, operation)
            stage = "RESERVE_ENTRY"
            order = coordinator.reserve(signal, gate, quantity_cap=cap, entry_style="PASSIVE")
            client.allowed_entry, client.allowed_quantity = order.client_id, order.quantity
            stage = "SUBMIT_ENTRY"
            coordinator.submit(key)
            stage = "VERIFY_OPEN_ORDER"
            before_row = open_order(coordinator, key)
            if coordinator.session_failed:
                raise SocketCheckBlocked("READY_STATE_INVALID")
            stage = "WAIT_SOCKET_LOSS"
            progress("DEMO_OPEN_WS_FAULT_READY")
            loop.call_soon_threadsafe(ready_event.set)
        except Exception as error:
            callback_failure, failure_stage = failure_code(error), stage
            raise

    @asynccontextmanager
    async def interrupted(url: SecretStr) -> AsyncIterator[Connection]:
        nonlocal injected, close_completed
        async with connector(url) as socket:

            class ObservedConnection:
                async def recv(self) -> str | bytes:
                    nonlocal observed_at, observed_elapsed_ms
                    try:
                        return await socket.recv()
                    except ConnectionClosed:
                        # Observe the same recv exception before EventInbox classifies
                        # it. No extra reader, frame mutation or exception suppression.
                        observed_at = store.now().isoformat()
                        observed_elapsed_ms = max(0, int((time.monotonic() - first_started) * 1000))
                        socket_observed.set()
                        raise

                async def ping(self) -> Any:
                    return await socket.ping()

            async def close_once() -> None:
                nonlocal injected, close_completed, requested_at
                await ready_event.wait()
                injected += 1
                requested_at = store.now().isoformat()
                await socket.close(code=1011)  # type: ignore[attr-defined]
                close_completed = True

            closer = asyncio.create_task(close_once())
            try:
                yield ObservedConnection()
            finally:
                if not injected:
                    closer.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(closer), timeout=3)
                except (asyncio.CancelledError, TimeoutError):
                    closer.cancel()
                    await asyncio.gather(closer, return_exceptions=True)

    def second_ready() -> None:
        nonlocal verified, callback_failure, failure_stage, stage
        try:
            stage = "VERIFY_RECONNECTED_ORDER"
            ledger = store.read(lease.account_id)
            if (
                coordinator.session_failed
                or not ledger.reconciled
                or not ledger.stream_ready
                or not ledger.paused
            ):
                raise SocketCheckBlocked("RECONNECT_NOT_VERIFIED")
            order = ledger.orders[key]
            row = client.request(
                "GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "origClientOrderId": order.client_id}
            )
            if not (
                isinstance(row, dict)
                and row.get("symbol") == "BTCUSDT"
                and row.get("clientOrderId") == order.client_id
                and row.get("status") == "CANCELED"
                and row.get("executedQty") is not None
                and Decimal(row["executedQty"]) == 0
                and row.get("origQty") is not None
                and Decimal(row["origQty"]) == order.quantity
                and row.get("price") is not None
                and Decimal(row["price"]) == order.signal.suggested_limit_price
                and row.get("side") == "BUY"
                and row.get("type") == "LIMIT"
                and row.get("reduceOnly") in (False, "false")
                and order.state.value == "CANCELED"
                and not order.filled
                and not order.remaining
                and not order.open_quantity
                and order.position_state == PositionState.CLOSED
            ):
                raise SocketCheckBlocked("CANCELLATION_NOT_VERIFIED")
            if row["orderId"] != before_row["orderId"]:
                raise SocketCheckBlocked("ORDER_IDENTITY_CHANGED")
            stage = "VERIFY_NO_DUPLICATE_ENTRY"
            coordinator.submit(key)
            if client.entry_post_attempts != 1 or client.entry_post_sent != 1:
                raise SocketCheckBlocked("DUPLICATE_ENTRY_ATTEMPT")
            progress("DEMO_OPEN_WS_FAULT_RECONNECTED")
            verified = True
        except Exception as error:
            callback_failure, failure_stage = failure_code(error), stage
            raise
        finally:
            loop.call_soon_threadsafe(second_event.set)

    try:
        if existed:
            raise SocketCheckBlocked("RECOVERY_ONLY")
        # Bound connection/setup/fault observation, not the session's finally
        # network/DB cleanup. Cancellation there can abort listen-key deletion
        # and falsely reject a socket loss already recorded as SOCKET_CLOSED.
        first = asyncio.create_task(
            session(coordinator, connector=interrupted, on_ready=first_ready)
        )
        observer = asyncio.create_task(socket_observed.wait())
        timed_out = False
        first_error: BaseException | None = None
        try:
            done, _ = await asyncio.wait(
                {first, observer}, timeout=phase_timeout, return_when=asyncio.FIRST_COMPLETED
            )
            timed_out = not done
            if socket_observed.is_set() and observed_elapsed_ms is not None:
                # The receive itself must have occurred inside the original window.
                timed_out = observed_elapsed_ms / 1000 >= phase_timeout
        finally:
            observer.cancel()
            stage = "DRAIN_DISCONNECTED_SESSION"
            try:
                first_error = await drain_session(
                    first, cancel=not socket_observed.is_set() and not first.done()
                )
                drained_at = store.now().isoformat()
            finally:
                await asyncio.gather(observer, return_exceptions=True)
        if callback_failure != "NONE":
            raise SocketCheckBlocked("CALLBACK_NOT_COMPLETED")
        if timed_out:
            failure_stage = "WAIT_SOCKET_LOSS"
            raise SocketCheckBlocked("PHASE_TIMEOUT")
        if first_error is not None and not isinstance(first_error, DemoFailure):
            if isinstance(first_error, Exception):
                raise first_error
            raise SocketCheckBlocked("SOCKET_LOSS_NOT_VERIFIED")
        failed = store.read(lease.account_id)
        detail = failed.last_private_session_failure
        if not (
            socket_observed.is_set()
            and injected == 1
            and close_completed
            and ready_event.is_set()
            and coordinator.session_failed
            and detail
            and detail.code == "SOCKET_CLOSED"
        ):
            raise SocketCheckBlocked("SOCKET_LOSS_NOT_VERIFIED")
        stage = "VERIFY_DISCONNECTED_GATE"
        blocked = (
            not failed.stream_ready
            and not failed.reconciled
            and failed.paused
            and gate_blocked(coordinator, failed)
        )
        if not blocked:
            raise SocketCheckBlocked("DISCONNECT_GATE_NOT_LOCKED")
        await settled_call(progress, "DEMO_OPEN_WS_FAULT_BLOCKED")
        # Exactly one new session; connected() performs original full REST reconciliation
        # (including fill protection) before second_ready. Never resume entries here.
        await settled_call(store.renew, lease)
        stage = "SESSION_RECONNECT"
        reconnect_count += 1
        task = asyncio.create_task(session(coordinator, connector=connector, on_ready=second_ready))
        waiter = asyncio.create_task(second_event.wait())
        try:
            done, _ = await asyncio.wait(
                {task, waiter}, timeout=phase_timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                await task
                raise SocketCheckBlocked("SECOND_SESSION_FAILED")
            if waiter not in done:
                raise SocketCheckBlocked("PHASE_TIMEOUT")
            if not verified or coordinator.session_failed:
                raise SocketCheckBlocked("RECONNECT_NOT_VERIFIED")
        finally:
            waiter.cancel()
            try:
                second_error = await drain_session(task, cancel=True)
            finally:
                await asyncio.gather(waiter, return_exceptions=True)
            if isinstance(second_error, Exception):
                raise second_error
            if coordinator.session_failed:
                raise SocketCheckBlocked("SECOND_SESSION_FAILED")
    except Exception as error:
        failure = callback_failure if callback_failure != "NONE" else failure_code(error)
        if failure_stage == "NONE":
            failure_stage = stage
    finally:
        try:
            await settled_call(store.renew, lease)
            await settled_call(client.sync_clock)
            await settled_call(
                coordinator.command, uuid5(operation, "socket-pause"), "PAUSE_ENTRIES"
            )
            state = store.read(lease.account_id)
            if any(state.positions.values()) or any(o.open_quantity for o in state.orders.values()):
                await settled_call(
                    coordinator.command, uuid5(operation, "socket-flat"), "EMERGENCY_FLATTEN"
                )
            completion = await confirm_exit(coordinator)
            await settled_call(coordinator.disconnect)
        except Exception as error:
            completion_failure = failure_code(error)
            completion = "UNKNOWN"
    final = store.read(lease.account_id)
    order = final.orders.get(key)
    history_ok = (
        final.model_dump(mode="json", include=preserved) == baseline
        and {k: v.model_dump(mode="json") for k, v in final.orders.items() if k != key}
        == old_orders
        and all(final.requests.get(k) == v for k, v in old_requests.items())
        and all(
            {k: v for k, v in getattr(final, name).items() if k != key}
            == {k: v for k, v in getattr(original, name).items() if k != key}
            for name in own_collections
        )
    )
    accepted = bool(
        verified
        and blocked
        and injected == reconnect_count == 1
        and not existed
        and failure == completion_failure == "NONE"
        and history_ok
        and completion == "VERIFIED_FLAT"
        and order
        and not order.filled
        and not order.remaining
        and not order.open_quantity
        and order.position_state == PositionState.CLOSED
        and final.risk_state.value == "NORMAL"
        and final.paused
        and not final.stream_ready
        and not final.reconciled
        and client.entry_post_attempts == client.entry_post_sent == 1
    )
    if not accepted and failure == "NONE":
        failure_stage = "FINAL_STATE"
        failure = (
            "HISTORY_CHANGED"
            if not history_ok
            else "FLAT_NOT_CONFIRMED"
            if completion != "VERIFIED_FLAT"
            else "FINAL_STATE_NOT_VERIFIED"
        )
    try:

        def audit(ledger: Any) -> None:
            ledger.operations.setdefault(
                str(uuid5(operation, "socket-result")),
                "OPEN_WS_DISCONNECT:" + ("VERIFIED" if accepted else failure),
            )

        store.change(lease, "DEMO_OPEN_WS_FAULT_RESULT", audit)
    except Exception:
        accepted = False
        if failure == "NONE":
            failure, failure_stage = "RESULT_AUDIT_FAILED", "RESULT_AUDIT"
    return {
        "disconnect_check": "OPEN_ORDER_SOCKET_LOSS_CANCEL_RECONNECT_VERIFIED"
        if accepted
        else "NOT_ACCEPTED",
        "scope": "OPEN_ORDER_LOCAL_SOCKET_CLOSE",
        "operation_id": key,
        "injected_close_count": injected,
        "reconnect_count": reconnect_count,
        "disconnect_code": "SOCKET_CLOSED" if blocked else "NOT_VERIFIED",
        "entry_gate_blocked_after_disconnect": blocked,
        "same_order_observed_before_after": verified,
        "cancelled_unfilled_order_verified": verified,
        "new_entry_post_count": client.entry_post_sent,
        "entry_post_attempts": client.entry_post_attempts,
        "recovered_existing_intent": existed,
        "first_session_started_at_utc": first_started_at,
        "socket_close_requested_at_utc": requested_at,
        "socket_closed_at_utc": observed_at,
        "first_session_completed_at_utc": drained_at,
        "socket_observed_elapsed_ms": observed_elapsed_ms,
        "fault_observation_timeout_seconds": phase_timeout,
        "last_recorded_private_session_failure": (
            {
                "stage": final.last_private_session_failure.stage,
                "code": final.last_private_session_failure.code,
            }
            if final.last_private_session_failure
            else None
        ),
        "failure": failure,
        "failure_stage": failure_stage,
        "completion_failure": completion_failure,
        "flat_confirmation": completion,
        "history_and_baselines_preserved": history_ok,
        "filled_quantity": str(order.filled) if order else "0",
        "risk_state": final.risk_state.value,
        "final_paused": final.paused,
        "final_stream_ready": final.stream_ready,
        "final_reconciled": final.reconciled,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
