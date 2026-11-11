"""Read-only installed-code and local storage-binding review. No HTTP or credentials."""

import hashlib
import json
import os
import re
import sys
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
                    "runtime_deployment_review": "BLOCKED_KEEP_PAUSED",
                    "stop_reason": "INSTALLED_SOURCE_HASH_MISMATCH",
                    "database_changed": False,
                    "http_attempts": 0,
                    "order_writes": 0,
                    "execution_approved": False,
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            )
        )
        raise SystemExit(2)

# ruff: noqa: E402
from sqlalchemy import create_engine, select

from packages.domain.config import database_url
from packages.persistence.demo import accounts
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


CANDIDATE_PINS = {
    "packages/persistence/demo_order_quota.py": "c1ebdc1ccf9d469bd04950716ce84969fafbf18770a43d69aba6b99d117bc4b1",
    "packages/persistence/demo_shared_weight.py": "57696024477a81c35b69530facec75420c049b45272d00472e61ef04589fccac",
    "packages/persistence/demo_shared_checkpoint.py": "14142d1a0ed1201688a71155c3d1772440580e7cc9127eb318040fdc9fa9c537",
    "services/execution/demo_managed_order_quota.py": "8d50abbe4da256ef7fe8200b0308be46bc2447b056be63782d461c977dfe6439",
    "services/execution/demo_managed_runtime.py": "6fb008830fded93d3f6063fc70e559e60a4ebf0489ed666624d4dfe996dbb02a",
    "services/execution/demo_runtime_storage.py": "120411bcad5bab49b8233fc57433956621f6fe19fdfa20dcde4c1e649e9284cc",
    "services/execution/demo_runtime_observation.py": "0d4f3c6f08cbc2efd965c892e466438c28552ddbf4b183f81968677fa5092901",
    "services/execution/demo_query_contract.py": "76a81b477465c9b0493f142030fbdeb271510138b30f9fc182ebf4ecf81ba81c",
    "services/execution/demo_runtime_cost_contract.py": "2ac8942803461666cfde468828ca87d911c21ec84ecd141e235a2adbdc9a2694",
    "services/execution/demo_runtime_capacity.py": "75efd11305368ab939eafde160109d64ee71bf6e8f322bf0743935a61ea0cd4d",
    "services/execution/demo_quota_startup_review.py": "459056f3ac54f7d22d76535d133d0ac75b2a899e264f0cab71aacdc4d60fb0a4",
}


def base_report():
    return {
        "runtime_deployment_review": "BLOCKED_KEEP_PAUSED",
        "review_scope": "LOCAL_DATABASE_AND_SOURCE_FILES_ONLY",
        "database_changed": False,
        "journal_changed": False,
        "storage_created": False,
        "leader_acquired": False,
        "http_attempts": 0,
        "credentials_accessed": False,
        "exchange_checked": False,
        "external_account_order_usage": "UNKNOWN",
        "external_ip_capacity_reserved": False,
        "venue_request_cost_upper_bounds_proven": False,
        "execution_approved": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "order_writes": 0,
        "next_action": "SHARE_REPORT_KEEP_PAUSED",
    }


def candidate_inventory(root):
    result = {"matched": [], "missing": [], "different": []}
    for name, expected in CANDIDATE_PINS.items():
        path = root / name
        # Hash content only; never import newer candidate modules into installed runtime.
        if not path.exists():
            result["missing"].append(name)
        elif not path.is_file() or path.is_symlink():
            result["different"].append(name)
        elif hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            result["matched"].append(name)
        else:
            result["different"].append(name)
    return result


def binding_status(operations, key, *, shared=False):
    raw = operations.get(key)
    if raw is None:
        return "NOT_PROVISIONED"
    try:
        binding = json.loads(raw)
        require(isinstance(binding, dict), "STORAGE_BINDING_INVALID")
        UUID(binding["journal"])
        if shared:
            require(
                binding.get("version") == 1 and type(binding["version"]) is int,
                "STORAGE_BINDING_INVALID",
            )
            require(
                isinstance(binding.get("scope"), str)
                and binding["scope"].startswith("DEMO-")
                and len(binding["scope"]) <= 80,
                "STORAGE_BINDING_INVALID",
            )
            UUID(binding["checkpoint_installation"])
            require(isinstance(binding.get("policy"), str), "STORAGE_BINDING_INVALID")
    except (ValueError, TypeError, KeyError, AttributeError):
        return "BINDING_INVALID"
    return "BINDING_PRESENT_FILES_NOT_CHECKED"


