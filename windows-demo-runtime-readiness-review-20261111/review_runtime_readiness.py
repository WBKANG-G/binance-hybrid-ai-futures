"""Read-only post-install controls and optional bounded DEMO evidence; no activation."""

# ruff: noqa: E402
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5


class Blocked(ValueError):
    pass


INSTALLED_PINS = {
    "alembic.ini": "e7955a0ffb9c32a9c5067d7885ee9197328f17a4a12fb4a5bb556c2b85763aa2",
    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",
    "integrations/binance_usdm/demo.py": "767d3a29b59882035bdfbf61d3271b39bfece0bff4b359e636db62ad9a5e84eb",
    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",
    "integrations/binance_usdm/demo_read_routing.py": "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d",
    "integrations/binance_usdm/demo_snapshot.py": "f80f6e51505ab1095c25b6ee76af01c258cff911aabd02c201bfcddc636f2258",
    "integrations/binance_usdm/demo_tp_admission.py": "c6a66aff461f2aab59d2f2ae1f42a44c6388268d93893ecf4854c3af111ee0d8",
    "integrations/binance_usdm/demo_tp_weight_model.py": "611948a52ffad41ecc448549f530558cd4df2585e23273d23422c8a995c7cbc3",
    "integrations/binance_usdm/demo_tp_window_budget.py": "9433a307767a2e60a4cdbc31a85a8cb386066ff9799249239633ead1cf76120b",
    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",
    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",
    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",
    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",
    "migrations/env.py": "4256345fc24dd52ba706aa9b712268b9df436d52bafa7238234272d754734086",
    "migrations/script.py.mako": "67042994cfc495007c508ccae15e44d21057d90222461630f9bc04d3657f5f6d",
    "migrations/versions/0001_public_market_data.py": "b62afcb18d6d6c42d710ca0cb0eb42001e174934f11678443dc5ac24d86932f7",
    "migrations/versions/0002_paper.py": "91ffad2a439b9f47e2636939a5aba091490942db7a87bb0d87124a531870c77d",
    "migrations/versions/0003_demo.py": "6b2a26ba34855a3dee6d3b2c67eb9b3ecac644b4e2e31ad6353e907a3c233762",
    "migrations/versions/0004_demo_urgent_alerts.py": "da9e4de16f1f9d4c15d70293d3b252084648fbbcb060f8ec7740b9a85f83e8ad",
    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",
    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "pyproject.toml": "54760a9fa52fc4cc877d2302c7952688ddd13e12f1efc8e44a0974aae8914019",
    "scripts/check_demo_disconnect.py": "c85c013412053237eda7ed82a8948aa4b50f9da1951d173360a1ecfb46228a1e",
    "scripts/check_demo_session_ready.py": "18259376c0404f98bc0cb91afeb31e33789f581562e14939989816c2fa8754d9",
    "scripts/check_demo_tp_trigger.py": "a0bdd07027d5e3ca9b54a1468841fe2d40cf072c38c3c91c6c58292c2e67d96b",
    "scripts/demo_key_session.py": "85c698bc8bbad98bad4051c346a5e4e0a3a3bd22f8bbb90a362f835cadc633dd",
    "scripts/review_tp_minute_budget.py": "e8462dddf04d6d9107d01d7fbe8373df9a3a1e0cf6361d5f057021b2f67e9870",
    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",
    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",
    "services/execution/demo_empty_db_restart.py": "6adcc8284b43050bc4a115f2796c001d424ba7e0261654c099075fb3d950e39f",
    "services/execution/demo_empty_worker_recovery.py": "8e3cac8da89fb80ef9f3654ae8591ffea7be1db1c7dc60070da72b72851b2070",
    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",
    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",
    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",
    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",
    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",
    "services/execution/demo_private_stability.py": "d3aee78f2da73c15c3eff5f89403fae666de5109ad3c2402585f2150df1a5123",
    "services/execution/demo_protected_worker_check.py": "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709",
    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",
    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",
    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",
    "services/execution/demo_tp_admission_candidate.py": "d8ab1b2ac699b38aaf75977ea97d49fa44ba60a52f518f997fd15f8a2489760a",
    "services/execution/demo_tp_budget_integration.py": "ab8b13e714c3298cb659b34a6ead0948dd6bf6c943548f0a3657cbc031786399",
    "services/execution/demo_tp_fenced_handoff_candidate.py": "9fcdf0c42b60dd7bae9f9fe2257a3d9e659581c3b711191b15c531b05084ee6c",
    "services/execution/demo_tp_first_confirmation_candidate.py": "0b31f29c94fb3f2e56c68c855737eddc312f80871f500867530d078e1190248f",
    "services/execution/demo_tp_staged_confirmation_candidate.py": "76544f006bdbcc17f54d19dae031e6eaebde831dca0537a15f73069f3d43335b",
    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",
    "services/execution/demo_tp_uncertain_cancel_candidate.py": "f188463cb0e2d17a160ed4f02fc9aac5b42d30938a44f2d03e8fa468bad48736",
    "services/execution/demo_validation.py": "725b36a93212733bafed401d1ba059d176d0f43b7a8154288d30df03e3b6b3e4",
    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",
    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",
    "services/execution/readiness_closed_history.py": "0c9cb90bc74402fdaed025f715a98a2e47169eee4d1f9990c75e8f5671e358da",
    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",
    "uv.lock": "49bbe38dc3f559466c4666febe686926db6503413a45bd766088ed2454e01097",
}


def source_cohort_valid(pins):
    try:
        return len(pins) == 55 and all(
            (
                Path(n).is_file() and hashlib.sha256(Path(n).read_bytes()).hexdigest() == h
                for n, h in pins.items()
            )
        )
    except (OSError, ValueError, TypeError):
        return False


