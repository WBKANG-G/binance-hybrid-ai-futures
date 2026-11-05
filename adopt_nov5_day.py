"""One authorized Nov5 daily baseline adoption via existing audited commands."""

import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

OPERATION = "191e9476-1763-46cb-8c3e-b79f8e31506e"
DATE = "2026-11-05"
ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
ADOPTED = "OPERATOR_ATTESTED_DAY_BASELINE_ADOPTED_ENTRIES_STILL_PAUSED"


def call(module, *args):
    result = subprocess.run(
        [sys.executable, "-m", "scripts." + module, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("EXISTING_COMMAND_BLOCKED")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("COMMAND_JSON_REQUIRED")
    return value


def require(condition):
    if not condition:
        raise ValueError("REVIEW_CONDITIONS_CHANGED")


def safe_state(report):
    require(report.get("risk_state") == "NORMAL")
    require(report.get("paused") is True)
    require(report.get("orders_enabled") is False)
    require(report.get("live_trading_enabled") is False)


def run(invoke=call, now=None):
    stage = "DATE_CHECK"
    apply_started = False
    try:
        today = (now or datetime.now(UTC)).astimezone(UTC) + timedelta(hours=9)
        require(today.date().isoformat() == DATE)
        UUID(OPERATION)
        stage = "LOCAL_PERIODS"
        local = invoke("review_demo_recovery", "--local-periods")
        safe_state(local)
        periods = local["periods"]
        require(local.get("recovery") == "LOCAL_PERIOD_CHECK_NO_DATABASE_CHANGE")
        require(periods["current_day_kst"] == DATE)
        require(periods["stored_week_kst"] == periods["current_week_kst"] == "2026-11-02")
        if local.get("period_keys_match") is True:
            stage = "READ_ONLY_READY"
            ready = invoke("check_demo_session_ready")
            validate_ready(ready)
            return {"daily_update": "ALREADY_CURRENT_NO_APPLY", "readiness": ready}
        require(local.get("period_keys_match") is False)
        require(periods["stored_day_kst"] == "2026-11-03")
        stage = "FRESH_SIGNED_PREVIEW"
        preview = invoke("review_demo_paused_day", "--attest-no-other-trades-since-exit")
        safe_state(preview)
        require(preview.get("review") == "PAUSED_NORMAL_DAY_PREVIEW_NO_DATABASE_CHANGE")
        require(preview.get("account_id") == ACCOUNT)
        require(preview.get("source") == "OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE")
        require(preview.get("old_daily_key") == "2026-11-03")
        require(preview.get("proposed_day_kst") == DATE)
        require(preview.get("database_changed") is False)
        require(preview.get("account_wide_exchange_export_verified") is False)
        require(preview.get("operator_attestation_used") is True)
        for name in (
            "weekly_base_preserved",
            "high_water_preserved",
            "old_valuation_timestamp_preserved",
            "requests_preserved",
            "risk_state_preserved",
        ):
            require(preview.get(name) is True)
        require(Decimal(preview["old_daily_base_usdt"]) == Decimal("4998.99761493"))
        require(Decimal(preview["proposed_daily_base_usdt"]) == Decimal("4998.99761493"))
        require(Decimal(preview["net_deposit_basis_usdt"]) == 0)
        token = preview["review_token"]
        require(isinstance(token, str) and re.fullmatch(r"[0-9a-f]{64}", token) is not None)
        stage = "APPLY_EXISTING_AUDITED_COMMAND"
        apply_started = True
        applied = invoke(
            "review_demo_paused_day",
            "--attest-no-other-trades-since-exit",
            "--accept-operator-evidence",
            "--apply-review",
            token,
            "--operation-id",
            OPERATION,
        )
        safe_state(applied)
        require(applied.get("day_baseline") in {ADOPTED, "ALREADY_RECORDED_NO_ACTION"})
        require(applied.get("operation_id") == OPERATION)
        require(applied.get("leader_released") is True)
        require(applied.get("unresolved_requests_acknowledged") is False)
        require(applied.get("weekly_base_preserved") is True)
        require(applied.get("high_water_preserved") is True)
        stage = "POST_APPLY_READ_ONLY_READY"
        ready = invoke("check_demo_session_ready")
        validate_ready(ready)
        return {
            "daily_update": "NOV5_DAY_ADOPTED_AND_READY_ENTRIES_STILL_PAUSED",
            "operation_id": OPERATION,
            "application": applied,
            "readiness": ready,
        }
    except Exception:  # noqa: BLE001 -- no raw private child output, response, environment or token
        return {
            "daily_update": "BLOCKED_NO_AUTOMATIC_RETRY",
            "stage": stage,
            "apply_started": apply_started,
            "application_may_have_completed": apply_started,
            "operation_id": OPERATION,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }


def validate_ready(ready):
    safe_state(ready)
    require(ready.get("session_ready") == "READ_ONLY_READY_ENTRIES_STILL_PAUSED")
    require(ready.get("exchange_empty_verified") is True)
    require(ready.get("database_changed") is False)
    require(ready.get("period_keys_match") is True)
    require(ready["periods"]["stored_day_kst"] == ready["periods"]["current_day_kst"] == DATE)
    require(
        ready["periods"]["stored_week_kst"] == ready["periods"]["current_week_kst"] == "2026-11-02"
    )


if __name__ == "__main__":
    result = run()
    print(json.dumps(result), flush=True)
    raise SystemExit(2 if result["daily_update"] == "BLOCKED_NO_AUTOMATIC_RETRY" else 0)
