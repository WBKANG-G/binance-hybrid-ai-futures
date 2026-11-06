"""Manual-source flat day/week reconstruction; never an automatic risk-lock reset."""

import hashlib
import json
import re
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number, snapshot
from packages.domain.contracts import OrderState, PositionState, RiskState
from packages.persistence.demo import DemoStore
from services.execution.demo_day_review import income_window
from services.execution.demo_history_review import trades_window
from services.execution.demo_models import AttestedPeriodBaseline, DemoLedger, DemoSnapshot
from services.execution.models import Lease
from services.execution.paper import account_risk
from services.risk.paper import AccountValue, period_keys

SOURCE: Final = "OPERATOR_ATTESTED_FLAT_PERIODS_WITH_SIGNED_USDT_EVIDENCE"
OPERATION = "OPERATOR_ATTESTED_PERIOD_BASELINES"
CODES = frozenset(
    {
        "EXPLICIT_OPERATOR_ATTESTATION_REQUIRED",
        "EXPLICIT_MANUAL_SOURCE_ACCEPTANCE_REQUIRED",
        "READ_ONLY_PAUSED_ISOLATED_CONTEXT_REQUIRED",
        "INVALID_LOCAL_FLAT_ANCHOR",
        "PERIOD_OR_ANCHOR_WINDOW_UNSUPPORTED",
        "NONZERO_OR_UNVERIFIED_DEPOSIT_BASIS",
        "CURRENT_EXPOSURE_OR_ACTIVE_ORDER",
        "HISTORICAL_FILLED_ORDER_NOT_VERIFIED",
        "SETTINGS_OR_TRANSFER_MISMATCH",
        "POST_ANCHOR_HISTORY_NOT_EMPTY",
        "USDT_ASSET_SCOPE_OR_BALANCE_MISMATCH",
        "ACCOUNT_OR_PERIOD_CHANGED",
        "EXISTING_LOSS_LIMIT_REACHED",
        "HIGH_WATER_REQUIRES_SEPARATE_REVIEW",
        "RECONSTRUCTED_RISK_GATES_FAILED",
        "PERIOD_OPERATION_CONFLICT",
        "PERIOD_PREVIEW_CHANGED",
        "DATABASE_CHANGED_DURING_REVIEW",
    }
)
TERMINAL = {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED}


class PeriodReviewBlocked(DemoFailure):
    def __init__(self, code: str):
        super().__init__(code if code in CODES else "PERIOD_REVIEW_BLOCKED")


def _flat_truth(ledger: DemoLedger, remote: DemoSnapshot) -> None:
    if (
        any(remote.positions.values())
        or remote.margin_used != 0
        or any(
            r["status"] not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}
            for r in remote.orders.values()
        )
        or any(
            r["algoStatus"] not in {"CANCELED", "EXPIRED", "FINISHED", "REJECTED"}
            for r in remote.algos.values()
        )
    ):
        raise PeriodReviewBlocked("CURRENT_EXPOSURE_OR_ACTIVE_ORDER")
    if (
        remote.leverage.get("BTCUSDT") != 2
        or remote.margin_types.get("BTCUSDT") != "ISOLATED"
        or remote.transfers != ledger.income_seen
    ):
        raise PeriodReviewBlocked("SETTINGS_OR_TRANSFER_MISMATCH")
    for order in ledger.orders.values():
        if not order.filled:
            continue  # Zero-fill legacy UNKNOWN requests remain unresolved and unchanged.
        entry = remote.orders.get(order.client_id)
        exit_row = remote.orders.get(order.exit_id or "")
        if (
            not entry
            or not exit_row
            or order.exit_state != "FILLED"
            or entry["symbol"] != "BTCUSDT"
            or entry["clientOrderId"] != order.client_id
            or entry["positionSide"] != "BOTH"
            or entry["side"] != ("BUY" if order.signal.direction == "LONG" else "SELL")
            or entry["type"] != "LIMIT"
            or entry["reduceOnly"] != "false"
            or entry["status"] not in {"FILLED", "CANCELED", "EXPIRED"}
            or signed_number(entry["origQty"]) != order.quantity
            or signed_number(entry["executedQty"]) != order.filled
            or signed_number(entry["price"]) != order.signal.suggested_limit_price
            or (order.server_order_id is not None and entry["orderId"] != order.server_order_id)
            or exit_row["symbol"] != "BTCUSDT"
            or exit_row["clientOrderId"] != order.exit_id
            or exit_row["positionSide"] != "BOTH"
            or exit_row["side"] != ("SELL" if order.signal.direction == "LONG" else "BUY")
            or exit_row["reduceOnly"] != "true"
            or exit_row["status"] != "FILLED"
            or signed_number(exit_row["executedQty"]) != order.filled
        ):
            raise PeriodReviewBlocked("HISTORICAL_FILLED_ORDER_NOT_VERIFIED")


