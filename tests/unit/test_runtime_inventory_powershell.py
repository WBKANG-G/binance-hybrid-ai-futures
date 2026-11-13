"""PowerShell runner validates transfer/context/output before displaying worker data."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_runtime_inventory_bundle import WORKER, package

PWSH = Path("/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh")
pytestmark = pytest.mark.skipif(
    not PWSH.exists(), reason="PowerShell 7 integration executable unavailable"
)


def quote(path):
    return "'" + str(path).replace("'", "''") + "'"


@pytest.mark.parametrize(
    "scenario",
    [
        "valid",
        "tampered-transfer",
        "wrong-context",
        "unexpected-private-field",
        "raw-private-output",
        "orders-enabled",
        "nonzero-worker",
    ],
)
def test_powershell_stops_before_mutation_and_never_prints_unvalidated_values(tmp_path, scenario):
    files = package()
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    configuration = json.loads(files["INVENTORY_CONFIG.json"])
    report = WORKER["base_report"]()
    report.update(
        runtime_inventory="READ_ONLY_INVENTORY_COMPLETE_KEEP_PAUSED",
        stage="COMPLETE",
        failure_category="NONE",
        source_reference=configuration["source_reference"],
        baseline_reference=configuration["baseline_reference"],
        baseline_files_total=len(configuration["baseline_files"]),
        baseline_files_verified=len(configuration["baseline_files"]),
        candidate_files_total=len(configuration["candidate_files"]),
        candidate_files_matching=0,
        changed_or_missing_candidate_files=list(configuration["candidate_files"]),
        expected_day_kst=configuration["expected_day_kst"],
        current_day_kst="2026-11-13",
        stored_day_kst="2026-11-13",
        stored_week_kst="2026-11-09",
        account_found=True,
        paused=True,
        period_keys_match=True,
        risk_state="NORMAL",
        database_configuration_used=True,
    )
    for table in ("demo_accounts", "demo_events", "demo_outbox"):
        report["table_presence"][table] = True
    if scenario == "unexpected-private-field":
        report["private_key"] = "PRIVATE_VALUE_DO_NOT_ECHO"
    if scenario == "orders-enabled":
        report["orders_enabled"] = True
    if scenario == "nonzero-worker":
        report.update(
            runtime_inventory="BLOCKED_KEEP_PAUSED",
            stage="DATABASE_CONFIGURATION",
            failure_category="DATABASE_NOT_CONFIGURED",
        )
    worker_output = (
        "PRIVATE_VALUE_DO_NOT_ECHO" if scenario == "raw-private-output" else json.dumps(report)
    )
    (tmp_path / "mock-output.txt").write_text(worker_output)
    if scenario == "tampered-transfer":
        (tmp_path / "worker.py").write_bytes(b"PRIVATE_VALUE_DO_NOT_ECHO")
    calls = tmp_path / "calls.jsonl"
    output = tmp_path / "mock-output.txt"
    project = "other" if scenario == "wrong-context" else "hybrid-windows"
    worker_exit = 2 if scenario == "nonzero-worker" else 0
    harness = f'''
$global:InventoryCalls={quote(calls)}
$global:InventoryOutput={quote(output)}
function global:docker {{
    $items=@($args)
    Add-Content -LiteralPath $global:InventoryCalls -Value (ConvertTo-Json -InputObject $items -Compress)
    $global:LASTEXITCODE=0
    if ($items[0] -eq 'inspect') {{
        switch ($items[2]) {{
            '{{{{.Id}}}}' {{ return ('a'*64) }}
            '{{{{.Name}}}}' {{ return '/hybrid-windows-api-1' }}
            '{{{{.State.Running}}}}' {{ return 'true' }}
            '{{{{.Config.WorkingDir}}}}' {{ return '/app' }}
            '{{{{.Config.User}}}}' {{ return '10001:10001' }}
            '{{{{json .Config.Labels}}}}' {{ return '{{"com.docker.compose.project":"{project}","com.docker.compose.service":"api"}}' }}
        }}
    }}
    if ($items[0] -eq 'exec') {{
        $input | Out-Null
        $global:LASTEXITCODE={worker_exit}
        return [IO.File]::ReadAllText($global:InventoryOutput)
    }}
    $global:LASTEXITCODE=2
}}
& {quote(tmp_path / "INVENTORY.ps1")} -ManifestSHA256 '{hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()}'
'''
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        text=True,
        timeout=20,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
            POWERSHELL_TELEMETRY_OPTOUT="1",
        ),
    )
    assert "PRIVATE_VALUE_DO_NOT_ECHO" not in result.stdout + result.stderr
    assert not result.stderr
    invocations = (
        [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    )
    assert all(row[0] in {"inspect", "exec"} for row in invocations)
    if scenario in {"valid", "nonzero-worker"}:
        assert report["runtime_inventory"] in result.stdout
    else:
        assert "RUNTIME_INVENTORY_COMPLETED" not in result.stdout
        assert "BLOCKED_KEEP_PAUSED" in result.stdout
    if scenario in {"tampered-transfer", "wrong-context"}:
        assert not any(row[0] == "exec" for row in invocations)
    if scenario == "valid":
        assert "RUNTIME_INVENTORY_COMPLETED_KEEP_PAUSED_NO_ORDERS" in result.stdout
        assert invocations[-1] == ["exec", "-i", "a" * 64, "python", "-I", "-B", "-"]


@pytest.mark.parametrize("mode", ["valid", "private-output", "blocked-worker"])
def test_native_powershell_file_entrypoint_has_expected_exit_status(tmp_path, mode):
    import sys

    files = package()
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    cfg = json.loads(files["INVENTORY_CONFIG.json"])
    report = WORKER["base_report"]()
    report.update(
        runtime_inventory="READ_ONLY_INVENTORY_COMPLETE_KEEP_PAUSED",
        stage="COMPLETE",
        failure_category="NONE",
        source_reference=cfg["source_reference"],
        baseline_reference=cfg["baseline_reference"],
        baseline_files_total=len(cfg["baseline_files"]),
        baseline_files_verified=len(cfg["baseline_files"]),
        candidate_files_total=len(cfg["candidate_files"]),
        candidate_files_matching=0,
        changed_or_missing_candidate_files=list(cfg["candidate_files"]),
        expected_day_kst=cfg["expected_day_kst"],
        current_day_kst="2026-11-13",
        stored_day_kst="2026-11-13",
        stored_week_kst="2026-11-09",
        account_found=True,
        paused=True,
        period_keys_match=True,
        risk_state="NORMAL",
        database_configuration_used=True,
    )
    for name in ("demo_accounts", "demo_events", "demo_outbox"):
        report["table_presence"][name] = True
    if mode == "blocked-worker":
        report.update(
            runtime_inventory="BLOCKED_KEEP_PAUSED",
            stage="DATABASE_CONFIGURATION",
            failure_category="DATABASE_NOT_CONFIGURED",
        )
    output = tmp_path / "mock-worker-output.json"
    output.write_text("PRIVATE_NATIVE_OUTPUT" if mode == "private-output" else json.dumps(report))
    mock = tmp_path / "docker"
    mock.write_text(
        "#!"
        + sys.executable
        + """\nimport json,os,sys
