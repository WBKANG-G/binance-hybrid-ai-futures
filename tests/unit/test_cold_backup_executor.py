"""Executor package and native PowerShell lifecycle faults; synthetic Docker only."""

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import build_windows_cold_backup_bundle as builder
from scripts import build_windows_cold_backup_plan_bundle as plan_builder
from scripts.build_demo_read_review_bundle import archive

ROOT = Path(__file__).resolve().parents[2]
PWSH = Path("/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh")


def package():
    release = os.environ.get("COLD_EXECUTOR_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {p[len(builder.FOLDER) + 1 :]: z.read(p) for p in z.namelist()}
    real = builder.git

    def source(*args):
        if args[0] == "show":
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", source), patch.object(plan_builder, "git", source):
        return builder.package_files("HEAD")


def materialize(root):
    for name, body in package().items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)


def test_bundle_is_pinned_and_exactly_scoped_to_reviewed_sources():
    files = package()
    assert len(files) == 15
    assert archive(files) == archive(package())
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    assert len(manifest["files"]) == 13
    for p, h in manifest["files"].items():
        assert hashlib.sha256(files[p]).hexdigest() == h
    assert (
        hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest().encode()
        in files["RUN_BACKUP.cmd"]
    )
    assert b"-ExecuteColdBackup" in files["RUN_BACKUP.cmd"]
    assert b"if ($ManifestSHA256" not in files["CONTEXT.ps1"]
    config = json.loads(files["BACKUP_CONFIG.json"])
    assert len(config["api_container_id"]) == len(config["database_container_id"]) == 64
    assert b"--signal=SIGKILL" not in files["BACKUP_CORE.ps1"]
    assert b"'update'" not in files["BACKUP_CORE.ps1"] and b"'rm'" not in files["BACKUP_CORE.ps1"]
    assert b"Config.Env" not in files["BACKUP_CORE.ps1"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "scenario",
    [
        "valid",
        "no-optin",
        "acl-failed",
        "scope-changed",
        "policy-changed",
        "active-lease",
        "wrong-counts",
        "extra-private-header",
        "image-save-failed",
        "disk-full",
        "pg-exit-unclean",
        "pg-control-unclean",
        "worker-private-output",
        "worker-extra-field",
        "worker-xattr-failed",
        "copy-corrupted",
        "source-restarted",
        "graceful-timeout",
    ],
)
def test_executor_lifecycle_faults_never_restart_or_delete_sources(tmp_path, scenario):
    materialize(tmp_path)
    config = json.loads((tmp_path / "BACKUP_CONFIG.json").read_text())
    config.update(api_container_id="a" * 64, database_container_id="b" * 64)
    (tmp_path / "BACKUP_CONFIG.json").write_text(json.dumps(config))
    output = tmp_path / "private backup output"
    fake = tmp_path / "docker"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """
import hashlib,json,os,sys
from pathlib import Path
root=Path(os.environ['COLD_EXEC_CASE_DIR']); case=os.environ['COLD_EXEC_CASE']
a=sys.argv[1:]
with (root/'calls.jsonl').open('a') as f: f.write(json.dumps(a)+'\\n')
state_path=root/'docker-state.json'
state=json.loads(state_path.read_text()) if state_path.exists() else dict(stopped=[],helpers=[],worker_ran=False)
def save(): state_path.write_text(json.dumps(state))
def item(ident):
    role={'a':'api','b':'db','c':'api','d':'api','e':'alerts','f':'web','1':'key-session'}[ident[0]]
    runtime=dict(Type='volume',Name='private_runtime',Source='/private/runtime',Destination='/app/demo-export-state',RW=True)
    database=dict(Type='volume',Name='private_database',Source='/private/database',Destination='/var/lib/postgresql',RW=True)
    mounts=[runtime] if ident[0] in 'acd' else ([database] if ident[0]=='b' else [])
    running=ident[0] in 'ab1' and ident not in state['stopped']
    if case=='source-restarted' and state['worker_ran'] and ident[0]=='a': running=True
    return dict(id=ident,name='/hybrid-windows-api-1' if ident[0]=='a' else '/private',image='sha256:'+('d' if ident[0]=='b' else 'c')*64,labels={'com.docker.compose.project':'hybrid-windows','com.docker.compose.service':role},mounts=mounts,running=running,restarting=False,paused=False,dead=False,restart=dict(Name='no',MaximumRetryCount=0),networks={})
if a==['ps','--all','--quiet','--no-trunc']:
    print('\\n'.join([c*64 for c in 'abcdef1']+state['helpers'])); sys.exit(0)
if a[0]=='inspect':
    if a[2]=='{{.State.Running}}': print('false' if a[3] in state['stopped'] else 'true')
    elif a[2]=='{{.State.ExitCode}} {{.State.OOMKilled}}': print('137 false' if case=='pg-exit-unclean' else '0 false')
    else: print(json.dumps(item(a[-1])))
    sys.exit(0)
if a[:2]==['volume','inspect']:
    print(json.dumps(dict(driver='local',scope='local',options=None))); sys.exit(0)
if a[:2]==['exec','b'*64]: print('/var/lib/postgresql/18/docker'); sys.exit(0)
if a[:3]==['exec','-i','a'*64]:
    assert '-I' in a and '-B' in a
    sys.stdin.read()
    report=dict(status='PAUSED_NO_ACTIVE_LEASE',paused=True,active_lease=case=='active-lease',orders=35 if case=='wrong-counts' else 34,unresolved_requests=4,database_changed=False)
    if case=='extra-private-header': report['private']='PRIVATE_SENTINEL'
    print(json.dumps(report)); sys.exit(0)
if a[:2]==['image','inspect']: print(a[-1]+' 1000'); sys.exit(0)
if a[:2]==['image','save']:
    if case=='image-save-failed': sys.exit(2)
    Path(a[3]).write_bytes(b'IMAGE_COPY'); sys.exit(0)
if a[0]=='kill':
    assert a[1] in ['--signal=SIGTERM','--signal=SIGINT']
    state['stopped'].append(a[2]); save(); print(a[2]); sys.exit(0)
if a[0]=='create':
    ident=('3' if 'pg_controldata' in a else '4')*64
    assert '--network' in a and a[a.index('--network')+1]=='none'
    assert '--read-only' in a and '--pull=never' in a
    assert not any('/run/demo-keys' in v for v in a)
    for i,v in enumerate(a):
        if v=='--mount' and ('source=private_database' in a[i+1] or 'source=private_runtime' in a[i+1]): assert 'readonly' in a[i+1]
    state['helpers'].append(ident); save(); print(ident); sys.exit(0)
if a[:2]==['volume','create']: print(a[-1]); sys.exit(0)
if a[:2]==['start','--attach'] and a[-1]=='3'*64:
    print('Database cluster state: '+('in production' if case=='pg-control-unclean' else 'shut down')); sys.exit(0)
blobs={'database.tar':b'd'*20480,'runtime.tar':b'r'*10240,'manifest.json':b'{"synthetic":true}\\n'}
if a[:3]==['start','--attach','--interactive']:
    sys.stdin.read(); state['worker_ran']=True; save()
    if case=='worker-private-output': print('PRIVATE_SENTINEL'); sys.exit(0)
    report=dict(status='COLD_ARCHIVES_CREATED',failure_category='NONE',manifest_sha256=hashlib.sha256(blobs['manifest.json']).hexdigest(),archives={g:dict(sha256=hashlib.sha256(blobs[g+'.tar']).hexdigest(),bytes=len(blobs[g+'.tar'])) for g in ['database','runtime']},file_counts=dict(database=10,runtime=1),source_changed=False)
    if case=='worker-extra-field': report['private']='PRIVATE_SENTINEL'
    if case=='worker-xattr-failed': report.update(status='BLOCKED',failure_category='COLD_EXTENDED_METADATA_UNSUPPORTED')
    print(json.dumps(report)); sys.exit(2 if case=='worker-xattr-failed' else 0)
if a[0]=='cp':
    name=a[1].split('/')[-1]
    data=blobs[name]
    if case=='copy-corrupted' and name=='runtime.tar': data=b'corrupt'
    Path(a[2]).write_bytes(data); sys.exit(0)
sys.exit(9)
"""
    )
    fake.chmod(0o700)

    def safe(p):
        return "'" + str(p).replace("'", "''") + "'"

    harness = tmp_path / "harness.ps1"
    harness.write_text(f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$PSNativeCommandArgumentPassing='Legacy'
. {safe(tmp_path / "CONTEXT.ps1")}
. {safe(tmp_path / "BACKUP_CORE.ps1")}
function New-PrivateBackupDirectory([string]$Token) {{
    if ($env:COLD_EXEC_CASE -eq 'acl-failed') {{ throw 'COLD_BACKUP_ACL_INVALID' }}
    $null=[IO.Directory]::CreateDirectory({safe(output)})
    return {safe(output)}
}}
function Assert-PrivateBackupDirectory([string]$Path) {{ if ($Path -ne {safe(output)}) {{ throw 'COLD_BACKUP_ACL_INVALID' }} }}
function Assert-PrivateBackupFiles([string]$Directory) {{ }}
function Backup-FreeBytes([string]$Directory) {{ if ($env:COLD_EXEC_CASE -eq 'disk-full') {{ return 0L }}; return 100000000000L }}
function Read-BackupPlan([string]$Root) {{
    $items=@((Container-Ids) | ForEach-Object {{ Read-Container $_ }})
    $participants=@()
    foreach ($item in $items) {{
        $role=Service $item
        if ($role -eq 'key-session') {{ continue }}
        $participants+=,[pscustomobject]@{{container_id=$item.id;service=$role;running=$item.running;restart_policy='no';restart_maximum_retry_count=0;uses_database_volume=($role -eq 'db');uses_runtime_volume=($role -eq 'api')}}
    }}
    if ($env:COLD_EXEC_CASE -eq 'policy-changed') {{ $participants[0].restart_policy='always' }}
    $apiId='a'*64
    if ($env:COLD_EXEC_CASE -eq 'scope-changed') {{ $apiId='f'*64 }}
    return [pscustomobject]@{{api_container_id=$apiId;database_container_id=('b'*64);postgresql_major=18;excluded_key_session_containers=1;participants=$participants;database_mount_destination='/var/lib/postgresql';source_context_sha256=(Digest ($items | ConvertTo-Json -Depth 12 -Compress))}}
}}
if ($env:COLD_EXEC_CASE -eq 'graceful-timeout') {{
    function Request-GracefulSourceStop([string]$Id,[string]$Signal,[int]$Seconds) {{
        $null=Backup-Docker @('kill',('--signal='+$Signal),$Id) 1
        throw 'COLD_GRACEFUL_STOP_TIMEOUT'
    }}
}}
$code=$null
Invoke-ColdBackup -Root {safe(tmp_path)} {"" if scenario == "no-optin" else "-ExecuteColdBackup"} | ForEach-Object {{ if ($_ -is [int]) {{ $code=$_ }} else {{ Write-Output $_ }} }}
exit $code
""")
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        timeout=30,
        env=dict(
            os.environ,
            PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
            COLD_EXEC_CASE=scenario,
            COLD_EXEC_CASE_DIR=str(tmp_path),
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
            POWERSHELL_TELEMETRY_OPTOUT="1",
        ),
    )
    assert not result.stderr, result.stderr
    assert "PRIVATE_SENTINEL" not in result.stdout
    calls = (
        [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
        if (tmp_path / "calls.jsonl").exists()
        else []
    )
    assert not any(row[0] in {"rm", "restart", "update"} for row in calls)
    assert not any(row[0] == "start" and row[-1] in {"a" * 64, "b" * 64} for row in calls)
    kills = [row for row in calls if row[0] == "kill"]
    if scenario == "valid":
        assert result.returncode == 0, result.stdout
        assert "COLD_BACKUP_SAVED_LOCAL_VERIFIED_KEEP_STOPPED_NO_ORDERS" in result.stdout
        assert [r[-1] for r in kills] == ["a" * 64, "b" * 64]
        receipt = json.loads((output / "BACKUP_RECEIPT.json").read_text())
        assert receipt["source_services_stopped"] and not receipt["automatic_resume"]
        assert not receipt["source_data_archive_restore_tested"]
    else:
        assert result.returncode == 2, result.stdout
        assert "COLD_BACKUP_SAVED_LOCAL_VERIFIED" not in result.stdout
        failed = json.loads(
            next(line for line in result.stdout.splitlines() if line.startswith("{"))
        )
        expected = {
            "no-optin": "COLD_EXPLICIT_EXECUTION_REQUIRED",
            "acl-failed": "COLD_BACKUP_ACL_INVALID",
            "scope-changed": "COLD_REVIEWED_SCOPE_CHANGED",
            "policy-changed": "COLD_RESTART_POLICY_OR_SCOPE_CHANGED",
            "active-lease": "COLD_PAUSE_OR_LEASE_CHECK_BLOCKED",
            "wrong-counts": "COLD_REVIEWED_ACCOUNT_COUNTS_CHANGED",
            "extra-private-header": "COLD_PAUSE_OR_LEASE_CHECK_BLOCKED",
            "image-save-failed": "COLD_DOCKER_COMMAND_FAILED",
            "disk-full": "COLD_HOST_SPACE_INSUFFICIENT",
            "pg-exit-unclean": "COLD_DATABASE_NOT_CLEANLY_STOPPED",
            "pg-control-unclean": "COLD_PG_CONTROL_NOT_SHUT_DOWN",
            "worker-private-output": "COLD_BACKUP_UNAVAILABLE",
            "worker-extra-field": "COLD_WORKER_OUTPUT_INVALID",
            "worker-xattr-failed": "COLD_EXTENDED_METADATA_UNSUPPORTED",
            "copy-corrupted": "COLD_LOCAL_ARCHIVE_HASH_MISMATCH",
            "source-restarted": "COLD_SOURCE_RUNNING_STATE_CHANGED",
            "graceful-timeout": "COLD_GRACEFUL_STOP_TIMEOUT",
        }
        assert failed["failure_category"] == expected[scenario], result.stdout
    if scenario in {
        "no-optin",
        "acl-failed",
        "scope-changed",
        "policy-changed",
        "active-lease",
        "wrong-counts",
        "extra-private-header",
        "image-save-failed",
        "disk-full",
    }:
        assert not kills
    elif scenario == "graceful-timeout":
        assert [r[-1] for r in kills] == ["a" * 64]
    else:
        assert [r[-1] for r in kills] == ["a" * 64, "b" * 64]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_transfer_tamper_blocks_before_any_docker_call(tmp_path):
    materialize(tmp_path)
    digest = hashlib.sha256((tmp_path / "TRANSFER_MANIFEST.json").read_bytes()).hexdigest()
    (tmp_path / "account_header.py").write_bytes(b"TAMPERED_PRIVATE_DATA")
    fake = tmp_path / "docker"
    fake.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(tmp_path / 'called')!r}).touch()\n"
    )
    fake.chmod(0o700)
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(tmp_path / "RUN_BACKUP.ps1"),
            "-ManifestSHA256",
            digest,
            "-ExecuteColdBackup",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env=dict(
            os.environ,
            PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
        ),
    )
    assert result.returncode == 2 and not result.stderr
    assert "TAMPERED_PRIVATE_DATA" not in result.stdout
    assert not (tmp_path / "called").exists()


@pytest.mark.skipif(
    not PWSH.exists() or os.name == "nt", reason="Linux PowerShell platform guard test"
)
def test_windows_acl_guard_fails_before_storage_operations_on_linux(tmp_path):
    materialize(tmp_path)
    safe = str(tmp_path / "BACKUP_CORE.ps1").replace("'", "''")
    script = tmp_path / "platform.ps1"
    script.write_text(
        f". '{safe}'\ntry {{ New-PrivateBackupDirectory 'synthetic' }} catch {{ Write-Output $_.Exception.Message; exit 2 }}\n"
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
    assert result.returncode == 2 and not result.stderr
    assert result.stdout.strip() == "COLD_WINDOWS_ACL_REQUIRED"
