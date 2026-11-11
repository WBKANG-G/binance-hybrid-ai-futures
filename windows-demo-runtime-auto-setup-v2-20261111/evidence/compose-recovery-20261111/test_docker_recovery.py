"""Lossless config projection and memory-only Docker Engine requests."""

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parent
PWSH = (
    os.environ.get("PWSH_EXECUTABLE")
    or shutil.which("pwsh")
    or "/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh"
)
ENV = os.environ | {
    "DOCKER_CONFIG": "/tmp/tp-budget-build-docker",
    "XDG_CACHE_HOME": "/tmp/runtime-installer-cache",
    "XDG_CONFIG_HOME": "/tmp/runtime-installer-config",
    "XDG_DATA_HOME": "/tmp/runtime-installer-data",
}
LEGACY = r"""
if ($env:RECOVERY_JSON_MODE -eq 'array_preserved') {
 function global:ConvertFrom-Json {
  [CmdletBinding()]param([Parameter(ValueFromPipeline=$true)][string]$InputObject)
  process {$value=Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $InputObject -NoEnumerate; if ($value -is [System.Array]) {Write-Output -NoEnumerate $value} else {$value}}
 }
}
"""


def pwsh(tmp_path, body, mode="enumerating", timeout=30):
    module = str(ROOT).replace("'", "''")
    script = tmp_path / ("case-" + uuid4().hex + ".ps1")
    script.write_text(
        LEGACY
        + f"\n$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n. '{module}/INSTALL_INSPECTION.ps1'\n. '{module}/DOCKER_RECOVERY.ps1'\n"
        + body
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        env=ENV | {"RECOVERY_JSON_MODE": mode},
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert "PRIVATE_" not in result.stdout
    return [json.loads(line) for line in result.stdout.splitlines() if line]


@pytest.fixture
def details():
    return {
        "Id": "c" * 64,
        "Name": "/hybrid-windows-api-1",
        "Image": "sha256:" + "a" * 64,
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "Config": {
            "Image": "old-image",
            "Hostname": "original-host",
            "User": "10001:10001",
            "WorkingDir": "/app",
            "Env": ["DUMMY_TOKEN=PRIVATE_한글", "LIVE_TRADING_ENABLED=false", "TRADING_MODE=PAPER"],
            "Cmd": ["python", "-m", "server"],
            "Entrypoint": None,
            "Healthcheck": {"Test": ["CMD", "python", "-V"], "Interval": 5000000000},
            "Labels": {
                "com.docker.compose.project": "hybrid-windows",
                "com.docker.compose.service": "api",
                "com.docker.compose.project.working_dir": "PRIVATE_MISSING_DIRECTORY",
                "com.docker.compose.project.config_files": "PRIVATE_MISSING_FILE",
            },
        },
        "HostConfig": {
            "AutoRemove": False,
            "Privileged": False,
            "PublishAllPorts": False,
            "ContainerIDFile": "",
            "VolumesFrom": None,
            "NetworkMode": "test-network",
            "PortBindings": {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}]},
            "Mounts": [
                {
                    "Type": "volume",
                    "Source": "data-volume",
                    "Target": "/app/demo-export-state",
                    "ReadOnly": False,
                }
            ],
        },
        "NetworkSettings": {
            "Networks": {
                "test-network": {
                    "Aliases": ["hybrid-windows-api-1", "api"],
                    "IPAMConfig": None,
                    "Links": None,
                    "DriverOpts": None,
                    "GwPriority": 0,
                }
            }
        },
        "Mounts": [
            {
                "Type": "volume",
                "Name": "data-volume",
                "Destination": "/app/demo-export-state",
                "RW": True,
            }
        ],
    }


