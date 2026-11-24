"""Approval-disabled original start boundary and synthetic-only server lifecycle."""

import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_original_inspection_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_copy_commit_bundle import database as database
from tests.unit.test_evidence_receipt_review import launch


def package():
    release = os.getenv("ORIGINAL_INSPECTION_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    def read(*args):
        if args[0] == "show":
            path = args[1].split(":", 1)[-1]
            if (
                path.startswith("tools/windows_demo_original_inspection/")
                or path == "docs/reviews/20261124/OWNER_REVIEW_WINDOWS_OBSERVATION.json"
            ):
                return (ROOT / path).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def write(tmp_path):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    return f


def test_review_bundle_is_disabled_and_source_guards_unchanged():
    f = package()
    old = builder.refresh_files(builder.REFRESH)
    for n in (
        "DOCKER_READ.ps1",
        "CONTEXT.ps1",
        "SOURCE_SCOPE.ps1",
        "COPY_CORE.ps1",
        "COPY_EXT.ps1",
        "COMPARE_ADMISSION.ps1",
        "FINANCIAL_EXT.ps1",
        "REFRESH_CORE.ps1",
        "worker.py",
        "READ_LIBRARY.py",
    ):
        assert f[n] == old[n]
    assert json.loads(f["CONFIG.json"])["inspection_approval_receipt_file_sha256"] is None
    assert (
        json.loads(f["ORIGINAL_START_ACCEPTANCE.json"])["original_database_start_authorized"]
        is False
    )
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    assert all(hashlib.sha256(f[n]).hexdigest() == h for n, h in m["files"].items())
    p = json.loads(f["INSPECTION_PROPOSAL.json"])
    assert all(hashlib.sha256(f[n]).hexdigest() == h for n, h in p["execution_file_sha256"].items())
    assert (
        "New-PrivateBackupDirectory ($inspectionApprovalId.Substring(0,32))"
        in f["RUN_INSPECT.ps1"].decode()
    )
    sql = f["original_read.sql"].decode()
    assert "READ ONLY" in sql and "ISOLATED_ORIGINAL_DATABASE_STATEMENT_TIMESTAMP" in sql
    for s in ("UPDATE ", "INSERT ", "DELETE ", "FOR UPDATE"):
        assert s not in sql
    core = f["INSPECTION_CORE.ps1"].decode()
    assert "'kill','--signal=SIGINT'" in core
    assert "SIGKILL" not in core and "'rm'" not in core
    assert "ORIGINAL_START_APPROVAL_REQUIRED" in core


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("mode", ["CheckOnly", "InspectOnce", "UnsealedExternalApproval"])
def test_prepared_launcher_never_starts_any_process_without_approval(tmp_path, mode):
    f = write(tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    for n in (
        "BACKUP_RECEIPT.json",
        "BACKUP_STATE.json",
        "manifest.json",
        "database.tar",
        "runtime.tar",
        "images.tar",
    ):
        (backup / n).write_text("{}")
    ext = tmp_path / "FINANCIAL_EXT.ps1"
    ext.write_text(
        ext.read_text()
        + f"""
function Find-RetainedBackup {{param($Config) return '{backup}'}}
function Assert-InputHashes {{param($Directory,$Config,$Receipt)}}
function Read-CopySources {{param($Config,$Receipt,$State) return ,@([pscustomobject]@{{id=('b'*64)}})}}
function Assert-RestoreSourceSnapshot {{param($Original,$Helpers,$Frozen) if($Helpers.Count -ne 0 -or $Frozen){{throw 'TEST_UNEXPECTED_MUTATION'}}}}
function New-PrivateBackupDirectory {{param($Token) throw 'TEST_DIRECTORY_CREATION_FORBIDDEN'}}
"""
    )
    if mode == "UnsealedExternalApproval":
        (tmp_path / "ORIGINAL_START_ACCEPTANCE.json").write_text(
            '{"original_database_start_authorized":true}'
        )
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    m["files"]["FINANCIAL_EXT.ps1"] = hashlib.sha256(ext.read_bytes()).hexdigest()
    raw = json.dumps(m).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    flag = "CheckOnly" if mode == "CheckOnly" else "InspectOnce"
    v = launch(
        tmp_path,
        f"function docker {{throw 'TEST_DOCKER_START_FORBIDDEN'}}\n& '{tmp_path}/RUN_INSPECT.ps1' -ManifestSHA256 '{hashlib.sha256(raw).hexdigest()}' -{flag}\nexit $LASTEXITCODE",
    )
    assert not v.stderr, v.stdout + v.stderr
    assert v.returncode == (0 if flag == "CheckOnly" else 2), v.stdout
    assert (
        "TEST_DOCKER_START_FORBIDDEN" not in v.stdout
        and "TEST_DIRECTORY_CREATION_FORBIDDEN" not in v.stdout
    )
    if flag != "CheckOnly":
        assert (
            "SCOPE_TRANSFER_INVALID"
            if mode == "UnsealedExternalApproval"
            else "ORIGINAL_START_APPROVAL_REQUIRED"
        ) in v.stdout


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "missing",
        "wrong-hash",
        "storage",
        "logical",
        "takeover",
        "retry",
        "proposal",
        "backup-config",
    ],
)
def test_start_consent_is_separate_exact_and_cannot_expand_to_logical_writes(tmp_path, fault):
    f = write(tmp_path)
    c = json.loads(f["CONFIG.json"])
    r = dict(
        policy="ORIGINAL_DATABASE_INSPECTION_ONLY_ACCEPTANCE_V1",
        proposal_file_sha256=c["inspection_proposal_file_sha256"],
        original_database_start_authorized=True,
        postgresql_startup_shutdown_storage_writes_authorized=True,
        single_attempt_only=True,
        approval_id="e" * 64,
        operator_reply="SYNTHETIC_TEST_REPLY_NOT_USER_AUTHORIZATION",
    )
    for k in (
        "original_ledger_write_authorized",
        "owner_lease_takeover_authorized",
        "baseline_adoption_authorized",
        "request_resolution_authorized",
        "resend_authorized",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        r[k] = False
    if fault == "storage":
        r["postgresql_startup_shutdown_storage_writes_authorized"] = False
    elif fault == "logical":
        r["original_ledger_write_authorized"] = True
    elif fault == "takeover":
        r["owner_lease_takeover_authorized"] = True
    elif fault == "retry":
        r["single_attempt_only"] = False
    elif fault == "proposal":
        r["proposal_file_sha256"] = "f" * 64
    if fault == "backup-config":
        c["manifest_sha256"] = "f" * 64
    b = json.dumps(r).encode()
    (tmp_path / "ORIGINAL_START_ACCEPTANCE.json").write_bytes(b)
    c["inspection_approval_receipt_file_sha256"] = (
        None
        if fault == "missing"
        else "f" * 64
        if fault == "wrong-hash"
        else hashlib.sha256(b).hexdigest()
    )
    (tmp_path / "test-config.json").write_text(json.dumps(c))
    v = launch(
        tmp_path,
        f"""
. '{tmp_path}/INSPECTION_CORE.ps1'
function Restore-Hash {{param($Path) return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}}
function Read-RestoreJson {{param($Path) return Get-Content -Raw $Path|ConvertFrom-Json}}
$c=Get-Content -Raw '{tmp_path}/test-config.json'|ConvertFrom-Json
try{{$null=Assert-InspectionApproval '{tmp_path}' $c;'ACCEPTED'}}catch{{'BLOCKED'}}
""",
    )
    assert v.returncode == 0 and not v.stderr, v.stdout + v.stderr
    assert v.stdout.strip() == ("ACCEPTED" if fault == "valid" else "BLOCKED")


def manifest():
    return dict(
        groups=dict(
            database=dict(entries=[dict(kind="directory", path="18/docker", uid=999, gid=999)])
        )
    )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("fault", ["valid", "unapproved", "wrong-volume", "uid", "gid", "bool-uid"])
def test_args_are_bound_to_one_original_volume_and_fixed_networkless_postgres(tmp_path, fault):
    write(tmp_path)
    m = manifest()
    if fault == "uid":
        m["groups"]["database"]["entries"][0]["uid"] = 0
    elif fault == "gid":
        m["groups"]["database"]["entries"][0]["gid"] = -1
    elif fault == "bool-uid":
        m["groups"]["database"]["entries"][0]["uid"] = True
    (tmp_path / "test-manifest.json").write_text(json.dumps(m))
    approved = "$false" if fault == "unapproved" else "$true"
    vol = "other" if fault == "wrong-volume" else "test-db"
    v = launch(
        tmp_path,
        f"""
. '{tmp_path}/INSPECTION_CORE.ps1'
$script:InspectionApproved={approved};$script:InspectionVolume='test-db'
$s=[pscustomobject]@{{database_volume='{vol}';canonical_images=@(('sha256:'+('a'*64)),('sha256:'+('b'*64)))}}
$m=Get-Content -Raw '{tmp_path}/test-manifest.json'|ConvertFrom-Json
try{{$args2=@(Inspection-ServerArgs $s $m);ConvertTo-Json -InputObject $args2 -Compress}}catch{{'BLOCKED'}}
""",
    )
    assert v.returncode == 0 and not v.stderr, v.stdout + v.stderr
    if fault != "valid":
        assert v.stdout.strip() == "BLOCKED"
    else:
        args = json.loads(v.stdout)
        assert args.count("--mount") == 1
        assert (
            args[args.index("--mount") + 1]
            == "type=volume,source=test-db,target=/var/lib/postgresql,volume-nocopy"
        )
        assert (
            args[args.index("--network") + 1] == "none"
            and args[args.index("--restart") + 1] == "no"
        )
        assert "-p" not in args and "--publish" not in args


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "original-id",
        "rw",
        "extra-mount",
        "network",
        "rootfs",
        "command",
        "user",
        "cap",
        "pid",
        "ports",
        "tmpfs",
    ],
)
def test_host_admission_rejects_identity_mount_command_or_isolation_drift(tmp_path, fault):
    write(tmp_path)
    item = dict(
        image="sha256:" + "b" * 64,
        restart=dict(Name="no", MaximumRetryCount=0),
        restarting=False,
        paused=False,
        dead=False,
        running=False,
        mounts=[dict(Type="volume", Name="test-db", Destination="/var/lib/postgresql", RW=True)],
    )
    host = dict(
        NetworkMode="none",
        ReadonlyRootfs=True,
        Privileged=False,
        Memory=1073741824,
        PidsLimit=128,
        CapDrop=["ALL"],
        CapAdd=None,
        SecurityOpt=["no-new-privileges"],
        Devices=[],
        Binds=None,
        PortBindings={},
        PublishAllPorts=False,
        PidMode="",
        IpcMode="private",
        UTSMode="",
        Tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=64m,mode=1777"},
    )
    user = "999:999"
    cmd = ["-D", "/var/lib/postgresql/18/docker"]
    if fault == "rw":
        item["mounts"][0]["RW"] = False
    elif fault == "extra-mount":
        item["mounts"].append(item["mounts"][0])
    elif fault == "network":
        host["NetworkMode"] = "bridge"
    elif fault == "rootfs":
        host["ReadonlyRootfs"] = False
    elif fault == "command":
        cmd = ["-D", "/other"]
    elif fault == "user":
        user = "0:0"
    elif fault == "cap":
        host["CapAdd"] = ["SYS_ADMIN"]
    elif fault == "pid":
        host["PidMode"] = "host"
    elif fault == "ports":
        host["PortBindings"] = {"5432/tcp": [dict(HostPort="5432")]}
    elif fault == "tmpfs":
        host["Tmpfs"]["/tmp"] = "rw,exec"
    for n, v in [("item", item), ("host", host), ("command", cmd), ("user", user)]:
        (tmp_path / (n + ".json")).write_text(json.dumps(v))
    orig = "c" * 64 if fault == "original-id" else "d" * 64
    v = launch(
        tmp_path,
        f"""
. '{tmp_path}/INSPECTION_CORE.ps1'
$script:InspectionId=('c'*64);$script:InspectionArgs=@('--user','999:999','-D','/var/lib/postgresql/18/docker')
function Read-Container {{param($Id) return Get-Content -Raw '{tmp_path}/item.json'|ConvertFrom-Json}}
function Backup-Docker {{param($Arguments,$Maximum) return @((Get-Content -Raw '{tmp_path}/host.json'),(Get-Content -Raw '{tmp_path}/user.json'),'["postgres"]',(Get-Content -Raw '{tmp_path}/command.json'))}}
try{{$null=Assert-InspectionServer ([pscustomobject]@{{database_volume='test-db';canonical_images=@(('sha256:'+('a'*64)),('sha256:'+('b'*64)))}}) @([pscustomobject]@{{id='{orig}'}});'ACCEPTED'}}catch{{'BLOCKED'}}
""",
    )
    assert v.returncode == 0 and not v.stderr, v.stdout + v.stderr
    assert v.stdout.strip() == ("ACCEPTED" if fault == "valid" else "BLOCKED")


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("fault", ["none", "query"])
def test_synthetic_volume_actual_start_read_twice_stop_without_account_changes(
    tmp_path, database, fault
):
    socket, api, args = database
    original = args[3]
    state = json.loads(subprocess.check_output(["docker", "inspect", original], text=True))[0]
    vol = next(m["Name"] for m in state["Mounts"] if m["Destination"] == "/var/lib/postgresql")
    image = state["Image"]
    ids = (
        subprocess.check_output(
            ["docker", "exec", original, "stat", "-c", "%u:%g", "/var/lib/postgresql/18/docker"],
            text=True,
        )
        .strip()
        .split(":")
    )
    subprocess.run(
        args,
        input="UPDATE demo_accounts SET owner=NULL,lease_until='2026-11-12T00:40:54.427273Z';",
        text=True,
        check=True,
        capture_output=True,
    )
    snap = "SELECT state::text || '|' || fence::text || '|' || coalesce(owner,'NULL') || '|' || coalesce(lease_until::text,'NULL') FROM demo_accounts;"
    before = subprocess.check_output(args, input=snap, text=True)
    subprocess.run(["docker", "kill", "--signal=SIGINT", original], check=True, capture_output=True)
    assert (
        subprocess.check_output(["docker", "wait", original], text=True, timeout=15).strip() == "0"
    )
    write(tmp_path)
    m = manifest()
    m["groups"]["database"]["entries"][0].update(uid=int(ids[0]), gid=int(ids[1]))
    (tmp_path / "test-manifest.json").write_text(json.dumps(m))
    ident = None
    try:
        v = launch(
            tmp_path,
            f"""
. '{tmp_path}/DOCKER_READ.ps1'
. '{tmp_path}/CONTEXT.ps1'
. '{tmp_path}/COMMON.ps1'
. '{tmp_path}/INSPECTION_CORE.ps1'
# Synthetic-only approval context; production proposal remains approval-disabled.
$script:InspectionApproved=$true;$script:InspectionVolume='{vol}'
function Assert-RestoreSourceSnapshot {{param($Original,$Helpers,$Frozen) $v=Read-Container '{original}';if($v.running){{throw 'ORIGINAL_TEST_WRITER_RUNNING'}}}}
function Assert-OriginalRead {{param($Account) if('{fault}' -ceq 'query'){{throw 'ORIGINAL_TEST_QUERY_FAILURE'}};if($Account.transaction_read_only -cne 'on' -or $Account.owner_review.original_owner_verified -ne $true -or $Account.orders -ne 34){{throw 'ORIGINAL_TEST_PROJECTION_INVALID'}}}}
$s=[pscustomobject]@{{database_volume='{vol}';canonical_images=@(('sha256:'+('a'*64)),'{image}')}}
$m=Get-Content -Raw '{tmp_path}/test-manifest.json'|ConvertFrom-Json
try{{Invoke-OriginalInspection '{tmp_path}' ([pscustomobject]@{{comparison_helper_id=('e'*64)}}) $s @([pscustomobject]@{{id='{original}'}}) '{tmp_path}' $m}}catch{{[pscustomobject]@{{error_code=$_.Exception.Message;started=$script:InspectionStarted;clean_exit=$script:InspectionStopped}}|ConvertTo-Json -Compress}}
""",
        )
        resource = tmp_path / "ORIGINAL_SERVER_RESOURCE.json"
        if resource.exists():
            ident = json.loads(resource.read_bytes())["container_id"]
        assert v.returncode == 0 and not v.stderr, v.stdout + v.stderr
        if fault == "none":
            assert (
                "ORIGINAL_INSPECTION_COMPLETE_TEMP_SERVER_STOPPED_NO_LEDGER_WRITE_NO_ORDERS"
                in v.stdout
            )
            r = json.loads((tmp_path / "ORIGINAL_INSPECTION_RECEIPT.json").read_bytes())
            assert r["original_database_ever_running"] and r["temporary_original_server_stopped"]
            assert not r["original_ledger_write_attempted"] and not r["original_apply_ready"]
            assert r["original_database_files_may_have_changed"]
            assert (
                r["ownership"]["lease_state"] == "EXPIRED" and not r["ownership"]["owner_present"]
            )
        else:
            r = json.loads(v.stdout)
            assert (
                r["error_code"] == "ORIGINAL_TEST_QUERY_FAILURE"
                and r["started"]
                and r["clean_exit"]
            )
            assert not (tmp_path / "ORIGINAL_INSPECTION_RECEIPT.json").exists()
        # Reopen only this synthetic DB with the fixture server to inspect the same logical row.
        subprocess.run(["docker", "start", original], check=True, capture_output=True)
        import time

        for _ in range(60):
            ready = subprocess.run(args, input="SELECT 1;", text=True, capture_output=True)
            if ready.returncode == 0:
                break
            time.sleep(0.25)
        after = subprocess.check_output(args, input=snap, text=True)
        assert before == after
    finally:
        if ident:
            subprocess.run(["docker", "rm", "-f", ident], check=True, capture_output=True)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("fault", ["none", "scope", "copy-claim", "readonly", "fence"])
