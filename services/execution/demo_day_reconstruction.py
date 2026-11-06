"""Explicit operator-attested USDT day baseline; never an exchange-wide proof.

Limited to no income after a verified local flat anchor and an unchanged USDT
wallet. The account-risk implementation and old valuation timestamps are unchanged;
locks stay active. The manual evidence source remains explicit in preview and receipt.
"""

import hashlib
import json
import re
from datetime import UTC, datetime, time
from decimal import Decimal, DecimalException
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number, snapshot
from packages.domain.contracts import RiskState
from packages.persistence.demo import DemoStore
from services.execution.demo_day_review import income_window
from services.execution.demo_history_review import _review_flat_history, review_flat_history
from services.execution.demo_models import AttestedDayBaseline, DemoLedger
from services.execution.models import Lease
from services.execution.paper import account_risk
from services.risk.paper import AccountValue, period_keys

SOURCE: Final = "OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE"
OPERATION = "ADOPT_OPERATOR_ATTESTED_USDT_DAY_BASELINE"


class DayReconstructionBlocked(DemoFailure):
    pass


def review_day_reconstruction(
    store: DemoStore,
    client: DemoClient,
    ledger: DemoLedger,
    *,
    account_id: str,
    attest_no_other_trades: bool,
) -> dict[str, object]:
    return _review_day_reconstruction(
        store,
        client,
        ledger,
        account_id=account_id,
        attest_no_other_trades=attest_no_other_trades,
        required_risk_state=RiskState.EMERGENCY,
    )


