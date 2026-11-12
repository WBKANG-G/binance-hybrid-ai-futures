"""Probe archive has pinned installed imports and exactly one native worker call."""

import base64
import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts import build_demo_accounted_probe_bundle as builder
from tests.unit.test_read_install_powershell import PWSH


@pytest.mark.parametrize("recover,flat_review", [(False, False), (True, False), (False, True)])
def test_deterministic_archive_pins_installed_code_and_isolates_worker(
    tmp_path, monkeypatch, recover, flat_review
):
    def git(*args):
        if args[0] == "rev-parse":
            return b"a" * 40
        path = args[1].split(":", 1)[1]
        return Path(path).read_bytes()

    monkeypatch.setattr(builder, "git", git)
    first = builder.build("ref", tmp_path / "one", recover=recover, flat_review=flat_review)
    second = builder.build("ref", tmp_path / "two", recover=recover, flat_review=flat_review)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as z:
        prefix = (
            "windows-demo-accounted-flat-review-20261112/"
            if flat_review
            else "windows-demo-accounted-clock-recovery-20261112/"
            if recover
            else "windows-demo-accounted-read-probe-20261112/"
        )
        manifest = json.loads(z.read(prefix + "TRANSFER_MANIFEST.json"))
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(z.read(prefix + name)).hexdigest() == digest
        cmd = z.read(prefix + "RUN_WORKER.cmd").decode()
        assert cmd.count("docker exec") == 1 and "python -I" in cmd
        assert ("%2 %3" if recover else "--review-flat" if flat_review else "--run-two-gets") in cmd
        assert max(map(len, cmd.splitlines())) < 8000
        loader = base64.b64decode(re.search(r"b64decode\('([^']+)'\)", cmd).group(1)).decode()
        assert 'sys.path.insert(0, "/app")' in loader
        assert "sys.path.insert(0, str(root))" not in loader
        assert "runpy.run_path" in loader and "run_module" not in loader
        with zipfile.ZipFile(io.BytesIO(z.read(prefix + "candidate.zip"))) as inner:
            pins = json.loads(inner.read("PROBE_MANIFEST.json"))
            assert len(pins["installed_files"]) == 94 and len(pins["runtime_modules"]) == 6
            assert len(inner.namelist()) == 3
            compile(inner.read("probe_fingerprint.py"), "helper", "exec")
            for name, digest in pins["candidate_files"].items():
                assert hashlib.sha256(inner.read(name)).hexdigest() == digest