def assess_row(row, *, now, root):
    ledger = DemoLedger.model_validate(row["state"])
    empty(ledger)
    require(row["owner"] is None, "ACTIVE_OWNER_BLOCKED")
    require(row["lease_until"] is None or row["lease_until"] <= now, "ACTIVE_OWNER_BLOCKED")
    require(day_recorded(ledger), "NOVEMBER11_DAY_RECEIPT_REQUIRED")
    require(
        period_keys(now, ledger.policy.week_start_kst) == (ledger.daily_key, ledger.weekly_key),
        "PERIOD_KEYS_MISMATCH",
    )
    before = ledger.model_dump_json()
    inventory = candidate_inventory(root)
    order = binding_status(ledger.operations, "MANAGED_QUOTA:STORAGE_V1")
    shared = binding_status(ledger.operations, "MANAGED_RUNTIME:SHARED_STORAGE_V1", shared=True)
    blockers = []
    if inventory["missing"]:
        blockers.append("CANDIDATE_CONTROL_CODE_MISSING")
    if inventory["different"]:
        blockers.append("CANDIDATE_CONTROL_VERSION_DIFFERENT")
    if order == "NOT_PROVISIONED":
        blockers.append("DURABLE_ORDER_STORAGE_NOT_PROVISIONED")
    elif order == "BINDING_INVALID":
        blockers.append("DURABLE_ORDER_STORAGE_BINDING_INVALID")
    else:
        blockers.append("DURABLE_ORDER_STORAGE_FILES_NOT_VERIFIED")
    if shared == "NOT_PROVISIONED":
        blockers.append("SHARED_WEIGHT_STORAGE_NOT_PROVISIONED")
    elif shared == "BINDING_INVALID":
        blockers.append("SHARED_WEIGHT_STORAGE_BINDING_INVALID")
    else:
        blockers.append("SHARED_WEIGHT_STORAGE_FILES_NOT_VERIFIED")
    require(ledger.model_dump_json() == before, "LOCAL_STATE_CHANGED")
    return base_report() | {
        "runtime_deployment_review": "LOCAL_DEPLOYMENT_REVIEW_COMPLETED_KEEP_PAUSED",
        "local_review_completed": True,
        "period_keys_match": True,
        "day_kst": ledger.daily_key,
        "week_kst": ledger.weekly_key,
        "daily_base_usdt": str(ledger.daily_base),
        "weekly_base_usdt": str(ledger.weekly_base),
        "high_water_usdt": str(ledger.high_water),
        "november11_day_receipt_verified": True,
        "paused": ledger.paused,
        "risk_state": ledger.risk_state.value,
        "candidate_control_files": inventory,
        "candidate_control_scope": "11_CRITICAL_FILES_ONLY_NOT_FULL_DEPENDENCY_VERIFICATION",
        "candidate_control_reference": "e03b6ba683732cd5872c30cf0d245a5b1fb6d325",
        "durable_order_storage": order,
        "shared_weight_storage": shared,
        "storage_files_verified": False,
        "deployment_blockers": blockers,
        "next_action": "PREPARE_CODE_ONLY_INSTALL_REVIEW"
        if inventory["missing"] or inventory["different"]
        else "PREPARE_STORAGE_CONFIGURATION_REVIEW",
    }


def review(engine, *, root=Path("."), now=None):
    require(engine.dialect.name == "postgresql", "POSTGRESQL_REQUIRED")
    with engine.connect() as conn, conn.begin():
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        conn.exec_driver_sql("SET LOCAL lock_timeout='2s'")
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()
        )
        observed = (
            now
            if now is not None
            else conn.exec_driver_sql("SELECT CURRENT_TIMESTAMP").scalar_one()
        )
        return assess_row(row, now=observed, root=root)


