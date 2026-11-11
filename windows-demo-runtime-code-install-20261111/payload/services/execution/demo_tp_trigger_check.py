"""Bounded actual TP observation; original execution and cleanup policy stay intact."""

import asyncio
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid5

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number
from packages.domain.contracts import Signal
from services.execution.demo import DemoCoordinator
from services.execution.demo_api_restart_check import RestartClient, empty
from services.execution.demo_models import DemoLedger
from services.execution.demo_validation import safe_failure, validate_once
from services.execution.models import PaperOrder
from services.risk.paper import GateSnapshot, RiskRejected

MAX_QUANTITY = Decimal("0.0007")
OBSERVATION_SECONDS = 300


class NonFlatObserved(DemoFailure):
    """A fresh nonzero position must prevent deletion of its remaining stop."""


class TPTriggerClient(RestartClient):
    """Observe signed evidence around original SL cleanup, without changing its decision."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.own_order: Callable[[], PaperOrder] | None = None
        self.reduce_post_attempts = 0
        self.sl_delete_attempts = 0
        self.tp_fill_before_sl_delete = False
        self.sl_cancel_query_verified = False
        self.proof_failure = "TP_FILL_NOT_OBSERVED"

    def _proof(self, order: PaperOrder) -> bool:
        # These are extra signed reads; the coordinator retains its own strict
        # double-snapshot flat proof and fencing before any original DELETE.
        tp = super().request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": order.tp_algo_id or ""})
        sl = super().request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": order.algo_id or ""})
        common = {
            "symbol": "BTCUSDT",
            "side": "SELL",
            "algoType": "CONDITIONAL",
            "positionSide": "BOTH",
            "workingType": "MARK_PRICE",
        }
        if not isinstance(tp, dict) or not isinstance(sl, dict):
            return False
        for row, kind, cid, price in (
            (tp, "TAKE_PROFIT_MARKET", order.tp_algo_id, order.signal.target_price),
            (sl, "STOP_MARKET", order.algo_id, order.signal.initial_stop_price),
        ):
            if (
                any(row.get(k) != v for k, v in common.items())
                or row.get("clientAlgoId") != cid
                or row.get("orderType") != kind
                or str(row.get("closePosition")).lower() != "true"
                or str(row.get("priceProtect")).lower() != "false"
                or signed_number(row.get("triggerPrice")) != price
            ):
                return False
        actual = str(tp.get("actualOrderId", ""))
        if (
            tp.get("algoStatus") != "FINISHED"
            or sl.get("algoStatus") != "NEW"
            or not actual.isdecimal()
            or int(actual) <= 0
            or order.remaining
            or order.exit_id
            or not order.filled
            or self.reduce_post_attempts
        ):
            return False
        execution = super().request(
            "GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "orderId": actual}
        )
        positions = super().request("GET", "/fapi/v3/positionRisk")
        if isinstance(positions, list) and any(
            isinstance(p, dict) and signed_number(p.get("positionAmt")) != 0 for p in positions
        ):
            raise NonFlatObserved("NONZERO_POSITION_BEFORE_STOP_DELETE")
        return bool(
            isinstance(execution, dict)
            and str(execution.get("orderId")) == actual
            and execution.get("symbol") == "BTCUSDT"
            and execution.get("side") == "SELL"
            and execution.get("positionSide") == "BOTH"
            and execution.get("status") == "FILLED"
            and execution.get("type") == "MARKET"
            and str(execution.get("reduceOnly")).lower() == "true"
            and signed_number(execution.get("executedQty")) == order.filled
            and signed_number(execution.get("avgPrice")) > 0
            and isinstance(positions, list)
            and any(isinstance(p, dict) and p.get("symbol") == "BTCUSDT" for p in positions)
            and all(
                isinstance(p, dict) and signed_number(p.get("positionAmt")) == 0 for p in positions
            )
        )

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        values = params or {}
        if method == "POST" and path == "/fapi/v1/order" and values.get("reduceOnly") == "true":
            self.reduce_post_attempts += 1
        own_sl = "DS-" + self.allowed_entry[3:] if self.allowed_entry else None
        cleanup = bool(
            method == "DELETE"
            and path == "/fapi/v1/algoOrder"
            and own_sl
            and values.get("clientAlgoId") == own_sl
        )
        proof = False
        if cleanup:
            self.sl_delete_attempts += 1
            try:
                proof = bool(self.own_order and self._proof(self.own_order()))
            except NonFlatObserved:
                self.proof_failure = "NONZERO_POSITION_BEFORE_STOP_DELETE"
                raise
            except Exception:
                self.proof_failure = "SIGNED_PROOF_QUERY_FAILED"
            # An unavailable experimental proof must not block the original
            # fenced safety cleanup or turn an ordinary exit into TP success.
        result = super().request(method, path, params)
        if cleanup and proof:
            self.tp_fill_before_sl_delete = True
            try:
                after = super().request("GET", path, {"clientAlgoId": own_sl or ""})
                self.sl_cancel_query_verified = bool(
                    isinstance(after, dict)
                    and after.get("clientAlgoId") == own_sl
                    and after.get("symbol") == "BTCUSDT"
                    and after.get("orderType") == "STOP_MARKET"
                    and after.get("algoStatus") == "CANCELED"
                )
                self.proof_failure = (
                    "NONE" if self.sl_cancel_query_verified else "SL_CANCEL_NOT_VERIFIED"
                )
            except Exception:
                self.proof_failure = "SIGNED_PROOF_QUERY_FAILED"
        return result


class TPTriggerCoordinator(DemoCoordinator):
    def reserve(
        self,
        signal: Signal,
        gate: GateSnapshot,
        *,
        quantity_cap: Decimal | None = None,
        entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "PASSIVE",
    ) -> PaperOrder:
        if (
            not isinstance(self.client, TPTriggerClient)
            or signal.direction != "LONG"
            or signal.symbol != "BTCUSDT"
            or signal.purpose != "DEMO_VALIDATION"
            or entry_style != "PASSIVE"
            or quantity_cap is None
            or not 0 < quantity_cap <= MAX_QUANTITY
            or quantity_cap * signal.suggested_limit_price > 100
        ):
            raise RiskRejected("TP observation requires capped passive LONG DEMO validation")
        order = super().reserve(signal, gate, quantity_cap=quantity_cap, entry_style=entry_style)
        self.client.allowed_entry = order.client_id
        self.client.allowed_quantity = order.quantity
        self.client.own_order = lambda: self.store.read(self.lease.account_id).orders[
            str(signal.signal_id)
        ]
        return order


class OppositeQuoteTPTriggerCoordinator(TPTriggerCoordinator):
    """Explicit additional DEMO test mode; legacy passive coordinator is unchanged."""

    def reserve(
        self,
        signal: Signal,
        gate: GateSnapshot,
        *,
        quantity_cap: Decimal | None = None,
        entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "OPPOSITE_QUOTE",
    ) -> PaperOrder:
        if (
            not isinstance(self.client, TPTriggerClient)
            or signal.direction != "LONG"
            or signal.symbol != "BTCUSDT"
            or signal.purpose != "DEMO_VALIDATION"
            or entry_style != "OPPOSITE_QUOTE"
            or quantity_cap is None
            or not 0 < quantity_cap <= MAX_QUANTITY
            or quantity_cap * signal.suggested_limit_price > 100
        ):
            raise RiskRejected("Opposite-quote TP observation requires capped LONG DEMO validation")
        # Keep common exact-minimum, quote, budget, margin, expiry and fencing gates.
        order = DemoCoordinator.reserve(
            self, signal, gate, quantity_cap=quantity_cap, entry_style=entry_style
        )
        self.client.allowed_entry = order.client_id
        self.client.allowed_quantity = order.quantity
        self.client.own_order = lambda: self.store.read(self.lease.account_id).orders[
            str(signal.signal_id)
        ]
        return order


class PooledReadTPTriggerCoordinator(OppositeQuoteTPTriggerCoordinator):
    """Bound idle polling by half the existing freshness budget; hints stay immediate."""

    def idle_wait_seconds(self, ledger: DemoLedger) -> float:
        return min(15.0, ledger.policy.max_snapshot_age_seconds / 2)


def history_preserved(before: DemoLedger, after: DemoLedger, key: str) -> bool:
    return bool(
        all(
            getattr(before, name) == getattr(after, name)
            for name in (
                "policy",
                "daily_key",
                "daily_base",
                "weekly_key",
                "weekly_base",
                "net_transfers",
                "margin_mode",
                "symbols",
                "recovery_receipts",
                "cancelled_exposure_corrections",
                "attested_day_baselines",
                "attested_period_baselines",
                "exit_anchored_day_baselines",
            )
        )
        and after.high_water >= before.high_water
        and all(after.orders.get(k) == v for k, v in before.orders.items())
        and all(after.requests.get(k) == v for k, v in before.requests.items())
        and all(after.income_seen.get(k) == v for k, v in before.income_seen.items())
        and all(after.operations.get(k) == v for k, v in before.operations.items())
        and all(
            {k: v for k, v in getattr(after, name).items() if k != key} == getattr(before, name)
            for name in (
                "entry_quantity_caps",
                "validation_entry_styles",
                "validation_evidence",
                "initial_fill_settlements",
            )
        )
        and all(
            after.request_no_send_evidence.get(k) == v
            for k, v in before.request_no_send_evidence.items()
        )
    )


async def run(
    coordinator: TPTriggerCoordinator,
    operation: UUID,
    *,
    entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "PASSIVE",
) -> dict[str, Any]:
    if entry_style not in {"PASSIVE", "OPPOSITE_QUOTE"} or (
        entry_style == "OPPOSITE_QUOTE"
    ) != isinstance(coordinator, OppositeQuoteTPTriggerCoordinator):
        raise DemoFailure("TP entry style requires its explicit coordinator")
    client = coordinator.client
    if not isinstance(client, TPTriggerClient):
        raise DemoFailure("TP observation client required")
    store, lease = coordinator.store, coordinator.lease
    key, marker = str(operation), str(uuid5(operation, "tp-trigger-observation"))
    before = store.read(lease.account_id)
    if key in before.orders or marker in before.operations:
        raise DemoFailure("Existing TP observation must not be submitted again")
    empty(coordinator)
    store.change(
        lease,
        "DEMO_TP_TRIGGER_START",
        lambda ledger: ledger.operations.setdefault(marker, "TP_TRIGGER_OBSERVATION_STARTED"),
    )
    failure = "NONE"
    try:
        await validate_once(
            coordinator,
            operation,
            "LONG",
            observation_seconds=OBSERVATION_SECONDS,
            entry_style=entry_style,
        )
    except Exception as error:
        failure = safe_failure(error)
    finally:
        await asyncio.to_thread(coordinator.disconnect)
    completion = "VERIFIED_FLAT" if failure == "NONE" else "UNKNOWN"
    after = store.read(lease.account_id)
    order = after.orders.get(key)
    history_ok = history_preserved(before, after, key)
    accepted = bool(
        failure == "NONE"
        and client.tp_fill_before_sl_delete
        and client.sl_cancel_query_verified
        and client.sl_delete_attempts == 1
        and client.entry_post_attempts == client.entry_post_sent == 1
        and client.reduce_post_attempts == 0
        and order
        and order.filled
        and not order.remaining
        and not order.open_quantity
        and order.position_state.value == "CLOSED"
        and not order.exit_id
        and after.validation_evidence.get(key, {}).get("stop_verified")
        and after.validation_evidence.get(key, {}).get("target_verified")
        and history_ok
        and after.paused
        and not after.stream_ready
        and not after.reconciled
        and after.risk_state.value == "NORMAL"
    )
    if not accepted and failure == "NONE":
        failure = (
            client.proof_failure if client.proof_failure != "NONE" else "FINAL_STATE_NOT_VERIFIED"
        )
    store.change(
        lease,
        "DEMO_TP_TRIGGER_RESULT",
        lambda ledger: ledger.operations.__setitem__(
            marker, "TP_TRIGGER:" + ("VERIFIED" if accepted else "NOT_ACCEPTED")
        ),
    )
    return {
        "tp_trigger_check": "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
        if accepted
        else "NOT_ACCEPTED",
        "scope": "BOUNDED_ACTUAL_TP_FILL_AND_ORIGINAL_STOP_CLEANUP",
        "operation_id": key,
        "entry_style": entry_style,
        "observation_seconds": OBSERVATION_SECONDS,
        "maximum_quantity_cap": str(MAX_QUANTITY),
        "signed_tp_fill_before_sl_delete_verified": client.tp_fill_before_sl_delete,
        "sl_cancel_query_verified": client.sl_cancel_query_verified,
        "sl_delete_attempts": client.sl_delete_attempts,
        "entry_post_attempts": client.entry_post_attempts,
        "new_entry_post_count": client.entry_post_sent,
        "operator_exit_post_attempts": client.reduce_post_attempts,
        "filled_quantity": str(order.filled) if order else "0",
        "position_state": order.position_state.value if order else "NOT_CREATED",
        "history_and_baselines_preserved": history_ok,
        "failure": failure,
        "flat_confirmation": completion,
        "risk_state": after.risk_state.value,
        "private_session_diagnostic": coordinator.session_diagnostic.model_dump(mode="json"),
        "final_paused": after.paused,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