def _review_day_reconstruction(
    store: DemoStore,
    client: DemoClient,
    ledger: DemoLedger,
    *,
    account_id: str,
    attest_no_other_trades: bool,
    required_risk_state: RiskState,
) -> dict[str, object]:
    if not attest_no_other_trades:
        raise DayReconstructionBlocked("EXPLICIT_OPERATOR_ATTESTATION_REQUIRED")
    now = store.now()
    day, week = period_keys(now, ledger.policy.week_start_kst)
    anchor = ledger.last_reconciled
    boundary = datetime.combine(
        now.astimezone(ZoneInfo("Asia/Seoul")).date(), time(), ZoneInfo("Asia/Seoul")
    ).astimezone(UTC)
    if (
        not account_id.startswith("DEMO-")
        or client.settings.demo_trading_enabled
        or client.settings.policy != ledger.policy
        or client.settings.margin_mode != "ISOLATED"
        or client.settings.symbols != ("BTCUSDT",)
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or required_risk_state not in {RiskState.NORMAL, RiskState.EMERGENCY}
        or ledger.risk_state != required_risk_state
        or not ledger.paused
        or anchor is None
        or ledger.value.as_of != anchor
        or ledger.daily_key >= day
        or anchor >= boundary
    ):
        raise DayReconstructionBlocked("ATTESTED_DAY_CONTEXT_MISMATCH")
    if ledger.weekly_key != week:
        raise DayReconstructionBlocked("WEEK_BASELINE_REQUIRES_SEPARATE_REVIEW")
    if (
        ledger.income_since_ms is None
        or not 0 <= ledger.income_since_ms <= int(anchor.timestamp() * 1000)
        or ledger.income_seen
        or ledger.net_transfers != 0
        or ledger.value.net_deposits != 0
    ):
        raise DayReconstructionBlocked("NONZERO_OR_UNKNOWN_TRANSFER_BASIS")
    first = snapshot(client, ledger, now)
    history = (
        review_flat_history(store, client, ledger)
        if required_risk_state == RiskState.EMERGENCY
        else _review_flat_history(store, client, ledger, required_risk_state="NORMAL")
    )
    if (
        history["last_known_full_exit_fill_found"] is not True
        or history["btc_trades_after_known_exit_before_boundary"] != 0
        or history["other_trades_at_exit_timestamp"] != 0
        or history["btc_trades_at_or_after_boundary"] != 0
        or history["wallet_change_matches_income"] is not True
    ):
        raise DayReconstructionBlocked("BTC_EXIT_AND_HISTORY_NOT_CONFIRMED")
    start_ms, end_ms = int(anchor.timestamp() * 1000), int(store.now().timestamp() * 1000)
    incomes = income_window(client, start_ms, end_ms)
    if incomes != income_window(client, start_ms, end_ms) or incomes:
        raise DayReconstructionBlocked("POST_ANCHOR_INCOME_NOT_EMPTY_OR_CHANGED")
    raw = client.request("GET", "/fapi/v3/account")
    if not isinstance(raw, dict) or not isinstance(raw.get("assets"), list):
        raise DayReconstructionBlocked("USDT_ASSET_SCOPE_MISSING")
    usdt = [r for r in raw["assets"] if isinstance(r, dict) and r.get("asset") == "USDT"]
    if len(usdt) != 1:
        raise DayReconstructionBlocked("USDT_ASSET_SCOPE_MISSING")
    try:
        wallet = signed_number(usdt[0].get("walletBalance"))
        if (
            wallet <= 0
            or signed_number(usdt[0].get("unrealizedProfit")) != 0
            or signed_number(usdt[0].get("marginBalance")) != wallet
            or signed_number(raw.get("totalWalletBalance")) != wallet
            or signed_number(raw.get("totalMarginBalance")) != wallet
            or signed_number(raw.get("totalUnrealizedProfit")) != 0
        ):
            raise DayReconstructionBlocked("USDT_TOTAL_OR_UNREALIZED_MISMATCH")
    except (DemoFailure, DecimalException, ValueError, TypeError):
        raise DayReconstructionBlocked("USDT_TOTAL_OR_UNREALIZED_MISMATCH") from None
    final = snapshot(client, ledger, store.now())
    if (
        first.fingerprint != final.fingerprint
        or first.wallet_balance != wallet
        or final.wallet_balance != wallet
        or ledger.value.equity != wallet
        or any(final.positions.values())
        or final.leverage.get("BTCUSDT") != 2
        or period_keys(store.now(), ledger.policy.week_start_kst) != (day, week)
    ):
        raise DayReconstructionBlocked("ACCOUNT_OR_DAY_CHANGED")
    candidate = wallet  # This path requires an explicitly verified zero net-deposit basis.
    for base, cap in (
        (ledger.daily_base, "daily_loss_limit_pct"),
        (ledger.weekly_base, "weekly_loss_limit_pct"),
        (ledger.high_water, "max_drawdown_pct"),
    ):
        if (base - candidate) / base * 100 >= ledger.policy.limit(cap):
            raise DayReconstructionBlocked("EXISTING_LOSS_LIMIT_REACHED")
    if ledger.high_water < candidate:
        raise DayReconstructionBlocked("HIGH_WATER_REQUIRES_SEPARATE_REVIEW")
    probe = ledger.model_copy(deep=True)
    probe.daily_key, probe.daily_base = day, candidate
    probe.value = AccountValue(
        as_of=store.now(),
        initial=ledger.value.initial,
        realized=wallet - ledger.value.initial,
        net_deposits=Decimal(0),
    )
    probe.risk_state = RiskState.NORMAL
    account_risk(probe, store.now())
    if probe.risk_state != RiskState.NORMAL:
        raise DayReconstructionBlocked("RECONSTRUCTED_RISK_GATES_FAILED")
    payload = {
        "account_id": account_id,
        "source": SOURCE,
        "boundary": boundary.isoformat(),
        "ledger": ledger.model_dump(mode="json", exclude={"reconciled", "stream_ready"}),
        "history_hash": history["evidence_hash"],
        "snapshot": final.fingerprint,
        "candidate": str(candidate),
        "post_anchor_income": incomes,
        "known_exit": history["last_known_full_exit_fill_utc"],
    }
    evidence = hashlib.sha256(
        json.dumps(
            {
                "history": history["evidence_hash"],
                "incomes": incomes,
                "snapshot": final.fingerprint,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    token = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "review": "OPERATOR_ATTESTED_DAY_PREVIEW_NO_DATABASE_CHANGE",
        "account_id": account_id,
        "source": SOURCE,
        "review_token": token,
        "old_daily_key": ledger.daily_key,
        "old_daily_base_usdt": str(ledger.daily_base),
        "proposed_day_kst": day,
        "boundary_utc": boundary.isoformat(),
        "proposed_daily_base_usdt": str(candidate),
        "net_deposit_basis_usdt": "0",
        "anchor_utc": anchor.isoformat(),
        "known_exit_utc": history["last_known_full_exit_fill_utc"],
        "attested_until_utc": store.now().isoformat(),
        "signed_evidence_hash": evidence,
        "exchange_fingerprint": final.fingerprint,
        "account_wide_exchange_export_verified": False,
        "operator_attestation_used": True,
        "weekly_base_preserved": True,
        "high_water_preserved": True,
        "old_valuation_timestamp_preserved": True,
        "requests_preserved": True,
        "risk_state": ledger.risk_state.value,
        "paused": ledger.paused,
        "database_changed": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "recovery_eligible": False,
    }


def apply_day_reconstruction(
    store: DemoStore,
    lease: Lease,
    client: DemoClient,
    operation_id: UUID,
    expected_token: str,
    *,
    attest_no_other_trades: bool,
    accept_operator_evidence: bool,
) -> str:
    if (
        not attest_no_other_trades
        or not accept_operator_evidence
        or not re.fullmatch(r"[0-9a-f]{64}", expected_token)
    ):
        raise DayReconstructionBlocked("EXPLICIT_MANUAL_SOURCE_ACCEPTANCE_REQUIRED")
    if client.settings.demo_trading_enabled:
        raise DayReconstructionBlocked("READ_ONLY_CLIENT_REQUIRED")

    def apply(ledger: DemoLedger) -> str:
        key = str(operation_id)
        receipt = ledger.attested_day_baselines.get(key)
        if receipt is not None:
            if receipt.review_token != expected_token or ledger.operations.get(key) != OPERATION:
                raise DayReconstructionBlocked("DAY_OPERATION_CONFLICT")
            return "ALREADY_RECORDED_NO_ACTION"
        if (
            key in ledger.operations
            or key in ledger.recovery_receipts
            or key in ledger.cancelled_exposure_corrections
        ):
            raise DayReconstructionBlocked("DAY_OPERATION_CONFLICT")
        report = review_day_reconstruction(
            store,
            client,
            ledger,
            account_id=lease.account_id,
            attest_no_other_trades=attest_no_other_trades,
        )
        if report["review_token"] != expected_token:
            raise DayReconstructionBlocked("DAY_PREVIEW_CHANGED")
        receipt = AttestedDayBaseline(
            review_token=expected_token,
            source=SOURCE,
            boundary_utc=datetime.fromisoformat(str(report["boundary_utc"])),
            baseline_usdt=Decimal(str(report["proposed_daily_base_usdt"])),
            old_daily_key=ledger.daily_key,
            old_daily_base=ledger.daily_base,
            anchor_utc=datetime.fromisoformat(str(report["anchor_utc"])),
            known_exit_utc=datetime.fromisoformat(str(report["known_exit_utc"])),
            attested_until_utc=store.now(),
            signed_evidence_hash=str(report["signed_evidence_hash"]),
            exchange_fingerprint=str(report["exchange_fingerprint"]),
        )
        ledger.daily_key, ledger.daily_base = str(report["proposed_day_kst"]), receipt.baseline_usdt
        ledger.attested_day_baselines[key] = receipt
        ledger.operations[key] = OPERATION
        ledger.reconciled = ledger.stream_ready = False
        return "OPERATOR_ATTESTED_DAILY_BASELINE_ADOPTED_KEEP_LOCK"

    return store.change(lease, "DEMO_OPERATOR_ATTESTED_DAY_BASELINE", apply)