def main():
    if len(sys.argv) != 1:
        print(json.dumps(base_report() | {"stop_reason": "NO_ARGUMENTS_ACCEPTED"}), flush=True)
        raise SystemExit(2)
    engine = None
    code = 2
    report = base_report() | {"stop_reason": "LOCAL_REVIEW_FAILED"}
    try:
        require(
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
            "LIVE_CONTEXT_FORBIDDEN",
        )
        require(source_cohort_valid(INSTALLED_PINS), "INSTALLED_SOURCE_HASH_MISMATCH")
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        report = review(engine)
        code = 0
    except Exception as error:
        allowed = {
            "POSTGRESQL_REQUIRED",
            "ACTIVE_OWNER_BLOCKED",
            "PERIOD_KEYS_MISMATCH",
            "NOVEMBER11_DAY_RECEIPT_REQUIRED",
            "DAY_OPERATION_CONFLICT",
            "PERIOD_OPERATION_CONFLICT",
            "PRIOR_PERIOD_RECEIPT_REQUIRED",
            "LOCAL_STATE_CHANGED",
            "LIVE_CONTEXT_FORBIDDEN",
            "INSTALLED_SOURCE_HASH_MISMATCH",
        }
        reason = (
            str(error)
            if isinstance(error, Blocked) and str(error) in allowed
            else "LOCAL_REVIEW_FAILED"
        )
        report = base_report() | {"stop_reason": reason}
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                report = base_report() | {"stop_reason": "DATABASE_DISPOSE_FAILED"}
                code = 2
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


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


def install_snapshot(engine, *, now=None):
    from packages.persistence.demo import events, outbox

    require(engine.dialect.name == "postgresql", "POSTGRESQL_REQUIRED")
    with engine.connect() as conn, conn.begin():
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        conn.exec_driver_sql("SET LOCAL lock_timeout='2s'")
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()
        )
        observed = (
            now
            if now is not None
            else conn.exec_driver_sql("SELECT CURRENT_TIMESTAMP").scalar_one()
        )
        ledger = DemoLedger.model_validate(row["state"])
        empty(ledger)
        require(
            row["owner"] is None and (row["lease_until"] is None or row["lease_until"] <= observed),
            "ACTIVE_OWNER_BLOCKED",
        )
        require(day_recorded(ledger), "NOVEMBER11_DAY_RECEIPT_REQUIRED")
        require(
            period_keys(observed, ledger.policy.week_start_kst)
            == (ledger.daily_key, ledger.weekly_key),
            "PERIOD_KEYS_MISMATCH",
        )
        require(
            "MANAGED_QUOTA:STORAGE_V1" not in ledger.operations
            and "MANAGED_RUNTIME:SHARED_STORAGE_V1" not in ledger.operations,
            "STORAGE_ALREADY_PROVISIONED_REVIEW_REQUIRED",
        )
        event_rows = (
            conn.execute(
                select(events).where(events.c.account_id == ACCOUNT).order_by(events.c.event_id)
            )
            .mappings()
            .all()
        )
        outbox_rows = (
            conn.execute(
                select(outbox)
                .join(events, outbox.c.event_id == events.c.event_id)
                .where(events.c.account_id == ACCOUNT)
                .order_by(outbox.c.event_id)
            )
            .mappings()
            .all()
        )
        data = {
            "account": dict(row),
            "events": [dict(x) for x in event_rows],
            "outbox": [dict(x) for x in outbox_rows],
        }
        # No row contents, URLs, credentials or filesystem paths are printed or saved.
        snapshot = hashlib.sha256(
            json.dumps(data, sort_keys=True, default=lambda v: v.isoformat()).encode()
        ).hexdigest()
        return snapshot, ledger