if __name__ == "__main__":
    if not source_cohort_valid(INSTALLED_PINS):
        print(
            json.dumps(
                {
                    "runtime_readiness_review": "BLOCKED_KEEP_PAUSED",
                    "stop_reason": "INSTALLED_SOURCE_HASH_MISMATCH",
                    "order_writes": 0,
                    "execution_approved": False,
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            )
        )
        raise SystemExit(2)

from sqlalchemy import create_engine, select

from packages.domain.config import database_url
from packages.persistence.demo import DemoStore, accounts, events, outbox
from services.execution.demo_empty_db_restart import empty
from services.execution.demo_models import DemoLedger
from services.risk.paper import period_keys

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"

INCIDENT = "041d1f35-870e-46cb-aad2-ed6059bc1095"

RECEIPT = str(uuid5(UUID(INCIDENT), "signed-operator-manual-close-v1"))


def require(ok, code):
    if not ok:
        raise Blocked(code)


TRADE_START = 1793947690122

CLOSE_MS = 1793947973987


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


PRIOR_DAY_KEY = str(uuid5(UUID(INCIDENT), "operator-attested-external-close-day-2026-11-07-v1"))

EXPECTED = {
    "old_day_kst": "2026-11-07",
    "proposed_day_kst": "2026-11-09",
    "old_week_kst": "2026-11-02",
    "proposed_week_kst": "2026-11-09",
    "old_daily_base_usdt": "4998.74183608",
    "proposed_daily_base_usdt": "4998.74183608",
    "old_weekly_base_usdt": "4998.99761493",
    "proposed_weekly_base_usdt": "4998.74183608",
    "high_water_usdt_preserved": "5000.00000000",
    "day_boundary_utc": "2026-11-08T15:00:00+00:00",
    "week_boundary_utc": "2026-11-08T15:00:00+00:00",
    "adoption_snapshot_anchor_utc": "2026-11-06T07:25:25.109073+00:00",
    "prior_attested_day_operation_id": PRIOR_DAY_KEY,
    "operator_attested_day_kst": "2026-11-09",
}

APPLY_KEY = str(uuid5(UUID(INCIDENT), "operator-attested-external-close-periods-2026-11-09-v1"))

OPERATION = "ADOPT_EXTERNAL_CLOSE_ATTESTED_PERIOD_BASELINES_V1"

SOURCE = "OPERATOR_ATTESTED_FLAT_PERIODS_WITH_SIGNED_USDT_EVIDENCE"


def preserved(ledger):
    data = ledger.model_dump(mode="json")
    for key in (
        "daily_key",
        "daily_base",
        "weekly_key",
        "weekly_base",
        "stream_ready",
        "reconciled",
    ):
        data.pop(key)
    for key in ("operations", "attested_period_baselines"):
        data[key].pop(APPLY_KEY, None)
    return digest(data)


def recorded(ledger):
    raw = ledger.operations.get(APPLY_KEY)
    receipt = ledger.attested_period_baselines.get(APPLY_KEY)
    if raw is None and receipt is None:
        require(
            not any(
                APPLY_KEY in x
                for x in (
                    ledger.recovery_receipts,
                    ledger.cancelled_exposure_corrections,
                    ledger.attested_day_baselines,
                    ledger.exit_anchored_day_baselines,
                )
            ),
            "PERIOD_OPERATION_CONFLICT",
        )
        return False
    require(raw is not None and receipt is not None, "PERIOD_OPERATION_CONFLICT")
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        raise Blocked("PERIOD_OPERATION_CONFLICT") from None
    require(
        isinstance(record, dict)
        and record.get("operation") == OPERATION
        and (record.get("reviewed_values") == EXPECTED)
        and (record.get("evidence_source") == "OPERATOR_ATTESTED_SIGNED_EXTERNAL_CLOSE_FLAT_ANCHOR")
        and (type(record.get("signed_history_cutoff_ms")) is int)
        and (
            datetime.fromtimestamp(record["signed_history_cutoff_ms"] / 1000, UTC)
            == receipt.signed_evidence_until_utc
        )
        and (record.get("preserved_ledger_sha256") == preserved(ledger))
        and (record.get("typed_receipt_sha256") == digest(receipt.model_dump(mode="json")))
        and (record.get("exact_day_midnight_valuation_verified") is False)
        and (record.get("exact_week_midnight_valuation_verified") is False)
        and (record.get("account_wide_exchange_export_verified") is False),
        "PERIOD_OPERATION_CONFLICT",
    )
    require(
        receipt.review_token == digest(EXPECTED)
        and receipt.source == SOURCE
        and (receipt.old_daily_key == EXPECTED["old_day_kst"])
        and (receipt.old_weekly_key == EXPECTED["old_week_kst"])
        and (receipt.old_daily_base == Decimal(EXPECTED["old_daily_base_usdt"]))
        and (receipt.old_weekly_base == Decimal(EXPECTED["old_weekly_base_usdt"]))
        and (receipt.day_boundary_utc == datetime.fromisoformat(EXPECTED["day_boundary_utc"]))
        and (receipt.week_boundary_utc == datetime.fromisoformat(EXPECTED["week_boundary_utc"]))
        and (
            receipt.anchor_utc
            == ledger.value.as_of
            == datetime.fromisoformat(EXPECTED["adoption_snapshot_anchor_utc"])
        )
        and (ledger.daily_key == EXPECTED["proposed_day_kst"])
        and (ledger.weekly_key == EXPECTED["proposed_week_kst"])
        and (
            ledger.daily_base
            == ledger.weekly_base
            == receipt.baseline_usdt
            == Decimal(EXPECTED["proposed_daily_base_usdt"])
        )
        and (ledger.high_water == Decimal(EXPECTED["high_water_usdt_preserved"]))
        and ledger.paused
        and (ledger.risk_state.value == "NORMAL")
        and (not ledger.reconciled)
        and (not ledger.stream_ready),
        "PERIOD_OPERATION_CONFLICT",
    )
    require(
        all(
            re.fullmatch("[0-9a-f]{64}", x)
            for x in (receipt.signed_evidence_hash, receipt.exchange_fingerprint)
        )
        and receipt.anchor_utc
        < receipt.week_boundary_utc
        <= receipt.day_boundary_utc
        <= receipt.signed_evidence_until_utc
        <= receipt.attested_until_utc,
        "PERIOD_OPERATION_CONFLICT",
    )
    return True


DAY_KEY = str(uuid5(UUID(INCIDENT), "operator-attested-external-close-day-2026-11-11-v1"))

DAY_SOURCE = "OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE"

DAY_OPERATION = "ADOPT_EXTERNAL_CLOSE_ATTESTED_DAY_BASELINE_20261111_V1"

REVIEWED = {
    "old_day_kst": "2026-11-09",
    "proposed_day_kst": "2026-11-11",
    "old_week_kst": "2026-11-09",
    "proposed_week_kst": "2026-11-09",
    "old_daily_base_usdt": "4998.74183608",
    "proposed_daily_base_usdt": "4998.74183608",
    "old_weekly_base_usdt": "4998.74183608",
    "proposed_weekly_base_usdt": "4998.74183608",
    "high_water_usdt_preserved": "5000.00000000",
    "day_boundary_utc": "2026-11-10T15:00:00+00:00",
    "week_boundary_utc": "2026-11-08T15:00:00+00:00",
    "prior_attested_period_operation_id": APPLY_KEY,
    "adoption_snapshot_anchor_utc": "2026-11-06T07:25:25.109073+00:00",
    "operator_attested_day_kst": "2026-11-11",
}


def day_preserved(ledger):
    data = ledger.model_dump(mode="json")
    for key in ("daily_key", "daily_base", "reconciled", "stream_ready"):
        data.pop(key)
    data["operations"].pop(DAY_KEY, None)
    data["attested_day_baselines"].pop(DAY_KEY, None)
    return digest(data)


def day_recorded(ledger):
    raw = ledger.operations.get(DAY_KEY)
    receipt = ledger.attested_day_baselines.get(DAY_KEY)
    if raw is None and receipt is None:
        require(
            not any(
                DAY_KEY in x
                for x in (
                    ledger.attested_period_baselines,
                    ledger.recovery_receipts,
                    ledger.cancelled_exposure_corrections,
                    ledger.exit_anchored_day_baselines,
                )
            ),
            "DAY_OPERATION_CONFLICT",
        )
        return False
    require(raw is not None and receipt is not None, "DAY_OPERATION_CONFLICT")
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        raise Blocked("DAY_OPERATION_CONFLICT") from None
    require(
        isinstance(record, dict)
        and record.get("operation") == DAY_OPERATION
        and (record.get("reviewed_values") == REVIEWED)
        and (record.get("evidence_source") == "OPERATOR_ATTESTED_SIGNED_EXTERNAL_CLOSE_FLAT_ANCHOR")
        and (record.get("preserved_ledger_sha256") == day_preserved(ledger))
        and (record.get("typed_receipt_sha256") == digest(receipt.model_dump(mode="json")))
        and (record.get("exact_midnight_valuation_verified") is False)
        and (record.get("account_wide_exchange_export_verified") is False),
        "DAY_OPERATION_CONFLICT",
    )
    require(
        receipt.review_token == digest(REVIEWED)
        and receipt.source == DAY_SOURCE
        and (receipt.old_daily_key == REVIEWED["old_day_kst"])
        and (receipt.old_daily_base == Decimal(REVIEWED["old_daily_base_usdt"]))
        and (ledger.daily_key == REVIEWED["proposed_day_kst"])
        and (
            ledger.daily_base
            == receipt.baseline_usdt
            == Decimal(REVIEWED["proposed_daily_base_usdt"])
        )
        and (receipt.boundary_utc == datetime.fromisoformat(REVIEWED["day_boundary_utc"]))
        and (
            receipt.anchor_utc
            == ledger.value.as_of
            == datetime.fromisoformat(REVIEWED["adoption_snapshot_anchor_utc"])
        )
        and (receipt.known_exit_utc == datetime.fromtimestamp(CLOSE_MS / 1000, UTC))
        and ledger.paused
        and (ledger.risk_state.value == "NORMAL")
        and (not ledger.reconciled)
        and (not ledger.stream_ready),
        "DAY_OPERATION_CONFLICT",
    )
    cutoff = record.get("signed_history_cutoff_ms")
    require(type(cutoff) is int and 0 < cutoff - TRADE_START <= 604800000, "DAY_OPERATION_CONFLICT")
    signed_until = datetime.fromtimestamp(cutoff / 1000, UTC)
    require(
        receipt.anchor_utc < receipt.boundary_utc <= signed_until <= receipt.attested_until_utc
        and period_keys(signed_until, ledger.policy.week_start_kst)
        == (REVIEWED["proposed_day_kst"], REVIEWED["proposed_week_kst"])
        and all(
            re.fullmatch("[0-9a-f]{64}", h)
            for h in (receipt.signed_evidence_hash, receipt.exchange_fingerprint)
        ),
        "DAY_OPERATION_CONFLICT",
    )
    prior = ledger.model_copy(deep=True)
    prior.daily_key, prior.daily_base = (receipt.old_daily_key, receipt.old_daily_base)
    prior.operations.pop(DAY_KEY)
    prior.attested_day_baselines.pop(DAY_KEY)
    require(recorded(prior), "PRIOR_PERIOD_RECEIPT_REQUIRED")
    return True


TARGET_PINS = {
    "integrations/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "integrations/binance_usdm/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "integrations/binance_usdm/demo.py": "767d3a29b59882035bdfbf61d3271b39bfece0bff4b359e636db62ad9a5e84eb",
    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",
    "integrations/binance_usdm/demo_snapshot.py": "f80f6e51505ab1095c25b6ee76af01c258cff911aabd02c201bfcddc636f2258",
    "integrations/binance_usdm/demo_tp_admission.py": "c6a66aff461f2aab59d2f2ae1f42a44c6388268d93893ecf4854c3af111ee0d8",
    "integrations/binance_usdm/demo_tp_weight_model.py": "611948a52ffad41ecc448549f530558cd4df2585e23273d23422c8a995c7cbc3",
    "integrations/binance_usdm/demo_tp_window_budget.py": "9433a307767a2e60a4cdbc31a85a8cb386066ff9799249239633ead1cf76120b",
    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",
    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",
    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",
    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",
    "packages/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "packages/domain/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",
    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",
    "packages/persistence/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "packages/persistence/demo_alerts.py": "fb8986d57ef850c7b707d59730b002ead125429e1d23d310a89b3c7e69385330",
    "packages/persistence/demo_order_quota.py": "c1ebdc1ccf9d469bd04950716ce84969fafbf18770a43d69aba6b99d117bc4b1",
    "packages/persistence/demo_shared_checkpoint.py": "14142d1a0ed1201688a71155c3d1772440580e7cc9127eb318040fdc9fa9c537",
    "packages/persistence/demo_shared_weight.py": "57696024477a81c35b69530facec75420c049b45272d00472e61ef04589fccac",
    "packages/persistence/market.py": "89265a7bc7d181863983f4d24e6bdbe7499bb5b8154a6b22c6aea46c0d216050",
    "packages/persistence/paper.py": "312f2ab4478e2cb7f980045c56ac1329d080d8e54d60f960a93ae0f2fe1d2bd7",
    "services/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",
    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",
    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",
    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",
    "services/execution/demo_execution_proof.py": "09c36ceab43021c5cf0d10da08af897124008be2e083deb560572640b3cbe0d4",
    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",
    "services/execution/demo_initial_fill.py": "aa5bcaa7f8b9c6112893c89e8e8e90c74eb16704f1dc16becc6c22bdaec24ba5",
    "services/execution/demo_managed_order_quota.py": "8d50abbe4da256ef7fe8200b0308be46bc2447b056be63782d461c977dfe6439",
    "services/execution/demo_managed_runtime.py": "6fb008830fded93d3f6063fc70e559e60a4ebf0489ed666624d4dfe996dbb02a",
    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",
    "services/execution/demo_order_admission.py": "a6804fa63566b8f7b77927f1a7e0bfe9c3b1b808d6639ffd762a901ee376baa8",
    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",
    "services/execution/demo_query_contract.py": "76a81b477465c9b0493f142030fbdeb271510138b30f9fc182ebf4ecf81ba81c",
    "services/execution/demo_quota_startup_review.py": "459056f3ac54f7d22d76535d133d0ac75b2a899e264f0cab71aacdc4d60fb0a4",
    "services/execution/demo_reconciliation.py": "7554e0fdcdab08dc222700bde8254ba7979c44c0ec4d82c1d2009ea0e0bb5040",
    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",
    "services/execution/demo_runtime_capacity.py": "75efd11305368ab939eafde160109d64ee71bf6e8f322bf0743935a61ea0cd4d",
    "services/execution/demo_runtime_confirmation.py": "e56aa98da24654a9f58710aa299014593df5a2648f01fe0b29eccd6ed1693edd",
    "services/execution/demo_runtime_cost_contract.py": "2ac8942803461666cfde468828ca87d911c21ec84ecd141e235a2adbdc9a2694",
    "services/execution/demo_runtime_guards.py": "bd8fa5a1c68a5781c30fe27980036e6528e5a0bd5de22efa1b903131b75cd325",
    "services/execution/demo_runtime_observation.py": "0d4f3c6f08cbc2efd965c892e466438c28552ddbf4b183f81968677fa5092901",
    "services/execution/demo_runtime_storage.py": "120411bcad5bab49b8233fc57433956621f6fe19fdfa20dcde4c1e649e9284cc",
    "services/execution/demo_session_diagnostics.py": "78d2bbeadf90bda14a08f5c7dcd8cc8285f9ee55389b07ffb4a8742d052d520b",
    "services/execution/demo_shared_weight_budget.py": "ca11a8a245ea6e2dcb6dbfaa549a04260c009a8373709cca05a6c8ad9daf393a",
    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",
    "services/execution/demo_tp_admission_candidate.py": "d8ab1b2ac699b38aaf75977ea97d49fa44ba60a52f518f997fd15f8a2489760a",
    "services/execution/demo_tp_budget_integration.py": "ab8b13e714c3298cb659b34a6ead0948dd6bf6c943548f0a3657cbc031786399",
    "services/execution/demo_tp_fenced_handoff_candidate.py": "9fcdf0c42b60dd7bae9f9fe2257a3d9e659581c3b711191b15c531b05084ee6c",
    "services/execution/demo_tp_first_confirmation_candidate.py": "0b31f29c94fb3f2e56c68c855737eddc312f80871f500867530d078e1190248f",
    "services/execution/demo_tp_staged_confirmation_candidate.py": "76544f006bdbcc17f54d19dae031e6eaebde831dca0537a15f73069f3d43335b",
    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",
    "services/execution/demo_tp_uncertain_cancel_candidate.py": "f188463cb0e2d17a160ed4f02fc9aac5b42d30938a44f2d03e8fa468bad48736",
    "services/execution/demo_validation.py": "725b36a93212733bafed401d1ba059d176d0f43b7a8154288d30df03e3b6b3e4",
    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",
    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",
    "services/execution/simulator.py": "fa3955b49de8b2a92ae67bea4b85d1f8d1e50581f967df2b96708bdfb3cdc2bd",
    "services/market_data/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "services/market_data/integrity.py": "4669f0339610bf1fd183913f542fef7aae9a447ad72d5e57fe2b34e7724d3d8b",
    "services/market_data/normalization.py": "ba06f00a0778cea08c39442fa9911d4140f1b0b1cf2f916ef7bc50c54cf6bb64",
    "services/protection/paper.py": "b60ac5ae41db6e7f1bbd8cb294e5adec18685220720a597a18828d83a411655a",
    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",
}

BASELINE_PINS = {
    "integrations/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "integrations/binance_usdm/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "integrations/binance_usdm/demo.py": "767d3a29b59882035bdfbf61d3271b39bfece0bff4b359e636db62ad9a5e84eb",
    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",
    "integrations/binance_usdm/demo_snapshot.py": "f80f6e51505ab1095c25b6ee76af01c258cff911aabd02c201bfcddc636f2258",
    "integrations/binance_usdm/demo_tp_admission.py": "c6a66aff461f2aab59d2f2ae1f42a44c6388268d93893ecf4854c3af111ee0d8",
    "integrations/binance_usdm/demo_tp_weight_model.py": "611948a52ffad41ecc448549f530558cd4df2585e23273d23422c8a995c7cbc3",
    "integrations/binance_usdm/demo_tp_window_budget.py": "9433a307767a2e60a4cdbc31a85a8cb386066ff9799249239633ead1cf76120b",
    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",
    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",
    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",
    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",
    "packages/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "packages/domain/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",
    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",
    "packages/persistence/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "packages/persistence/demo_alerts.py": "fb8986d57ef850c7b707d59730b002ead125429e1d23d310a89b3c7e69385330",
    "packages/persistence/demo_order_quota.py": None,
    "packages/persistence/demo_shared_checkpoint.py": None,
    "packages/persistence/demo_shared_weight.py": None,
    "packages/persistence/market.py": "89265a7bc7d181863983f4d24e6bdbe7499bb5b8154a6b22c6aea46c0d216050",
    "packages/persistence/paper.py": "312f2ab4478e2cb7f980045c56ac1329d080d8e54d60f960a93ae0f2fe1d2bd7",
    "services/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",
    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",
    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",
    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",
    "services/execution/demo_execution_proof.py": "09c36ceab43021c5cf0d10da08af897124008be2e083deb560572640b3cbe0d4",
    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",
    "services/execution/demo_initial_fill.py": "aa5bcaa7f8b9c6112893c89e8e8e90c74eb16704f1dc16becc6c22bdaec24ba5",
    "services/execution/demo_managed_order_quota.py": None,
    "services/execution/demo_managed_runtime.py": None,
    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",
    "services/execution/demo_order_admission.py": None,
    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",
    "services/execution/demo_query_contract.py": None,
    "services/execution/demo_quota_startup_review.py": None,
    "services/execution/demo_reconciliation.py": "7554e0fdcdab08dc222700bde8254ba7979c44c0ec4d82c1d2009ea0e0bb5040",
    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",
    "services/execution/demo_runtime_capacity.py": None,
    "services/execution/demo_runtime_confirmation.py": None,
    "services/execution/demo_runtime_cost_contract.py": None,
    "services/execution/demo_runtime_guards.py": None,
    "services/execution/demo_runtime_observation.py": None,
    "services/execution/demo_runtime_storage.py": None,
    "services/execution/demo_session_diagnostics.py": "78d2bbeadf90bda14a08f5c7dcd8cc8285f9ee55389b07ffb4a8742d052d520b",
    "services/execution/demo_shared_weight_budget.py": None,
    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",
    "services/execution/demo_tp_admission_candidate.py": "d8ab1b2ac699b38aaf75977ea97d49fa44ba60a52f518f997fd15f8a2489760a",
    "services/execution/demo_tp_budget_integration.py": "ab8b13e714c3298cb659b34a6ead0948dd6bf6c943548f0a3657cbc031786399",
    "services/execution/demo_tp_fenced_handoff_candidate.py": "9fcdf0c42b60dd7bae9f9fe2257a3d9e659581c3b711191b15c531b05084ee6c",
    "services/execution/demo_tp_first_confirmation_candidate.py": "0b31f29c94fb3f2e56c68c855737eddc312f80871f500867530d078e1190248f",
    "services/execution/demo_tp_staged_confirmation_candidate.py": "76544f006bdbcc17f54d19dae031e6eaebde831dca0537a15f73069f3d43335b",
    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",
    "services/execution/demo_tp_uncertain_cancel_candidate.py": "f188463cb0e2d17a160ed4f02fc9aac5b42d30938a44f2d03e8fa468bad48736",
    "services/execution/demo_validation.py": "725b36a93212733bafed401d1ba059d176d0f43b7a8154288d30df03e3b6b3e4",
    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",
    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",
    "services/execution/simulator.py": "fa3955b49de8b2a92ae67bea4b85d1f8d1e50581f967df2b96708bdfb3cdc2bd",
    "services/market_data/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "services/market_data/integrity.py": "4669f0339610bf1fd183913f542fef7aae9a447ad72d5e57fe2b34e7724d3d8b",
    "services/market_data/normalization.py": "ba06f00a0778cea08c39442fa9911d4140f1b0b1cf2f916ef7bc50c54cf6bb64",
    "services/protection/paper.py": "b60ac5ae41db6e7f1bbd8cb294e5adec18685220720a597a18828d83a411655a",
    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",
}


def verify_install_files(phase, root=Path(".")):
    for name, target in TARGET_PINS.items():
        path = root / name
        if phase == "pre" and not path.exists():
            require(BASELINE_PINS[name] is None, "INSTALL_DEPENDENCY_MISSING")
            continue
        require(path.is_file() and not path.is_symlink(), "INSTALL_SOURCE_CONFLICT")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        allowed = {target} if phase == "post" else {target, BASELINE_PINS[name]}
        require(actual in allowed, "INSTALL_SOURCE_CONFLICT")


def verify_default_refusal():
    from packages.persistence.demo_order_quota import LocalQuotaStopped
    from services.execution.demo_managed_runtime import assemble_managed_runtime

    try:
        assemble_managed_runtime(None, None, None, None, None)
    except LocalQuotaStopped as error:
        require(
            str(error) == "RUNTIME_REAL_EXECUTION_DISABLED", "DEFAULT_EXECUTION_REFUSAL_CHANGED"
        )
    else:
        raise Blocked("DEFAULT_EXECUTION_REFUSAL_CHANGED")


SETUP_KEY = "MANAGED_RUNTIME:STORAGE_SETUP_20261111_V1"

DONE_KEY = "MANAGED_RUNTIME:STORAGE_SETUP_COMPLETE_20261111_V1"

ORDER_KEY = "MANAGED_QUOTA:STORAGE_V1"

SHARED_KEY = "MANAGED_RUNTIME:SHARED_STORAGE_V1"

SETUP_KEYS = (SETUP_KEY, DONE_KEY, ORDER_KEY, SHARED_KEY)

STORAGE_PARENT = Path("/app/demo-export-state")

STORAGE_DIRECTORY = "demo-runtime-20261111"

STORAGE_SCOPE = "DEMO-WINDOWS-TP-MANAGED-WRITERS-V1"

SOURCE_REFERENCE = "e03b6ba683732cd5872c30cf0d245a5b1fb6d325"


def setup_preserved(ledger):
    original = ledger.model_copy(deep=True)
    for key in SETUP_KEYS:
        original.operations.pop(key, None)
    return digest(original.model_dump(mode="json"))


def setup_context(store, lease=None):
    with store.engine.connect() as conn, conn.begin():
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()
        )
    if lease is None:
        require(
            row["owner"] is None
            and (row["lease_until"] is None or row["lease_until"] <= store.now()),
            "SETUP_ACTIVE_OWNER",
        )
    else:
        require(
            row["owner"] == str(lease.owner)
            and row["fence"] == lease.fence
            and row["lease_until"] is not None
            and row["lease_until"] > store.now(),
            "SETUP_CURRENT_LEASE_REQUIRED",
        )
    ledger = DemoLedger.model_validate(row["state"])
    empty(ledger)
    require(ledger.paused and ledger.risk_state.value == "NORMAL", "SETUP_PAUSED_NORMAL_REQUIRED")
    require(
        period_keys(store.now(), ledger.policy.week_start_kst)
        == (ledger.daily_key, ledger.weekly_key),
        "SETUP_PERIOD_MISMATCH",
    )
    original = ledger.model_copy(deep=True)
    for key in SETUP_KEYS:
        original.operations.pop(key, None)
    require(day_recorded(original), "SETUP_DAY_RECEIPT_REQUIRED")
    return ledger


