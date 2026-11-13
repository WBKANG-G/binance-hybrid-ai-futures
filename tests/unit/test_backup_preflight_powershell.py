"""Native PowerShell -File execution with a strict fake docker executable."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_backup_scope_preflight import WORKER, config, package

PWSH = Path("/tmp/runtime-installer-pwsh/opt/microsoft/powershell/7/pwsh")
pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell 7 executable unavailable")


def good_report():
    report = WORKER["base_report"]()
    report.update(
        backup_scope_preflight="READ_ONLY_SCOPE_OBSERVED_KEEP_PAUSED",
        stage="COMPLETE",
        failure_category="NONE",
        source_reference=config()["source_reference"],
        account_found=True,
        account_paused=True,
        active_lease_observed=False,
        database_configuration_used=True,
        database_scope="a" * 64,
        catalog={
            k: 0
            for k in (
                "functions",
                "triggers",
                "indexes",
                "constraints",
                "cross_schema_foreign_keys",
                "cross_schema_inheritance",
            )
        },
        storage=dict(
            root=WORKER["STORAGE"],
            metadata_sha256="b" * 64,
            entries=1,
            files=1,
            directories=0,
            logical_bytes=20,
            sqlite_wal_files=0,
            sqlite_shm_files=0,
            sqlite_journal_files=0,
            group_or_other_writable_entries=0,
            groups={
                k: dict(files=0, logical_bytes=0) for k in ("SHARED_WEIGHT", "ORDER_QUOTA", "OTHER")
            },
        ),
    )
    report["tables"] = [
        dict(
            name="read_response_evidence_events_v2",
            identity_sha256="c" * 64,
            kind="r",
            persistence="p",
            total_bytes=32768,
        ),
        dict(
            name="UNCLASSIFIED",
            identity_sha256="d" * 64,
            kind="S",
            persistence="p",
            total_bytes=8192,
        ),
    ]
    return report


@pytest.mark.parametrize(
    "scenario",
    [
        "valid",
        "tampered-transfer",
        "wrong-user",
        "missing-mount",
        "nested-mount",
        "read-only-mount",
        "changed-image",
        "changed-mount",
        "changed-container",
        "private-output",
        "private-extra-field",
        "private-table-name",
        "private-storage-name",
        "orders-enabled",
        "fake-backup-ready",
        "blocked-worker",
        "inconsistent-status",
        "huge-output",
        "bad-array",
        "missing-field",
        "float-count",
    ],
)
def test_runner_never_displays_private_or_unvalidated_output_or_mutates_docker(tmp_path, scenario):
    files = package()
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    report = good_report()
    if scenario == "private-extra-field":
        report["private_key"] = "PRIVATE_NEVER_ECHO"
    elif scenario == "private-table-name":
        report["tables"] = [
            dict(
                name="PRIVATE_NEVER_ECHO",
                identity_sha256="a" * 64,
                kind="r",
                persistence="p",
                total_bytes=0,
            )
        ]
    elif scenario == "private-storage-name":
        report["storage"]["root"] = "PRIVATE_NEVER_ECHO"
    elif scenario == "orders-enabled":
        report["orders_enabled"] = True
    elif scenario == "fake-backup-ready":
        report["backup_ready"] = True
    elif scenario == "blocked-worker":
        report.update(
            backup_scope_preflight="BLOCKED_KEEP_PAUSED",
            stage="DATABASE_CONFIGURATION",
            failure_category="PREFLIGHT_DATABASE_NOT_CONFIGURED",
        )
    elif scenario == "bad-array":
        report["tables"] = {}
    elif scenario == "missing-field":
        report.pop("metadata_only")
    elif scenario == "float-count":
        report["storage"]["files"] = 1.5
    output = "PRIVATE_NEVER_ECHO" if scenario == "private-output" else json.dumps(report)
    if scenario == "huge-output":
        output = "PRIVATE_NEVER_ECHO" * 5000
    (tmp_path / "output.json").write_text(output)
    if scenario == "tampered-transfer":
        (tmp_path / "worker.py").write_bytes(b"PRIVATE_NEVER_ECHO")
    # The executable boundary is the same -File invocation as the shipped CMD.
    fake = tmp_path / "docker"
    fake.write_text(
        f"#!{sys.executable}\n"
        + """
