"""Bounded API restart experiment under an independent, live DEMO supervisor."""

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid5

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import operational_snapshot
from packages.domain.contracts import PositionState, Signal
from services.execution.demo import DemoCoordinator
from services.execution.demo_preview import ceil_step
from services.execution.demo_stream import session, settled_call
from services.execution.demo_validation import confirm_exit, prepare, safe_failure
from services.risk.paper import GateSnapshot, RiskRejected, minimum_entry_quantity, size, unit_loss

API_URL = "http://api:8000/api/v1/system/instance"
WAIT_SECONDS = 45


class RestartBlocked(DemoFailure):
    def __init__(self, code: str):
        self.code = (
            code
            if code
            in {
                "API_INSTANCE_UNAVAILABLE",
                "API_CONTEXT_INVALID",
                "EMPTY_NORMAL_PAUSED_REQUIRED",
                "ENTRY_NOT_OPEN_UNFILLED",
                "API_RESTART_NOT_OBSERVED",
                "STREAM_FAILED",
                "PRE_RECONCILIATION_GATE_NOT_LOCKED",
                "DUPLICATE_ENTRY_ATTEMPT",
                "ENTRY_SCOPE_INVALID",
                "EXPERIMENT_BUDGET_EXCEEDED",
                "HISTORY_CHANGED",
                "WORKER_CONTAINER_REQUIRED",
                "CHECK_FAILED",
                "LEASE_RELEASE_FAILED",
            }
            else "CHECK_FAILED"
        )
        super().__init__(self.code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def api_instance() -> str:
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(
            Request(API_URL, method="GET"), timeout=2
        ) as response:
            raw = response.read(16385)
            if len(raw) > 16384:
                raise RestartBlocked("API_CONTEXT_INVALID")
            body = json.loads(raw)
    except (URLError, HTTPError, TimeoutError, OSError):
        raise RestartBlocked("API_INSTANCE_UNAVAILABLE") from None
    except (ValueError, UnicodeError):
        raise RestartBlocked("API_CONTEXT_INVALID") from None
    if (
        not isinstance(body, dict)
        or body.get("environment") not in {"PAPER", "DEMO"}
        or body.get("orders_enabled") is not False
        or body.get("live_trading_enabled") is not False
        or not isinstance(body.get("instance_id"), str)
    ):
        raise RestartBlocked("API_CONTEXT_INVALID")
    try:
        return str(UUID(body["instance_id"]))
    except ValueError:
        raise RestartBlocked("API_CONTEXT_INVALID") from None


class RestartClient(DemoClient):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.allowed_entry: str | None = None
        self.allowed_quantity = Decimal(0)
        self.entry_post_attempts = 0
        self.entry_post_sent = 0

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        values = params or {}
        if method == "POST" and path == "/fapi/v1/order" and values.get("reduceOnly") != "true":
            self.entry_post_attempts += 1
            if self.entry_post_attempts != 1:
                raise RestartBlocked("DUPLICATE_ENTRY_ATTEMPT")
            if (
                values.get("newClientOrderId") != self.allowed_entry
                or self.allowed_entry is None
                or values.get("symbol") != "BTCUSDT"
                or values.get("side") != "BUY"
                or values.get("type") != "LIMIT"
                or Decimal(values.get("quantity", "0")) != self.allowed_quantity
            ):
                raise RestartBlocked("ENTRY_SCOPE_INVALID")
            self.entry_post_sent += 1
        return super().request(method, path, params)


def empty(coordinator: DemoCoordinator) -> None:
    ledger = coordinator.store.read(coordinator.lease.account_id)
    if (
        not ledger.paused
        or ledger.risk_state.value != "NORMAL"
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or ledger.verified_leverage.get("BTCUSDT") != 2
        or any(ledger.positions.values())
        or any(
            o.remaining or o.open_quantity or o.position_state != PositionState.CLOSED
            for o in ledger.orders.values()
        )
    ):
        raise RestartBlocked("EMPTY_NORMAL_PAUSED_REQUIRED")
    remote = operational_snapshot(coordinator.client, ledger, coordinator.store.now())
    if (
        any(remote.positions.values())
        or any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in remote.orders.values())
        or any(r["algoStatus"] == "NEW" for r in remote.algos.values())
    ):
        raise RestartBlocked("EMPTY_NORMAL_PAUSED_REQUIRED")