@pytest.mark.skipif(not Path(PWSH).exists(), reason="PowerShell required")
@pytest.mark.parametrize("flat_review", [False, True])
@pytest.mark.parametrize("case", ["success", "partial", "identity", "tamper"])
def test_powershell_validates_identity_and_never_retries(tmp_path, case, flat_review):
    root = tmp_path / "probe with spaces"
    root.mkdir()
    runner = "FLAT_REVIEW" if flat_review else "PROBE"
    for name, source in [
        (runner + ".ps1", "tools/windows-demo-accounted-probe/" + runner + ".ps1"),
        ("INSTALL_INSPECTION.ps1", "tools/windows-demo-read-install/INSTALL_INSPECTION.ps1"),
    ]:
        (root / name).write_bytes(Path(source).read_bytes())
    report = dict(
        accounted_read_probe=(
            "FIVE_GETS_FLAT_OBSERVED_KEEP_PAUSED" if flat_review else "TWO_GETS_AUDITED_KEEP_PAUSED"
        )
        if case == "success"
        else "BLOCKED_KEEP_PAUSED",
        installed_transport_verified=True,
        orders_enabled=False,
        live_trading_enabled=False,
        execution_approved=False,
    )
    report.update(
        http_attempts=5,
        completed_reads=5,
        modeled_weight_charged=91,
        audit_events_added=10,
        resource_checkpoints_added=10,
        prior_two_get_receipt_verified=True,
        positions_empty_at_read=True,
        regular_orders_empty_at_read=True,
        algo_orders_empty_at_read=True,
        financial_ledger_unchanged_verified=True,
        snapshot_atomic=False,
    )
    counter = tmp_path / "worker-count"
    (root / "RUN_WORKER.cmd").write_text(
        "#!/bin/sh\necho called >> '"
        + str(counter)
        + "'\necho '"
        + json.dumps(report)
        + "'\nexit "
        + ("0" if case == "success" else "1")
        + "\n"
    )
    (root / "RUN_WORKER.cmd").chmod(0o700)
    manifest = json.dumps(
        dict(files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()})
    ).encode()
    (root / "TRANSFER_MANIFEST.json").write_bytes(manifest)
    if case == "tamper":
        (root / "RUN_WORKER.cmd").write_text("tamper")
    details = dict(
        Id="a" * 64,
        Name="/hybrid-windows-api-1",
        Config=dict(
            User="10001",
            WorkingDir="/app",
            Env=["SECRET=PRIVATE"],
            Labels={
                "com.docker.compose.project": "wrong" if case == "identity" else "hybrid-windows",
                "com.docker.compose.service": "api",
            },
        ),
        State=dict(Running=True, Health=dict(Status="healthy")),
    )
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps(details))
    wrapper = tmp_path / "wrapper.ps1"
    wrapper.write_text(
        "function global:docker { $global:LASTEXITCODE=0; Get-Content -Raw -LiteralPath '"
        + str(metadata)
        + "' }\n& '"
        + str(root / (runner + ".ps1"))
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
    assert result.returncode == (0 if case == "success" else 1), result.stdout + result.stderr
    assert "PRIVATE" not in result.stdout + result.stderr
    assert (
        counter.read_text().splitlines() == ["called"]
        if case in ("success", "partial")
        else not counter.exists()
    )
    assert (
        (
            "FIVE_DEMO_GETS_FLAT_OBSERVED_KEEP_PAUSED_NO_ORDERS"
            if flat_review
            else "TWO_DEMO_GETS_AUDITED_KEEP_PAUSED_NO_ORDERS"
        )
        in result.stdout
    ) == (case == "success")


@pytest.mark.skipif(not Path(PWSH).exists(), reason="PowerShell required")
@pytest.mark.parametrize(
    "case", ["success", "recover_blocked", "review_blocked", "probe_blocked", "ready"]
)
def test_recovery_runner_waits_read_only_then_sends_probe_once(tmp_path, case):
    root = tmp_path / "recovery with spaces"
    root.mkdir()
    for name, source in [
        ("RECOVER_AND_PROBE.ps1", "tools/windows-demo-accounted-probe/RECOVER_AND_PROBE.ps1"),
        ("INSTALL_INSPECTION.ps1", "tools/windows-demo-read-install/INSTALL_INSPECTION.ps1"),
    ]:
        (root / name).write_bytes(Path(source).read_bytes())
    base = dict(orders_enabled=False, live_trading_enabled=False, execution_approved=False)
    recovery = base | dict(
        accounted_clock_recovery="RECOVERED_KEEP_PAUSED"
        if case == "ready"
        else "WAIT_STARTUP_HOLD",
        startup_ready=case == "ready",
        installed_recovery_verified=True,
        http_attempts=0,
        modeled_weight_charged=0,
        financial_ledger_unchanged_verified=True,
        recovery_post_snapshot_sha256="b" * 64,
        shared_journal_changed=case != "ready",
    )
    ready = recovery | dict(
        accounted_clock_recovery="RECOVERED_KEEP_PAUSED",
        startup_ready=True,
        shared_journal_changed=False,
    )
    success = base | dict(
        accounted_read_probe="TWO_GETS_AUDITED_KEEP_PAUSED", installed_transport_verified=True
    )
    counter = tmp_path / "ops"
    shell = '#!/bin/sh\necho "$2" >> \'' + str(counter) + '\'\ncase "$2" in\n'
    for op, result, failed in [
        ("--recover-clock", recovery, case == "recover_blocked"),
        ("--review-clock", ready, case == "review_blocked"),
        ("--run-two-gets", success, case == "probe_blocked"),
    ]:
        if failed:
            result = base | dict(
                accounted_read_probe="BLOCKED_KEEP_PAUSED", stop_reason="SYNTHETIC_REFUSAL"
            )
        shell += (
            op + ")\necho '" + json.dumps(result) + "'\nexit " + ("1" if failed else "0") + "\n;;\n"
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
        + "' }\nfunction global:Start-Sleep { param($Seconds) if ($Seconds -ne 5) { throw 'BAD_WAIT' } }\n& '"
        + str(root / "RECOVER_AND_PROBE.ps1")
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
    assert result.returncode == (0 if case in ("success", "ready") else 1), (
        result.stdout + result.stderr
    )
    expected = ["--recover-clock"]
    if case != "recover_blocked" and case != "ready":
        expected += ["--review-clock"]
    if case in ("success", "ready", "probe_blocked"):
        expected += ["--run-two-gets"]
    assert counter.read_text().splitlines() == expected
    assert ("CLOCK_RECOVERED_TWO_DEMO_GETS_AUDITED_KEEP_PAUSED_NO_ORDERS" in result.stdout) == (
        case in ("success", "ready")
    )