def _setup_root(parent):
    import stat

    require(parent.is_dir() and not parent.is_symlink(), "SETUP_PRIVATE_PARENT_REQUIRED")
    info = parent.stat()
    require(
        info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
        "SETUP_PRIVATE_PARENT_REQUIRED",
    )
    root = parent / STORAGE_DIRECTORY
    require(not root.is_symlink(), "SETUP_PRIVATE_ROOT_REQUIRED")
    if root.exists():
        info = root.stat()
        require(
            root.is_dir() and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "SETUP_PRIVATE_ROOT_REQUIRED",
        )
    return root


def _read_setup(ledger):
    raw = ledger.operations.get(SETUP_KEY)
    require(raw is not None, "SETUP_INTENT_REQUIRED")
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        raise Blocked("SETUP_INTENT_INVALID") from None
    require(
        isinstance(record, dict)
        and record.get("version") == 1
        and type(record["version"]) is int
        and record.get("account") == ACCOUNT
        and record.get("source") == SOURCE_REFERENCE
        and record.get("scope") == STORAGE_SCOPE
        and record.get("directory") == STORAGE_DIRECTORY
        and record.get("preserved_ledger_sha256") == setup_preserved(ledger),
        "SETUP_INTENT_INVALID",
    )
    try:
        UUID(record["shared_journal"])
    except (ValueError, TypeError, KeyError):
        raise Blocked("SETUP_INTENT_INVALID") from None
    return record


