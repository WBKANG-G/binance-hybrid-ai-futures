"""Explicit operator review of flat DEMO emergencies. Never submits exchange writes."""

import hashlib
import json
import re
from decimal import Decimal
from uuid import UUID

from integrations.binance_usdm.demo import DemoClient, DemoFailure, DemoNotFound
from integrations.binance_usdm.demo_snapshot import snapshot
from integrations.binance_usdm.recovery_transport import recovery_connection
from packages.domain.contracts import OrderState, RiskState
from packages.persistence.demo import DemoStore
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery_budget import (
    AUDIT_BATCH_SIZE,
    MAX_HISTORY_REQUESTS,
    AuditBudgetExceeded,
    RecoveryReadClient,
)
from services.execution.models import Lease
from services.execution.paper import account_risk
from services.risk.paper import AccountValue, period_keys

RECOVERY_BLOCK_CODES = frozenset(
    {
        "RECOVERY_CONTEXT_MISMATCH",
        "RECOVERY_LOCK_STATE_MISMATCH",
        "LOCAL_EXPOSURE_OR_PENDING_ENTRY",
        "SNAPSHOT_CHANGED",
        "EXCHANGE_EXPOSURE_OR_ACTIVE_ORDER",
        "SETTINGS_OR_TRANSFER_MISMATCH",
        "REQUEST_AUDIT_LIMIT",
        "REQUEST_AUDIT_TIME_BUDGET",
        "REQUEST_AUDIT_QUERY_BUDGET",
        "REQUEST_AUDIT_CLOCK_BUDGET",
        "REQUEST_ID_UNSUPPORTED",
        "REQUEST_IDENTITY_MISMATCH",
        "REQUEST_ACTIVE_OR_INVALID",
        "SNAPSHOT_CHANGED_DURING_AUDIT",
        "OPERATOR_ACKNOWLEDGEMENT_REQUIRED",
        "RECOVERY_OPERATION_CONFLICT",
        "REVIEW_TOKEN_CHANGED",
        "PERIOD_BASELINE_MISSING",
        "LOSS_OR_ACCOUNT_RISK_BLOCKED",
        "DATABASE_CHANGED_DURING_REVIEW",
    }
)


class RecoveryBlocked(DemoFailure):
    """Allowlisted diagnostic code; never carries a raw private response."""

    def __init__(self, code: str, message: str, *, periods: dict[str, str] | None = None):
        super().__init__(message)
        self.code = code if code in RECOVERY_BLOCK_CODES else "RECOVERY_CHECK_BLOCKED"
        self.transport_diagnostics: dict[str, str | int] = {}
        self.periods = {
            key: value if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else "INVALID_DATE"
            for key, value in (periods or {}).items()
            if key in {"stored_day_kst", "current_day_kst", "stored_week_kst", "current_week_kst"}
        }