def terms(coordinator: DemoCoordinator, operation: UUID) -> tuple[Signal, GateSnapshot, Decimal]:
    base, gate, _ = prepare(coordinator, operation, "LONG", entry_style="PASSIVE")
    rules = coordinator.rules["BTCUSDT"]
    price = rules.floor_price(gate.bid * Decimal("0.997"))
    stop = rules.floor_price(min(price, gate.mark) * Decimal("0.995"))
    target = ceil_step(max(price, gate.mark) * Decimal("1.01"), rules.tick_size)
    cap = minimum_entry_quantity(rules, price)
    if cap * price > 100 or not stop < price < gate.ask < target:
        raise RestartBlocked("EXPERIMENT_BUDGET_EXCEEDED")
    # A fixed DEMO-only resting-order experiment, not a strategy/LIVE default.
    # Actual admission still runs unmodified size/reserve/submit freshness and risk gates.
    signal = Signal.model_validate(
        base.model_dump()
        | {
            "suggested_limit_price": price,
            "initial_stop_price": stop,
            "target_price": target,
            "expires_at": base.expires_at + timedelta(seconds=50),
        }
    )
    ledger = coordinator.store.read(coordinator.lease.account_id)
    allowed, _ = size(
        ledger.policy,
        signal,
        gate,
        ledger.value.equity,
        rules,
        coordinator.store.now(),
        quantity_cap=cap,
    )
    if allowed != cap:
        raise RestartBlocked("EXPERIMENT_BUDGET_EXCEEDED")
    return signal, gate, cap


def open_order(coordinator: DemoCoordinator, key: str) -> dict[str, Any]:
    order = coordinator.store.read(coordinator.lease.account_id).orders[key]
    row = coordinator.client.request(
        "GET",
        "/fapi/v1/order",
        {
            "symbol": "BTCUSDT",
            "origClientOrderId": order.client_id,
        },
    )
    if (
        not isinstance(row, dict)
        or row.get("symbol") != "BTCUSDT"
        or row.get("clientOrderId") != order.client_id
        or row.get("status") != "NEW"
        or Decimal(row.get("executedQty", "-1")) != 0
        or row.get("type") != "LIMIT"
        or row.get("side") != "BUY"
        or row.get("reduceOnly") not in (False, "false")
        or Decimal(row.get("origQty", "0")) != order.quantity
        or Decimal(row.get("price", "0")) != order.signal.suggested_limit_price
        or (order.server_order_id is not None and str(row.get("orderId")) != order.server_order_id)
        or order.filled
        or order.open_quantity
        or order.remaining != order.quantity
    ):
        raise RestartBlocked("ENTRY_NOT_OPEN_UNFILLED")
    return row


