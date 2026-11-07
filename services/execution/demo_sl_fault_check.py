"""Own-SL local build failure: original emergency gate and automatic reduce-only exit."""

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal
from unittest.mock import patch
from uuid import UUID, uuid5

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.filters import SymbolRules
from integrations.binance_usdm.orders import OrderIntent
from packages.domain.contracts import AlgoOrder
from services.execution import demo as core
from services.execution.demo import DemoCoordinator
from services.execution.demo_api_restart_check import RestartBlocked, RestartClient, empty
from services.execution.demo_open_disconnect_check import drain_session
from services.execution.demo_stream import private_connect, session, settled_call
from services.execution.demo_validation import confirm_exit, prepare, safe_failure
from services.risk.paper import RiskRejected


class SLFaultBlocked(DemoFailure):
    def __init__(self, code: str):
        self.code = (
            code
            if code
            in {
                "LIVE_CONTEXT_FORBIDDEN",
                "ENTRY_SCOPE_INVALID",
                "RECOVERY_ONLY",
                "OPERATION_ALREADY_EXISTS",
                "ENTRY_FILL_PROOF_NOT_VERIFIED",
                "REPEATED_INJECTION",
                "UNEXPECTED_PROTECTION_TRANSPORT",
                "FAULT_NOT_OBSERVED",
                "EXIT_UNRESOLVED",
                "SESSION_FAILED",
                "FAULT_GATE_NOT_VERIFIED",
                "NO_SEND_PROOF_NOT_VERIFIED",
                "HISTORY_CHANGED",
                "FINAL_STATE_NOT_VERIFIED",
                "FLAT_NOT_CONFIRMED",
                "RESULT_AUDIT_FAILED",
                "LEASE_RELEASE_FAILED",
                "CHECK_FAILED",
            }
            else "CHECK_FAILED"
        )
        super().__init__(self.code)


def code(error: Exception) -> str:
    return (
        error.code if isinstance(error, (SLFaultBlocked, RestartBlocked)) else safe_failure(error)
    )


class SLFaultClient(RestartClient):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.block_sl_id: str | None = None
        self.block_tp_id: str | None = None
        self.unexpected_protection_attempts = 0
        self.reduce_post_attempts = 0
        self.before_exit: Callable[[], None] | None = None

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        values = params or {}
        if (
            method == "POST"
            and path == "/fapi/v1/algoOrder"
            and values.get("clientAlgoId") in {self.block_sl_id, self.block_tp_id}
            and values.get("clientAlgoId") is not None
        ):
            self.unexpected_protection_attempts += 1
            raise SLFaultBlocked("UNEXPECTED_PROTECTION_TRANSPORT")
        if method == "POST" and path == "/fapi/v1/order" and values.get("reduceOnly") == "true":
            self.reduce_post_attempts += 1
            if self.before_exit is not None:
                self.before_exit()  # Records only; diagnostic failure must never block an exit.
        return super().request(method, path, params)


