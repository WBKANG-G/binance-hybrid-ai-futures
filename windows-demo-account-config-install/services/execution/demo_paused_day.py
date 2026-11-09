"""Operator-attested flat day rollover for NORMAL/paused; no risk-state substitution."""

import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from integrations.binance_usdm.demo import DemoClient
from integrations.binance_usdm.recovery_transport import recovery_connection
from packages.domain.contracts import RiskState
from packages.persistence.demo import DemoStore
from services.execution.demo_day_reconstruction import (
    SOURCE,
    DayReconstructionBlocked,
    _review_day_reconstruction,
)
from services.execution.demo_models import AttestedDayBaseline, DemoLedger
from services.execution.demo_recovery_budget import AuditBudgetExceeded, RecoveryReadClient
from services.execution.models import Lease

OPERATION = "ADOPT_PAUSED_NORMAL_ATTESTED_DAY_BASELINE"


def review_paused_day(
    store: DemoStore,
    client: DemoClient,
    ledger: DemoLedger,
    *,
    account_id: str,
    attest_no_other_trades: bool,
) -> dict[str, object]:
    # This public entry point cannot handle or reset any loss/emergency/unhealthy lock.
    if (
        ledger.risk_state != RiskState.NORMAL
        or not ledger.paused
        or client.settings.demo_trading_enabled
    ):
        raise DayReconstructionBlocked("NORMAL_PAUSED_READ_ONLY_REQUIRED")
    try:
        with recovery_connection(client) as pooled:
            bounded = RecoveryReadClient(client)
            report = _review_day_reconstruction(
                store,
                bounded,
                ledger,
                account_id=account_id,
                attest_no_other_trades=attest_no_other_trades,
                required_risk_state=RiskState.NORMAL,
            )
            bounded.budget.check()
            report.update(
                {
                    "review": "PAUSED_NORMAL_DAY_PREVIEW_NO_DATABASE_CHANGE",
                    "risk_state_preserved": True,
                    "read_diagnostic": {
                        **(
                            pooled.diagnostics()
                            if pooled
                            else {"audit_transport_mode": "UNPOOLED_TRANSPORT"}
                        ),
                        "signed_get_count": bounded.budget.signed_queries,
                        "clock_probe_count": bounded.budget.clock_probes,
                    },
                }
            )
            return report
    except AuditBudgetExceeded as error:
        raise DayReconstructionBlocked(error.code) from None


def apply_paused_day(
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
        report = review_paused_day(
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
        return "OPERATOR_ATTESTED_DAY_BASELINE_ADOPTED_ENTRIES_STILL_PAUSED"

    return store.change(lease, "DEMO_PAUSED_NORMAL_ATTESTED_DAY_BASELINE", apply)