def review_periods(
    store: DemoStore,
    client: DemoClient,
    ledger: DemoLedger,
    *,
    account_id: str,
    attest_no_activity: bool,
) -> dict[str, object]:
    # Preserve the original public EMERGENCY-only review/apply contract.
    return _review_periods(
        store,
        client,
        ledger,
        account_id=account_id,
        attest_no_activity=attest_no_activity,
        required_risk_state=RiskState.EMERGENCY,
    )


def _review_periods(
    store: DemoStore,
    client: DemoClient,
    ledger: DemoLedger,
    *,
    account_id: str,
    attest_no_activity: bool,
    required_risk_state: RiskState,
) -> dict[str, object]:
    if not attest_no_activity:
        raise PeriodReviewBlocked("EXPLICIT_OPERATOR_ATTESTATION_REQUIRED")
    if (
        not account_id.startswith("DEMO-")
        or len(account_id) > 80
        or client.settings.demo_trading_enabled
        or client.settings.policy != ledger.policy
        or client.settings.margin_mode != "ISOLATED"
        or client.settings.symbols != ("BTCUSDT",)
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or ledger.risk_state != required_risk_state
        or not ledger.paused
        or ledger.verified_leverage.get("BTCUSDT") != 2
    ):
        raise PeriodReviewBlocked("READ_ONLY_PAUSED_ISOLATED_CONTEXT_REQUIRED")
    anchor = ledger.last_reconciled
    if (
        anchor is None
        or ledger.value.as_of != anchor
        or any(ledger.positions.values())
        or not ledger.orders
        or any(
            o.state not in TERMINAL
            or o.position_state != PositionState.CLOSED
            or o.remaining
            or o.open_quantity
            or o.actual_risk
            or o.protected
            for o in ledger.orders.values()
        )
    ):
        raise PeriodReviewBlocked("INVALID_LOCAL_FLAT_ANCHOR")
    if (
        ledger.net_transfers != 0
        or ledger.value.net_deposits != 0
        or ledger.income_seen
        or ledger.income_since_ms is None
        or ledger.income_since_ms > int(anchor.timestamp() * 1000)
    ):
        raise PeriodReviewBlocked("NONZERO_OR_UNVERIFIED_DEPOSIT_BASIS")
    now = store.now()
    day, week = period_keys(now, ledger.policy.week_start_kst)
    day_boundary = datetime.combine(
        datetime.fromisoformat(day).date(), time(), ZoneInfo("Asia/Seoul")
    ).astimezone(UTC)
    week_boundary = datetime.combine(
        datetime.fromisoformat(week).date(), time(), ZoneInfo("Asia/Seoul")
    ).astimezone(UTC)
    if (
        not anchor < week_boundary <= day_boundary <= now
        or not 0 < (now - anchor).total_seconds() <= 7 * 86400
        or (ledger.daily_key, ledger.weekly_key)
        != period_keys(anchor, ledger.policy.week_start_kst)
        or ledger.daily_key >= day
        or ledger.weekly_key >= week
    ):
        raise PeriodReviewBlocked("PERIOD_OR_ANCHOR_WINDOW_UNSUPPORTED")
    start = int(anchor.timestamp() * 1000)
    client.sync_clock()
    first = snapshot(client, ledger, store.now())
    _flat_truth(ledger, first)
    end = int(store.now().timestamp() * 1000)
    if trades_window(client, start, end) or income_window(client, start, end):
        raise PeriodReviewBlocked("POST_ANCHOR_HISTORY_NOT_EMPTY")
    client.sync_clock()
    raw = client.request("GET", "/fapi/v3/account")
    if not isinstance(raw, dict) or not isinstance(raw.get("assets"), list):
        raise PeriodReviewBlocked("USDT_ASSET_SCOPE_OR_BALANCE_MISMATCH")
    usdt = [r for r in raw["assets"] if isinstance(r, dict) and r.get("asset") == "USDT"]
    if len(usdt) != 1:
        raise PeriodReviewBlocked("USDT_ASSET_SCOPE_OR_BALANCE_MISMATCH")
    wallet = signed_number(usdt[0].get("walletBalance"))
    if (
        wallet <= 0
        or wallet != ledger.value.equity
        or signed_number(usdt[0].get("marginBalance")) != wallet
        or signed_number(usdt[0].get("unrealizedProfit")) != 0
        or signed_number(raw.get("totalWalletBalance")) != wallet
        or signed_number(raw.get("totalMarginBalance")) != wallet
        or signed_number(raw.get("totalUnrealizedProfit")) != 0
    ):
        raise PeriodReviewBlocked("USDT_ASSET_SCOPE_OR_BALANCE_MISMATCH")
    # Repeat through a later cutoff, not merely the first fixed window.
    cutoff = store.now()
    end = int(cutoff.timestamp() * 1000)
    if trades_window(client, start, end) or income_window(client, start, end):
        raise PeriodReviewBlocked("POST_ANCHOR_HISTORY_NOT_EMPTY")
    client.sync_clock()
    final = snapshot(client, ledger, store.now())
    _flat_truth(ledger, final)
    if (
        first.fingerprint != final.fingerprint
        or first.wallet_balance != wallet
        or final.wallet_balance != wallet
        or first.equity != wallet
        or final.equity != wallet
        or not 0
        <= (store.now() - final.as_of).total_seconds()
        <= ledger.policy.max_snapshot_age_seconds
        or period_keys(store.now(), ledger.policy.week_start_kst) != (day, week)
    ):
        raise PeriodReviewBlocked("ACCOUNT_OR_PERIOD_CHANGED")
    for base, cap in (
        (ledger.daily_base, "daily_loss_limit_pct"),
        (ledger.weekly_base, "weekly_loss_limit_pct"),
        (ledger.high_water, "max_drawdown_pct"),
    ):
        if (base - wallet) / base * 100 >= ledger.policy.limit(cap):
            raise PeriodReviewBlocked("EXISTING_LOSS_LIMIT_REACHED")
    if ledger.high_water < wallet:
        raise PeriodReviewBlocked("HIGH_WATER_REQUIRES_SEPARATE_REVIEW")
    probe = ledger.model_copy(deep=True)
    probe.daily_key, probe.weekly_key = day, week
    probe.daily_base = probe.weekly_base = wallet
    probe.value = AccountValue(
        as_of=store.now(),
        initial=ledger.value.initial,
        realized=wallet - ledger.value.initial,
        net_deposits=Decimal(0),
    )
    probe.risk_state = RiskState.NORMAL
    account_risk(probe, store.now())
    if probe.risk_state != RiskState.NORMAL:
        raise PeriodReviewBlocked("RECONSTRUCTED_RISK_GATES_FAILED")
    evidence = hashlib.sha256(
        json.dumps(
            {
                "anchor": anchor.isoformat(),
                "wallet": str(wallet),
                "snapshot": final.fingerprint,
                "btc_trades": {},
                "usdt_income": {},
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    token = hashlib.sha256(
        json.dumps(
            {
                "account": account_id,
                "source": SOURCE,
                "ledger": ledger.model_dump(mode="json", exclude={"reconciled", "stream_ready"}),
                "day": day,
                "week": week,
                "evidence": evidence,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "review": "OPERATOR_ATTESTED_PERIOD_PREVIEW_NO_DATABASE_CHANGE",
        "account_id": account_id,
        "source": SOURCE,
        "review_token": token,
        "old_day_kst": ledger.daily_key,
        "old_daily_base_usdt": str(ledger.daily_base),
        "old_week_kst": ledger.weekly_key,
        "old_weekly_base_usdt": str(ledger.weekly_base),
        "proposed_day_kst": day,
        "proposed_week_kst": week,
        "day_boundary_utc": day_boundary.isoformat(),
        "week_boundary_utc": week_boundary.isoformat(),
        "proposed_daily_base_usdt": str(wallet),
        "proposed_weekly_base_usdt": str(wallet),
        "net_deposit_basis_usdt": "0",
        "anchor_utc": anchor.isoformat(),
        "signed_evidence_until_utc": cutoff.isoformat(),
        "signed_evidence_hash": evidence,
        "exchange_fingerprint": final.fingerprint,
        "post_anchor_btc_trades": 0,
        "post_anchor_income_records": 0,
        "operator_attestation_used": True,
        "account_wide_exchange_export_verified": False,
        "high_water_preserved": True,
        "old_valuation_timestamp_preserved": True,
        "requests_preserved": True,
        "risk_state": ledger.risk_state.value,
        "paused": ledger.paused,
        "database_changed": False,
        "unresolved_requests_acknowledged": False,
        "recovery_eligible": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def apply_periods(
    store: DemoStore,
    lease: Lease,
    client: DemoClient,
    operation_id: UUID,
    expected_token: str,
    *,
    attest_no_activity: bool,
    accept_operator_evidence: bool,
) -> str:
    if (
        not attest_no_activity
        or not accept_operator_evidence
        or not re.fullmatch(r"[0-9a-f]{64}", expected_token)
    ):
        raise PeriodReviewBlocked("EXPLICIT_MANUAL_SOURCE_ACCEPTANCE_REQUIRED")
    if client.settings.demo_trading_enabled:
        raise PeriodReviewBlocked("READ_ONLY_PAUSED_ISOLATED_CONTEXT_REQUIRED")

    def apply(ledger: DemoLedger) -> str:
        key = str(operation_id)
        receipt = ledger.attested_period_baselines.get(key)
        if receipt is not None:
            if receipt.review_token != expected_token or ledger.operations.get(key) != OPERATION:
                raise PeriodReviewBlocked("PERIOD_OPERATION_CONFLICT")
            return "ALREADY_RECORDED_NO_ACTION"
        if any(
            key in records
            for records in (
                ledger.operations,
                ledger.recovery_receipts,
                ledger.cancelled_exposure_corrections,
                ledger.attested_day_baselines,
                ledger.exit_anchored_day_baselines,
            )
        ):
            raise PeriodReviewBlocked("PERIOD_OPERATION_CONFLICT")
        report = review_periods(
            store,
            client,
            ledger,
            account_id=lease.account_id,
            attest_no_activity=attest_no_activity,
        )
        if report["review_token"] != expected_token:
            raise PeriodReviewBlocked("PERIOD_PREVIEW_CHANGED")
        receipt = AttestedPeriodBaseline(
            review_token=expected_token,
            source=SOURCE,
            day_boundary_utc=datetime.fromisoformat(str(report["day_boundary_utc"])),
            week_boundary_utc=datetime.fromisoformat(str(report["week_boundary_utc"])),
            baseline_usdt=Decimal(str(report["proposed_daily_base_usdt"])),
            old_daily_key=ledger.daily_key,
            old_daily_base=ledger.daily_base,
            old_weekly_key=ledger.weekly_key,
            old_weekly_base=ledger.weekly_base,
            anchor_utc=datetime.fromisoformat(str(report["anchor_utc"])),
            signed_evidence_until_utc=datetime.fromisoformat(
                str(report["signed_evidence_until_utc"])
            ),
            attested_until_utc=store.now(),
            signed_evidence_hash=str(report["signed_evidence_hash"]),
            exchange_fingerprint=str(report["exchange_fingerprint"]),
        )
        ledger.daily_key, ledger.weekly_key = (
            str(report["proposed_day_kst"]),
            str(report["proposed_week_kst"]),
        )
        ledger.daily_base = ledger.weekly_base = receipt.baseline_usdt
        ledger.attested_period_baselines[key] = receipt
        ledger.operations[key] = OPERATION
        ledger.reconciled = ledger.stream_ready = False
        return "OPERATOR_ATTESTED_PERIOD_BASELINES_ADOPTED_KEEP_LOCK"

    return store.change(lease, "DEMO_OPERATOR_ATTESTED_PERIOD_BASELINES", apply)