def _storage_objects(anchor, root, record, clocks):
    from services.execution.demo_runtime_storage import RuntimeStorage

    return RuntimeStorage(
        root / "order-quota",
        root / "shared-weight",
        STORAGE_SCOPE,
        UUID(record["shared_journal"]),
        anchor,
    )


def _verify_prepared(ledger, anchor, root, clocks):
    from packages.persistence.demo_order_quota import DurableOrderQuota
    from packages.persistence.demo_shared_checkpoint import AnchoredSharedWeightJournal
    from services.execution.demo_managed_order_quota import validate_journal_bindings
    from services.execution.demo_runtime_storage import runtime_storage_binding

    record = _read_setup(ledger)
    require(record.get("stage") == "PREPARED", "SETUP_INCOMPLETE_NO_AUTOMATIC_RETRY")
    require(
        str(anchor.installation_id()) == record.get("anchor_installation"), "SETUP_ANCHOR_CHANGED"
    )
    try:
        order_id = UUID(record["order_journal"])
    except (ValueError, TypeError, KeyError):
        raise Blocked("SETUP_INTENT_INVALID") from None
    order = DurableOrderQuota(
        root / "order-quota",
        ACCOUNT,
        order_id,
        read_only=True,
        monotonic=clocks.monotonic,
        wall_clock=clocks.wall_clock,
    )
    try:
        validate_journal_bindings(ledger, order)
        require(not order.flow_records() and not order._events(), "SETUP_ORDER_FLOWS_NOT_EMPTY")
    finally:
        order.close()
    storage = _storage_objects(anchor, root, record, clocks)
    require(
        record.get("expected_runtime_binding") == runtime_storage_binding(storage),
        "SETUP_INTENT_INVALID",
    )
    shared_reader = AnchoredSharedWeightJournal(
        storage.shared_directory,
        STORAGE_SCOPE,
        storage.shared_identity,
        anchors=anchor,
        read_only=True,
        monotonic=clocks.monotonic,
        wall_clock=clocks.wall_clock,
        boot=clocks.boot,
    )
    with shared_reader._transaction() as (connection, meta, _):
        require(
            meta["total"] == 0
            and connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0,
            "SETUP_SHARED_REQUESTS_NOT_EMPTY",
        )
    pressure = shared_reader.view()
    require(
        pressure["pending_attempts"] == 0 and pressure["pressure"] == 0,
        "SETUP_SHARED_REQUESTS_NOT_EMPTY",
    )
    return record, storage, pressure


def _verify_done(ledger, record):
    raw = ledger.operations.get(DONE_KEY)
    require(raw is not None, "SETUP_COMPLETE_RECEIPT_REQUIRED")
    try:
        receipt = json.loads(raw)
    except (ValueError, TypeError):
        raise Blocked("SETUP_COMPLETE_RECEIPT_INVALID") from None
    require(
        isinstance(receipt, dict)
        and receipt.get("version") == 1
        and type(receipt.get("version")) is int
        and receipt.get("setup_intent_sha256") == digest(record)
        and receipt.get("preserved_ledger_sha256") == setup_preserved(ledger)
        and receipt.get("order_binding_sha256") == digest(ledger.operations.get(ORDER_KEY))
        and receipt.get("runtime_binding_sha256") == digest(record["expected_runtime_binding"])
        and ledger.operations.get(SHARED_KEY) == record["expected_runtime_binding"]
        and receipt.get("execution_approved") is False,
        "SETUP_COMPLETE_RECEIPT_INVALID",
    )