if sys.argv[1]=='inspect':
    mapping={'{{.Id}}':'a'*64,'{{.Name}}':'/hybrid-windows-api-1','{{.State.Running}}':'true','{{.Config.WorkingDir}}':'/app','{{.Config.User}}':'10001:10001','{{json .Config.Labels}}':json.dumps({'com.docker.compose.project':'hybrid-windows','com.docker.compose.service':'api','private_label':'PRIVATE_NATIVE_LABEL'})}
    print(mapping[sys.argv[3]])
else:
    sys.stdin.buffer.read()
    print(open(os.environ['MOCK_WORKER_OUTPUT']).read())
    raise SystemExit(int(os.environ['MOCK_WORKER_EXIT']))
"""
    )
    mock.chmod(0o755)
    env = dict(
        os.environ,
        PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        POWERSHELL_TELEMETRY_OPTOUT="1",
        MOCK_WORKER_OUTPUT=str(output),
        MOCK_WORKER_EXIT="2" if mode == "blocked-worker" else "0",
    )
    process = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(tmp_path / "INVENTORY.ps1"),
            "-ManifestSHA256",
            hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest(),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert process.returncode == (0 if mode == "valid" else 2)
    assert not process.stderr and "PRIVATE_NATIVE" not in process.stdout
    assert ("RUNTIME_INVENTORY_COMPLETED_KEEP_PAUSED_NO_ORDERS" in process.stdout) is (
        mode == "valid"
    )