import json,os,sys
from pathlib import Path
root=Path(os.environ['PREFLIGHT_CASE_DIR'])
scenario=os.environ['PREFLIGHT_CASE']
args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as f: f.write(json.dumps(args)+'\\n')
if args[0]=='inspect':
    fmt=args[2]
    previous=(root/'calls.jsonl').read_text().splitlines()
    count=sum(json.loads(line)[2:3]==[fmt] for line in previous)
    values={
      '{{.Id}}':'a'*64,'{{.Name}}':'/hybrid-windows-api-1','{{.State.Running}}':'true',
      '{{.Config.WorkingDir}}':'/app','{{.Config.User}}':'10001:10001',
      '{{.Image}}':'sha256:'+'b'*64,
      '{{json .Config.Labels}}':json.dumps({'com.docker.compose.project':'hybrid-windows','com.docker.compose.service':'api'})}
    if scenario=='wrong-user': values['{{.Config.User}}']='0'
    if scenario=='changed-image' and count>1: values['{{.Image}}']='sha256:'+'c'*64
    if scenario=='changed-container' and count>1: values['{{.Id}}']='d'*64
    mount=dict(Type='volume',Source='PRIVATE_HOST_PATH_NEVER_ECHO',Destination='/app/demo-export-state',RW=True)
    if scenario=='changed-mount' and count>1: mount['Source']='PRIVATE_CHANGED_HOST_PATH'
    if scenario=='read-only-mount': mount['RW']=False
    mounts=[mount]
    if scenario=='missing-mount': mounts=[]
    if scenario=='nested-mount': mounts.append(dict(Type='bind',Source='PRIVATE_NESTED',Destination='/app/demo-export-state/hidden',RW=True))
    values['{{json .Mounts}}']=json.dumps(mounts)
    if fmt not in values: sys.exit(3)
    print(values[fmt])
    sys.exit(0)
if args==['exec','-i','a'*64,'python','-I','-B','-']:
    payload=sys.stdin.read()
    assert 'CONFIG=json.loads' in payload and 'backup-scope-preflight' in payload
    print((root/'output.json').read_text())
    sys.exit(2 if scenario in {'blocked-worker','inconsistent-status'} else 0)
sys.exit(9)
"""
    )
    fake.chmod(0o700)
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(tmp_path / "PREFLIGHT.ps1"),
            "-ManifestSHA256",
            hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest(),
        ],
        text=True,
        capture_output=True,
        timeout=25,
        env=dict(
            os.environ,
            PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
            PREFLIGHT_CASE_DIR=str(tmp_path),
            PREFLIGHT_CASE=scenario,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
            POWERSHELL_TELEMETRY_OPTOUT="1",
        ),
    )
    assert not result.stderr, result.stderr
    assert "PRIVATE" not in result.stdout + result.stderr
    calls = (
        [json.loads(p) for p in (tmp_path / "calls.jsonl").read_text().splitlines()]
        if (tmp_path / "calls.jsonl").exists()
        else []
    )
    assert all(p[0] in {"inspect", "exec"} for p in calls)
    assert all("Config.Env" not in "|".join(p) for p in calls)
    if scenario == "valid":
        assert result.returncode == 0, result.stdout
        assert "BACKUP_SCOPE_OBSERVED_KEEP_PAUSED_NO_BACKUP_NO_ORDERS" in result.stdout
        line = next(p for p in result.stdout.splitlines() if p.startswith("{"))
        parsed = json.loads(line)
        assert parsed["container_context"]["storage_mount_type"] == "volume"
        assert parsed["worker_report"]["backup_ready"] is False
    else:
        assert result.returncode == 2, result.stdout
        assert "BLOCKED_KEEP_PAUSED" in result.stdout
        assert "BACKUP_SCOPE_OBSERVED_KEEP_PAUSED_NO_BACKUP_NO_ORDERS" not in result.stdout
    if scenario in {
        "tampered-transfer",
        "wrong-user",
        "missing-mount",
        "nested-mount",
        "read-only-mount",
    }:
        assert not any(p[0] == "exec" for p in calls)
