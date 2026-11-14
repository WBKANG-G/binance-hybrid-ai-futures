"""In-process planner transport: fixed diagnostic projection, no raw child output."""

import json
import os
import subprocess

import pytest

from tests.unit.test_cold_backup_executor import PWSH, materialize


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "blocked",
        "private-code",
        "private-stage",
        "private-probe",
        "extra-output",
        "unsafe-flag",
        "throw-private",
    ],
)
def test_plan_object_transport_exposes_only_allowed_diagnostic_fields(tmp_path, scenario):
    materialize(tmp_path)
    result = dict(
        backup_plan="BLOCKED_KEEP_PAUSED",
        stage="POSTGRES_CAPABILITY",
        failure_category="BACKUP_PLAN_POSTGRES_LAYOUT_UNSUPPORTED",
        postgres_probe=dict(
            pgdata="UNSUPPORTED_LAYOUT",
            database_user="OBSERVED",
            database_name="OBSERVED",
            server_version="OBSERVED",
            control_version="OBSERVED",
        ),
        private_data="PRIVATE_SENTINEL",
    )
    for name in (
        "source_services_stopped",
        "source_restart_policies_changed",
        "database_changed",
        "files_changed",
        "backup_created",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        result[name] = False
    if scenario == "success":
        result["backup_plan"] = "READ_ONLY_COLD_BACKUP_PLAN_OBSERVED_KEEP_PAUSED"
    elif scenario == "private-code":
        result["failure_category"] = "PRIVATE_SENTINEL"
    elif scenario == "private-stage":
        result["stage"] = "PRIVATE_SENTINEL"
    elif scenario == "private-probe":
        result["postgres_probe"]["pgdata"] = "PRIVATE_SENTINEL"
    elif scenario == "unsafe-flag":
        result["source_services_stopped"] = True
    (tmp_path / "result.json").write_text(json.dumps(result))

    def safe(p):
        return "'" + str(p).replace("'", "''") + "'"

    stub = "param([string]$ManifestSHA256,[switch]$ReturnPlanObject)\nif (-not $ReturnPlanObject) { throw 'PRIVATE_SENTINEL' }\n"
    if scenario == "throw-private":
        stub += "throw 'PRIVATE_SENTINEL'\n"
    elif scenario == "extra-output":
        stub += "Write-Output 'PRIVATE_SENTINEL'\n"
    stub += (
        "return (Get-Content -Raw -LiteralPath "
        + safe(tmp_path / "result.json")
        + " | ConvertFrom-Json)\n"
    )
    (tmp_path / "plan/PLAN.ps1").write_text(stub)
    harness = tmp_path / "adapter.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n. "
        + safe(tmp_path / "BACKUP_CORE.ps1")
        + "\ntry { $value=Read-BackupPlan "
        + safe(tmp_path)
        + "; $script:ColdPlanDiagnostic | ConvertTo-Json -Depth 8 -Compress; exit 0 } catch { $script:ColdPlanDiagnostic | ConvertTo-Json -Depth 8 -Compress; exit 2 }\n"
    )
    process = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        timeout=15,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
        ),
    )
    assert not process.stderr
    assert "PRIVATE_SENTINEL" not in process.stdout
    diagnostic = json.loads(process.stdout)
    assert set(diagnostic) == {"status", "stage", "failure_category", "postgres_probe"}
    if scenario == "success":
        assert process.returncode == 0 and diagnostic["status"] == "PLAN_OBSERVED"
    elif scenario == "blocked":
        assert process.returncode == 2 and diagnostic["status"] == "PLAN_BLOCKED"
        assert diagnostic["stage"] == "POSTGRES_CAPABILITY"
        assert diagnostic["failure_category"] == "BACKUP_PLAN_POSTGRES_LAYOUT_UNSUPPORTED"
        assert diagnostic["postgres_probe"]["pgdata"] == "UNSUPPORTED_LAYOUT"
    else:
        assert process.returncode == 2 and diagnostic["status"] == "NO_VALID_PLAN_RESPONSE"
        assert diagnostic["failure_category"] == "BACKUP_PLAN_RESPONSE_INVALID"
