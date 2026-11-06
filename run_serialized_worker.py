"""One new UUID for the already-installed serialized protected worker; no deployment."""

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

OPERATION = "e85a00f2-6b8e-4b39-bac2-b958a001c8c0"
DATE_KST = "2026-11-06"
HASHES: dict[str, str] = {
    "services/execution/demo_protected_worker_check.py": "9c264588fa7802efc0dcaa4f923153072fa85ba6fd888a23a2dda8ede4966238",
    "scripts/check_demo_protected_worker.py": "b06813e8ffe64e4ad5a89700d5610e612a9cc1eb44b3b3396f397c5e7abf58ee",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",
    "services/execution/demo_stream.py": "409098cb05abbfb97eb9b9f1db2070ca6bb5782bc6dd7542311e4eb703b6f456",
    "services/execution/demo_validation.py": "d703860caf01135b3f5e82a444adc5afde1795e1f4d48bbd515cc4b9de5666a0",
    "services/execution/demo_preview.py": "58ba6376f61f8467d2b3c9e75923444105f5fe610f9bde4455a94b5fb5d6f267",
    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",
    "scripts/check_demo_session_ready.py": "400d60be1e2c8c48115c74bb7c9c99918e626d4263a0c4eb879504d738101e60",
}


class RunnerBlocked(ValueError):
    pass


def require(value: bool, code: str) -> None:
    if not value:
        raise RunnerBlocked(code)


def check_sources(root: Path = Path(".")) -> None:
    require(
        all(
            hashlib.sha256((root / n).read_bytes()).hexdigest() == h
            for n, h in HASHES.items()
        ),
        "INSTALLED_SOURCE_MISMATCH",
    )


def today(clock: Callable[[], datetime]) -> str:
    now = clock()
    require(now.tzinfo is not None, "UTC_CLOCK_REQUIRED")
    return now.astimezone(timezone(timedelta(hours=9))).date().isoformat()


def check_ready() -> dict[str, Any]:
    process = subprocess.run(
        [sys.executable, "-m", "scripts.check_demo_session_ready"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=150,
        check=False,
    )
    require(process.returncode == 0, "READ_ONLY_READY_COMMAND_BLOCKED")
    report = json.loads(process.stdout)
    if not isinstance(report, dict):
        raise RunnerBlocked("READY_JSON_REQUIRED")
    return report


def validate_ready(report: dict[str, Any]) -> None:
    require(
        report.get("session_ready") == "READ_ONLY_READY_ENTRIES_STILL_PAUSED"
        and report.get("risk_state") == "NORMAL"
        and report.get("paused") is True
        and report.get("orders_enabled") is False
        and report.get("live_trading_enabled") is False
        and report.get("database_changed") is False
        and report.get("exchange_empty_verified") is True
        and report.get("period_keys_match") is True,
        "NORMAL_PAUSED_EMPTY_READINESS_REQUIRED",
    )
    periods = report.get("periods", {})
    require(
        periods.get("stored_day_kst") == periods.get("current_day_kst") == DATE_KST
        and periods.get("stored_week_kst")
        == periods.get("current_week_kst")
        == "2026-11-02",
        "PERIOD_SCOPE_CHANGED",
    )


def launch() -> int:
    # The core supervisor owns bounded recovery. Do not impose a process timeout
    # that could terminate it during lease waiting or safety cleanup.
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.check_demo_protected_worker",
            "--enable-demo",
            "--enable-protected-worker-kill",
            "--operation-id",
            OPERATION,
        ],
        stdin=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return process.returncode


def run(
    *,
    verify: Callable[[], None] = check_sources,
    ready: Callable[[], dict[str, Any]] = check_ready,
    execute: Callable[[], int] = launch,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    stage = "DATE_CHECK"
    started = False
    try:
        require(today(clock) == DATE_KST, "KST_DATE_CHANGED")
        UUID(OPERATION)
        stage = "SOURCE_CHECK"
        verify()
        stage = "READ_ONLY_READY"
        report = ready()
        validate_ready(report)
        require(today(clock) == DATE_KST, "KST_DATE_CHANGED")
        print(
            json.dumps(
                {
                    "serialized_worker_runner_stage": "SOURCE_AND_NORMAL_PAUSED_EMPTY_VERIFIED",
                    "operation_id": OPERATION,
                    "new_entry_cap": "0.0007",
                    "notional_cap_usdt": "100",
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )
        stage = "SINGLE_TRIAL"
        started = True
        code = execute()
        return {
            "serialized_worker_runner": "VERIFIED"
            if code == 0
            else "NOT_VERIFIED_NO_AUTOMATIC_RETRY",
            "operation_id": OPERATION,
            "execution_started": True,
            "process_exit_code": code,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    except Exception as error:  # noqa: BLE001 - redact the command boundary's private details
        return {
            "serialized_worker_runner": "BLOCKED_NO_AUTOMATIC_RETRY",
            "stage": stage,
            "error_code": str(error)
            if isinstance(error, RunnerBlocked)
            else "RUNNER_FAILURE_DETAILS_REDACTED",
            "operation_id": OPERATION,
            "execution_started": started,
            "application_may_have_completed": started,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result["serialized_worker_runner"] == "VERIFIED" else 2)