def local_period_status(store: DemoStore, ledger: DemoLedger) -> dict[str, object]:
    day, week = period_keys(store.now(), ledger.policy.week_start_kst)
    periods = RecoveryBlocked(
        "PERIOD_BASELINE_MISSING",
        "Local period check",
        periods={
            "stored_day_kst": ledger.daily_key,
            "current_day_kst": day,
            "stored_week_kst": ledger.weekly_key,
            "current_week_kst": week,
        },
    ).periods
    return {
        "recovery": "LOCAL_PERIOD_CHECK_NO_DATABASE_CHANGE",
        "period_keys_match": (day, week) == (ledger.daily_key, ledger.weekly_key),
        "periods": periods,
        "risk_state": ledger.risk_state.value,
        "paused": ledger.paused,
        "exchange_checked": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def _check_loss_gates(store: DemoStore, ledger: DemoLedger, equity: Decimal) -> None:
    probe = ledger.model_copy(deep=True)
    probe.value = AccountValue(
        as_of=store.now(),
        initial=ledger.value.initial,
        realized=equity - ledger.value.initial - ledger.net_transfers,
        net_deposits=ledger.net_transfers,
    )
    probe.risk_state = RiskState.NORMAL
    account_risk(probe, store.now())
    if probe.risk_state != RiskState.NORMAL:
        current_day, current_week = period_keys(store.now(), ledger.policy.week_start_kst)
        if (current_day, current_week) != (ledger.daily_key, ledger.weekly_key):
            raise RecoveryBlocked(
                "PERIOD_BASELINE_MISSING",
                "Loss/day-boundary/risk gate prevents recovery",
                periods={
                    "stored_day_kst": ledger.daily_key,
                    "current_day_kst": current_day,
                    "stored_week_kst": ledger.weekly_key,
                    "current_week_kst": current_week,
                },
            )
        raise RecoveryBlocked(
            "LOSS_OR_ACCOUNT_RISK_BLOCKED", "Loss/day-boundary/risk gate prevents recovery"
        )


def review(store: DemoStore, client: DemoClient, ledger: DemoLedger) -> dict[str, object]:
    with recovery_connection(client) as pooled:
        try:
            report = _review_checked(store, client, ledger)
            report.update(
                pooled.diagnostics() if pooled else {"audit_transport_mode": "UNPOOLED_TRANSPORT"}
            )
            return report
        except AuditBudgetExceeded as error:
            blocked = RecoveryBlocked(error.code, "Recovery audit exceeded safe resource budget")
            blocked.transport_diagnostics = (
                pooled.diagnostics() if pooled else {"audit_transport_mode": "UNPOOLED_TRANSPORT"}
            )
            raise blocked from None
        except RecoveryBlocked as error:
            error.transport_diagnostics = (
                pooled.diagnostics() if pooled else {"audit_transport_mode": "UNPOOLED_TRANSPORT"}
            )
            raise
        except DemoFailure as error:
            vars(error)["transport_diagnostics"] = (
                pooled.diagnostics() if pooled else {"audit_transport_mode": "UNPOOLED_TRANSPORT"}
            )
            raise


def _review_checked(store: DemoStore, client: DemoClient, ledger: DemoLedger) -> dict[str, object]:
    if (
        client.settings.demo_trading_enabled
        or client.settings.margin_mode != "ISOLATED"
        or client.settings.symbols != ("BTCUSDT",)
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or ledger.policy != client.settings.policy
    ):
        raise RecoveryBlocked(
            "RECOVERY_CONTEXT_MISMATCH", "Recovery requires read-only isolated BTCUSDT DEMO context"
        )
    if ledger.risk_state != RiskState.EMERGENCY or not ledger.paused:
        raise RecoveryBlocked(
            "RECOVERY_LOCK_STATE_MISMATCH", "Recovery applies only to paused EMERGENCY"
        )
    if any(ledger.positions.values()) or any(
        o.remaining
        or o.open_quantity
        or o.state
        not in {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED}
        for o in ledger.orders.values()
    ):
        raise RecoveryBlocked(
            "LOCAL_EXPOSURE_OR_PENDING_ENTRY",
            "Local exposure or nonterminal entry prevents recovery",
        )
    if len(ledger.requests) > MAX_HISTORY_REQUESTS:
        raise RecoveryBlocked("REQUEST_AUDIT_LIMIT", "Too many requests for bounded recovery")
    audit_client = RecoveryReadClient(client)
    client = audit_client
    first = snapshot(client, ledger, store.now())
    second = snapshot(client, ledger, store.now())
    if first.fingerprint != second.fingerprint:
        raise RecoveryBlocked("SNAPSHOT_CHANGED", "Recovery snapshots changed")
    if (
        any(second.positions.values())
        or any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in second.orders.values())
        or any(r["algoStatus"] == "NEW" for r in second.algos.values())
    ):
        raise RecoveryBlocked(
            "EXCHANGE_EXPOSURE_OR_ACTIVE_ORDER",
            "Exchange exposure or active orders prevent recovery",
        )
    if second.leverage.get("BTCUSDT") != 2 or second.transfers != ledger.income_seen:
        raise RecoveryBlocked(
            "SETTINGS_OR_TRANSFER_MISMATCH",
            "Recovery settings/transfers require separate reconciliation",
        )
    _check_loss_gates(store, ledger, second.equity)
    observations: dict[str, str] = {}
    identifiers = sorted(ledger.requests)
    batches = 0
    for offset in range(0, len(identifiers), AUDIT_BATCH_SIZE):
        audit_client.budget.check()
        batches += 1
        for identifier in identifiers[offset : offset + AUDIT_BATCH_SIZE]:
            kind = identifier.split("-", 1)[0]
            if kind not in {"DE", "DX", "DS", "DT"}:
                raise RecoveryBlocked(
                    "REQUEST_ID_UNSUPPORTED", "Unsupported historical request prevents recovery"
                )
            algo = kind in {"DS", "DT"}
            try:
                raw = client.request(
                    "GET",
                    "/fapi/v1/algoOrder" if algo else "/fapi/v1/order",
                    {"clientAlgoId": identifier}
                    if algo
                    else {"symbol": "BTCUSDT", "origClientOrderId": identifier},
                )
            except DemoNotFound:
                observations[identifier] = "NOT_FOUND_UNRESOLVED"
                continue
            if (
                not isinstance(raw, dict)
                or raw.get("symbol") != "BTCUSDT"
                or raw.get("clientAlgoId" if algo else "clientOrderId") != identifier
            ):
                raise RecoveryBlocked(
                    "REQUEST_IDENTITY_MISMATCH", "Historical request identity mismatch"
                )
            status = raw.get("algoStatus" if algo else "status")
            allowed = (
                {"CANCELED", "EXPIRED", "FINISHED", "REJECTED"}
                if algo
                else {"FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"}
            )
            if status not in allowed:
                raise RecoveryBlocked(
                    "REQUEST_ACTIVE_OR_INVALID",
                    "Active/unknown historical request prevents recovery",
                )
            observations[identifier] = str(status)
    final = snapshot(client, ledger, store.now())
    if final.fingerprint != second.fingerprint or final.transfers != second.transfers:
        raise RecoveryBlocked(
            "SNAPSHOT_CHANGED_DURING_AUDIT", "Recovery changed during request audit"
        )
    _check_loss_gates(store, ledger, final.equity)
    audit_client.budget.check()
    stable = ledger.model_dump(mode="json", exclude={"reconciled", "stream_ready"})
    token = hashlib.sha256(
        json.dumps(
            {"ledger": stable, "exchange": second.fingerprint, "observations": observations},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    audit_client.budget.check()
    return {
        "review_token": token,
        "audited_request_count": len(observations),
        "audit_batch_count": batches,
        "signed_query_count": audit_client.budget.signed_queries,
        "audit_clock_probes": audit_client.budget.clock_probes,
        "unresolved_count": sum(v == "NOT_FOUND_UNRESOLVED" for v in observations.values()),
        "risk_gates": "PASS",
        "position_nonzero": False,
        "active_orders": 0,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def acknowledge_flat_emergency(
    store: DemoStore,
    lease: Lease,
    client: DemoClient,
    operation_id: UUID,
    expected_token: str,
    *,
    acknowledge_unresolved: bool,
) -> str:
    if not acknowledge_unresolved or len(expected_token) != 64:
        raise RecoveryBlocked(
            "OPERATOR_ACKNOWLEDGEMENT_REQUIRED",
            "Explicit operator acknowledgement and review token required",
        )

    def apply(ledger: DemoLedger) -> str:
        key = str(operation_id)
        if key in ledger.recovery_receipts:
            if ledger.recovery_receipts[key] != expected_token:
                raise RecoveryBlocked(
                    "RECOVERY_OPERATION_CONFLICT", "Recovery operation ID conflict"
                )
            return "ALREADY_RECORDED_NO_ACTION"
        if key in ledger.operations:
            raise RecoveryBlocked("RECOVERY_OPERATION_CONFLICT", "Recovery operation ID conflict")
        result = review(store, client, ledger)
        if result["review_token"] != expected_token:
            raise RecoveryBlocked(
                "REVIEW_TOKEN_CHANGED", "Recovery review changed; no state updated"
            )
        ledger.recovery_receipts[key] = expected_token
        ledger.operations[key] = "ACKNOWLEDGE_FLAT_EMERGENCY"
        ledger.risk_state = RiskState.NORMAL
        ledger.paused = True
        ledger.reconciled = False
        ledger.stream_ready = False
        # requests, orders, no-send evidence, accounting and old events stay intact.
        return "EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED"

    return store.change(lease, "DEMO_OPERATOR_ACKNOWLEDGED_FLAT_EMERGENCY", apply)