def _setup_report(status, ledger=None, **values):
    report = {
        "runtime_storage_setup": status,
        "execution_approved": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "http_attempts": 0,
        "order_writes": 0,
        "credentials_accessed": False,
        "actual_ip_capacity_reserved": False,
        "external_account_order_usage": "UNKNOWN",
        "venue_request_cost_upper_bounds_proven": False,
        "startup_hold_seconds": 70.001,
    }
    if ledger is not None:
        report.update(
            day_kst=ledger.daily_key,
            week_kst=ledger.weekly_key,
            paused=ledger.paused,
            risk_state=ledger.risk_state.value,
            daily_base_usdt=str(ledger.daily_base),
            weekly_base_usdt=str(ledger.weekly_base),
            high_water_usdt=str(ledger.high_water),
        )
    return report | values


def setup_status(store, anchor, *, parent=STORAGE_PARENT, clocks=None):
    from services.execution.demo_runtime_storage import RuntimeClocks

    clocks = clocks or RuntimeClocks()
    ledger = setup_context(store)
    root = _setup_root(parent)
    if SETUP_KEY not in ledger.operations:
        require(
            not any(k in ledger.operations for k in (ORDER_KEY, SHARED_KEY, DONE_KEY)),
            "SETUP_UNRELATED_BINDINGS_PRESENT",
        )
        require(
            not root.exists() and not root.is_symlink(), "SETUP_ORPHAN_DIRECTORY_NO_AUTOMATIC_RETRY"
        )
        return _setup_report(
            "PREPARATION_REQUIRED", ledger, database_changed=False, filesystem_changed=False
        )
    record, storage, pressure = _verify_prepared(ledger, anchor, root, clocks)
    if DONE_KEY in ledger.operations:
        _verify_done(ledger, record)
        status = "CONFIGURED_KEEP_PAUSED"
    else:
        require(
            SHARED_KEY not in ledger.operations
            or ledger.operations[SHARED_KEY] == record["expected_runtime_binding"],
            "SETUP_BINDING_CONFLICT",
        )
        status = "READY_TO_FINALIZE" if pressure["startup_ready"] else "WAIT_STARTUP_HOLD"
    return _setup_report(
        status,
        ledger,
        database_changed=False,
        filesystem_changed=False,
        storage_bindings_verified=True,
        startup_ready=pressure["startup_ready"],
        preserved_ledger_sha256=setup_preserved(ledger),
    )


