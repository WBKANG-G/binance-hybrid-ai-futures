"""Native PowerShell 7 metadata parsing tests; no Docker command or real secrets."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
PWSH = (
    os.environ.get("PWSH_EXECUTABLE")
    or shutil.which("pwsh")
    or "/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh"
)


LEGACY_PARSER = r"""
if ($env:INSTALL_TEST_JSON_MODE -eq 'array_preserved') {
    function global:ConvertFrom-Json {
        [CmdletBinding()]
        param([Parameter(ValueFromPipeline=$true)][string]$InputObject)
        process {
            $parsed = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $InputObject -NoEnumerate
            Write-Output -NoEnumerate $parsed
        }
    }
}
"""


@pytest.fixture(autouse=True, params=["enumerating", "array_preserved"])
def parser_mode(request, monkeypatch):
    monkeypatch.setenv("INSTALL_TEST_JSON_MODE", request.param)
    return request.param


@pytest.fixture
def details(tmp_path):
    directory = tmp_path / "PRIVATE_HOST_PATH with spaces"
    directory.mkdir()
    config = directory / "compose.yml"
    config.write_text("services: {}")
    return {
        "Name": "/hybrid-windows-api-1",
        "Image": "sha256:" + "a" * 64,
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "Config": {
            "Env": ["API_SECRET=PRIVATE_KEY_MUST_NOT_PRINT"],
            "Labels": {
                "com.docker.compose.project": "hybrid-windows",
                "com.docker.compose.service": "api",
                "com.docker.compose.project.working_dir": str(directory),
                "com.docker.compose.project.config_files": str(config),
            },
        },
    }


def run_inspection(tmp_path, payload, exit_code=0):
    fixture = tmp_path / "inspect.json"
    fixture.write_text(payload)
    script = tmp_path / "test.ps1"
    module = str(ROOT / "INSTALL_INSPECTION.ps1").replace("'", "''")
    path = str(fixture).replace("'", "''")
    script.write_text(f"""{LEGACY_PARSER}
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. '{module}'
try {{
 $details=Convert-InstallInspection -InspectOutput @((Get-Content -Raw -LiteralPath '{path}')) -InspectExitCode {exit_code}
 $safe=Get-SafeInstallInspection $details
 $safe | ConvertTo-Json -Compress | Write-Output
 $context=Get-InstallComposeContext $details
 @{{result='VALIDATED'; file_count=@($context.Files).Count}} | ConvertTo-Json -Compress | Write-Output
}} catch {{
 @{{result='BLOCKED';reason=$_.Exception.Message}} | ConvertTo-Json -Compress | Write-Output
}}
""")
    env = os.environ | {
        "XDG_CACHE_HOME": "/tmp/runtime-installer-cache",
        "XDG_CONFIG_HOME": "/tmp/runtime-installer-config",
        "XDG_DATA_HOME": "/tmp/runtime-installer-data",
    }
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert "PRIVATE_" not in result.stdout
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_native_powershell_single_container_array_and_private_path_are_supported(details, tmp_path):
    output = run_inspection(tmp_path, json.dumps([details]))
    assert output[-1] == {"result": "VALIDATED", "file_count": 1}
    assert output[0]["container_running"] and output[0]["compose_working_directory_available"]


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("stopped", "API_CONTAINER_NOT_RUNNING"),
        ("health_missing", "API_HEALTHCHECK_MISSING"),
        ("health_starting", "API_CONTAINER_HEALTH_STARTING"),
        ("unhealthy", "API_CONTAINER_NOT_HEALTHY"),
        ("labels_missing", "COMPOSE_LABELS_MISSING"),
        ("project_missing", "COMPOSE_PROJECT_LABEL_MISMATCH"),
        ("service_wrong", "COMPOSE_SERVICE_LABEL_MISMATCH"),
        ("directory_label_missing", "COMPOSE_WORKING_DIRECTORY_LABEL_MISSING"),
        ("directory_gone", "COMPOSE_WORKING_DIRECTORY_NOT_FOUND"),
        ("files_label_missing", "COMPOSE_CONFIG_FILES_LABEL_MISSING"),
        ("file_gone", "COMPOSE_CONFIG_FILE_NOT_FOUND"),
        ("image_invalid", "BASE_IMAGE_ID_INVALID"),
        ("identity_wrong", "CONTAINER_IDENTITY_MISMATCH"),
    ],
)
def test_native_powershell_reports_specific_safe_reason(details, tmp_path, fault, reason):
    labels = details["Config"]["Labels"]
    if fault == "stopped":
        details["State"]["Running"] = False
    elif fault == "health_missing":
        details["State"].pop("Health")
    elif fault == "health_starting":
        details["State"]["Health"]["Status"] = "starting"
    elif fault == "unhealthy":
        details["State"]["Health"]["Status"] = "unhealthy"
    elif fault == "labels_missing":
        details["Config"].pop("Labels")
    elif fault == "project_missing":
        labels.pop("com.docker.compose.project")
    elif fault == "service_wrong":
        labels["com.docker.compose.service"] = "web"
    elif fault == "directory_label_missing":
        labels.pop("com.docker.compose.project.working_dir")
    elif fault == "directory_gone":
        labels["com.docker.compose.project.working_dir"] += "/missing"
    elif fault == "files_label_missing":
        labels.pop("com.docker.compose.project.config_files")
    elif fault == "file_gone":
        labels["com.docker.compose.project.config_files"] += ".missing"
    elif fault == "image_invalid":
        details["Image"] = "bad"
    else:
        details["Name"] = "/other"
    output = run_inspection(tmp_path, json.dumps([details]))
    assert output[-1] == {"result": "BLOCKED", "reason": reason}


@pytest.mark.parametrize(
    "payload,exit_code,reason",
    [
        ("not-json", 0, "CONTAINER_INSPECT_JSON_INVALID"),
        ("[]", 0, "CONTAINER_INSPECT_SHAPE_INVALID"),
        ("null", 0, "CONTAINER_INSPECT_SHAPE_INVALID"),
        ("[{},{}]", 0, "CONTAINER_INSPECT_SHAPE_INVALID"),
        ("PRIVATE_DOCKER_ERROR", 1, "CONTAINER_INSPECT_COMMAND_FAILED"),
    ],
)
def test_native_powershell_invalid_docker_output_is_redacted(tmp_path, payload, exit_code, reason):
    assert run_inspection(tmp_path, payload, exit_code)[-1] == {
        "result": "BLOCKED",
        "reason": reason,
    }


@pytest.mark.parametrize(
    "fault,expected_reason",
    [
        (None, None),
        ("health_missing", "API_HEALTHCHECK_MISSING"),
        ("directory_gone", "COMPOSE_WORKING_DIRECTORY_NOT_FOUND"),
        ("project_missing", "COMPOSE_PROJECT_LABEL_MISMATCH"),
    ],
)
def test_native_installer_orchestration_stops_before_mutations_for_invalid_metadata(
    details, tmp_path, fault, expected_reason
):
    import hashlib

    packet = tmp_path / "installer packet"
    packet.mkdir()
    for name in (
        "INSTALL_CODE.ps1",
        "INSTALL_INSPECTION.ps1",
        "guard_runtime_code_install.py",
        "Dockerfile",
    ):
        shutil.copy2(ROOT / name, packet / name)
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in packet.iterdir()}
    (packet / "TRANSFER_MANIFEST.json").write_text(json.dumps({"files": manifest}))
    if fault == "health_missing":
        details["State"].pop("Health")
    elif fault == "directory_gone":
        details["Config"]["Labels"]["com.docker.compose.project.working_dir"] += "/missing"
    elif fault == "project_missing":
        details["Config"]["Labels"].pop("com.docker.compose.project")
    fixture = tmp_path / "docker.json"
    fixture.write_text(json.dumps([details]))
    log = tmp_path / "commands.jsonl"
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "docker"
    executable.write_text("""#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['MOCK_DOCKER_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
