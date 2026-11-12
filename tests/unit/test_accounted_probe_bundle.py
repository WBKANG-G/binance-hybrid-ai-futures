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


def test_deterministic_archive_pins_installed_code_and_isolates_worker(tmp_path, monkeypatch):
    def git(*args):
        if args[0] == "rev-parse":
            return b"a" * 40
        path = args[1].split(":", 1)[1]
        return Path(path).read_bytes()

    monkeypatch.setattr(builder, "git", git)
    first = builder.build("ref", tmp_path / "one")
    second = builder.build("ref", tmp_path / "two")
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as z:
        prefix = "windows-demo-accounted-read-probe-20261112/"
        manifest = json.loads(z.read(prefix + "TRANSFER_MANIFEST.json"))
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(z.read(prefix + name)).hexdigest() == digest
        cmd = z.read(prefix + "RUN_WORKER.cmd").decode()
        assert cmd.count("docker exec") == 1 and "python -I" in cmd and "--run-two-gets" in cmd
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
@pytest.mark.parametrize("case", ["success", "partial", "identity", "tamper"])
def test_powershell_validates_identity_and_never_retries(tmp_path, case):
    root = tmp_path / "probe with spaces"
    root.mkdir()
    for name, source in [
        ("PROBE.ps1", "tools/windows-demo-accounted-probe/PROBE.ps1"),
        ("INSTALL_INSPECTION.ps1", "tools/windows-demo-read-install/INSTALL_INSPECTION.ps1"),
    ]:
        (root / name).write_bytes(Path(source).read_bytes())
    report = dict(
        accounted_read_probe="TWO_GETS_AUDITED_KEEP_PAUSED"
        if case == "success"
        else "BLOCKED_KEEP_PAUSED",
        installed_transport_verified=True,
        orders_enabled=False,
        live_trading_enabled=False,
        execution_approved=False,
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
        + str(root / "PROBE.ps1")
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
    assert ("TWO_DEMO_GETS_AUDITED_KEEP_PAUSED_NO_ORDERS" in result.stdout) == (case == "success")
