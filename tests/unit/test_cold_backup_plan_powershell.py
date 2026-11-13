"""Windows-compatible read-only plan: native PowerShell -File with fake Docker."""

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import build_windows_cold_backup_plan_bundle as builder
from scripts.build_demo_read_review_bundle import archive

ROOT = Path(__file__).resolve().parents[2]
PWSH = Path("/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh")


def package():
    release = os.environ.get("COLD_PLAN_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {name[len(builder.FOLDER) + 1 :]: z.read(name) for name in z.namelist()}
    real = builder.git

    def read(*args):
        if args[0] == "show":
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def test_plan_package_has_no_mutation_candidate_and_is_pinned():
    files = package()
    assert set(files) == {
        "PLAN.ps1",
        "PLAN_CONFIG.json",
        "README_KO.md",
        "TRANSFER_MANIFEST.json",
        "RUN_PLAN.cmd",
    }
    assert archive(files) == archive(package())
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    for name, digest in manifest["files"].items():
        assert hashlib.sha256(files[name]).hexdigest() == digest
    assert (
        hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest().encode()
        in files["RUN_PLAN.cmd"]
    )
    for forbidden in (
        b"Config.Env",
        b"docker stop",
        b"docker update",
        b"docker run",
        b"docker cp",
        b"cold_backup_candidate",
        b"PGPASSWORD",
        b"POSTGRES_PASSWORD",
        b"'sh','-c'",
        b"printf",
    ):
        assert forbidden not in files["PLAN.ps1"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell 7 executable unavailable")
@pytest.mark.parametrize(
    "scenario",
    [
        "valid",
        "unrelated-paused",
        "tampered-transfer",
        "unknown-service",
        "foreign-volume-user",
        "foreign-db-network",
        "nested-runtime",
        "extra-db-mount",
        "db-bind",
        "volume-options",
        "wrong-pgdata",
        "wrong-role",
        "tool-version-mismatch",
        "private-raw-json",
        "changed-source",
        "changed-container-list",
        "key-scope",
        "missing-db",
        "api-stopped",
        "restarting-source",
        "missing-env",
        "duplicate-env-output",
        "unsupported-server",
    ],
)
@pytest.mark.parametrize("argument_mode", ["Standard", "Legacy"])
def test_native_runner_is_read_only_and_sanitizes_topology_failures(
    tmp_path, scenario, argument_mode
):
    files = package()
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    if scenario == "tampered-transfer":
        (tmp_path / "PLAN.ps1").write_bytes(files["PLAN.ps1"] + b"\n# PRIVATE_NEVER_ECHO\n")
    fake = tmp_path / "docker"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """
import json,os,sys
from pathlib import Path
root=Path(os.environ['COLD_PLAN_CASE_DIR'])
case=os.environ['COLD_PLAN_CASE']
args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as f: f.write(json.dumps(args)+'\\n')
calls=[json.loads(x) for x in (root/'calls.jsonl').read_text().splitlines()]
ids=[c*64 for c in 'abcdef']
if case=='missing-db': ids.remove('b'*64)
if case in {'foreign-volume-user','foreign-db-network','unrelated-paused'}: ids.append('1'*64)
if args==['ps','--all','--quiet','--no-trunc']:
    if case=='changed-container-list' and sum(x==args for x in calls)>1: ids.append('2'*64)
    print('\\n'.join(ids)); sys.exit(0)
if args[:2]==['volume','inspect']:
    print(json.dumps(dict(driver='local',scope='local',options={'PRIVATE_OPTION':'PRIVATE_NEVER_ECHO'} if case=='volume-options' else None))); sys.exit(0)
if args[0]=='inspect':
    ident=args[-1]
    if case=='private-raw-json': print('PRIVATE_NEVER_ECHO'); sys.exit(0)
    role={'a':'api','b':'db','c':'alerts','d':'web','e':'key-session','f':'api','1':'foreign'}[ident[0]]
    def mount(name,target): return dict(Type='volume',Name=name,Source='/var/lib/docker/volumes/'+name+'/_data',Destination=target,RW=True)
    runtime=mount('PRIVATE_RUNTIME','/app/demo-export-state')
    database=mount('PRIVATE_DATABASE','/var/lib/postgresql')
    key=mount('PRIVATE_KEY_RAM','/run/demo-keys')
    mounts={'a':[runtime,key],'b':[database],'c':[key],'d':[],'e':[key],'f':[runtime,key],'1':[]}[ident[0]]
    if case=='nested-runtime' and ident[0]=='a': mounts.append(mount('PRIVATE_NESTED','/app/demo-export-state/child'))
    if case=='extra-db-mount' and ident[0]=='b': mounts.append(mount('PRIVATE_CONFIG','/etc/postgresql'))
    if case=='db-bind' and ident[0]=='b': mounts[0]['Type']='bind'
    if case=='foreign-volume-user' and ident[0]=='1': mounts=[database]
    if case=='key-scope' and ident[0]=='a': mounts[1]['Source']=runtime['Source']
    count=sum(x[0]=='inspect' and x[-1]==ident for x in calls)
    labels={'com.docker.compose.project':'hybrid-windows' if ident[0]!='1' else 'PRIVATE_FOREIGN_PROJECT','com.docker.compose.service':role,'PRIVATE_LABEL':'PRIVATE_NEVER_ECHO'}
    if case=='unknown-service' and ident[0]=='c': labels['com.docker.compose.service']='PRIVATE_NEVER_ECHO'
    network='d'*64 if ident[0]!='1' or case=='foreign-db-network' else 'e'*64
    value=dict(id=ident,name='/hybrid-windows-api-1' if ident[0]=='a' else '/PRIVATE_NAME',image='sha256:'+('c'*64 if case=='changed-source' and count>1 and ident[0]=='a' else 'b'*64),labels=labels,mounts=mounts,running=ident[0]!='f',restarting=case=='restarting-source' and ident[0]=='a',paused=case=='unrelated-paused' and ident[0]=='1',dead=False,restart=dict(Name='no',MaximumRetryCount=0),networks={'PRIVATE_NETWORK':dict(NetworkID=network)})
    if case=='api-stopped' and ident[0]=='a': value['running']=False
    print(json.dumps(value)); sys.exit(0)
if args[:2]==['exec','b'*64]:
    command=args[2:]
    allowed=[['printenv','PGDATA'],['printenv','POSTGRES_USER'],['printenv','POSTGRES_DB'],['postgres','--version'],['pg_controldata','--version']]
    assert command in allowed
    if case=='missing-env' and command==['printenv','POSTGRES_USER']: sys.exit(1)
    output={
      ('printenv','PGDATA'):'/PRIVATE_NEVER_ECHO' if case=='wrong-pgdata' else '/var/lib/postgresql/18/docker',
      ('printenv','POSTGRES_USER'):'PRIVATE_NEVER_ECHO!' if case=='wrong-role' else 'dev',
      ('printenv','POSTGRES_DB'):'hybrid',
      ('postgres','--version'):'postgres (PostgreSQL) '+('19.0' if case=='unsupported-server' else '18.6'),
      ('pg_controldata','--version'):'pg_controldata (PostgreSQL) '+('17.2' if case=='tool-version-mismatch' else '18.6')}
    print(output[tuple(command)])
    if case=='duplicate-env-output' and command==['printenv','PGDATA']: print('PRIVATE_NEVER_ECHO')
    sys.exit(0)
sys.exit(9)
"""
    )
    fake.chmod(0o700)
    launcher = tmp_path / "launch.ps1"
    safe_path = str(tmp_path / "PLAN.ps1").replace("'", "''")
    digest = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    launcher.write_text(
        f"$PSNativeCommandArgumentPassing='{argument_mode}'\n& '{safe_path}' -ManifestSHA256 '{digest}'\nexit $LASTEXITCODE\n"
    )
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(launcher),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=dict(
            os.environ,
            PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
            COLD_PLAN_CASE=scenario,
            COLD_PLAN_CASE_DIR=str(tmp_path),
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
            POWERSHELL_TELEMETRY_OPTOUT="1",
        ),
    )
    assert not result.stderr, result.stderr
    assert "PRIVATE" not in result.stdout + result.stderr
    calls = (
        [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text().splitlines()]
        if (tmp_path / "calls.jsonl").exists()
        else []
    )
    assert all(row[0] in {"inspect", "ps", "exec", "volume"} for row in calls)
    assert all(row[:2] == ["volume", "inspect"] for row in calls if row[0] == "volume")
    if scenario in {"valid", "unrelated-paused"}:
        assert result.returncode == 0, result.stdout
        assert "COLD_BACKUP_PLAN_OBSERVED_KEEP_PAUSED_NO_STOP_NO_BACKUP_NO_ORDERS" in result.stdout
        report = json.loads(next(x for x in result.stdout.splitlines() if x.startswith("{")))
        assert len(report["participants"]) == 5 and report["excluded_key_session_containers"] == 1
        assert report["postgresql_major"] == 18
        assert not report["backup_ready"] and not report["source_services_stopped"]
        assert report["postgres_probe"] == dict(
            pgdata="SUPPORTED_LAYOUT",
            database_user="VALID_IDENTIFIER",
            database_name="VALID_IDENTIFIER",
            server_version="SUPPORTED_VERSION",
            control_version="MATCHING_VERSION",
        )
    else:
        assert result.returncode == 2, result.stdout
        assert "BLOCKED_KEEP_PAUSED" in result.stdout
    if scenario in {
        "wrong-pgdata",
        "wrong-role",
        "tool-version-mismatch",
        "missing-env",
        "unsupported-server",
    }:
        failed = json.loads(next(x for x in result.stdout.splitlines() if x.startswith("{")))
        key, status = {
            "wrong-pgdata": ("pgdata", "UNSUPPORTED_LAYOUT"),
            "wrong-role": ("database_user", "INVALID_FORMAT"),
            "tool-version-mismatch": ("control_version", "VERSION_MISMATCH_OR_INVALID"),
            "missing-env": ("database_user", "READ_FAILED"),
            "unsupported-server": ("server_version", "UNSUPPORTED_VERSION"),
        }[scenario]
        assert failed["postgres_probe"][key] == status
    if scenario == "tampered-transfer":
        assert not calls
