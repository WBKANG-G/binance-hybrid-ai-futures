"""Offline safety tests; no Docker, account, key, email or exchange access."""

import copy
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import run_serialized_worker as runner


def ready_report():
    return {
        "session_ready": "READ_ONLY_READY_ENTRIES_STILL_PAUSED",
        "risk_state": "NORMAL",
        "paused": True,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "database_changed": False,
        "exchange_empty_verified": True,
        "period_keys_match": True,
        "periods": {
            "stored_day_kst": "2026-11-06",
            "current_day_kst": "2026-11-06",
            "stored_week_kst": "2026-11-02",
            "current_week_kst": "2026-11-02",
        },
    }


def run_case(report=None, *, code=0, dates=None, verify_error=None, execute_error=None):
    calls = []
    dates = iter(dates or [datetime(2026, 11, 6, 1, tzinfo=UTC)] * 2)

    def verify():
        calls.append("verify")
        if verify_error:
            raise verify_error

    def ready():
        calls.append("ready")
        return ready_report() if report is None else report

    def execute():
        calls.append("execute")
        if execute_error:
            raise execute_error
        return code

    return runner.run(
        verify=verify, ready=ready, execute=execute, clock=lambda: next(dates)
    ), calls


def test_safe_state_launches_exactly_once():
    result, calls = run_case()
    assert calls == ["verify", "ready", "execute"]
    assert result["serialized_worker_runner"] == "VERIFIED"
    assert result["operation_id"] == "e85a00f2-6b8e-4b39-bac2-b958a001c8c0"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_ready", "BLOCKED"),
        ("risk_state", "EMERGENCY"),
        ("paused", False),
        ("orders_enabled", True),
        ("live_trading_enabled", True),
        ("database_changed", True),
        ("exchange_empty_verified", False),
        ("period_keys_match", False),
    ],
)
def test_unsafe_readiness_never_launches(field, value):
    report = ready_report()
    report[field] = value
    result, calls = run_case(report)
    assert calls == ["verify", "ready"]
    assert result["execution_started"] is False


@pytest.mark.parametrize("field", list(ready_report()))
def test_missing_readiness_fields_never_launch(field):
    report = ready_report()
    del report[field]
    result, calls = run_case(report)
    assert "execute" not in calls
    assert result["execution_started"] is False


@pytest.mark.parametrize("field", list(ready_report()["periods"]))
def test_changed_period_never_launches(field):
    report = copy.deepcopy(ready_report())
    report["periods"][field] = "2026-11-05"
    result, calls = run_case(report)
    assert "execute" not in calls
    assert result["error_code"] == "PERIOD_SCOPE_CHANGED"


@pytest.mark.parametrize(
    "date",
    [datetime(2026, 11, 5, 14, tzinfo=UTC), datetime(2026, 11, 6, 15, tzinfo=UTC)],
)
def test_wrong_kst_date_blocks_before_sources(date):
    result, calls = run_case(dates=[date])
    assert calls == []
    assert result["error_code"] == "KST_DATE_CHANGED"


def test_midnight_crossing_blocks_after_readiness():
    result, calls = run_case(
        dates=[
            datetime(2026, 11, 6, 14, 59, tzinfo=UTC),
            datetime(2026, 11, 6, 15, tzinfo=UTC),
        ]
    )
    assert calls == ["verify", "ready"]
    assert result["error_code"] == "KST_DATE_CHANGED"


def test_naive_clock_blocks():
    result, calls = run_case(dates=[datetime(2026, 11, 6)])
    assert calls == []
    assert result["error_code"] == "UTC_CLOCK_REQUIRED"


def test_source_failure_blocks_before_readiness():
    result, calls = run_case(
        verify_error=runner.RunnerBlocked("INSTALLED_SOURCE_MISMATCH")
    )
    assert calls == ["verify"]
    assert result["execution_started"] is False


def test_unsuccessful_trial_has_no_retry():
    result, calls = run_case(code=2)
    assert calls == ["verify", "ready", "execute"]
    assert result["serialized_worker_runner"] == "NOT_VERIFIED_NO_AUTOMATIC_RETRY"


def test_error_details_redacted_and_uncertain_execution_marked():
    result, calls = run_case(execute_error=RuntimeError("SECRET_EXAMPLE"))
    assert calls == ["verify", "ready", "execute"]
    assert result["application_may_have_completed"] is True
    assert "SECRET_EXAMPLE" not in json.dumps(result)


def test_hash_check_rejects_tampering(tmp_path, monkeypatch):
    path = tmp_path / "module.py"
    path.write_bytes(b"expected")
    monkeypatch.setattr(
        runner, "HASHES", {"module.py": hashlib.sha256(b"expected").hexdigest()}
    )
    runner.check_sources(tmp_path)
    path.write_bytes(b"changed")
    with pytest.raises(runner.RunnerBlocked, match="INSTALLED_SOURCE_MISMATCH"):
        runner.check_sources(tmp_path)


def test_launch_scope_and_no_supervisor_timeout(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 2)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.launch() == 2
    assert calls == [
        (
            [
                sys.executable,
                "-m",
                "scripts.check_demo_protected_worker",
                "--enable-demo",
                "--enable-protected-worker-kill",
                "--operation-id",
                runner.OPERATION,
            ],
            {"stdin": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "check": False},
        )
    ]


@pytest.mark.parametrize("body,code", [("[]", 0), ("{}", 2), ("not-json", 0)])
def test_ready_subprocess_failure(body, code, monkeypatch):
    def fake_run(args, **kwargs):
        assert args == [sys.executable, "-m", "scripts.check_demo_session_ready"]
        assert kwargs == {
            "stdin": subprocess.DEVNULL,
            "capture_output": True,
            "text": True,
            "timeout": 150,
            "check": False,
        }
        return subprocess.CompletedProcess(args, code, body, "PRIVATE_EXAMPLE")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    with pytest.raises((runner.RunnerBlocked, json.JSONDecodeError)):
        runner.check_ready()


def test_plan_matches_actual_runner_and_installed_sources():
    plan = json.loads(Path("RUN_PLAN.json").read_text())
    assert plan["operation_id"] == runner.OPERATION
    assert plan["runtime_source_hashes"] == runner.HASHES
    assert plan["maximum_entry_attempts"] == plan["maximum_protected_child_kills"] == 1
    assert plan["app_rebuild"] is False
    assert plan["automatic_recovery_or_resume"] is False
    assert runner.OPERATION not in {
        "5bb5814c-55e0-4b7f-882c-a7a449ec2182",
        "0f72dd2e-9c5d-4567-aa46-9f3772f79ea7",
    }


def test_cmd_preserves_results_pins_launcher_and_calls_once():
    cmd = Path("RUN-ONE-SERIALIZED-WORKER.cmd").read_text()
    assert (
        hashlib.sha256(Path("run_serialized_worker.py").read_bytes()).hexdigest() in cmd
    )
    assert cmd.index("if exist serialized-protected-worker-result.log") < cmd.index(
        "docker exec"
    )
    assert cmd.count("docker exec") == 1
    assert (
        "python - < run_serialized_worker.py > serialized-protected-worker-result.log"
        in cmd
    )
    assert 'set "TRIAL_EXIT=%errorlevel%"' in cmd
    assert "exit /b %TRIAL_EXIT%" in cmd
    for forbidden in ("docker compose", "--register", "--clear", "del ", "--resume"):
        assert forbidden not in cmd
