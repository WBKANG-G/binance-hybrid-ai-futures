"""NORMAL/paused period preview and explicit fenced adoption; entries stay paused."""

import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from integrations.binance_usdm.demo import DemoClient
from integrations.binance_usdm.recovery_transport import recovery_connection
from packages.domain.contracts import RiskState
from packages.persistence.demo import DemoStore
from services.execution.demo_models import AttestedPeriodBaseline, DemoLedger
from services.execution.demo_period_review import SOURCE, PeriodReviewBlocked, _review_periods
from services.execution.demo_recovery_budget import AuditBudgetExceeded, RecoveryReadClient
from services.execution.models import Lease


def review_paused_periods(
    store: DemoStore,
    client: DemoClient,
    ledger: DemoLedger,
    *,
    account_id: str,
    attest_no_activity: bool,
) -> dict[str, object]:
    if not attest_no_activity:
        raise PeriodReviewBlocked("EXPLICIT_OPERATOR_ATTESTATION_REQUIRED")
    if (
        ledger.risk_state != RiskState.NORMAL
        or not ledger.paused
        or client.settings.demo_trading_enabled
    ):
        raise PeriodReviewBlocked("READ_ONLY_PAUSED_ISOLATED_CONTEXT_REQUIRED")
    try:
        with recovery_connection(client) as pooled:
            bounded = RecoveryReadClient(client)
            report = _review_periods(
                store,
                bounded,
                ledger,
                account_id=account_id,
                attest_no_activity=attest_no_activity,
                required_risk_state=RiskState.NORMAL,
            )
            bounded.budget.check()
            report.update(
                {
                    "review": "PAUSED_NORMAL_PERIOD_PREVIEW_NO_DATABASE_CHANGE",
                    "risk_state_preserved": True,
                    "preview_only": True,
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
        # Preserve fixed safe resource codes without printing transport/credential data.
        raise PeriodPreviewBudgetBlocked(error.code) from None


class PeriodPreviewBudgetBlocked(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


OPERATION = "ADOPT_PAUSED_NORMAL_ATTESTED_PERIOD_BASELINES"
ADOPTED = "OPERATOR_ATTESTED_PERIOD_BASELINES_ADOPTED_ENTRIES_STILL_PAUSED"


def recorded_period_apply(ledger: DemoLedger, operation: UUID, token: str) -> bool:
    key = str(operation)
    receipt = ledger.attested_period_baselines.get(key)
    if receipt is not None:
        if (
            receipt.review_token != token
            or receipt.source != SOURCE
            or ledger.operations.get(key) != OPERATION
        ):
            raise PeriodReviewBlocked("PERIOD_OPERATION_CONFLICT")
        return True
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
    return False


def apply_paused_periods(
    store: DemoStore,
    lease: Lease,
    client: DemoClient,
    operation: UUID,
    token: str,
    *,
    attest_no_activity: bool,
    accept_operator_evidence: bool,
) -> str:
    if (
        not attest_no_activity
        or not accept_operator_evidence
        or operation.version != 4
        or not re.fullmatch(r"[0-9a-f]{64}", token)
    ):
        raise PeriodReviewBlocked("EXPLICIT_MANUAL_SOURCE_ACCEPTANCE_REQUIRED")
    if client.settings.demo_trading_enabled:
        raise PeriodReviewBlocked("READ_ONLY_PAUSED_ISOLATED_CONTEXT_REQUIRED")
    # An identical recorded request is a read-only no-op, including its audit rows.
    if recorded_period_apply(store.read(lease.account_id), operation, token):
        return "ALREADY_RECORDED_NO_ACTION"

    def adopt(ledger: DemoLedger) -> str:
        if recorded_period_apply(ledger, operation, token):
            return "ALREADY_RECORDED_NO_ACTION"
        report = review_paused_periods(
            store,
            client,
            ledger,
            account_id=lease.account_id,
            attest_no_activity=attest_no_activity,
        )
        if report["review_token"] != token:
            raise PeriodReviewBlocked("PERIOD_PREVIEW_CHANGED")
        receipt = AttestedPeriodBaseline(
            review_token=token,
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
        ledger.attested_period_baselines[str(operation)] = receipt
        ledger.operations[str(operation)] = OPERATION
        ledger.reconciled = ledger.stream_ready = False
        return ADOPTED

    return store.change(lease, "DEMO_PAUSED_NORMAL_ATTESTED_PERIOD_BASELINES", adopt)
