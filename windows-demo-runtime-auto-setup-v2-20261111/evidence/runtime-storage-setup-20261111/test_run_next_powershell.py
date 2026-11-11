"""Native PS pipeline tests with mock Docker; storage IO safety is tested separately."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
PWSH = (
    os.environ.get("PWSH_EXECUTABLE")
    or shutil.which("pwsh")
    or "/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh"
)
LEGACY = r"""
if ($env:INSTALL_TEST_JSON_MODE -eq 'array_preserved') {
 function global:ConvertFrom-Json {
  [CmdletBinding()] param([Parameter(ValueFromPipeline=$true)][string]$InputObject)
  process {$parsed=Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $InputObject -NoEnumerate; if ($parsed -is [System.Array]) {Write-Output -NoEnumerate $parsed} else {$parsed}}
 }
}
function global:Start-Sleep { param([int]$Seconds) }
"""


@pytest.mark.parametrize("mode", ["enumerating", "array_preserved"])
@pytest.mark.parametrize(
    "case",
    [
        "missing_code",
        "code_installed",
        "configured",
        "prepare_failure",
        "finalize_failure",
        "unexpected_status",
    ],
)
def test_one_command_only_advances_through_verified_states(tmp_path, mode, case):
    packet = tmp_path / "PRIVATE_PATH with spaces"
    packet.mkdir()
    for name in ("RUN_NEXT.ps1", "configure_runtime_storage.py", "INSTALL_INSPECTION.ps1"):
        shutil.copy2(ROOT / name, packet / name)
    child = packet / "code-install"
    child.mkdir()
    (
        child / "INSTALL_CODE.ps1"
    ).write_text("""$state=Get-Content -Raw -LiteralPath $env:MOCK_STATE | ConvertFrom-Json
$state.installed=$true
$state | ConvertTo-Json | Set-Content -LiteralPath $env:MOCK_STATE
Write-Host 'MOCK_CODE_INSTALLATION'
exit 0
""")
    manifest = {
        str(p.relative_to(packet)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in packet.rglob("*")
        if p.is_file()
    }
    (packet / "TRANSFER_MANIFEST.json").write_text(json.dumps({"files": manifest}))
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "installed": case != "missing_code",
                "prepared": case == "configured",
                "done": case == "configured",
                "polls": 0,
            }
        )
    )
    log = tmp_path / "commands.jsonl"
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text("""#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['MOCK_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
assert args[:2]==['exec','-i']
sys.stdin.read()
phase=args[args.index('--phase')+1]
s=json.load(open(os.environ['MOCK_STATE']));case=os.environ['MOCK_CASE'];code=0
if case=='unexpected_status':status='UNEXPECTED'
elif phase=='prepare':
 assert '--apply-storage' in args
 if case=='prepare_failure':status='BLOCKED_KEEP_PAUSED';code=2
 else:s['prepared']=True;status='WAIT_STARTUP_HOLD'
elif phase=='finalize':
 assert '--apply-storage' in args
 if case=='finalize_failure':status='BLOCKED_KEEP_PAUSED';code=2
 else:s['done']=True;status='CONFIGURED_KEEP_PAUSED'
elif not s['installed']:status='CODE_INSTALL_REQUIRED';code=4
elif s['done']:status='CONFIGURED_KEEP_PAUSED'
elif s['prepared']:s['polls']+=1;status='READY_TO_FINALIZE'
else:status='PREPARATION_REQUIRED'
json.dump(s,open(os.environ['MOCK_STATE'],'w'))
print(json.dumps({'runtime_storage_setup':status,'execution_approved':False,'credentials_accessed':False}))
raise SystemExit(code)
""")
    docker.chmod(0o755)
    bootstrap = tmp_path / "bootstrap.ps1"
    bootstrap.write_text(
        LEGACY
        + "\n& '"
        + str(packet / "RUN_NEXT.ps1").replace("'", "''")
        + "'\nexit $LASTEXITCODE\n"
    )
    env = os.environ | {
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "MOCK_LOG": str(log),
        "MOCK_STATE": str(state),
        "MOCK_CASE": case,
        "INSTALL_TEST_JSON_MODE": mode,
        "XDG_CACHE_HOME": "/tmp/runtime-installer-cache",
        "XDG_CONFIG_HOME": "/tmp/runtime-installer-config",
        "XDG_DATA_HOME": "/tmp/runtime-installer-data",
    }
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(bootstrap)],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert "PRIVATE_" not in result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    phases = [c[c.index("--phase") + 1] for c in calls]
    if case in {"prepare_failure", "finalize_failure", "unexpected_status"}:
        assert result.returncode == 2, result.stdout + result.stderr
        assert "CODE_AND_STORAGE_CONFIGURED" not in result.stdout
        if case == "prepare_failure":
            assert "finalize" not in phases
        if case == "finalize_failure":
            assert phases.count("finalize") == 1
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "CODE_AND_STORAGE_CONFIGURED_KEEP_PAUSED_NO_ORDERS" in result.stdout
        if case == "configured":
            assert phases == ["status", "status"]
        else:
            assert phases.count("prepare") == 1 and phases.count("finalize") == 1
        assert ("MOCK_CODE_INSTALLATION" in result.stdout) == (case == "missing_code")
