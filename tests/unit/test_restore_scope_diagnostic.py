"""Diagnostic artifact is forced to CheckOnly even with execution switches."""

import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_isolated_restore_bundle as base
from scripts import build_windows_restore_diagnostic_bundle as builder
from scripts.build_demo_read_review_bundle import hashes
from tests.unit.test_cold_backup_executor import PWSH, ROOT


def package():
    release = os.environ.get("RESTORE_DIAGNOSTIC_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {p[len(builder.FOLDER) + 1 :]: z.read(p) for p in z.namelist()}
    real = builder.git

    def source(*args):
        return (ROOT / args[1].split(":", 1)[1]).read_bytes() if args[0] == "show" else real(*args)

    with patch.object(builder, "git", source), patch.object(base, "git", source):
        return builder.package_files("HEAD")


def materialize(root):
    root.mkdir(parents=True, exist_ok=True)
    for name, body in package().items():
        (root / name).write_bytes(body)


def test_diagnostic_has_no_execution_command_and_pins_all_sources():
    files = package()
    assert len(files) == 11 and "RUN_RESTORE.cmd" not in files
    for p, h in json.loads(files["TRANSFER_MANIFEST.json"])["files"].items():
        assert hashlib.sha256(files[p]).hexdigest() == h
    launcher = files["RUN_RESTORE.ps1"].decode()
    assert "Invoke-IsolatedRestore -Root $backupRoot -CheckOnly |" in launcher
    assert "-ExecuteRestore:$ExecuteRestore" not in launcher
    assert "source_state_may_have_changed=$false" in launcher


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "switches", ["", "-CheckOnly", "-ExecuteRestore", "-ExecuteRestore -CheckOnly"]
)
def test_launcher_only_invokes_check_even_if_execution_requested(tmp_path, switches):
    materialize(tmp_path)
    core = tmp_path / "RESTORE_CORE.ps1"
    core.write_text(
        core.read_text()
        + """
function Invoke-IsolatedRestore([string]$Root,[switch]$ExecuteRestore,[switch]$CheckOnly) {
 if ($CheckOnly -and -not $ExecuteRestore) { Write-Output 'CHECK_ONLY_FORCED'; return 0 }
 throw 'EXECUTION_MUST_NOT_BE_REQUESTED'
}
"""
    )
    files = {
        p: (tmp_path / p).read_bytes()
        for p in json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_text())["files"]
    }
    manifest = json.dumps(dict(files=hashes(files)), sort_keys=True).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(manifest)
    harness = tmp_path / "test.ps1"
    harness.write_text(
        'function docker {throw "DOCKER_MUST_NOT_RUN"}\n& "'
        + str(tmp_path / "RUN_RESTORE.ps1")
        + '" -ManifestSHA256 '
        + hashlib.sha256(manifest).hexdigest()
        + " "
        + switches
        + "\nexit $LASTEXITCODE\n"
    )
    result = subprocess.run(
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
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["TRANSFER_FILES_VERIFIED", "CHECK_ONLY_FORCED"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,expected",
    [
        ("reorder", "OK"),
        ("rw", "COLD_SOURCE_CONFIGURATION_CHANGED"),
        ("source", "COLD_SOURCE_CONFIGURATION_CHANGED"),
        ("extra-field", "COLD_SOURCE_CONFIGURATION_CHANGED"),
        ("running", "COLD_SOURCE_RUNNING_STATE_CHANGED"),
    ],
)
def test_fresh_snapshot_ignores_only_mount_list_order(tmp_path, case, expected):
    materialize(tmp_path)
    old = dict(
        id="a" * 64,
        running=False,
        restarting=False,
        paused=False,
        dead=False,
        mounts=[
            dict(
                Destination="/source/database",
                Source="/vol/database",
                RW=False,
                Name="db",
                Type="volume",
            ),
            dict(Destination="/output", Source="/vol/output", RW=True, Name="out", Type="volume"),
        ],
    )
    fresh = json.loads(json.dumps(old))
    fresh["mounts"].reverse()
    if case == "rw":
        fresh["mounts"][1]["RW"] = True
    if case == "source":
        fresh["mounts"][1]["Source"] = "/different/source"
    if case == "extra-field":
        fresh["mounts"][1]["Unexpected"] = "PRIVATE_SENTINEL"
    if case == "running":
        fresh["running"] = True
    (tmp_path / "records.json").write_text(json.dumps(dict(old=old, fresh=fresh)))
    harness = tmp_path / "snap-test.ps1"
    harness.write_text("""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/RESTORE_CORE.ps1"
$script:value=Get-Content -Raw "$PSScriptRoot/records.json" | ConvertFrom-Json
function Container-Ids {return ('a'*64)}
function Read-Container {return $script:value.fresh}
try {Assert-RestoreSourceSnapshot @($script:value.old) @() $false; Write-Output 'OK'} catch {Write-Output $_.Exception.Message}
""")
    result = subprocess.run(
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
    assert result.returncode == 0 and not result.stderr
    assert result.stdout.strip() == expected
