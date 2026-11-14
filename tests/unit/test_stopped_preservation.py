"""Stopped-scope refusal paths, sealed package, and no original lifecycle commands."""

import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_stopped_preservation_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT


def package():
    release = os.environ.get("STOPPED_PRESERVATION_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {p[len(builder.FOLDER) + 1 :]: z.read(p) for p in z.namelist()}
    real = builder.git

    def source(*args):
        if args[0] == "show":
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", source):
        return builder.package_files("HEAD")


def materialize(root):
    for name, body in package().items():
        (root / name).write_bytes(body)


def test_sealed_package_has_no_original_lifecycle_or_key_read():
    files = package()
    assert len(files) == 11
    for p, h in json.loads(files["TRANSFER_MANIFEST.json"])["files"].items():
        assert hashlib.sha256(files[p]).hexdigest() == h
    for name in ("COMMON.ps1", "PRESERVE_CORE.ps1", "CONTEXT.ps1"):
        source = files[name].decode()
        for command in (
            "'kill'",
            "'stop'",
            "@('exec'",
            "'update'",
            "'rm'",
            "Config.Env",
            "Request-Graceful",
        ):
            assert command not in source
    assert b"'start','--attach'" not in files["PRESERVE_CORE.ps1"]  # worker stdin only
    assert b"preserve_unclean_stopped_source=$true" in files["PRESERVE_CORE.ps1"]
    assert b"p.unlink" not in files["preserve_driver.py"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,reason",
    [
        ("valid", None),
        ("running", "PRESERVE_SOURCE_NOT_STOPPED"),
        ("key-running", "PRESERVE_SOURCE_NOT_STOPPED"),
        ("restart", "PRESERVE_SOURCE_NOT_STOPPED"),
        ("unknown-role", "PRESERVE_SCOPE_CHANGED"),
        ("wrong-id", "PRESERVE_SCOPE_CHANGED"),
        ("external", "PRESERVE_EXTERNAL_CONSUMER"),
        ("key-volume", "PRESERVE_EXTERNAL_CONSUMER"),
        ("nested", "PRESERVE_SCOPE_CHANGED"),
        ("overlap", "PRESERVE_SCOPE_CHANGED"),
        ("changed", "COLD_SOURCE_CONFIGURATION_CHANGED"),
    ],
)
def test_stopped_scope_refuses_unsafe_sources(tmp_path, case, reason):
    materialize(tmp_path)
    runtime = dict(
        Type="volume",
        Name="runtime",
        Source="/vol/runtime",
        Destination="/app/demo-export-state",
        RW=True,
    )
    database = dict(
        Type="volume",
        Name="database",
        Source="/vol/database",
        Destination="/var/lib/postgresql",
        RW=True,
    )
    records = []
    for ident, role in zip(
        "abcdef1", ("api", "db", "api", "api", "alerts", "web", "key-session"), strict=True
    ):
        records.append(
            dict(
                id=ident * 64,
                name="/hybrid-windows-api-1" if ident == "a" else "/private",
                image="sha256:" + "9" * 64,
                labels={
                    "com.docker.compose.project": "hybrid-windows",
                    "com.docker.compose.service": role,
                },
                mounts=[runtime.copy()]
                if ident in "acd"
                else ([database.copy()] if ident == "b" else []),
                running=False,
                paused=False,
                dead=False,
                restarting=False,
                restart=dict(Name="no", MaximumRetryCount=0),
                networks={},
            )
        )
    if case in ("running", "key-running"):
        records[0 if case == "running" else 6]["running"] = True
    if case == "restart":
        records[0]["restart"]["Name"] = "always"
    if case == "unknown-role":
        records[4]["labels"]["com.docker.compose.service"] = "other"
    if case == "external":
        records.append(dict(records[2], id="2" * 64, labels={}))
    if case == "key-volume":
        records[6]["mounts"] = [runtime.copy()]
    if case == "nested":
        records[0]["mounts"].append(dict(runtime, Destination="/app/demo-export-state/nested"))
    if case == "overlap":
        for r in records:
            for m in r["mounts"]:
                if m["Name"] == "runtime":
                    m["Source"] = "/vol/database/child"
    (tmp_path / "records.json").write_text(json.dumps(records))
    cfg = dict(
        api_container_id=("3" if case == "wrong-id" else "a") * 64, database_container_id="b" * 64
    )
    (tmp_path / "CONFIG.json").write_text(json.dumps(cfg))
    script = tmp_path / "test.ps1"
    script.write_text(
        """
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/CONTEXT.ps1"
. "$PSScriptRoot/COMMON.ps1"
. "$PSScriptRoot/PRESERVE_CORE.ps1"
$script:records=@(Get-Content -Raw "$PSScriptRoot/records.json" | ConvertFrom-Json)
function Container-Ids { return @($script:records | ForEach-Object {$_.id} | Sort-Object) }
function Read-Container([string]$Id) { return ($script:records | Where-Object {$_.id -ceq $Id} | ConvertTo-Json -Depth 12 | ConvertFrom-Json) }
function Docker-Lines([string[]]$Arguments,[int]$Maximum=128) {
    if ($Arguments[0] -ceq 'volume' -and $Arguments[1] -ceq 'inspect') { return @('"local"','"local"','null') }
    throw 'UNEXPECTED_DOCKER_CALL'
}
"""
        + (
            "function Assert-SourceSnapshot { throw 'COLD_SOURCE_CONFIGURATION_CHANGED' }\n"
            if case == "changed"
            else ""
        )
        + "Invoke-StoppedPreservation -Root $PSScriptRoot -CheckOnly | ForEach-Object { if ($_ -is [int]) {exit $_} else {Write-Output $_} }\n"
    )
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(script)],
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
    assert not result.stderr, result.stderr
    assert result.returncode == (2 if reason else 0), result.stdout
    if reason:
        assert json.loads(result.stdout.splitlines()[-1])["failure_category"] == reason
    else:
        assert "STOPPED_SCOPE_CHECKED_NO_SOURCE_START_NO_BACKUP_NO_ORDERS" in result.stdout


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("case", ["no-optin", "both-modes", "transfer-tampered"])
def test_launcher_refuses_before_any_docker_or_storage(tmp_path, case):
    materialize(tmp_path)
    manifest = (tmp_path / "TRANSFER_MANIFEST.json").read_bytes()
    if case == "transfer-tampered":
        (tmp_path / "preserve_driver.py").write_bytes(b"PRIVATE_SENTINEL")
    mode = "-ExecutePreservation -CheckOnly" if case == "both-modes" else ""
    script = tmp_path / "test.ps1"
    script.write_text(
        "function docker {throw 'DOCKER_MUST_NOT_RUN'}\n"
        f"& '$PSScriptRoot/RUN_PRESERVE.ps1' -ManifestSHA256 {hashlib.sha256(manifest).hexdigest()} {mode}\nexit $LASTEXITCODE\n".replace(
            "'$PSScriptRoot/", '"$PSScriptRoot/'
        ).replace("RUN_PRESERVE.ps1'", 'RUN_PRESERVE.ps1"')
    )
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(script)],
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
    assert result.returncode == 2 and not result.stderr, result.stdout + result.stderr
    assert "DOCKER_MUST_NOT_RUN" not in result.stdout and "PRIVATE_SENTINEL" not in result.stdout
    report = json.loads(result.stdout.splitlines()[-1])
    assert report["failure_category"] == (
        "COLD_TRANSFER_OR_LAUNCHER_FAILED"
        if case == "transfer-tampered"
        else "PRESERVE_EXPLICIT_MODE_REQUIRED"
    )
