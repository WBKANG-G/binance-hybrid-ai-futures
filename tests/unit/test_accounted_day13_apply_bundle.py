"""Pinned maintenance helper and Windows runner: apply once, then read-only status."""

import hashlib
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts import build_demo_accounted_probe_bundle as builder
from tests.unit.test_read_install_powershell import PWSH


def test_bundle_pins_ephemeral_adoption_helper_and_both_entrypoints(tmp_path, monkeypatch):
    def git(*args):
        if args[0] == "rev-parse":
            return b"a" * 40
        return Path(args[1].split(":", 1)[1]).read_bytes()

    monkeypatch.setattr(builder, "git", git)
    artifact = builder.build("ref", tmp_path, day13_apply=True)
    prefix = "windows-demo-accounted-day13-apply-20261113/"
    with zipfile.ZipFile(artifact) as z:
        manifest = json.loads(z.read(prefix + "TRANSFER_MANIFEST.json"))
        for n, sha in manifest["files"].items():
            assert hashlib.sha256(z.read(prefix + n)).hexdigest() == sha
        assert b"-StatusOnly" in z.read(prefix + "RUN_DAY13_STATUS.cmd")
        assert b"DAY13_APPLY.ps1" in z.read(prefix + "RUN_DAY13_APPLY.cmd")
        cmd = z.read(prefix + "RUN_WORKER.cmd")
        assert b"%2 %3" in cmd and max(map(len, cmd.splitlines())) < 8000
        with zipfile.ZipFile(io.BytesIO(z.read(prefix + "candidate.zip"))) as payload:
            assert len(payload.namelist()) == 4
            pins = json.loads(payload.read("PROBE_MANIFEST.json"))
            assert len(pins["installed_files"]) == 94
            for n, sha in pins["candidate_files"].items():
                assert hashlib.sha256(payload.read(n)).hexdigest() == sha
            assert "scripts/adopt_demo_accounted_day13.py" in pins["candidate_files"]
    with pytest.raises(ValueError, match="Choose one"):
        builder.build("ref", tmp_path, day13_apply=True, day13=True)


@pytest.mark.skipif(not Path(PWSH).exists(), reason="PowerShell required")
@pytest.mark.parametrize("case", ["success", "apply_failed", "status_failed", "status_only"])
def test_runner_does_not_reapply_or_retry_queries(tmp_path, case):
    root = tmp_path / "apply with spaces"
    root.mkdir()
    for name, source in [
        ("DAY13_APPLY.ps1", "tools/windows-demo-accounted-probe/DAY13_APPLY.ps1"),
        ("INSTALL_INSPECTION.ps1", "tools/windows-demo-read-install/INSTALL_INSPECTION.ps1"),
    ]:
        (root / name).write_bytes(Path(source).read_bytes())
    report = dict(
        accounted_day13_adoption="APPLIED_VERIFIED_KEEP_PAUSED",
        installed_adoption_modules_verified=True,
        orders_enabled=False,
        live_trading_enabled=False,
        execution_approved=False,
        day_adopted=True,
        period_keys_match=True,
        prior_financial_history_preserved=True,
        prior_receipts_and_files_preserved=True,
        application_http_attempts=8,
        application_modeled_weight_charged=131,
        application_audit_events_added=16,
        application_resource_checkpoints_added=16,
        financial_event_added=1,
        financial_outbox_added=1,
    )
    ops = tmp_path / "ops"
    shell = '#!/bin/sh\necho "$2" >> ' + str(ops) + '\ncase "$2" in\n'
    for op, count, failed in [
        ("--apply-day13", 8, case == "apply_failed"),
        ("--day13-adoption-status", 0, case == "status_failed"),
    ]:
        value = report | dict(http_attempts=count)
        if failed:
            value = dict(
                accounted_read_probe="BLOCKED_KEEP_PAUSED", application_may_have_completed=True
            )
        shell += (
            op + ")\necho '" + json.dumps(value) + "'\nexit " + ("1" if failed else "0") + "\n;;\n"
        )
    shell += "*) exit 99;;\nesac\n"
    (root / "RUN_WORKER.cmd").write_text(shell)
    (root / "RUN_WORKER.cmd").chmod(0o700)
    manifest = json.dumps(
        dict(files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()})
    ).encode()
    (root / "TRANSFER_MANIFEST.json").write_bytes(manifest)
    metadata = tmp_path / "meta.json"
    metadata.write_text(
        json.dumps(
            dict(
                Id="a" * 64,
                Name="/hybrid-windows-api-1",
                Config=dict(
                    User="10001",
                    WorkingDir="/app",
                    Env=["SECRET=PRIVATE"],
                    Labels={
                        "com.docker.compose.project": "hybrid-windows",
                        "com.docker.compose.service": "api",
                    },
                ),
                State=dict(Running=True, Health=dict(Status="healthy")),
            )
        )
    )
    wrapper = tmp_path / "wrapper.ps1"
    wrapper.write_text(
        "function global:docker { $global:LASTEXITCODE=0; Get-Content -Raw -LiteralPath '"
        + str(metadata)
        + "' }\n& '"
        + str(root / "DAY13_APPLY.ps1")
        + "' -ManifestSHA256 "
        + hashlib.sha256(manifest).hexdigest()
        + (" -StatusOnly" if case == "status_only" else "")
        + "\nexit $LASTEXITCODE\n"
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(wrapper)],
        capture_output=True,
        text=True,
        timeout=20,
        env=dict(
            os.environ,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
        ),
    )
    assert result.returncode == (0 if case in ("success", "status_only") else 1), (
        result.stdout + result.stderr
    )
    expected = (
        ["--day13-adoption-status"]
        if case == "status_only"
        else ["--apply-day13"]
        if case == "apply_failed"
        else ["--apply-day13", "--day13-adoption-status"]
    )
    assert ops.read_text().splitlines() == expected
    assert ("DAY13_APPLIED_AUDIT_AND_HISTORY_VERIFIED_KEEP_PAUSED_NO_ORDERS" in result.stdout) == (
        case in ("success", "status_only")
    )
    assert "PRIVATE" not in result.stdout + result.stderr
