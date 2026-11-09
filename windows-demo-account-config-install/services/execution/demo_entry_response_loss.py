"""One own-entry SDK acknowledgement loss, persisted UNKNOWN and signed same-ID recovery."""

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid5

from integrations.binance_usdm.demo import DemoFailure, DemoUnknown
from integrations.binance_usdm.demo_snapshot import ORDER_KEYS, pick
from services.execution.demo import DemoCoordinator
from services.execution.demo_api_restart_check import RestartBlocked, RestartClient, empty
from services.execution.demo_open_disconnect_check import drain_session
from services.execution.demo_stream import private_connect, session, settled_call
from services.execution.demo_validation import confirm_exit, prepare, safe_failure
from services.risk.paper import RiskRejected


class EntryLossBlocked(DemoFailure):
    def __init__(self, code: str):
        self.code = (
            code
            if code
            in {
                "LIVE_CONTEXT_FORBIDDEN",
                "ENTRY_SCOPE_INVALID",
                "RECOVERY_ONLY",
                "OPERATION_ALREADY_EXISTS",
                "RECOVERY_PROOF_NOT_VERIFIED",
                "REPEATED_INJECTION",
                "RESPONSE_NOT_ELIGIBLE",
                "FAULT_NOT_OBSERVED",
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
        error.code if isinstance(error, (EntryLossBlocked, RestartBlocked)) else safe_failure(error)
    )


class EntryLossClient(RestartClient):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.drop_armed = False
        self.drop_count = 0
        self.expected_price = Decimal(0)
        self.ack_server_id: str | None = None
        self.lookup_count = 0
        self.same_id_verified = False
        self.reduce_post_attempts = 0
        self.before_recovery_read: Callable[[], None] | None = None

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        values = params or {}
        entry = method == "POST" and path == "/fapi/v1/order" and values.get("reduceOnly") != "true"
        lookup = (
            method == "GET"
            and path == "/fapi/v1/order"
            and self.drop_count == 1
            and values.get("origClientOrderId") == self.allowed_entry
        )
        if method == "GET" and self.drop_count == 1 and self.before_recovery_read is not None:
            self.before_recovery_read()  # Diagnostic only; cannot suppress the signed recovery query.
        if method == "POST" and path == "/fapi/v1/order" and values.get("reduceOnly") == "true":
            self.reduce_post_attempts += 1
        result = super().request(method, path, params)
        if entry and self.drop_armed:
            identifier = result.get("orderId") if isinstance(result, dict) else None
            if (
                not isinstance(result, dict)
                or result.get("clientOrderId") != self.allowed_entry
                or result.get("symbol") != "BTCUSDT"
                or type(identifier) not in {int, str}
                or not str(identifier).isascii()
                or not str(identifier).isdigit()
                or int(str(identifier)) <= 0
                or self.drop_count
            ):
                raise EntryLossBlocked("RESPONSE_NOT_ELIGIBLE")
            self.ack_server_id = str(identifier)
            self.drop_count = 1
            self.drop_armed = False
            # Actual SDK request succeeded; suppress only the coordinator's local ACK.
            # This is not a real network timeout or an invented exchange response.
            raise DemoUnknown("Explicit own-entry SDK acknowledgement loss injection")
        if lookup:
            self.lookup_count += 1
            row = pick(result, ORDER_KEYS)
            if (
                row["clientOrderId"] == self.allowed_entry
                and row["orderId"] == self.ack_server_id
                and row["symbol"] == "BTCUSDT"
                and row["side"] == "BUY"
                and row["type"] == "LIMIT"
                and row["reduceOnly"] == "false"
                and row["positionSide"] == "BOTH"
                and Decimal(row["origQty"]) == self.allowed_quantity
                and Decimal(row["price"]) == self.expected_price
                and row["status"] in {"NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED"}
            ):
                self.same_id_verified = True
        return result


async def run(
    coordinator: DemoCoordinator,
    operation: UUID,
    *,
    connector: Any = private_connect,
    observation_timeout: float = 90,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    store, lease, client = coordinator.store, coordinator.lease, coordinator.client
    if not isinstance(client, EntryLossClient):
        raise EntryLossBlocked("ENTRY_SCOPE_INVALID")
    key = str(operation)
    start_tag = str(uuid5(operation, "entry-loss-start"))
    result_tag = str(uuid5(operation, "entry-loss-result"))
    original = store.read(lease.account_id)
    existed = key in original.orders
    if any(k != key and (o.remaining or o.open_quantity) for k, o in original.orders.items()):
        raise EntryLossBlocked("ENTRY_SCOPE_INVALID")
    if existed and (
        original.operations.get(start_tag) != "ENTRY_ACK_LOSS_CHECK"
        or original.orders[key].signal.purpose != "DEMO_VALIDATION"
        or original.orders[key].signal.direction != "LONG"
        or original.orders[key].signal.symbol != "BTCUSDT"
        or original.validation_entry_styles.get(key) != "OPPOSITE_QUOTE"
    ):
        raise EntryLossBlocked("ENTRY_SCOPE_INVALID")
    if not existed:
        if start_tag in original.operations or result_tag in original.operations:
            raise EntryLossBlocked("OPERATION_ALREADY_EXISTS")
        empty(coordinator)
    old_orders = {k: v.model_dump(mode="json") for k, v in original.orders.items() if k != key}
    callback_failure = proof_error = "NONE"
    failure = completion_failure = "NONE"
    stage = "SESSION_CONNECT"
    failure_stage = "NONE"
    unknown_persisted = gate_verified = recovery_verified = False
    operator_emergency_requested = False
    completion = "UNKNOWN"

    def before_recovery_read() -> None:
        nonlocal unknown_persisted, gate_verified, proof_error
        try:
            ledger = store.read(lease.account_id)
            order = ledger.orders[key]
            if order.state.value == "UNKNOWN" and ledger.requests.get(order.client_id) == "UNKNOWN":
                unknown_persisted = True
                try:
                    coordinator._fresh(ledger.model_copy(deep=True))
                except RiskRejected:
                    gate_verified = True
        except Exception as error:
            proof_error = code(error)  # Query still proceeds; never hide a proof failure.

    def ready() -> None:
        nonlocal stage, callback_failure
        try:
            stage = "RESUME_ENTRIES"
            coordinator.resume_entries(uuid5(operation, "entry-loss-resume"))
            stage = "PREPARE_ENTRY"
            signal, gate, cap = prepare(
                coordinator, operation, "LONG", entry_style="OPPOSITE_QUOTE"
            )
            stage = "RESERVE_ENTRY"
            order = coordinator.reserve(
                signal, gate, quantity_cap=cap, entry_style="OPPOSITE_QUOTE"
            )
            client.allowed_entry, client.allowed_quantity = order.client_id, order.quantity
            client.expected_price = signal.suggested_limit_price
            client.drop_armed = True
            print(
                json.dumps(
                    {
                        "entry_loss_stage": "RESERVED_NOT_YET_SUBMITTED",
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
            stage = "SIGNED_SAME_ID_QUERY"
            client.request(
                "GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "origClientOrderId": order.client_id}
            )
            stage = "WAIT_SAME_ID_RECOVERY"
        except Exception as error:
            callback_failure = code(error)
            raise

    task: asyncio.Task[None] | None = None
    with patch.object(client, "before_recovery_read", before_recovery_read):
        try:
            if existed:
                raise EntryLossBlocked("RECOVERY_ONLY")

            def start(ledger: Any) -> None:
                ledger.operations[start_tag] = "ENTRY_ACK_LOSS_CHECK"

            store.change(lease, "DEMO_ENTRY_LOSS_CHECK_START", start)
            task = asyncio.create_task(session(coordinator, connector=connector, on_ready=ready))
            deadline = time.monotonic() + observation_timeout
            while time.monotonic() < deadline:
                if task.done():
                    await task
                    raise EntryLossBlocked("SESSION_FAILED")
                if (
                    callback_failure != "NONE"
                    or proof_error != "NONE"
                    or coordinator.session_failed
                ):
                    raise EntryLossBlocked("SESSION_FAILED")
                current = await settled_call(store.read, lease.account_id)
                order = current.orders.get(key)
                if (
                    client.drop_count == 1
                    and unknown_persisted
                    and gate_verified
                    and client.same_id_verified
                    and order
                    and order.server_order_id == client.ack_server_id
                    and order.state.value not in {"UNKNOWN", "SUBMITTING", "RISK_RESERVED"}
                ):
                    recovery_verified = True
                    print(
                        json.dumps(
                            {
                                "entry_loss_stage": "PERSISTED_UNKNOWN_SAME_ID_QUERY_RECOVERED",
                                "response_drop_count": client.drop_count,
                                "new_entry_post_count": client.entry_post_sent,
                                "live_trading_enabled": False,
                            }
                        ),
                        flush=True,
                    )
                    break
                await asyncio.sleep(poll_seconds)
            else:
                raise EntryLossBlocked("FAULT_NOT_OBSERVED")
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
                    coordinator.command, uuid5(operation, "entry-loss-pause"), "PAUSE_ENTRIES"
                )
                current = store.read(lease.account_id)
                if any(current.positions.values()) or any(
                    o.open_quantity for o in current.orders.values()
                ):
                    operator_emergency_requested = True
                    await settled_call(
                        coordinator.command,
                        uuid5(operation, "entry-loss-flat"),
                        "EMERGENCY_FLATTEN",
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
        and client.drop_count == 1
        and unknown_persisted
        and gate_verified
        and recovery_verified
        and client.same_id_verified
        and client.entry_post_attempts == client.entry_post_sent == 1
        and failure == completion_failure == "NONE"
        and completion == "VERIFIED_FLAT"
        and history_ok
        and order
        and not order.remaining
        and not order.open_quantity
        and order.position_state.value == "CLOSED"
        and order.server_order_id == client.ack_server_id
        and final.requests.get(order.client_id) == "UNKNOWN"  # Original evidence preserved.
        and final.risk_state.value in {"NORMAL", "EMERGENCY"}
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
                result_tag, "ENTRY_ACK_LOSS_CHECK:" + ("VERIFIED" if accepted else failure)
            )

        store.change(lease, "DEMO_ENTRY_LOSS_CHECK_RESULT", result)
    except Exception:
        accepted = False
        if failure == "NONE":
            failure, failure_stage = "RESULT_AUDIT_FAILED", "RESULT_AUDIT"
    return {
        "entry_loss_check": "ENTRY_ACK_LOSS_SAME_ID_NO_DUPLICATE_FLAT_VERIFIED"
        if accepted
        else "NOT_ACCEPTED",
        "scope": "LOCAL_SDK_ENTRY_ACK_LOSS_AFTER_SUCCESS_RESPONSE",
        "operation_id": key,
        "response_drop_count": client.drop_count,
        "unknown_intent_persisted": unknown_persisted,
        "entry_gate_blocked_while_unknown": gate_verified,
        "same_id_signed_query_verified": client.same_id_verified,
        "same_id_query_count": client.lookup_count,
        "local_order_recovered_before_cleanup": recovery_verified,
        "new_entry_post_count": client.entry_post_sent,
        "entry_post_attempts": client.entry_post_attempts,
        "reduce_only_post_attempts": client.reduce_post_attempts,
        "operator_emergency_requested": operator_emergency_requested,
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
