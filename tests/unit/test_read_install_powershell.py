"""PowerShell installer control flow: preserved config, fail-before-stop and rollback."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PWSH = shutil.which("pwsh") or "/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh"
pytestmark = pytest.mark.skipif(not Path(PWSH).exists(), reason="PowerShell required")


def execute(tmp_path, fault="success"):
    root = tmp_path / "install with spaces"
    root.mkdir()
    (root / "INSTALL.ps1").write_bytes(
        Path("tools/windows-demo-read-install/INSTALL.ps1").read_bytes()
    )
    (root / "INSTALL_INSPECTION.ps1").write_bytes(
        Path("tools/windows-demo-read-install/INSTALL_INSPECTION.ps1").read_bytes()
    )
    (root / "DOCKER_RECOVERY.ps1").write_text("""
function Get-RecoveryCreatePlan($Details,$TargetImage) { return @{Image=$TargetImage;HostConfig=@{RestartPolicy=@{Name='always';MaximumRetryCount=0}}} }
function Get-RecoveryJsonCopy($Value) { return ($Value | ConvertTo-Json -Depth 20 | ConvertFrom-Json) }
function Get-RecoveryPlanDigest($Plan) { return 'FIXED_PLAN' }
function New-PreservedRecoveryContainer($Plan,$Name) { return ('c'*64) }
function Read-RecoveryContainer($Id) { return $global:newDetails }
function Restore-PreservedRecoveryContainer($OriginalId,$CandidateId,$ContainerName,$FailedName) { $global:currentNew=$false; $global:calls.Add('RESTORED_ORIGINAL'); return $true }
""")
    (root / "SOURCE_REFERENCE.json").write_text(json.dumps(dict(candidate_reference="f" * 40)))
    report = dict(
        read_code_install="PRECHECKED_READ_CODE_INSTALL_KEEP_PAUSED",
        preserved_snapshot_sha256="e" * 64,
        target_already_installed=fault == "already",
        installed_command_verified=False,
        orders_enabled=False,
        live_trading_enabled=False,
        execution_approved=False,
    )
    post = report | dict(
        read_code_install="POSTCHECKED_READ_CODE_INSTALLED_KEEP_PAUSED",
        installed_command_verified=True,
    )
    guard = root / "RUN_GUARD.cmd"
    guard.write_text(
        "#!/bin/sh\nif [ \"$2\" = 'post' ]; then\necho '"
        + json.dumps(post)
        + "'\nexit "
        + ("1" if fault == "post" else "0")
        + "\nfi\necho '"
        + json.dumps(report)
        + "'\nexit "
        + ("1" if fault == "pre" else "0")
        + "\n"
    )
    guard.chmod(0o700)
    manifest = json.dumps(
        dict(files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()})
    ).encode()
    (root / "TRANSFER_MANIFEST.json").write_bytes(manifest)
    details = dict(
        Id="a" * 64,
        Name="/hybrid-windows-api-1",
        Image="sha256:" + "b" * 64,
        State=dict(Running=True, Health=dict(Status="healthy")),
        Config=dict(Env=["API_SECRET=PRIVATE_VALUE"]),
        Mounts=[
            dict(
                Destination="/app/demo-export-state",
                Type="tmpfs" if fault == "storage" else "volume",
                RW=True,
            )
        ],
    )
    new_details = details | dict(Id="c" * 64, Image="sha256:" + "d" * 64)
    (tmp_path / "old.json").write_text(json.dumps(details))
    (tmp_path / "new.json").write_text(json.dumps(new_details))
    wrapper = tmp_path / "wrapper.ps1"
    wrapper.write_text(
        """
$global:calls=New-Object 'System.Collections.Generic.List[string]'
$global:currentNew=$false
$global:oldDetails=Get-Content -Raw -LiteralPath 'OLD' | ConvertFrom-Json
$global:newDetails=Get-Content -Raw -LiteralPath 'NEW' | ConvertFrom-Json
function global:docker {
 $global:LASTEXITCODE=0
 $global:calls.Add(($args -join ' '))
 if ($args[0] -eq 'inspect') {
  if ($global:currentNew) { $global:newDetails | ConvertTo-Json -Depth 10 -Compress }
  else { $global:oldDetails | ConvertTo-Json -Depth 10 -Compress }
 }
 if ($args[0] -eq 'start' -and $args[1] -eq ('c'*64)) { $global:currentNew=$true }
}
& 'SCRIPT' -ManifestSHA256 HASH
$code=$LASTEXITCODE
Write-Output ('CALLS:'+($global:calls | ConvertTo-Json -Compress))
exit $code
""".replace("OLD", str(tmp_path / "old.json"))
        .replace("NEW", str(tmp_path / "new.json"))
        .replace("SCRIPT", str(root / "INSTALL.ps1"))
        .replace("HASH", hashlib.sha256(manifest).hexdigest())
    )
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        timeout=25,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
        ),
    )


def test_success_preserves_backup_and_disables_its_restart(tmp_path):
    result = execute(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "READ_CODE_IMAGE_INSTALLED_AUDIT_VERIFIED_KEEP_PAUSED_NO_ORDERS" in result.stdout
    assert "update --restart=no " in result.stdout
    assert "PRIVATE" not in result.stdout + result.stderr


@pytest.mark.parametrize("fault", ["storage", "pre"])
def test_guard_failure_does_not_stop_original_api(tmp_path, fault):
    result = execute(tmp_path, fault)
    assert result.returncode == 1
    assert '"api_replacement_started":false' in result.stdout
    assert "stop --time" not in result.stdout and "PRIVATE" not in result.stdout + result.stderr


def test_post_failure_restores_original_restart_policy_and_verifies_data(tmp_path):
    result = execute(tmp_path, "post")
    assert result.returncode == 1
    assert '"rollback_status":"ORIGINAL_API_RESTORED"' in result.stdout
    assert '"rollback_data_verified":true' in result.stdout
    assert "update --restart=always " in result.stdout
    assert "RESTORED_ORIGINAL" in result.stdout


def test_already_installed_verifies_without_build_or_replace(tmp_path):
    result = execute(tmp_path, "already")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "READ_CODE_ALREADY_INSTALLED_VERIFIED_KEEP_PAUSED_NO_ORDERS" in result.stdout
    assert "build --pull" not in result.stdout and "stop --time" not in result.stdout