PINNED_PROBE_SHA256 = "81ded98a1a36e7e7a46c25d3b5828e03cc41a16de44e0d0578f73219cb37c33d"
PINNED_PROBE_SOURCE = '"""Four GETs only: review DEMO request-weight and account order-quota evidence.\n\nDoes not install code, open a stream, access a DB, acquire leadership or authorize\norders. The dedicated new USER_DATA query does not extend SDK route allowlists.\n"""\n\n# SDK imports intentionally follow the stdlib-only source cohort check.\n# ruff: noqa: E402\n\nimport hashlib\nimport json\nimport math\nimport os\nimport time\nfrom collections.abc import Iterable, Mapping\nfrom pathlib import Path\nfrom typing import Any, Protocol\nfrom urllib.error import HTTPError, URLError\nfrom urllib.parse import parse_qs\nfrom urllib.request import Request, build_opener\n\n# Check the installed cohort using stdlib only, before importing any SDK source.\nINSTALLED_PINS: dict[str, str] = {\n    "alembic.ini": "e7955a0ffb9c32a9c5067d7885ee9197328f17a4a12fb4a5bb556c2b85763aa2",\n    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",\n    "integrations/binance_usdm/demo.py": "767d3a29b59882035bdfbf61d3271b39bfece0bff4b359e636db62ad9a5e84eb",\n    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",\n    "integrations/binance_usdm/demo_read_routing.py": "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d",\n    "integrations/binance_usdm/demo_snapshot.py": "f80f6e51505ab1095c25b6ee76af01c258cff911aabd02c201bfcddc636f2258",\n    "integrations/binance_usdm/demo_tp_admission.py": "c6a66aff461f2aab59d2f2ae1f42a44c6388268d93893ecf4854c3af111ee0d8",\n    "integrations/binance_usdm/demo_tp_weight_model.py": "611948a52ffad41ecc448549f530558cd4df2585e23273d23422c8a995c7cbc3",\n    "integrations/binance_usdm/demo_tp_window_budget.py": "9433a307767a2e60a4cdbc31a85a8cb386066ff9799249239633ead1cf76120b",\n    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",\n    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",\n    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",\n    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",\n    "migrations/env.py": "4256345fc24dd52ba706aa9b712268b9df436d52bafa7238234272d754734086",\n    "migrations/script.py.mako": "67042994cfc495007c508ccae15e44d21057d90222461630f9bc04d3657f5f6d",\n    "migrations/versions/0001_public_market_data.py": "b62afcb18d6d6c42d710ca0cb0eb42001e174934f11678443dc5ac24d86932f7",\n    "migrations/versions/0002_paper.py": "91ffad2a439b9f47e2636939a5aba091490942db7a87bb0d87124a531870c77d",\n    "migrations/versions/0003_demo.py": "6b2a26ba34855a3dee6d3b2c67eb9b3ecac644b4e2e31ad6353e907a3c233762",\n    "migrations/versions/0004_demo_urgent_alerts.py": "da9e4de16f1f9d4c15d70293d3b252084648fbbcb060f8ec7740b9a85f83e8ad",\n    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",\n    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",\n    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",\n    "pyproject.toml": "54760a9fa52fc4cc877d2302c7952688ddd13e12f1efc8e44a0974aae8914019",\n    "scripts/check_demo_disconnect.py": "c85c013412053237eda7ed82a8948aa4b50f9da1951d173360a1ecfb46228a1e",\n    "scripts/check_demo_session_ready.py": "18259376c0404f98bc0cb91afeb31e33789f581562e14939989816c2fa8754d9",\n    "scripts/check_demo_tp_trigger.py": "a0bdd07027d5e3ca9b54a1468841fe2d40cf072c38c3c91c6c58292c2e67d96b",\n    "scripts/demo_key_session.py": "85c698bc8bbad98bad4051c346a5e4e0a3a3bd22f8bbb90a362f835cadc633dd",\n    "scripts/review_tp_minute_budget.py": "e8462dddf04d6d9107d01d7fbe8373df9a3a1e0cf6361d5f057021b2f67e9870",\n    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",\n    "services/execution/demo_api_restart_check.py": "b96ca788094995dafb7a69d9bbbd7f6a6dd16687d30bc9bd4271164c307ab239",\n    "services/execution/demo_empty_db_restart.py": "6adcc8284b43050bc4a115f2796c001d424ba7e0261654c099075fb3d950e39f",\n    "services/execution/demo_empty_worker_recovery.py": "8e3cac8da89fb80ef9f3654ae8591ffea7be1db1c7dc60070da72b72851b2070",\n    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",\n    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",\n    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",\n    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",\n    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",\n    "services/execution/demo_private_stability.py": "d3aee78f2da73c15c3eff5f89403fae666de5109ad3c2402585f2150df1a5123",\n    "services/execution/demo_protected_worker_check.py": "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709",\n    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",\n    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",\n    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",\n    "services/execution/demo_tp_admission_candidate.py": "d8ab1b2ac699b38aaf75977ea97d49fa44ba60a52f518f997fd15f8a2489760a",\n    "services/execution/demo_tp_budget_integration.py": "ab8b13e714c3298cb659b34a6ead0948dd6bf6c943548f0a3657cbc031786399",\n    "services/execution/demo_tp_fenced_handoff_candidate.py": "9fcdf0c42b60dd7bae9f9fe2257a3d9e659581c3b711191b15c531b05084ee6c",\n    "services/execution/demo_tp_first_confirmation_candidate.py": "0b31f29c94fb3f2e56c68c855737eddc312f80871f500867530d078e1190248f",\n    "services/execution/demo_tp_staged_confirmation_candidate.py": "76544f006bdbcc17f54d19dae031e6eaebde831dca0537a15f73069f3d43335b",\n    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",\n    "services/execution/demo_tp_uncertain_cancel_candidate.py": "f188463cb0e2d17a160ed4f02fc9aac5b42d30938a44f2d03e8fa468bad48736",\n    "services/execution/demo_validation.py": "725b36a93212733bafed401d1ba059d176d0f43b7a8154288d30df03e3b6b3e4",\n    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",\n    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",\n    "services/execution/readiness_closed_history.py": "0c9cb90bc74402fdaed025f715a98a2e47169eee4d1f9990c75e8f5671e358da",\n    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",\n    "uv.lock": "49bbe38dc3f559466c4666febe686926db6503413a45bd766088ed2454e01097",\n}\n\n\ndef _verify_source_bytes_before_import() -> None:\n    if len(INSTALLED_PINS) != 55:\n        raise ValueError("PIN_COHORT_INVALID")\n    for name, digest in INSTALLED_PINS.items():\n        path = Path(name)\n        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:\n            raise ValueError("INSTALLED_SOURCE_MISMATCH")\n\n\nif __name__ == "__main__":\n    try:\n        _verify_source_bytes_before_import()\n    except Exception:\n        print(\n            json.dumps(\n                {\n                    "tp_entry_admission_review": "BLOCKED_KEEP_PAUSED_NO_RETRY",\n                    "stop_reason": "INSTALLED_SOURCE_MISMATCH",\n                    "database_accessed": False,\n                    "order_writes": 0,\n                    "execution_approved": False,\n                    "orders_enabled": False,\n                    "live_trading_enabled": False,\n                }\n            )\n        )\n        raise SystemExit(2) from None\n\nfrom pydantic import SecretStr\n\nfrom integrations.binance_usdm.demo import (\n    REST_HOST,\n    DemoClient,\n    DemoCredentials,\n    DemoFailure,\n    DemoRateLimited,\n    DemoSettings,\n    DemoUnknown,\n    Transport,\n)\nfrom integrations.binance_usdm.demo_rate_limits import rate_headers\nfrom integrations.binance_usdm.demo_tp_admission import published_limit\nfrom integrations.binance_usdm.rest import NoRedirect, RESTResponse, prepare_signed_query\nfrom packages.domain.config import UniqueKeyLoader\nfrom scripts.demo_key_session import credentials\nfrom services.execution.demo_order_admission import (\n    AdmissionEvidenceInvalid,\n    OrderSample,\n    assess_order_admission,\n    published_order_windows,\n)\n\nORDER_USAGE_PATH = "/fapi/v1/rateLimit/order"\nPROBE_PATHS = frozenset({"/fapi/v1/time", "/fapi/v1/exchangeInfo", ORDER_USAGE_PATH})\nMAX_PROBE_REQUESTS = 4\nMAX_PROBE_SECONDS = 10\n\n\nclass EvidenceStop(DemoFailure):\n    pass\n\n\ndef evidence_require(condition: bool, code: str) -> None:\n    if not condition:\n        raise EvidenceStop(code)\n\n\ndef verify_installed_sources() -> None:\n    evidence_require(len(INSTALLED_PINS) == 55, "PIN_COHORT_INVALID")\n    for name, digest in INSTALLED_PINS.items():\n        path = Path(name)\n        evidence_require(path.is_file(), "INSTALLED_SOURCE_MISMATCH")\n        evidence_require(\n            hashlib.sha256(path.read_bytes()).hexdigest() == digest, "INSTALLED_SOURCE_MISMATCH"\n        )\n\n\nclass AdmissionHeaders(Protocol):\n    def items(self) -> Iterable[tuple[str, str]]: ...\n\n\ndef admission_response_headers(raw: AdmissionHeaders) -> dict[str, str]:\n    safe = rate_headers(dict(raw.items()))\n    for counter in ("x-mbx-order-count-10s", "x-mbx-order-count-1m"):\n        values = [value for name, value in raw.items() if name.lower() == counter]\n        if values:\n            evidence_require(\n                len(values) == 1\n                and isinstance(values[0], str)\n                and values[0].isascii()\n                and values[0].isdecimal()\n                and len(values[0]) <= 9,\n                "ORDER_COUNTER_HEADER_INVALID",\n            )\n            safe[counter] = values[0]\n    return safe\n\n\ndef admission_read_transport(\n    method: str, path: str, query: SecretStr, api_key: SecretStr | None\n) -> RESTResponse:\n    # Unlike the SDK\'s generic request transport, this boundary has no mutation\n    # branch. Neither caller-controlled URLs nor redirects are permitted.\n    evidence_require(REST_HOST == "https://demo-fapi.binance.com", "DEMO_HOST_REQUIRED")\n    evidence_require(method == "GET" and path in PROBE_PATHS, "READ_SCOPE_FORBIDDEN")\n    params = parse_qs(query.get_secret_value(), keep_blank_values=True)\n    if path == ORDER_USAGE_PATH:\n        evidence_require(\n            set(params) == {"timestamp", "recvWindow", "signature"}\n            and all(len(v) == 1 for v in params.values())\n            and params["timestamp"][0].isascii()\n            and params["timestamp"][0].isdecimal()\n            and params["recvWindow"] == ["5000"]\n            and len(params["signature"][0]) == 64\n            and all(c in "0123456789abcdef" for c in params["signature"][0])\n            and isinstance(api_key, SecretStr)\n            and bool(api_key.get_secret_value()),\n            "SIGNED_READ_SCOPE_FORBIDDEN",\n        )\n    else:\n        evidence_require(\n            not query.get_secret_value() and api_key is None, "PUBLIC_READ_SCOPE_FORBIDDEN"\n        )\n    headers = {"Accept": "application/json"}\n    if api_key is not None:\n        headers["X-MBX-APIKEY"] = api_key.get_secret_value()\n    target = REST_HOST + path + ("?" + query.get_secret_value() if params else "")\n    request = Request(target, method="GET", headers=headers)\n    try:\n        with build_opener(NoRedirect()).open(request, timeout=5) as response:\n            return RESTResponse(\n                response.status,\n                response.read(4_194_305),\n                admission_response_headers(response.headers),\n            )\n    except HTTPError as error:\n        try:\n            return RESTResponse(\n                error.code,\n                error.read(4_194_305),\n                rate_headers(dict(error.headers.items()))\n                if error.code in {418, 429}\n                else admission_response_headers(error.headers),\n            )\n        finally:\n            error.close()\n    except (URLError, OSError):\n        raise DemoUnknown("Read-only admission transport unavailable") from None\n\n\nclass AdmissionReadMonitor:\n    def __init__(self, sender: Transport, *, monotonic: Any = time.monotonic):\n        self.sender, self.clock = sender, monotonic\n        self.started = self.last = monotonic()\n        self.records: list[tuple[str, int]] = []\n        self.weight_limit: int | None = None\n        self.peak_weight = 0\n        self.limited = False\n        self.last_order_headers: dict[str, int] = {}\n\n    def check(self) -> None:\n        now = self.clock()\n        evidence_require(\n            math.isfinite(now)\n            and math.isfinite(self.started)\n            and self.last <= now\n            and 0 <= now - self.started < MAX_PROBE_SECONDS,\n            "PROBE_CLOCK_OR_TIME_BUDGET",\n        )\n        self.last = now\n        evidence_require(not self.limited, "RATE_LIMITED_NO_RETRY")\n        if self.weight_limit is not None:\n            evidence_require(\n                self.peak_weight + 100 < self.weight_limit * 3 // 5,\n                "READ_WEIGHT_HEADROOM_INSUFFICIENT",\n            )\n\n    def enable(self, info: Any) -> None:\n        self.weight_limit = published_limit(info)\n        published_order_windows(info)\n        self.check()\n\n    def __call__(\n        self, method: str, path: str, query: SecretStr, key: SecretStr | None\n    ) -> RESTResponse:\n        evidence_require(method == "GET" and path in PROBE_PATHS, "READ_SCOPE_FORBIDDEN")\n        evidence_require(len(self.records) < MAX_PROBE_REQUESTS, "PROBE_HTTP_BUDGET")\n        if path == ORDER_USAGE_PATH:\n            evidence_require(self.weight_limit is not None, "LIMIT_DISCOVERY_REQUIRED")\n        self.check()\n        self.records.append((path, 0))  # Count attempts even when a response is lost.\n        response = self.sender(method, path, query, key)\n        if response.status in {418, 429}:\n            self.limited = True\n            return response  # Let the original SDK set cooldown and raise its type.\n        self.last_order_headers = {}\n        for counter in ("x-mbx-order-count-10s", "x-mbx-order-count-1m"):\n            values = [value for name, value in response.headers.items() if name.lower() == counter]\n            if values:\n                evidence_require(\n                    len(values) == 1\n                    and isinstance(values[0], str)\n                    and values[0].isascii()\n                    and values[0].isdecimal()\n                    and len(values[0]) <= 9,\n                    "ORDER_COUNTER_HEADER_INVALID",\n                )\n                self.last_order_headers[counter] = int(values[0])\n        safe = rate_headers(response.headers)\n        value = safe.get("x-mbx-used-weight-1m")\n        evidence_require(value is not None, "WEIGHT_HEADER_UNAVAILABLE")\n        self.peak_weight = max(self.peak_weight, int(value or "0"))\n        self.check()\n        return response\n\n\nclass AdmissionReadClient(DemoClient):\n    def __init__(self, settings: DemoSettings, key: DemoCredentials, **kwargs: Any):\n        evidence_require(\n            not settings.demo_trading_enabled\n            and not settings.live_trading_enabled\n            and key.environment == "DEMO",\n            "READ_ONLY_CONTEXT_REQUIRED",\n        )\n        super().__init__(settings, key, **kwargs)\n\n    def public(self, path: str, params: Mapping[str, str] | None = None) -> Any:\n        evidence_require(\n            path in {"/fapi/v1/time", "/fapi/v1/exchangeInfo"} and not params,\n            "PUBLIC_READ_SCOPE_FORBIDDEN",\n        )\n        return super().public(path, params)\n\n    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:\n        raise EvidenceStop("GENERIC_SIGNED_REQUEST_FORBIDDEN")\n\n    def listen_key(self, *args: Any, **kwargs: Any) -> SecretStr | None:\n        raise EvidenceStop("LISTEN_KEY_FORBIDDEN")\n\n    def query_order_usage(self) -> Any:\n        evidence_require(\n            not self.settings.demo_trading_enabled and not self.settings.live_trading_enabled,\n            "READ_ONLY_CONTEXT_REQUIRED",\n        )\n        now = self.clock_ms()\n        if now < self.cooldown_until:\n            raise DemoRateLimited("Read-only admission cooldown active")\n        evidence_require(\n            self.synced_at is not None and 0 <= now - self.synced_at <= 5000,\n            "ADMISSION_CLOCK_STALE",\n        )\n        signed = prepare_signed_query(\n            {},\n            api_key=self.credentials.api_key,\n            secret=self.credentials.secret,\n            timestamp_ms=now + self.offset,\n        )\n        return self._decode(\n            self.send("GET", ORDER_USAGE_PATH, signed.query, signed.api_key), mutation=False\n        )\n\n\ndef review_admission(client: AdmissionReadClient, monitor: AdmissionReadMonitor) -> dict[str, Any]:\n    client.sync_clock()\n    info = client.public("/fapi/v1/exchangeInfo")\n    monitor.enable(info)\n    first_body = client.query_order_usage()\n    first = OrderSample(monitor.clock(), first_body, monitor.last_order_headers.copy())\n    second_body = client.query_order_usage()\n    second = OrderSample(monitor.clock(), second_body, monitor.last_order_headers.copy())\n    monitor.check()\n    report = assess_order_admission(info, first, second, now=monitor.clock())\n    report.update(\n        {\n            "tp_entry_admission_review": "READ_ONLY_EVIDENCE_COLLECTED_KEEP_PAUSED",\n            "published_weight_limit_1m": monitor.weight_limit,\n            "observed_max_used_weight_1m": monitor.peak_weight,\n            "http_attempts": len(monitor.records),\n            "signed_order_usage_queries": 2,\n            "readiness_rechecked": False,\n            "database_accessed": False,\n            "database_changed": False,\n            "leader_acquired": False,\n            "order_writes": 0,\n            "pause_modified": False,\n            "runtime_installed": False,\n            "orders_enabled": False,\n            "live_trading_enabled": False,\n            "ip_capacity_reserved": False,\n            "production_activation_enabled": False,\n            "remaining_approval_blockers": [\n                "REQUEST_COST_CONTRACT_REVIEW_PENDING",\n                "CROSS_PROCESS_PRESSURE_POLICY_REVIEW_PENDING",\n                "PRODUCTION_DEFAULT_EXECUTION_DISABLED",\n            ],\n            "next_action": "REVIEW_EVIDENCE_NO_ORDER_OR_RUNTIME_SWITCH",\n        }\n    )\n    return report\n\n\ndef main() -> None:\n    # Reject LIVE/mixed-key context before reading even a session credential.\n    code = 2\n    report: dict[str, Any] = {\n        "tp_entry_admission_review": "BLOCKED_KEEP_PAUSED_NO_RETRY",\n        "stop_reason": "CHECK_UNAVAILABLE",\n        "database_accessed": False,\n        "order_writes": 0,\n        "execution_approved": False,\n        "orders_enabled": False,\n        "live_trading_enabled": False,\n        "next_action": "KEEP_PAUSED_NO_TP_RETRY",\n    }\n    try:\n        evidence_require(\n            os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE"\n            and os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"\n            and not any(\n                os.getenv(k)\n                for k in (\n                    "BINANCE_API_KEY",\n                    "BINANCE_API_SECRET",\n                    "BINANCE_LIVE_API_KEY",\n                    "BINANCE_LIVE_API_SECRET",\n                )\n            ),\n            "LIVE_OR_MIXED_CONTEXT_FORBIDDEN",\n        )\n        verify_installed_sources()\n        import yaml\n\n        cfg = DemoSettings.model_validate(\n            yaml.load(\n                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader\n            )\n        ).model_copy(update={"demo_trading_enabled": False})\n        try:\n            key = credentials(prompt=False)\n        except Exception:\n            raise EvidenceStop("DEMO_CREDENTIAL_SESSION_UNAVAILABLE") from None\n        monitor = AdmissionReadMonitor(admission_read_transport)\n        client = AdmissionReadClient(cfg, key, send=monitor)\n        report = review_admission(client, monitor)\n        code = 0 if not report["order_quota_blockers"] else 2\n    except DemoRateLimited:\n        report["stop_reason"] = "RATE_LIMITED_NO_RETRY"\n    except (AdmissionEvidenceInvalid, EvidenceStop) as error:\n        report["stop_reason"] = str(error) if str(error) in EVIDENCE_CODES else "CHECK_UNAVAILABLE"\n    except DemoFailure:\n        report["stop_reason"] = "DEMO_READ_QUERY_FAILED"\n    except Exception:\n        pass  # Never print network/SDK/credential bodies, URLs, queries or keys.\n    print(json.dumps(report), flush=True)\n    if code:\n        raise SystemExit(code)\n\n\nEVIDENCE_CODES = frozenset(\n    {\n        "ORDER_COUNTER_HEADER_INVALID",\n        "ORDER_INTEGER_INVALID",\n        "ORDER_WINDOW_INVALID",\n        "ORDER_INTERVAL_UNSUPPORTED",\n        "ORDER_LIMITS_UNAVAILABLE",\n        "ORDER_WINDOW_DUPLICATE",\n        "REQUIRED_ORDER_WINDOWS_MISSING",\n        "ORDER_USAGE_UNAVAILABLE",\n        "ORDER_SAMPLE_STALE_OR_CLOCK_INVALID",\n        "ORDER_WINDOW_COVERAGE_MISMATCH",\n        "PIN_COHORT_INVALID",\n        "INSTALLED_SOURCE_MISMATCH",\n        "DEMO_HOST_REQUIRED",\n        "READ_SCOPE_FORBIDDEN",\n        "SIGNED_READ_SCOPE_FORBIDDEN",\n        "PUBLIC_READ_SCOPE_FORBIDDEN",\n        "PROBE_CLOCK_OR_TIME_BUDGET",\n        "RATE_LIMITED_NO_RETRY",\n        "READ_WEIGHT_HEADROOM_INSUFFICIENT",\n        "PROBE_HTTP_BUDGET",\n        "LIMIT_DISCOVERY_REQUIRED",\n        "WEIGHT_HEADER_UNAVAILABLE",\n        "READ_ONLY_CONTEXT_REQUIRED",\n        "GENERIC_SIGNED_REQUEST_FORBIDDEN",\n        "LISTEN_KEY_FORBIDDEN",\n        "ADMISSION_CLOCK_STALE",\n        "LIVE_OR_MIXED_CONTEXT_FORBIDDEN",\n        "DEMO_CREDENTIAL_SESSION_UNAVAILABLE",\n    }\n)\n\nif __name__ == "__main__":\n    main()\n'