if args[0]=='inspect':
 if '--format' in args:print('sha256:'+'a'*64)
 else:print(open(os.environ['MOCK_DOCKER_FIXTURE']).read())
elif args[0]=='exec':
 sys.stdin.read()
 phase=args[args.index('--phase')+1]
 print(json.dumps({'runtime_code_install':'PRECHECKED_CODE_ONLY_KEEP_PAUSED' if phase=='pre' else 'POSTCHECKED_CODE_ONLY_KEEP_PAUSED','state_snapshot_sha256':'b'*64}))
elif args[0] in {'tag','build','compose'}:print('MOCK_DOCKER_ONLY')
else:raise SystemExit(95)
""")
    executable.chmod(0o755)
    env = os.environ | {
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "MOCK_DOCKER_LOG": str(log),
        "MOCK_DOCKER_FIXTURE": str(fixture),
        "XDG_CACHE_HOME": "/tmp/runtime-installer-cache",
        "XDG_CONFIG_HOME": "/tmp/runtime-installer-config",
        "XDG_DATA_HOME": "/tmp/runtime-installer-data",
    }
    bootstrap = tmp_path / "bootstrap.ps1"
    install_path = str(packet / "INSTALL_CODE.ps1").replace("'", "''")
    bootstrap.write_text(LEGACY_PARSER + "\n& '" + install_path + "'\nexit $LASTEXITCODE\n")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(bootstrap)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )
    assert "PRIVATE_" not in result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    if fault:
        assert result.returncode == 2, result.stdout + result.stderr
        report = json.loads(result.stdout.splitlines()[-1])
        assert (
            report["stop_reason"] == expected_reason and report["api_replacement_started"] is False
        )
        assert [a[0] for a in calls] == ["inspect"]
        assert not (packet / "INSTALL_STATE.json").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "CODE_ONLY_INSTALL_VERIFIED_KEEP_PAUSED_STORAGE_NOT_PROVISIONED" in result.stdout
        compose = next(a for a in calls if a[0] == "compose")
        assert compose[-1] == "api" and "--no-deps" in compose and "--no-build" in compose
        assert "--pull" in compose and "never" in compose
        assert (packet / "INSTALL_STATE.json").exists()