@pytest.mark.parametrize("mode", ["enumerating", "array_preserved"])
def test_projection_preserves_private_env_mounts_network_ports_and_command(details, tmp_path, mode):
    fixture = tmp_path / "inspect.json"
    fixture.write_text(json.dumps(details))
    source = str(fixture).replace("'", "''")
    report = pwsh(
        tmp_path,
        f"""$d=Get-Content -Raw -LiteralPath '{source}' | ConvertFrom-Json
$p=Get-RecoveryCreatePlan -Details $d -TargetImage 'new-image'
@{{env_same=((ConvertTo-Json -InputObject $p.Env -Compress) -eq (ConvertTo-Json -InputObject $d.Config.Env -Compress));
 host_same=((ConvertTo-Json -InputObject $p.HostConfig -Depth 100 -Compress) -eq (ConvertTo-Json -InputObject $d.HostConfig -Depth 100 -Compress));
 command_same=((ConvertTo-Json -InputObject $p.Cmd -Compress) -eq (ConvertTo-Json -InputObject $d.Config.Cmd -Compress));
 original_unchanged=($d.Config.Image -eq 'old-image');image_changed=($p.Image -eq 'new-image');
 alias_count=@($p.NetworkingConfig.EndpointsConfig['test-network'].Aliases).Count}} | ConvertTo-Json -Compress
""",
        mode,
    )[0]
    assert report == {
        "env_same": True,
        "host_same": True,
        "command_same": True,
        "original_unchanged": True,
        "image_changed": True,
        "alias_count": 2,
    }


@pytest.mark.parametrize("mode", ["enumerating", "array_preserved"])
@pytest.mark.parametrize(
    "fault,reason",
    [
        ("name", "RECOVERY_IDENTITY_MISMATCH"),
        ("user", "RECOVERY_USER_UNSUPPORTED"),
        ("auto_remove", "RECOVERY_HOST_OPTIONS_UNSUPPORTED"),
        ("privileged", "RECOVERY_HOST_OPTIONS_UNSUPPORTED"),
        ("dynamic_port", "RECOVERY_DYNAMIC_PORT_UNSUPPORTED"),
        ("unrepresented_volume", "RECOVERY_UNREPRESENTED_VOLUME"),
        ("shadow_code", "RECOVERY_MOUNT_SHADOWS_CODE"),
        ("static_network", "RECOVERY_STATIC_NETWORK_UNSUPPORTED"),
        ("wrong_project", "RECOVERY_COMPOSE_IDENTITY_MISMATCH"),
    ],
)
def test_unsupported_metadata_blocks_before_engine_access(details, tmp_path, mode, fault, reason):
    if fault == "name":
        details["Name"] = "/other"
    elif fault == "user":
        details["Config"]["User"] = "root"
    elif fault == "auto_remove":
        details["HostConfig"]["AutoRemove"] = True
    elif fault == "privileged":
        details["HostConfig"]["Privileged"] = True
    elif fault == "dynamic_port":
        details["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostPort"] = ""
    elif fault == "unrepresented_volume":
        details["HostConfig"]["Mounts"] = []
    elif fault == "shadow_code":
        details["Mounts"][0]["Destination"] = "/app/services"
    elif fault == "static_network":
        details["NetworkSettings"]["Networks"]["test-network"]["IPAMConfig"] = {
            "IPv4Address": "172.20.0.6"
        }
    else:
        details["Config"]["Labels"]["com.docker.compose.project"] = "other"
    fixture = tmp_path / "inspect.json"
    fixture.write_text(json.dumps(details))
    path = str(fixture).replace("'", "''")
    result = pwsh(
        tmp_path,
        f"""$d=Get-Content -Raw -LiteralPath '{path}' | ConvertFrom-Json
try {{$p=Get-RecoveryCreatePlan -Details $d -TargetImage 'new-image'; @{{result='UNEXPECTED_PASS'}} | ConvertTo-Json -Compress}}
catch {{@{{reason=$_.Exception.Message}} | ConvertTo-Json -Compress}}
""",
        mode,
    )[0]
    assert result == {"reason": reason}