def readonly_token(engine):
    with engine.connect() as conn, conn.begin():
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()
        )
        history = (
            conn.execute(
                select(events).where(events.c.account_id == ACCOUNT).order_by(events.c.event_id)
            )
            .mappings()
            .all()
        )
        messages = (
            conn.execute(
                select(outbox)
                .join(events, outbox.c.event_id == events.c.event_id)
                .where(events.c.account_id == ACCOUNT)
                .order_by(outbox.c.event_id)
            )
            .mappings()
            .all()
        )
        body = {
            "account": dict(row),
            "events": [dict(r) for r in history],
            "outbox": [dict(r) for r in messages],
        }
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, default=lambda x: x.isoformat()).encode()
        ).hexdigest()


def readonly_files(root):
    import stat

    result = {}
    total = 0
    for file in sorted(root.rglob("*")):
        require(not file.is_symlink(), "REVIEW_STORAGE_SYMLINK")
        if file.is_dir():
            continue
        info = file.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "REVIEW_STORAGE_FILE_INVALID")
        total += info.st_size
        require(
            len(result) < 16 and info.st_size <= 16 * 1024 * 1024 and total <= 32 * 1024 * 1024,
            "REVIEW_STORAGE_SIZE_LIMIT",
        )
        result[str(file.relative_to(root))] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def collect_exchange_evidence():
    import sys
    import types

    require(
        hashlib.sha256(PINNED_PROBE_SOURCE.encode()).hexdigest() == PINNED_PROBE_SHA256,
        "REVIEW_PROBE_SOURCE_CHANGED",
    )
    name = "_pinned_runtime_admission_read_probe"
    module = types.ModuleType(name)
    sys.modules[name] = module
    monitor = None
    try:
        exec(compile(PINNED_PROBE_SOURCE, "<pinned_admission_probe>", "exec"), module.__dict__)
        import yaml

        module.evidence_require(
            not any(
                os.getenv(k)
                for k in (
                    "BINANCE_API_KEY",
                    "BINANCE_API_SECRET",
                    "BINANCE_LIVE_API_KEY",
                    "BINANCE_LIVE_API_SECRET",
                )
            ),
            "LIVE_OR_MIXED_CONTEXT_FORBIDDEN",
        )
        module.verify_installed_sources()
        cfg = module.DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(),
                Loader=module.UniqueKeyLoader,
            )
        ).model_copy(update={"demo_trading_enabled": False})
        try:
            key = module.credentials(prompt=False)
        except Exception:
            return {
                "collection_status": "DEMO_SESSION_UNAVAILABLE",
                "stop_reason": "DEMO_CREDENTIAL_SESSION_UNAVAILABLE",
                "http_attempts": 0,
                "usage_counts_verified": False,
            }
        monitor = module.AdmissionReadMonitor(module.admission_read_transport)
        client = module.AdmissionReadClient(cfg, key, send=monitor)
        raw = module.review_admission(client, monitor)
        fields = (
            "order_quota_evidence",
            "sample_age_ms",
            "required_modeled_order_slots",
            "order_plan",
            "windows",
            "order_quota_blockers",
            "algo_trigger_counter_upper_bound_proven",
            "published_weight_limit_1m",
            "observed_max_used_weight_1m",
            "signed_order_usage_queries",
        )
        report = {k: raw[k] for k in fields if k in raw}
        report.update(
            collection_status="READ_ONLY_EVIDENCE_COLLECTED",
            http_attempts=len(monitor.records),
            usage_counts_verified=not bool(raw["order_quota_blockers"]),
            installed_controls_rechecked=True,
            readiness_rechecked=False,
            exposure_checked=False,
            repeat_same_limits_only_probe_recommended=False,
            execution_approved=False,
        )
        return report
    except Exception as error:
        reason = "DEMO_EVIDENCE_CHECK_FAILED"
        if isinstance(error, getattr(module, "DemoRateLimited", ())):
            reason = "RATE_LIMITED_NO_RETRY"
        elif isinstance(
            error,
            tuple(
                kind
                for name in ("EvidenceStop", "AdmissionEvidenceInvalid")
                if isinstance((kind := getattr(module, name, None)), type)
            ),
        ) and str(error) in getattr(module, "EVIDENCE_CODES", frozenset()):
            reason = str(error)
        return {
            "collection_status": "BLOCKED_NO_RETRY",
            "stop_reason": reason,
            "http_attempts": len(monitor.records) if monitor is not None else 0,
            "usage_counts_verified": False,
        }
    finally:
        sys.modules.pop(name, None)


