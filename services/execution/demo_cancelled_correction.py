"""Audited correction of proven unfilled cancelled-entry local exposure only.

This does not acknowledge unknown requests, reset risk baselines or unlock DEMO.
"""

import hashlib
import json
import re
from decimal import Decimal
from uuid import UUID

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from packages.domain.contracts import PositionState
from packages.persistence.demo import DemoStore
from services.execution.demo_cancelled_history import review_cancelled_history
from services.execution.demo_models import CancelledExposureCorrection, DemoLedger
from services.execution.models import Lease
from services.risk.paper import period_keys

OPERATION = "CORRECT_CANCELLED_UNFILLED_EXPOSURE"


class CancelledCorrectionBlocked(DemoFailure):
    pass


def review_correction(
    store: DemoStore, client: DemoClient, ledger: DemoLedger, *, account_id: str
) -> dict[str, object]:
    if not account_id.startswith("DEMO-"):
        raise CancelledCorrectionBlocked("DEMO_ACCOUNT_NAMESPACE_REQUIRED")
    report = review_cancelled_history(store, client, ledger)
    if report["wallet_change_matches_income"] is not True:
        raise CancelledCorrectionBlocked("WALLET_HISTORY_NOT_RECONCILED")
    if (
        report["last_known_full_exit_fill_found"] is not True
        or report["btc_trades_after_known_exit_before_boundary"] != 0
        or report["other_trades_at_exit_timestamp"] != 0
        or report["btc_trades_at_or_after_boundary"] != 0
    ):
        raise CancelledCorrectionBlocked("LEGACY_EXIT_HISTORY_NOT_CONFIRMED")
    keys = sorted(key for key, order in ledger.orders.items() if order.open_quantity)
    if not 1 <= len(keys) <= 10:
        raise CancelledCorrectionBlocked("CORRECTION_ORDER_LIMIT")
    # Lease acquisition only invalidates these flags. It must not change the
    # preimage bound to the operator's preview token.
    preimage = ledger.model_dump(mode="json", exclude={"reconciled", "stream_ready"})
    payload = {
        "operation": OPERATION,
        "account_id": account_id,
        "ledger": preimage,
        "evidence_hash": report["evidence_hash"],
        "exchange_fingerprint": report["validated_exchange_fingerprint"],
        "day_week_kst": period_keys(store.now(), ledger.policy.week_start_kst),
        "order_keys": keys,
    }
    token = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "correction": "REVIEW_ONLY_NO_DATABASE_CHANGE",
        "account_id": account_id,
        "review_token": token,
        "evidence_hash": report["evidence_hash"],
        "exchange_fingerprint": report["validated_exchange_fingerprint"],
        "proposed_orders": [
            {
                "order_key": key,
                "open_quantity_before": str(ledger.orders[key].open_quantity),
                "open_quantity_after": "0",
                "actual_risk_before": str(ledger.orders[key].actual_risk),
                "actual_risk_after": "0",
                "position_state_before": ledger.orders[key].position_state.value,
                "position_state_after": "CLOSED",
                "entry_state_preserved": ledger.orders[key].state.value,
                "filled_preserved": str(ledger.orders[key].filled),
                "exit_state_preserved": ledger.orders[key].exit_state,
            }
            for key in keys
        ],
        "risk_state": ledger.risk_state.value,
        "paused": ledger.paused,
        "unresolved_requests_acknowledged": False,
        "risk_baselines_changed": False,
        "recovery_eligible": False,
        "database_changed": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def apply_correction(
    store: DemoStore,
    lease: Lease,
    client: DemoClient,
    operation_id: UUID,
    expected_token: str,
) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_token):
        raise CancelledCorrectionBlocked("CORRECTION_REVIEW_TOKEN_REQUIRED")
    # This guard also applies on the idempotent receipt path.
    if client.settings.demo_trading_enabled:
        raise CancelledCorrectionBlocked("READ_ONLY_CLIENT_REQUIRED")

    def apply(ledger: DemoLedger) -> str:
        key = str(operation_id)
        receipt = ledger.cancelled_exposure_corrections.get(key)
        if receipt is not None:
            if receipt.review_token != expected_token or ledger.operations.get(key) != OPERATION:
                raise CancelledCorrectionBlocked("CORRECTION_OPERATION_CONFLICT")
            return "ALREADY_RECORDED_NO_ACTION"
        if key in ledger.operations or key in ledger.recovery_receipts:
            raise CancelledCorrectionBlocked("CORRECTION_OPERATION_CONFLICT")
        report = review_correction(store, client, ledger, account_id=lease.account_id)
        if report["review_token"] != expected_token:
            raise CancelledCorrectionBlocked("CORRECTION_REVIEW_CHANGED")
        keys = [key for key, order in ledger.orders.items() if order.open_quantity]
        originals = {key: ledger.orders[key].model_copy(deep=True) for key in keys}
        for order_key in keys:
            order = ledger.orders[order_key]
            order.open_quantity = Decimal(0)
            order.actual_risk = Decimal(0)
            order.position_state = PositionState.CLOSED
            # Entry CANCELED, filled=0, exit UNKNOWN and every request stay intact.
        ledger.cancelled_exposure_corrections[key] = CancelledExposureCorrection(
            review_token=expected_token,
            evidence_hash=str(report["evidence_hash"]),
            exchange_fingerprint=str(report["exchange_fingerprint"]),
            corrected_at=store.now(),
            original_orders=originals,
        )
        ledger.operations[key] = OPERATION
        ledger.reconciled = ledger.stream_ready = False
        # risk_state, paused, value, last_reconciled, baselines, high_water,
        # transfers, request states and protection identities are untouched.
        return "LOCAL_CANCELLED_EXPOSURE_CORRECTED_KEEP_LOCK"

    return store.change(lease, "DEMO_CANCELLED_EXPOSURE_CORRECTED", apply)
