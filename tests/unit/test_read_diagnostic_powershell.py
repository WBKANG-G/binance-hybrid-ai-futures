"""Read-only Windows wrapper preserves safe guard details and never invokes install."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PWSH = shutil.which("pwsh") or "/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh"
pytestmark = pytest.mark.skipif(not Path(PWSH).exists(), reason="PowerShell required")


@pytest.mark.parametrize("blocked", [False, True])
def test_diagnostic_uses_only_inspect_and_pre_guard(tmp_path, blocked):
    root = tmp_path / "diagnostic with spaces"
    root.mkdir()
    for name in ("DIAGNOSE.ps1", "INSTALL_INSPECTION.ps1"):
        (root / name).write_bytes(Path("tools/windows-demo-read-install", name).read_bytes())
    report = dict(
        read_code_install="BLOCKED_KEEP_PAUSED"
        if blocked
        else "PRECHECKED_READ_CODE_INSTALL_KEEP_PAUSED",
        failure_category="DATABASE_PERMISSION",
        sqlstate="42501",
        operation_stage="DATABASE_TABLE_READ",
        orders_enabled=False,
        execution_approved=False,
        live_trading_enabled=False,
    )
    command = root / "RUN_GUARD.cmd"
    command.write_text(
        "#!/bin/sh\ntest \"$2\" = 'pre' || exit 9\necho '"
        + json.dumps(report)
        + "'\nexit "
        + ("1" if blocked else "0")
        + "\n"
    )
    command.chmod(0o700)
    manifest = json.dumps(
        dict(files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()})
    ).encode()
    (root / "TRANSFER_MANIFEST.json").write_bytes(manifest)
    metadata = dict(
        Id="a" * 64,
        Name="/hybrid-windows-api-1",
        Config=dict(
            User="10001",
            WorkingDir="/app",
            Env=["API_KEY=PRIVATE_VALUE"],
            Labels={
                "com.docker.compose.project": "hybrid-windows",
                "com.docker.compose.service": "api",
            },
        ),
        State=dict(Running=True, Health=dict(Status="healthy")),
    )
    path = tmp_path / "inspect.json"
    path.write_text(json.dumps([metadata]))
    wrapper = tmp_path / "wrapper.ps1"
    wrapper.write_text(
        "$global:calls=New-Object 'System.Collections.Generic.List[string]'\nfunction global:docker { $global:LASTEXITCODE=0; $global:calls.Add(($args -join ' ')); Get-Content -Raw -LiteralPath '"
        + str(path)
        + "' }\n& '"
        + str(root / "DIAGNOSE.ps1")
        + "' -ManifestSHA256 "
        + hashlib.sha256(manifest).hexdigest()
        + "\n$code=$LASTEXITCODE\nWrite-Output ('CALLS:'+($global:calls | ConvertTo-Json -Compress))\nexit $code\n"
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
    assert result.returncode == (1 if blocked else 0), result.stdout + result.stderr
    assert '"sqlstate": "42501"' in result.stdout
    assert "PRIVATE" not in result.stdout + result.stderr
    assert 'CALLS:"inspect hybrid-windows-api-1"' in result.stdout
    assert " stop " not in result.stdout and " build " not in result.stdout