def review_controls(
    store,
    anchor,
    *,
    parent=STORAGE_PARENT,
    clocks=None,
    collect=False,
    collector=collect_exchange_evidence,
):
    from integrations.binance_usdm.demo_tp_admission import TPModelProfile
    from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS
    from services.execution.demo_runtime_capacity import capacity_plan
    from services.execution.demo_runtime_cost_contract import cost_contract_report
    from services.execution.demo_runtime_storage import RuntimeClocks

    clocks = clocks or RuntimeClocks()
    before = readonly_token(store.engine)
    root = _setup_root(parent)
    files = readonly_files(root)
    status = setup_status(store, anchor, parent=parent, clocks=clocks)
    require(
        status["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED",
        "REVIEW_STORAGE_CONFIGURATION_REQUIRED",
    )
    ledger = setup_context(store)
    verify_default_refusal()
    contract = cost_contract_report(MODELED_TP_ROUTE_COSTS)
    plan = capacity_plan(TPModelProfile(historical_orders=len(ledger.orders)))
    evidence = (
        collector()
        if collect
        else {
            "collection_status": "NOT_REQUESTED",
            "http_attempts": 0,
            "usage_counts_verified": False,
        }
    )
    require(readonly_token(store.engine) == before, "REVIEW_DATABASE_CHANGED_DURING_READ")
    require(readonly_files(root) == files, "REVIEW_STORAGE_CHANGED_DURING_READ")
    blockers = []
    if evidence.get("usage_counts_verified") is not True:
        blockers.append("ACCOUNT_ORDER_USAGE_NOT_VERIFIED")
    if not plan["proof_fits_empty_window"] or not plan["entry_fits_empty_window"]:
        blockers.append("MODELED_BATCH_DOES_NOT_FIT")
    blockers += [
        "VENUE_REQUEST_COST_UPPER_BOUNDS_UNPROVEN",
        "SHARED_IP_CAPACITY_NOT_RESERVED",
        "PRODUCTION_DEFAULT_EXECUTION_DISABLED",
        "FRESH_EXCHANGE_EXPOSURE_READINESS_NOT_CHECKED",
    ]
    return {
        "runtime_readiness_review": "LOCAL_CONTROLS_VERIFIED_KEEP_PAUSED",
        "installed_controls_verified": True,
        "storage_configuration_verified": True,
        "financial_receipts_verified": True,
        "preserved_ledger_sha256": setup_preserved(ledger),
        "database_changed_by_review": False,
        "journal_changed_by_review": False,
        "leader_acquired": False,
        "session_started": False,
        "day_kst": ledger.daily_key,
        "week_kst": ledger.weekly_key,
        "risk_state": ledger.risk_state.value,
        "paused": ledger.paused,
        "documented_cost_contract": contract,
        "modeled_window_capacity": plan,
        "exchange_order_evidence": evidence,
        "http_attempts": evidence.get("http_attempts", 0),
        "external_account_usage_verified": evidence.get("usage_counts_verified") is True,
        "execution_blockers": blockers,
        "execution_approved": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "order_writes": 0,
        "runtime_installed_by_this_review": False,
        "next_action": "REVIEW_REMAINING_EXECUTION_GATES_NO_REINSTALL_OR_ORDER",
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect-exchange-evidence", action="store_true")
    args = parser.parse_args()
    engines = []
    code = 2
    report = {
        "runtime_readiness_review": "BLOCKED_KEEP_PAUSED",
        "stop_reason": "REVIEW_UNAVAILABLE",
        "order_writes": 0,
        "execution_approved": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
    try:
        require(
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
            "REVIEW_LIVE_CONTEXT_FORBIDDEN",
        )
        require(source_cohort_valid(INSTALLED_PINS), "INSTALLED_SOURCE_HASH_MISMATCH")
        verify_install_files("post")
        from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore

        for _ in range(2):
            engines.append(
                create_engine(
                    database_url().get_secret_value(), connect_args={"connect_timeout": 2}
                )
            )
        report = review_controls(
            DemoStore(engines[0]),
            SharedWeightAnchorStore(engines[1]),
            collect=args.collect_exchange_evidence,
        )
        code = 0
    except Exception as error:
        allowed = {
            "REVIEW_PROBE_SOURCE_CHANGED",
            "REVIEW_STORAGE_SYMLINK",
            "REVIEW_STORAGE_FILE_INVALID",
            "REVIEW_STORAGE_SIZE_LIMIT",
            "REVIEW_STORAGE_CONFIGURATION_REQUIRED",
            "REVIEW_DATABASE_CHANGED_DURING_READ",
            "REVIEW_STORAGE_CHANGED_DURING_READ",
            "REVIEW_LIVE_CONTEXT_FORBIDDEN",
            "INSTALLED_SOURCE_HASH_MISMATCH",
            "INSTALL_SOURCE_CONFLICT",
            "SETUP_ACTIVE_OWNER",
            "SETUP_PERIOD_MISMATCH",
            "SETUP_INTENT_INVALID",
            "SETUP_COMPLETE_RECEIPT_INVALID",
            "SETUP_PRIVATE_PARENT_REQUIRED",
            "SETUP_PRIVATE_ROOT_REQUIRED",
            "DAY_OPERATION_CONFLICT",
            "PERIOD_OPERATION_CONFLICT",
            "SETUP_ORDER_FLOWS_NOT_EMPTY",
            "SETUP_SHARED_REQUESTS_NOT_EMPTY",
        }
        report["stop_reason"] = (
            str(error)
            if isinstance(error, Blocked) and str(error) in allowed
            else "REVIEW_UNAVAILABLE"
        )
    finally:
        failed = False
        for engine in reversed(engines):
            try:
                engine.dispose()
            except Exception:
                failed = True
        if failed:
            report = {
                "runtime_readiness_review": "BLOCKED_KEEP_PAUSED",
                "stop_reason": "REVIEW_CLEANUP_FAILED",
                "order_writes": 0,
                "execution_approved": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
            code = 2
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
