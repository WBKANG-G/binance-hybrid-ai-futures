"""Actual PowerShell orchestrator with a synthetic Docker executable only."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PWSH = Path("/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh")


@pytest.mark.parametrize(
    "mode", ["success", "blocked", "invalid_json", "unsafe_flags", "bad_final", "tamper"]
)
def test_runner_never_reinstalls_restarts_or_retries_recovery(tmp_path, mode):
    if not PWSH.exists():
        pytest.skip("PowerShell is required for the orchestrator verification")
    src = Path(__file__).with_name("RUN_RECOVER.ps1")
    shutil.copy2(src, tmp_path / src.name)
    payload = tmp_path / "recover_unused_shared_clock.py"
    payload.write_text("# synthetic payload, never executed\n")
    manifest = {
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (payload, tmp_path / src.name)
        }
    }
    (tmp_path / "TRANSFER_MANIFEST.json").write_text(json.dumps(manifest))
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "docker"
    fake.write_text("""#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
sys.stdin.read()
p=Path(os.environ['MOCK_CALLS'])
a=json.loads(p.read_text()) if p.exists() else []
a.append(sys.argv[1:]);p.write_text(json.dumps(a))
mode=os.environ['MOCK_MODE']
if mode=='blocked':
 print(json.dumps({'runtime_clock_recovery':'BLOCKED_KEEP_PAUSED','stop_reason':'SHARED_CHECKPOINT_MISMATCH'}));sys.exit(2)
if mode=='invalid_json':print('malformed');sys.exit(0)
s='WAIT_STARTUP_HOLD' if len(a)==1 else 'RECOVERED_KEEP_PAUSED'
if mode=='bad_final' and len(a)>1:s='UNEXPECTED'
r={'runtime_clock_recovery':s,'startup_ready':len(a)>1,'execution_approved':mode=='unsafe_flags','orders_enabled':False,'live_trading_enabled':False}
print(json.dumps(r))
""")
    fake.chmod(0o700)
    calls = tmp_path / "calls.json"
    if mode == "tamper":
        payload.write_text("# changed after manifest creation\n")
    env = os.environ | {
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "MOCK_CALLS": str(calls),
        "MOCK_MODE": mode,
        "XDG_CACHE_HOME": "/tmp/runtime-installer-cache",
        "XDG_CONFIG_HOME": "/tmp/runtime-installer-config",
        "XDG_DATA_HOME": "/tmp/runtime-installer-data",
    }
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-Command",
            "function Start-Sleep { param([int]$Seconds) }; & './RUN_RECOVER.ps1'; exit $LASTEXITCODE",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )
    records = json.loads(calls.read_text()) if calls.exists() else []
    if mode == "success":
        assert (
            result.returncode == 0
            and "SHARED_CLOCK_RECOVERED_KEEP_PAUSED_NO_ORDERS" in result.stdout
        )
        assert len(records) == 3
    else:
        assert (
            result.returncode == 2
            and "SHARED_CLOCK_RECOVERED_KEEP_PAUSED_NO_ORDERS" not in result.stdout
        )
        assert len(records) == (0 if mode == "tamper" else 2 if mode == "bad_final" else 1)
    for i, record in enumerate(records):
        assert record[:5] == ["exec", "-i", "hybrid-windows-api-1", "python", "-"]
        assert record[5:] == (
            ["--phase", "recover", "--apply-clock-recovery"] if i == 0 else ["--phase", "status"]
        )