def test_original_projection_validator_requires_original_scope_and_original_guards(tmp_path, fault):
    from tests.unit import test_copy_financial_bundle as financial
    from tests.unit import test_owner_review_bundle as owner

    a = owner.owner_account(False, "EXPIRED")
    a["owner_review"].update(
        lease_clock_scope="ISOLATED_ORIGINAL_DATABASE_STATEMENT_TIMESTAMP",
        original_owner_verified=True,
    )
    if fault == "scope":
        a["owner_review"]["lease_clock_scope"] = "COPY_DATABASE_STATEMENT_TIMESTAMP"
    elif fault == "copy-claim":
        a["owner_review"]["original_owner_verified"] = False
    elif fault == "readonly":
        a["transaction_read_only"] = "off"
    elif fault == "fence":
        a["owner_review"]["fence"] = 111
    real = financial.launch

    def native(path, body):
        body = body.replace(
            f". '{tmp_path}/FINANCIAL_EXT.ps1'",
            f". '{tmp_path}/FINANCIAL_EXT.ps1'\n. '{tmp_path}/INSPECTION_RESULT.ps1'",
        ).replace("try{Assert-CopyFinancial $account;", "try{Assert-OriginalRead $account;")
        return real(path, body)

    with patch.object(financial, "package", package), patch.object(financial, "launch", native):
        result = financial.validate(tmp_path, a)
    assert (result == "ACCEPTED") == (fault == "none"), result