async def run(
    coordinator: DemoCoordinator,
    operation: UUID,
    *,
    connector: Any = private_connect,
    observation_timeout: float = 90,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    store, lease, client = coordinator.store, coordinator.lease, coordinator.client
    if not isinstance(client, SLFaultClient):
        raise SLFaultBlocked("ENTRY_SCOPE_INVALID")
    key = str(operation)
    start_tag = str(uuid5(operation, "sl-fault-start"))
    result_tag = str(uuid5(operation, "sl-fault-result"))
    original = store.read(lease.account_id)
    existed = key in original.orders
    if any(k != key and (o.remaining or o.open_quantity) for k, o in original.orders.items()):
        raise SLFaultBlocked("ENTRY_SCOPE_INVALID")
    if existed and (
        original.operations.get(start_tag) != "SL_LOCAL_BUILD_FAULT_CHECK"
        or original.orders[key].signal.purpose != "DEMO_VALIDATION"
        or original.orders[key].signal.direction != "LONG"
        or original.orders[key].signal.symbol != "BTCUSDT"
        or original.validation_entry_styles.get(key) != "OPPOSITE_QUOTE"
    ):
        raise SLFaultBlocked("ENTRY_SCOPE_INVALID")
    if not existed:
        if start_tag in original.operations or result_tag in original.operations:
            raise SLFaultBlocked("OPERATION_ALREADY_EXISTS")
        empty(coordinator)
    old_orders = {k: v.model_dump(mode="json") for k, v in original.orders.items() if k != key}
    callback_failure = proof_error = "NONE"
    failure = completion_failure = "NONE"
    stage = "SESSION_CONNECT"
    failure_stage = "NONE"
    injections = 0
    fill_scope_verified = paused_verified = gate_verified = False
    automatic_exit_observed = False
    cleanup_started = False
    no_send_verified = operator_emergency_requested = False
    fault_at: str | None = None
    completion = "UNKNOWN"
    original_builder: Callable[..., OrderIntent] = vars(core)["build_protection"]

    def guarded_builder(
        protection: AlgoOrder,
        rules: SymbolRules,
        *,
        working_type: Literal["MARK_PRICE", "CONTRACT_PRICE"],
    ) -> OrderIntent:
        nonlocal injections, fill_scope_verified, proof_error, fault_at
        if protection.position_id != operation or protection.order_type != "STOP_MARKET":
            return original_builder(protection, rules, working_type=working_type)
        try:
            ledger = store.read(lease.account_id)
            order = ledger.orders[key]
            if (
                protection.client_algo_id != order.algo_id
                or protection.client_algo_id != client.block_sl_id
                or protection.account_id != lease.account_id
                or protection.symbol != "BTCUSDT"
                or protection.side != "SELL"
                or protection.trigger_price != order.signal.initial_stop_price
                or working_type != "MARK_PRICE"
                or order.protected
                or order.filled <= 0
                or order.open_quantity <= 0
                or ledger.requests.get(order.algo_id or "") != "SUBMITTING"
            ):
                raise SLFaultBlocked("ENTRY_FILL_PROOF_NOT_VERIFIED")
            if injections:
                raise SLFaultBlocked("REPEATED_INJECTION")
            fill_scope_verified = True
            injections += 1
            fault_at = store.now().isoformat()
        except Exception as error:
            proof_error = code(error)
            raise
        # Original submission_attempted=False path: NOT_SENT / LOCAL_VALIDATION_FAILED.
        # No exchange response is fabricated; original SL-failure emergency logic runs.
        raise ValueError("Explicit own-SL local build fault injection")

    def before_exit() -> None:
        nonlocal automatic_exit_observed, paused_verified, gate_verified
        nonlocal no_send_verified, proof_error
        if cleanup_started:
            return  # Fallback cleanup cannot count as the original automatic response.
        try:
            ledger = store.read(lease.account_id)
            order = ledger.orders[key]
            no_send_verified = bool(
                injections == 1
                and ledger.requests.get(order.algo_id or "") == "NOT_SENT"
                and ledger.request_no_send_evidence.get(order.algo_id or "")
                == "LOCAL_VALIDATION_FAILED"
            )
            paused_verified = bool(
                ledger.paused
                and ledger.risk_state.value == "EMERGENCY"
                and not order.protected
                and order.open_quantity > 0
                and order.tp_algo_id is None
            )
            try:
                coordinator._fresh(ledger.model_copy(deep=True))
            except RiskRejected:
                gate_verified = True
            automatic_exit_observed = bool(no_send_verified and paused_verified and gate_verified)
        except Exception as error:
            proof_error = code(error)
            # Always allow the original risk-reducing exit to proceed.

    def ready() -> None:
        nonlocal stage, callback_failure
        try:
            stage = "RESUME_ENTRIES"
            coordinator.resume_entries(uuid5(operation, "sl-fault-resume"))
            stage = "PREPARE_ENTRY"
            signal, gate, cap = prepare(
                coordinator, operation, "LONG", entry_style="OPPOSITE_QUOTE"
            )
            stage = "RESERVE_ENTRY"
            order = coordinator.reserve(
                signal, gate, quantity_cap=cap, entry_style="OPPOSITE_QUOTE"
            )
            client.allowed_entry, client.allowed_quantity = order.client_id, order.quantity
            client.block_sl_id = "DS-" + order.client_id[3:]
            client.block_tp_id = "DT-" + order.client_id[3:]
            print(
                json.dumps(
                    {
                        "sl_fault_stage": "RESERVED_NOT_YET_SUBMITTED",
                        "operation_id": key,
                        "quantity": str(order.quantity),
                        "quantity_cap": str(cap),
                        "limit_price": str(signal.suggested_limit_price),
                        "stop_price": str(signal.initial_stop_price),
                        "target_price": str(signal.target_price),
                        "live_trading_enabled": False,
                    }
                ),
                flush=True,
            )
            stage = "SUBMIT_ENTRY"
            coordinator.submit(key)
            stage = "WAIT_SL_BUILD_FAULT"
        except Exception as error:
            callback_failure = code(error)
            raise

    task: asyncio.Task[None] | None = None
    with (
        patch.object(core, "build_protection", guarded_builder),
        patch.object(client, "before_exit", before_exit),
    ):
        try:
            if existed:
                raise SLFaultBlocked("RECOVERY_ONLY")

            def start(ledger: Any) -> None:
                ledger.operations[start_tag] = "SL_LOCAL_BUILD_FAULT_CHECK"

            store.change(lease, "DEMO_SL_FAULT_CHECK_START", start)
            task = asyncio.create_task(session(coordinator, connector=connector, on_ready=ready))
            deadline = time.monotonic() + observation_timeout
            while time.monotonic() < deadline:
                if task.done():
                    await task
                    raise SLFaultBlocked("SESSION_FAILED")
                if (
                    callback_failure != "NONE"
                    or proof_error != "NONE"
                    or coordinator.session_failed
                ):
                    raise SLFaultBlocked("SESSION_FAILED")
                current = await settled_call(store.read, lease.account_id)
                order = current.orders.get(key)
                if automatic_exit_observed and order and order.exit_state == "UNKNOWN":
                    raise SLFaultBlocked("EXIT_UNRESOLVED")
                if (
                    automatic_exit_observed
                    and order
                    and order.exit_state == "FILLED"
                    and not order.open_quantity
                    and not order.remaining
                ):
                    print(
                        json.dumps(
                            {
                                "sl_fault_stage": "SL_NOT_SENT_GATE_BLOCKED_AUTO_EXIT_FILLED",
                                "fault_injection_count": injections,
                                "automatic_exit_observed": automatic_exit_observed,
                                "live_trading_enabled": False,
                            }
                        ),
                        flush=True,
                    )
                    break
                await asyncio.sleep(poll_seconds)
            else:
                raise SLFaultBlocked("FAULT_NOT_OBSERVED")
        except Exception as error:
            failure = (
                callback_failure
                if callback_failure != "NONE"
                else proof_error
                if proof_error != "NONE"
                else code(error)
            )
            failure_stage = stage
        finally:
            cleanup_started = True
            if task is not None:
                session_error = await drain_session(task, cancel=True)
                if (session_error is not None or coordinator.session_failed) and failure == "NONE":
                    failure, failure_stage = "SESSION_FAILED", "SESSION_CLEANUP"
            if callback_failure != "NONE":
                failure = callback_failure
            if proof_error != "NONE":
                failure = proof_error
            try:
                await settled_call(store.renew, lease)
                await settled_call(client.sync_clock)
                await settled_call(
                    coordinator.command, uuid5(operation, "sl-fault-pause"), "PAUSE_ENTRIES"
                )
                current = store.read(lease.account_id)
                if any(current.positions.values()) or any(
                    o.open_quantity for o in current.orders.values()
                ):
                    operator_emergency_requested = True
                    await settled_call(
                        coordinator.command, uuid5(operation, "sl-fault-flat"), "EMERGENCY_FLATTEN"
                    )
                completion = await confirm_exit(coordinator)
                await settled_call(coordinator.disconnect)
            except Exception as error:
                completion_failure = code(error)
    final = store.read(lease.account_id)
    order = final.orders.get(key)
    fixed_fields = (
        "policy",
        "daily_key",
        "daily_base",
        "weekly_key",
        "weekly_base",
        "high_water",
        "net_transfers",
        "margin_mode",
        "symbols",
        "recovery_receipts",
        "cancelled_exposure_corrections",
        "attested_day_baselines",
        "attested_period_baselines",
        "exit_anchored_day_baselines",
    )
    history_ok = (
        {k: v.model_dump(mode="json") for k, v in final.orders.items() if k != key} == old_orders
        and all(final.requests.get(k) == v for k, v in original.requests.items())
        and all(final.income_seen.get(k) == v for k, v in original.income_seen.items())
        and all(getattr(final, k) == getattr(original, k) for k in fixed_fields)
        and all(
            {k: v for k, v in getattr(final, name).items() if k != key}
            == {k: v for k, v in getattr(original, name).items() if k != key}
            for name in (
                "entry_quantity_caps",
                "validation_entry_styles",
                "validation_evidence",
                "initial_fill_settlements",
            )
        )
        and all(
            final.request_no_send_evidence.get(k) == v
            for k, v in original.request_no_send_evidence.items()
        )
    )
    accepted = bool(
        not existed
        and injections == 1
        and fill_scope_verified
        and paused_verified
        and gate_verified
        and no_send_verified
        and automatic_exit_observed
        and not operator_emergency_requested
        and client.unexpected_protection_attempts == 0
        and client.entry_post_attempts == client.entry_post_sent == 1
        and client.reduce_post_attempts == 1
        and failure == completion_failure == "NONE"
        and completion == "VERIFIED_FLAT"
        and history_ok
        and order
        and order.filled > 0
        and not order.remaining
        and not order.open_quantity
        and order.position_state.value == "CLOSED"
        and order.exit_state == "FILLED"
        and order.tp_algo_id is None
        and final.requests.get(order.algo_id or "") == "NOT_SENT"
        and final.request_no_send_evidence.get(order.algo_id or "") == "LOCAL_VALIDATION_FAILED"
        and final.risk_state.value == "EMERGENCY"
        and final.paused
        and not final.stream_ready
        and not final.reconciled
    )
    if not accepted and failure == "NONE":
        failure = (
            "HISTORY_CHANGED"
            if not history_ok
            else "FLAT_NOT_CONFIRMED"
            if completion != "VERIFIED_FLAT"
            else "FINAL_STATE_NOT_VERIFIED"
        )
        failure_stage = "FINAL_STATE"
    try:

        def result(ledger: Any) -> None:
            ledger.operations.setdefault(
                result_tag, "SL_LOCAL_BUILD_FAULT:" + ("VERIFIED" if accepted else failure)
            )

        store.change(lease, "DEMO_SL_FAULT_CHECK_RESULT", result)
    except Exception:
        accepted = False
        if failure == "NONE":
            failure, failure_stage = "RESULT_AUDIT_FAILED", "RESULT_AUDIT"
    return {
        "sl_fault_check": "SL_BUILD_FAILURE_GATE_AUTO_FLAT_VERIFIED"
        if accepted
        else "NOT_ACCEPTED",
        "scope": "LOCAL_SL_BUILD_FAILURE_ORIGINAL_AUTO_FLATTEN",
        "operation_id": key,
        "fault_injection_count": injections,
        "fault_at_utc": fault_at,
        "entry_fill_scope_verified": fill_scope_verified,
        "entries_paused_after_fault": paused_verified,
        "entry_gate_blocked_after_fault": gate_verified,
        "sl_not_sent_proof_verified": no_send_verified,
        "unexpected_protection_transport_attempts": client.unexpected_protection_attempts,
        "new_entry_post_count": client.entry_post_sent,
        "entry_post_attempts": client.entry_post_attempts,
        "reduce_only_post_attempts": client.reduce_post_attempts,
        "automatic_exit_observed_before_cleanup": automatic_exit_observed,
        "fallback_operator_emergency_requested": operator_emergency_requested,
        "recovered_existing_intent": existed,
        "filled_quantity": str(order.filled) if order else "0",
        "exit_state": order.exit_state if order else "NONE",
        "flat_confirmation": completion,
        "history_and_baselines_preserved": history_ok,
        "failure": failure,
        "failure_stage": failure_stage,
        "completion_failure": completion_failure,
        "private_session_diagnostic": coordinator.session_diagnostic.model_dump(mode="json"),
        "risk_state": final.risk_state.value,
        "final_paused": final.paused,
        "final_stream_ready": final.stream_ready,
        "final_reconciled": final.reconciled,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