def install_review(engine, phase, *, expected=None, root=Path("."), now=None):
    require(phase in {"pre", "post"}, "INSTALL_PHASE_INVALID")
    require(
        phase == "pre" or isinstance(expected, str) and re.fullmatch("[0-9a-f]{64}", expected),
        "INSTALL_EXPECTED_SNAPSHOT_REQUIRED",
    )
    verify_install_files(phase, root)
    if phase == "post":
        verify_default_refusal()
    snapshot, ledger = install_snapshot(engine, now=now)
    if phase == "post":
        require(snapshot == expected, "INSTALL_DATABASE_STATE_CHANGED")
    return base_report() | {
        "runtime_code_install": "PRECHECKED_CODE_ONLY_KEEP_PAUSED"
        if phase == "pre"
        else "POSTCHECKED_CODE_ONLY_KEEP_PAUSED",
        "phase": phase,
        "state_snapshot_sha256": snapshot,
        "dependency_files_verified": len(TARGET_PINS),
        "candidate_code_installed": phase == "post",
        "default_execution_disabled_verified": phase == "post",
        "database_snapshot_unchanged": phase == "post",
        "day_kst": ledger.daily_key,
        "week_kst": ledger.weekly_key,
        "daily_base_usdt": str(ledger.daily_base),
        "weekly_base_usdt": str(ledger.weekly_base),
        "high_water_usdt": str(ledger.high_water),
        "paused": ledger.paused,
        "risk_state": ledger.risk_state.value,
        "durable_order_storage": "NOT_PROVISIONED",
        "shared_weight_storage": "NOT_PROVISIONED",
        "next_action": "PREPARE_STORAGE_CONFIGURATION_REVIEW"
        if phase == "post"
        else "INSTALL_CODE_ONLY",
    }


def install_main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Code-only install guard; no keys, HTTP, provisioning or activation"
    )
    parser.add_argument("--phase", choices=["pre", "post"], required=True)
    parser.add_argument("--expected-state")
    args = parser.parse_args()
    engine = None
    code = 2
    report = base_report() | {
        "runtime_code_install": "BLOCKED_KEEP_PAUSED",
        "stop_reason": "INSTALL_REVIEW_FAILED",
    }
    try:
        require(
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
            "LIVE_CONTEXT_FORBIDDEN",
        )
        require(source_cohort_valid(INSTALLED_PINS), "INSTALLED_SOURCE_HASH_MISMATCH")
        # File checks precede all database access; post also verifies imports/default refusal.
        verify_install_files(args.phase)
        if args.phase == "post":
            verify_default_refusal()
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        report = install_review(engine, args.phase, expected=args.expected_state)
        code = 0
    except Exception as error:
        allowed = {
            "INSTALL_DEPENDENCY_MISSING",
            "INSTALL_SOURCE_CONFLICT",
            "DEFAULT_EXECUTION_REFUSAL_CHANGED",
            "POSTGRESQL_REQUIRED",
            "ACTIVE_OWNER_BLOCKED",
            "NOVEMBER11_DAY_RECEIPT_REQUIRED",
            "PERIOD_KEYS_MISMATCH",
            "DAY_OPERATION_CONFLICT",
            "PERIOD_OPERATION_CONFLICT",
            "PRIOR_PERIOD_RECEIPT_REQUIRED",
            "STORAGE_ALREADY_PROVISIONED_REVIEW_REQUIRED",
            "INSTALL_PHASE_INVALID",
            "INSTALL_EXPECTED_SNAPSHOT_REQUIRED",
            "INSTALL_DATABASE_STATE_CHANGED",
            "LIVE_CONTEXT_FORBIDDEN",
            "INSTALLED_SOURCE_HASH_MISMATCH",
        }
        reason = (
            str(error)
            if isinstance(error, Blocked) and str(error) in allowed
            else "INSTALL_REVIEW_FAILED"
        )
        report = base_report() | {
            "runtime_code_install": "BLOCKED_KEEP_PAUSED",
            "stop_reason": reason,
        }
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                report = base_report() | {
                    "runtime_code_install": "BLOCKED_KEEP_PAUSED",
                    "stop_reason": "DATABASE_DISPOSE_FAILED",
                }
                code = 2
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    install_main()