def test_http_error_body_is_not_exposed_and_chunked_utf8_is_decoded(tmp_path):
    report = pwsh(
        tmp_path,
        r"""
try {$null=Convert-RecoveryHttpResponse "HTTP/1.1 400 Bad Request`r`nContent-Length: 14`r`n`r`nPRIVATE_SECRET"; throw 'UNEXPECTED'}
catch {$reason=$_.Exception.Message}
$text='{"value":"한글"}'
$n=[Text.Encoding]::UTF8.GetByteCount($text)
$raw="HTTP/1.1 200 OK`r`nTransfer-Encoding: chunked`r`n`r`n"+$n.ToString('x')+"`r`n"+$text+"`r`n0`r`n`r`n"
$v=Convert-RecoveryHttpResponse $raw
@{reason=$reason;utf8_valid=($v.value -eq '한글')} | ConvertTo-Json -Compress
""",
    )[0]
    assert report == {"reason": "RECOVERY_ENGINE_REQUEST_REJECTED", "utf8_valid": True}


def test_real_engine_stdio_transport_without_request_files(tmp_path):
    report = pwsh(
        tmp_path,
        "$v=Invoke-RecoveryEngine -Method GET -Path '/version'\n@{valid=($v.ApiVersion -match '^1\\.[0-9]+$')} | ConvertTo-Json -Compress\n",
    )[0]
    assert report == {"valid": True}


def test_real_container_recreation_retains_env_volume_network_ports_and_backup(tmp_path):
    token = uuid4().hex[:14]
    name = "phase4-recovery-api-" + token
    candidate = "phase4-recovery-candidate-" + token
    backup = "phase4-recovery-backup-" + token
    network = "phase4-recovery-network-" + token
    volume = "phase4-recovery-volume-" + token
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    def docker(*args):
        r = subprocess.run(
            ["docker", *args], env=ENV, capture_output=True, text=True, check=False, timeout=35
        )
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def inspect(container):
        return json.loads(docker("inspect", container))[0]

    docker("network", "create", network)
    docker("volume", "create", volume)
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            "api",
            "--user",
            "10001",
            "-e",
            "DUMMY_TOKEN=PRIVATE_한글",
            "-e",
            "LIVE_TRADING_ENABLED=false",
            "-e",
            "TRADING_MODE=PAPER",
            "--label",
            "com.docker.compose.project=hybrid-windows",
            "--label",
            "com.docker.compose.service=api",
            "--label",
            "com.docker.compose.project.working_dir=PRIVATE_MISSING_DIRECTORY",
            "--label",
            "com.docker.compose.project.config_files=PRIVATE_MISSING_FILE",
            "-v",
            volume + ":/app/demo-export-state",
            "-p",
            f"127.0.0.1:{port}:8000",
            "--health-cmd",
            "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2)\"",
            "--health-interval",
            "1s",
            "--health-timeout",
            "3s",
            "--health-retries",
            "15",
            "--entrypoint",
            "python",
            "phase4-runtime-code-install:20261111",
            "-m",
            "http.server",
            "8000",
        )
        for _ in range(40):
            if inspect(name)["State"]["Health"]["Status"] == "healthy":
                break
            time.sleep(0.3)
        original = inspect(name)
        assert original["State"]["Health"]["Status"] == "healthy"
        docker(
            "exec",
            name,
            "python",
            "-c",
            "from pathlib import Path; Path('/app/demo-export-state/marker').write_text('PERSISTENT_TEST')",
        )
        fixture = tmp_path / "before.json"
        fixture.write_text(json.dumps(original))
        path = str(fixture).replace("'", "''")
        report = pwsh(
            tmp_path,
            f"""$d=Get-Content -Raw -LiteralPath '{path}' | ConvertFrom-Json
$p=Get-RecoveryCreatePlan -Details $d -TargetImage 'phase4-observation-lease-review:20261111' -ExpectedName '{name}'
$id=New-PreservedRecoveryContainer -Plan $p -Name '{candidate}'
@{{created_id=$id}} | ConvertTo-Json -Compress
""",
            timeout=35,
        )[0]
        assert inspect(name)["State"]["Running"] and not inspect(candidate)["State"]["Running"]
        docker("stop", "--time", "5", name)
        docker("rename", name, backup)
        docker("rename", candidate, name)
        docker("start", name)
        for _ in range(40):
            if inspect(name)["State"]["Health"]["Status"] == "healthy":
                break
            time.sleep(0.3)
        after = inspect(name)
        assert after["State"]["Health"]["Status"] == "healthy"
        assert after["Id"] == report["created_id"] and inspect(backup)["Id"] == original["Id"]
        assert not inspect(backup)["State"]["Running"]
        assert after["Config"]["Env"] == original["Config"]["Env"]
        assert after["HostConfig"] == original["HostConfig"]
        for k, v in original["Config"].items():
            if k != "Image":
                assert after["Config"][k] == v, k
        assert after["Mounts"] == original["Mounts"]
        assert (
            after["NetworkSettings"]["Networks"][network]["Aliases"]
            == original["NetworkSettings"]["Networks"][network]["Aliases"]
        )
        assert (
            docker(
                "exec",
                name,
                "python",
                "-c",
                "from pathlib import Path; print(Path('/app/demo-export-state/marker').read_text())",
            )
            == "PERSISTENT_TEST"
        )
    finally:
        for container in (name, candidate, backup):
            subprocess.run(
                ["docker", "rm", "-f", container], env=ENV, capture_output=True, check=False
            )
        subprocess.run(["docker", "volume", "rm", volume], env=ENV, capture_output=True, check=True)
        subprocess.run(
            ["docker", "network", "rm", network], env=ENV, capture_output=True, check=True
        )


