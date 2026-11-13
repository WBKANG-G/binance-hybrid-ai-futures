"""Gate bundle carries only review helpers; native runner never requests mutation."""

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


def test_local_receipt_archive_is_readonly_and_pins_all_helpers(tmp_path, monkeypatch):
    def git(*args):
        if args[0] == "rev-parse":
            return b"a" * 40
        return Path(args[1].split(":", 1)[1]).read_bytes()

    monkeypatch.setattr(builder, "git", git)
    artifact = builder.build("ref", tmp_path, local_receipts=True)
    prefix = "windows-demo-historical-local-receipts-20261113/"
    with zipfile.ZipFile(artifact) as z:
        assert prefix + "RUN_HISTORICAL_LOCAL_RECEIPTS.cmd" in z.namelist()
        assert prefix + "RUN_DAY13_STATUS.cmd" not in z.namelist()
        assert b"--historical-local-receipts" in z.read(prefix + "RUN_WORKER.cmd")
        manifest = json.loads(z.read(prefix + "TRANSFER_MANIFEST.json"))
        for n, sha in manifest["files"].items():
            assert hashlib.sha256(z.read(prefix + n)).hexdigest() == sha
        with zipfile.ZipFile(io.BytesIO(z.read(prefix + "candidate.zip"))) as p:
            pins = json.loads(p.read("PROBE_MANIFEST.json"))
            assert len(pins["installed_files"]) == 94
            assert len(p.namelist()) == 8
            assert "scripts/review_demo_historical_receipts.py" in pins["candidate_files"]
            assert "scripts/review_demo_post_adoption.py" in pins["candidate_files"]
            assert "COST_EVIDENCE.json" in pins["candidate_files"]
            for n, sha in pins["candidate_files"].items():
                assert hashlib.sha256(p.read(n)).hexdigest() == sha
    with pytest.raises(ValueError):
        builder.build("ref", tmp_path, local_receipts=True, historical=True)


@pytest.mark.skipif(not Path(PWSH).exists(), reason="PowerShell required")
@pytest.mark.parametrize("failed", [False, True])
def test_gate_runner_calls_worker_once_without_repair_or_apply(tmp_path, failed):
    root = tmp_path / "gate with spaces"
    root.mkdir()
    for n, source in [
        (
            "HISTORICAL_LOCAL_RECEIPTS.ps1",
            "tools/windows-demo-accounted-probe/HISTORICAL_LOCAL_RECEIPTS.ps1",
        ),
        ("INSTALL_INSPECTION.ps1", "tools/windows-demo-read-install/INSTALL_INSPECTION.ps1"),
    ]:
        (root / n).write_bytes(Path(source).read_bytes())
    report = dict(
        local_historical_receipt_review="COMPLETED_KEEP_PAUSED",
        installed_control_files_verified=True,
        previous_exit_evidence_verified=True,
        all_reviewed_state_unchanged=True,
        http_attempts=0,
        modeled_weight_charged=0,
        credential_session_accessed=False,
        order_writes=0,
        orders_enabled=False,
        live_trading_enabled=False,
        execution_approved=False,
        reconciliation_performed=False,
        independent_remote_resolution_verified=False,
    )
    ops = tmp_path / "ops"
    body = (
        "#!/bin/sh\necho called >> "
        + str(ops)
        + "\necho '"
        + json.dumps(report)
        + "'\nexit "
        + ("1" if failed else "0")
        + "\n"
    )
    (root / "RUN_WORKER.cmd").write_text(body)
    (root / "RUN_WORKER.cmd").chmod(0o700)
    manifest = json.dumps(
        dict(files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()})
    ).encode()
    (root / "TRANSFER_MANIFEST.json").write_bytes(manifest)
    meta = tmp_path / "meta.json"
    meta.write_text(
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
        + str(meta)
        + "' }\n& '"
        + str(root / "HISTORICAL_LOCAL_RECEIPTS.ps1")
        + "' -ManifestSHA256 "
        + hashlib.sha256(manifest).hexdigest()
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
    assert result.returncode == (1 if failed else 0), result.stdout + result.stderr
    assert ops.read_text().splitlines() == ["called"]
    assert ("HISTORICAL_LOCAL_RECEIPTS_REVIEWED_KEEP_PAUSED_NO_ORDERS" in result.stdout) == (
        not failed
    )
    assert "PRIVATE" not in result.stdout + result.stderr
