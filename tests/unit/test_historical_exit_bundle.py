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


def test_exit_archive_pins_one_followup_operation(tmp_path, monkeypatch):
    def git(*args):
        if args[0] == "rev-parse":
            return b"a" * 40
        return Path(args[1].split(":", 1)[1]).read_bytes()

    monkeypatch.setattr(builder, "git", git)
    artifact = builder.build("ref", tmp_path, exit_followup=True)
    prefix = "windows-demo-historical-exit-evidence-20261113/"
    with zipfile.ZipFile(artifact) as z:
        assert prefix + "RUN_HISTORICAL_EXIT.cmd" in z.namelist()
        assert prefix + "RUN_DAY13_STATUS.cmd" not in z.namelist()
        assert prefix + "RUN_HISTORICAL_EXIT_STATUS.cmd" in z.namelist()
        assert b"%2" in z.read(prefix + "RUN_WORKER.cmd")
        manifest = json.loads(z.read(prefix + "TRANSFER_MANIFEST.json"))
        for n, sha in manifest["files"].items():
            assert hashlib.sha256(z.read(prefix + n)).hexdigest() == sha
        with zipfile.ZipFile(io.BytesIO(z.read(prefix + "candidate.zip"))) as p:
            pins = json.loads(p.read("PROBE_MANIFEST.json"))
            assert len(pins["installed_files"]) == 94
            assert len(p.namelist()) == 7
            assert "scripts/review_demo_historical_requests.py" in pins["candidate_files"]
            assert "scripts/review_demo_post_adoption.py" in pins["candidate_files"]
            assert "COST_EVIDENCE.json" in pins["candidate_files"]
            for n, sha in pins["candidate_files"].items():
                assert hashlib.sha256(p.read(n)).hexdigest() == sha
    with pytest.raises(ValueError):
        builder.build("ref", tmp_path, exit_followup=True, historical=True)


@pytest.mark.skipif(not Path(PWSH).exists(), reason="PowerShell required")
@pytest.mark.parametrize(
    "failed,status,missing",
    [(False, False, False), (False, False, True), (False, True, False), (True, False, False)],
)
def test_runner_invokes_one_action_without_repair_or_retry(tmp_path, failed, status, missing):
    root = tmp_path / "gate with spaces"
    root.mkdir()
    for n, source in [
        ("HISTORICAL_EXIT.ps1", "tools/windows-demo-accounted-probe/HISTORICAL_EXIT.ps1"),
        ("INSTALL_INSPECTION.ps1", "tools/windows-demo-read-install/INSTALL_INSPECTION.ps1"),
    ]:
        (root / n).write_bytes(Path(source).read_bytes())
    observed = 1
    report = dict(
        historical_request_evidence="OBSERVED_KEEP_PAUSED",
        installed_control_files_verified=True,
        installed_transport_verified=True,
        financial_ledger_unchanged_verified=True,
        audit_history_verified=True,
        prior_resource_rows_preserved=True,
        orders_enabled=False,
        live_trading_enabled=False,
        execution_approved=False,
        order_writes=0,
        reconciliation_performed=False,
        request_states_unchanged=True,
        capacity_shortfall_resolved=False,
        full_history_proof_replaced=False,
        requests_observed=observed,
        requests_unqueried=0,
        previous_historical_evidence_verified=True,
        evidence_scope="PREVIOUSLY_UNQUERIED_EXIT_ONLY",
        attempts=2 + observed,
        http_attempts=0 if status else 2 + observed,
        status_read_only=status,
        credential_session_accessed=not status,
    )
    ops = tmp_path / "ops"
    body = (
        '#!/bin/sh\necho "$2" >> '
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
        + str(root / "HISTORICAL_EXIT.ps1")
        + ("' -StatusOnly -ManifestSHA256 " if status else "' -ManifestSHA256 ")
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
    assert ops.read_text().splitlines() == [
        "--historical-exit-status" if status else "--historical-exit-evidence"
    ]
    assert ("HISTORICAL_EXIT_EVIDENCE_RECORDED_KEEP_PAUSED_NO_ORDERS" in result.stdout) == (
        not failed
    )
    assert "PRIVATE" not in result.stdout + result.stderr