@pytest.mark.parametrize("mode", ["enumerating", "array_preserved"])
@pytest.mark.parametrize("case", ["success", "create_rejected", "postcheck_rejected"])
def test_missing_compose_full_installer_preserves_old_until_candidate_and_restores_on_failure(
    details, tmp_path, mode, case
):
    import hashlib

    packet = tmp_path / "PRIVATE_PACKET with spaces"
    packet.mkdir()
    for name in ("INSTALL_CODE.ps1", "INSTALL_INSPECTION.ps1", "DOCKER_RECOVERY.ps1"):
        shutil.copy2(ROOT / name, packet / name)
    (packet / "guard_runtime_code_install.py").write_text(
        "# Test mock consumes this source without execution.\n"
    )
    (packet / "Dockerfile").write_text("FROM scratch\n")
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in packet.iterdir()}
    (packet / "TRANSFER_MANIFEST.json").write_text(json.dumps({"files": manifest}))
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "details": details,
                "old_name": "hybrid-windows-api-1",
                "old_running": True,
                "new_name": None,
                "new_running": False,
                "body": None,
                "new_id": "d" * 64,
                "case": case,
            }
        )
    )
    log = tmp_path / "commands.jsonl"
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "docker"
    executable.write_text("""#!/usr/bin/env python3
import copy,json,os,sys
args=sys.argv[1:]
with open(os.environ['MOCK_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
p=os.environ['MOCK_STATE'];s=json.load(open(p));old=s['details']['Id'];new=s['new_id']
def save():json.dump(s,open(p,'w'))
def get(query):
 if query in (old,s['old_name']):
  d=copy.deepcopy(s['details']);d['Name']='/'+s['old_name'];d['State']['Running']=s['old_running'];return d
 if s['body'] is not None and query in (new,s['new_name']):
  d=copy.deepcopy(s['details']);d.update(Id=new,Name='/'+s['new_name'],Image='sha256:'+'e'*64)
  d['Config']={k:v for k,v in s['body'].items() if k not in ('HostConfig','NetworkingConfig')}
  d['HostConfig']=s['body']['HostConfig'];d['State']['Running']=s['new_running'];return d
 raise SystemExit(1)
if args[:2]==['system','dial-stdio']:
 first=sys.stdin.buffer.readline().decode();headers={}
 while True:
  line=sys.stdin.buffer.readline()
  if line==b'\\r\\n':break
  k,v=line.decode().split(':',1);headers[k.lower()]=v.strip()
 raw=sys.stdin.buffer.read(int(headers['content-length']))
 code=200
 if first.startswith('GET /version '):data={'ApiVersion':'1.51'}
 elif s['case']=='create_rejected':code=400;data={'message':'PRIVATE_ENV_MUST_NOT_LEAK'}
 else:
  s['body']=json.loads(raw);s['new_name']=first.split('?name=')[1].split(' ')[0];data={'Id':new};code=201;save()
 body=json.dumps(data).encode();sys.stdout.buffer.write(('HTTP/1.1 '+str(code)+' OK\\r\\nContent-Type: application/json\\r\\nContent-Length: '+str(len(body))+'\\r\\nConnection: close\\r\\n\\r\\n').encode()+body);sys.stdout.buffer.flush()
elif args[0]=='inspect':
 d=get(args[-1])
 if '--format' not in args:print(json.dumps([d]))
 elif '{{.Image}}' in args:print(d['Image'])
 else:print(('true' if d['State']['Running'] else 'false')+'|healthy')
elif args[0]=='exec':
 sys.stdin.read();phase=args[args.index('--phase')+1]
 if phase=='post' and s['case']=='postcheck_rejected':
  print(json.dumps({'runtime_code_install':'BLOCKED_KEEP_PAUSED','stop_reason':'POSTCHECK_FAILED'}));raise SystemExit(2)
 print(json.dumps({'runtime_code_install':'PRECHECKED_CODE_ONLY_KEEP_PAUSED' if phase=='pre' else 'POSTCHECKED_CODE_ONLY_KEEP_PAUSED','state_snapshot_sha256':'b'*64}))
elif args[0]=='stop':
 if args[-1]==old:s['old_running']=False
 else:s['new_running']=False
 save();print(args[-1])
elif args[0]=='rename':
 if args[1]==old:s['old_name']=args[2]
 else:s['new_name']=args[2]
 save()
elif args[0]=='start':
 if args[1]==old:s['old_running']=True
 elif args[1]==s['new_name'] or args[1]==new:s['new_running']=True
 else:raise SystemExit(99)
 save();print(args[1])
elif args[0] in ('tag','build','rm'):print('MOCK_METADATA_ONLY')
else:raise SystemExit(98)
""")
    executable.chmod(0o755)
    bootstrap = tmp_path / "bootstrap.ps1"
    bootstrap.write_text(
        LEGACY
        + "\n& '"
        + str(packet / "INSTALL_CODE.ps1").replace("'", "''")
        + "'\nexit $LASTEXITCODE\n"
    )
    env = ENV | {
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "RECOVERY_JSON_MODE": mode,
        "MOCK_LOG": str(log),
        "MOCK_STATE": str(state),
    }
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(bootstrap)],
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert "PRIVATE_" not in result.stdout + result.stderr
    final = json.loads(state.read_text())
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    if case == "success":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PRESERVED_CONTAINER_CONFIGURATION_VERIFIED_BACKUP_RETAINED" in result.stdout
        assert final["new_name"] == "hybrid-windows-api-1" and final["new_running"]
        assert (
            final["old_name"].startswith("hybrid-windows-api-backup-") and not final["old_running"]
        )
        assert not any(c[0] == "compose" for c in calls)
        create = next(
            i
            for i, c in enumerate(calls)
            if c[:2] == ["system", "dial-stdio"]
            and i > next(j for j, x in enumerate(calls) if x[:2] == ["system", "dial-stdio"])
        )
        assert create < next(i for i, c in enumerate(calls) if c[0] == "stop")
    elif case == "create_rejected":
        assert result.returncode == 2, result.stdout + result.stderr
        report = json.loads(result.stdout.splitlines()[-1])
        assert (
            report["stop_reason"] == "RECOVERY_ENGINE_REQUEST_REJECTED"
            and not report["api_replacement_started"]
        )
        assert final["old_running"] and final["old_name"] == "hybrid-windows-api-1"
        assert not any(c[0] in ("stop", "rename", "start") for c in calls)
    else:
        assert result.returncode == 2, result.stdout + result.stderr
        report = json.loads(result.stdout.splitlines()[-1])
        assert (
            report["rollback_status"] == "ORIGINAL_API_RESTORED"
            and report["rollback_database_verified"]
        )
        assert final["old_running"] and final["old_name"] == "hybrid-windows-api-1"
        assert not final["new_running"] and final["new_name"].startswith(
            "hybrid-windows-api-failed-"
        )
    info = packet / "INSTALL_STATE.json"
    if info.exists():
        assert "PRIVATE_" not in info.read_text()