async def preview(coordinator: DemoCoordinator, *, session_call: Any = session) -> dict[str, Any]:
    empty(coordinator)
    instance = api_instance()
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    report: dict[str, Any] = {}

    def ready() -> None:
        signal, _, cap = terms(coordinator, UUID(int=0))
        report.update(
            {
                "preview": "OPEN_API_RESTART_NO_ENTRY",
                "api_instance": instance,
                "limit_price": str(signal.suggested_limit_price),
                "quantity_cap": str(cap),
                "notional_usdt": str(cap * signal.suggested_limit_price),
                "stop_price": str(signal.initial_stop_price),
                "target_price": str(signal.target_price),
                "estimated_stop_loss_usdt": str(
                    cap
                    * unit_loss(
                        coordinator.client.settings.policy,
                        signal.suggested_limit_price,
                        signal.initial_stop_price,
                    )
                ),
                "resting_offset_pct": "0.3",
                "experiment_order_ttl_seconds": 60,
                "restart_wait_seconds": WAIT_SECONDS,
                "entry_signal_supplied": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        )
        loop.call_soon_threadsafe(done.set)

    task = asyncio.create_task(session_call(coordinator, on_ready=ready))
    waiter = asyncio.create_task(done.wait())
    try:
        completed, _ = await asyncio.wait(
            {task, waiter}, timeout=45, return_when=asyncio.FIRST_COMPLETED
        )
        if task in completed:
            await task
        if not report or coordinator.session_failed:
            raise RestartBlocked("CHECK_FAILED")
        return report
    finally:
        waiter.cancel()
        task.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        try:
            await task
        except asyncio.CancelledError:
            pass


async def run(
    coordinator: DemoCoordinator,
    operation: UUID,
    *,
    instance_reader: Callable[[], str] = api_instance,
    wait_seconds: float = WAIT_SECONDS,
    poll_seconds: float = 1,
    session_call: Any = session,
) -> dict[str, Any]:
    store, lease, client = coordinator.store, coordinator.lease, coordinator.client
    if not isinstance(client, RestartClient):
        raise RestartBlocked("ENTRY_SCOPE_INVALID")
    key = str(operation)
    original = store.read(lease.account_id)
    existed = key in original.orders
    if any(k != key and (o.remaining or o.open_quantity) for k, o in original.orders.items()):
        raise RestartBlocked("ENTRY_SCOPE_INVALID")
    if existed and (
        original.orders[key].signal.purpose != "DEMO_VALIDATION"
        or original.orders[key].signal.direction != "LONG"
        or original.orders[key].signal.symbol != "BTCUSDT"
    ):
        raise RestartBlocked("ENTRY_SCOPE_INVALID")
    if not existed:
        empty(coordinator)
    baseline_orders = {k: v.model_dump(mode="json") for k, v in original.orders.items() if k != key}
    baseline_requests = dict(original.requests)
    api_reads = 0
    api_unavailable = 0
    last_seen_instance: str | None = None

    def read_api() -> str:
        nonlocal api_reads, api_unavailable, last_seen_instance
        api_reads += 1
        try:
            value = instance_reader()
        except RestartBlocked as error:
            if error.code == "API_INSTANCE_UNAVAILABLE":
                api_unavailable += 1
            raise
        last_seen_instance = value
        return value

    before_instance = read_api() if not existed else None
    after_instance: str | None = None
    verified = False
    callback_done = asyncio.Event()
    loop = asyncio.get_running_loop()
    failure = "NONE"
    completion = "UNKNOWN"
    completion_failure = "NONE"
    callback_failure = "NONE"
    failure_stage = "NONE"
    stage = "SESSION_CONNECT"
    ready_at_utc: str | None = None
    wait_deadline_utc: str | None = None
    wait_started: float | None = None
    wait_elapsed_ms = 0

    def ready() -> None:
        nonlocal verified, after_instance, callback_failure, failure_stage, stage
        nonlocal ready_at_utc, wait_deadline_utc, wait_started, wait_elapsed_ms
        try:
            if existed:
                return  # Recover the existing intent in finally; never make another entry.
            stage = "RESUME_ENTRIES"
            coordinator.resume_entries(uuid5(operation, "restart-resume"))
            stage = "PREPARE_ENTRY"
            signal, gate, cap = terms(coordinator, operation)
            stage = "RESERVE_ENTRY"
            order = coordinator.reserve(signal, gate, quantity_cap=cap, entry_style="PASSIVE")
            client.allowed_entry, client.allowed_quantity = order.client_id, order.quantity
            stage = "SUBMIT_ENTRY"
            coordinator.submit(key)
            stage = "VERIFY_OPEN_ORDER"
            before_row = open_order(coordinator, key)
            ready_time = store.now()
            wait_limit = min(
                wait_seconds, max(0, (signal.expires_at - ready_time).total_seconds() - 2)
            )
            if wait_limit <= 0:
                raise RestartBlocked("API_RESTART_NOT_OBSERVED")
            ready_at_utc = ready_time.isoformat()
            wait_deadline_utc = (ready_time + timedelta(seconds=wait_limit)).isoformat()
            wait_started = time.monotonic()
            deadline = wait_started + wait_limit

            def publish_ready(ledger: Any) -> None:
                ledger.operations[str(uuid5(operation, "restart-ready"))] = "OPEN_API_RESTART_READY"

            store.change(lease, "DEMO_OPEN_API_RESTART_READY", publish_ready)
            print(
                json.dumps(
                    {
                        "restart_check_stage": "OPEN_ORDER_READY_RESTART_API_NOW",
                        "operation_id": key,
                        "api_instance_before": before_instance,
                        "ready_at_utc": ready_at_utc,
                        "wait_deadline_utc": wait_deadline_utc,
                        "quantity": str(order.quantity),
                        "limit_price": str(signal.suggested_limit_price),
                        "new_entry_post_count": client.entry_post_sent,
                        "live_trading_enabled": False,
                    }
                ),
                flush=True,
            )
            while time.monotonic() < deadline:
                if coordinator.session_failed:
                    raise RestartBlocked("STREAM_FAILED")
                store.renew(lease)
                client.sync_clock()
                stage = "VERIFY_PENDING_ORDER"
                try:
                    open_order(coordinator, key)
                except RestartBlocked:
                    # A changed/filling entry must immediately enter the original
                    # reconciliation/protection path. While it remains signed NEW
                    # with zero fill, keep the just-published ready state stable
                    # for the host handoff instead of repeatedly invalidating it.
                    stage = "RECONCILE_CHANGED_ORDER"
                    coordinator.reconcile()
                    raise
                if time.monotonic() >= deadline:
                    raise RestartBlocked("API_RESTART_NOT_OBSERVED")
                stage = "READ_API_INSTANCE"
                try:
                    current = read_api()
                except RestartBlocked as error:
                    if error.code != "API_INSTANCE_UNAVAILABLE":
                        raise
                else:
                    if time.monotonic() >= deadline:
                        raise RestartBlocked("API_RESTART_NOT_OBSERVED")
                    if current != before_instance:
                        stage = "LOCK_RESTART_GATE"
                        after_instance = current

                        def lock(ledger: Any) -> None:
                            ledger.paused = True
                            ledger.reconciled = ledger.stream_ready = False

                        store.change(lease, "DEMO_API_RESTART_GATE_LOCKED", lock)
                        probe = store.read(lease.account_id).model_copy(deep=True)
                        probe.paused = False
                        try:
                            coordinator._fresh(probe)
                        except RiskRejected:
                            pass
                        else:
                            raise RestartBlocked("PRE_RECONCILIATION_GATE_NOT_LOCKED")
                        stage = "RECONCILE_AFTER_API_RESTART"
                        coordinator.connected()  # Existing guard WSS remains alive; REST recheck.
                        if coordinator.session_failed:
                            raise RestartBlocked("STREAM_FAILED")
                        after_row = open_order(coordinator, key)
                        if after_row["orderId"] != before_row["orderId"]:
                            raise RestartBlocked("ENTRY_SCOPE_INVALID")
                        stage = "VERIFY_NO_DUPLICATE_ENTRY"
                        coordinator.submit(key)  # Same durable intent: must not issue another POST.
                        if client.entry_post_attempts != 1 or client.entry_post_sent != 1:
                            raise RestartBlocked("DUPLICATE_ENTRY_ATTEMPT")
                        verified = True
                        store.change(lease, "DEMO_OPEN_API_RESTART_OBSERVED", lambda _: None)
                        return
                time.sleep(poll_seconds)
            stage = "WAIT_API_RESTART"
            raise RestartBlocked("API_RESTART_NOT_OBSERVED")
        except Exception as error:
            # Preserve the worker's reason BEFORE announcing completion. The
            # session awaiter may be canceled before it receives this exception.
            callback_failure = (
                error.code if isinstance(error, RestartBlocked) else safe_failure(error)
            )
            failure_stage = stage
            raise
        finally:
            if wait_started is not None:
                wait_elapsed_ms = min(
                    3600000, max(0, int((time.monotonic() - wait_started) * 1000))
                )
            loop.call_soon_threadsafe(callback_done.set)

    task = asyncio.create_task(session_call(coordinator, on_ready=ready))
    waiter = asyncio.create_task(callback_done.wait())
    try:
        done, _ = await asyncio.wait(
            {task, waiter}, timeout=90, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            await task
        if waiter not in done or coordinator.session_failed:
            raise RestartBlocked("CHECK_FAILED")
    except Exception as error:
        failure = (
            callback_failure
            if callback_failure != "NONE"
            else (error.code if isinstance(error, RestartBlocked) else safe_failure(error))
        )
        if failure_stage == "NONE":
            failure_stage = stage
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as error:
            failure = error.code if isinstance(error, RestartBlocked) else "CHECK_FAILED"
        if callback_failure != "NONE":
            failure = callback_failure
        if coordinator.session_failed and failure == "NONE":
            failure = "STREAM_FAILED"
        try:
            await settled_call(store.renew, lease)
            await settled_call(client.sync_clock)
            await settled_call(
                coordinator.command, uuid5(operation, "restart-pause"), "PAUSE_ENTRIES"
            )
            state = store.read(lease.account_id)
            if any(state.positions.values()) or any(o.open_quantity for o in state.orders.values()):
                await settled_call(
                    coordinator.command, uuid5(operation, "restart-flat"), "EMERGENCY_FLATTEN"
                )
            completion = await confirm_exit(coordinator)
            await settled_call(coordinator.disconnect)
        except Exception as error:
            completion = "UNKNOWN"
            completion_failure = safe_failure(error)
    final = store.read(lease.account_id)
    order = final.orders.get(key)
    history_ok = (
        {k: v.model_dump(mode="json") for k, v in final.orders.items() if k != key}
        == baseline_orders
        and all(final.requests.get(k) == v for k, v in baseline_requests.items())
        and all(
            getattr(original, k) == getattr(final, k)
            for k in (
                "daily_key",
                "daily_base",
                "weekly_key",
                "weekly_base",
                "high_water",
                "net_transfers",
                "income_seen",
                "policy",
                "margin_mode",
                "symbols",
                "verified_leverage",
                "margin_types",
                "recovery_receipts",
                "cancelled_exposure_corrections",
                "attested_day_baselines",
                "exit_anchored_day_baselines",
                "attested_period_baselines",
            )
        )
    )
    accepted = bool(
        verified
        and not existed
        and failure == "NONE"
        and completion == "VERIFIED_FLAT"
        and history_ok
        and order
        and not order.filled
        and not order.remaining
        and not order.open_quantity
        and order.position_state == PositionState.CLOSED
        and client.entry_post_sent == 1
        and final.paused
        and not final.stream_ready
        and not final.reconciled
    )
    if not accepted and failure == "NONE":
        failure_stage = "FINAL_STATE"
        if existed:
            failure = "RECOVERY_ONLY"
        elif not verified:
            failure = "API_RESTART_NOT_VERIFIED"
        elif completion != "VERIFIED_FLAT":
            failure = "FLAT_NOT_CONFIRMED"
        elif not history_ok:
            failure = "HISTORY_CHANGED"
        elif order and order.filled:
            failure = "ENTRY_FILLED_DURING_CHECK"
        else:
            failure = "FINAL_STATE_NOT_VERIFIED"
    try:
        result_key = str(uuid5(operation, "restart-result"))

        def record_result(ledger: Any) -> None:
            if result_key not in ledger.operations:
                ledger.operations[result_key] = "OPEN_API_RESTART:" + (
                    "VERIFIED" if accepted else failure
                )

        store.change(lease, "DEMO_OPEN_API_RESTART_RESULT", record_result)
    except Exception:
        accepted = False
        if failure == "NONE":
            failure = "RESULT_AUDIT_FAILED"
            failure_stage = "RESULT_AUDIT"
    return {
        "restart_check": "OPEN_ORDER_API_RESTART_VERIFIED" if accepted else "NOT_ACCEPTED",
        "scope": "API_RESTART_WITH_INDEPENDENT_GUARD",
        "operation_id": key,
        "api_instance_before": before_instance,
        "api_instance_after": after_instance,
        "same_order_observed_before_after": verified,
        "new_entry_post_count": client.entry_post_sent,
        "entry_post_attempts": client.entry_post_attempts,
        "recovered_existing_intent": existed,
        "failure": failure,
        "failure_stage": failure_stage if failure != "NONE" else "NONE",
        "completion_failure": completion_failure,
        "ready_at_utc": ready_at_utc,
        "wait_deadline_utc": wait_deadline_utc,
        "wait_elapsed_ms": wait_elapsed_ms,
        "api_reads": api_reads,
        "api_unavailable_count": api_unavailable,
        "last_seen_api_instance": last_seen_instance,
        "risk_state": final.risk_state.value,
        "flat_confirmation": completion,
        "history_and_baselines_preserved": history_ok,
        "filled_quantity": str(order.filled) if order else "0",
        "final_paused": final.paused,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
