"""Sealed isolated-recovery package, input refusal and clone-worker safety."""

import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_isolated_restore_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT


def package():
    release = os.environ.get("ISOLATED_RESTORE_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {p[len(builder.FOLDER) + 1 :]: z.read(p) for p in z.namelist()}
    real = builder.git

    def source(*args):
        return (ROOT / args[1].split(":", 1)[1]).read_bytes() if args[0] == "show" else real(*args)

    with patch.object(builder, "git", source):
        return builder.package_files("HEAD")


def materialize(root):
    root.mkdir(parents=True, exist_ok=True)
    for name, body in package().items():
        (root / name).write_bytes(body)


def test_package_pins_user_supplied_hashes_and_has_no_source_commands():
    files = package()
    assert len(files) == 12
    config = json.loads(files["CONFIG.json"])
    assert (
        config["receipt_sha256"]
        == "4aff6c186597d9ea97a12928101961f5143c93dfae36b5b647109d11676b6778"
    )
    assert (
        config["manifest_sha256"]
        == "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d"
    )
    for p, h in json.loads(files["TRANSFER_MANIFEST.json"])["files"].items():
        assert hashlib.sha256(files[p]).hexdigest() == h
    source = files["RESTORE_CORE.ps1"].decode()
    assert "--signal=SIGKILL" not in source and "'rm'" not in source and "'update'" not in source
    assert (
        "source='+$State.database_volume" not in source
        and "source='+$state.database_volume" not in source
    )
    for setting in (
        "listen_addresses=",
        "default_transaction_read_only=on",
        "shared_preload_libraries=",
        "restore_command=",
        "archive_command=",
    ):
        assert setting in source
    assert "Config.Env" not in source
    assert "RESTORE_REPLICA_LAYOUT_UNSUPPORTED" in files["restore_worker.py"].decode()


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,reason",
    [
        ("valid", None),
        ("receipt-tamper", "RESTORE_RETAINED_HASH_MISMATCH"),
        ("archive-tamper", "RESTORE_INPUT_HASH_MISMATCH"),
        ("state-tamper", "RESTORE_INPUT_HASH_MISMATCH"),
        ("images-tamper", "RESTORE_INPUT_HASH_MISMATCH"),
        ("manifest-tamper", "RESTORE_RETAINED_HASH_MISMATCH"),
        ("running", "RESTORE_ORIGINAL_NOT_STOPPED"),
        ("restart", "RESTORE_ORIGINAL_NOT_STOPPED"),
        ("helper-rw", "RESTORE_PRESERVATION_HELPER_CHANGED"),
        ("helper-running", "RESTORE_PRESERVATION_HELPER_CHANGED"),
        ("external", "RESTORE_EXTERNAL_SOURCE_CONSUMER"),
        ("state-scope", "RESTORE_SOURCE_SCOPE_CHANGED"),
        ("no-optin", "RESTORE_EXPLICIT_MODE_REQUIRED"),
        ("both-modes", "RESTORE_EXPLICIT_MODE_REQUIRED"),
    ],
)
def test_check_refuses_mutation_before_creating_any_resources(tmp_path, case, reason):
    materialize(tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    runtime = dict(
        Type="volume",
        Name="runtime",
        Source="/vol/runtime",
        Destination="/app/demo-export-state",
        RW=True,
    )
    database = dict(
        Type="volume",
        Name="database",
        Source="/vol/database",
        Destination="/var/lib/postgresql",
        RW=True,
    )
    records = []
    for ident, role in zip(
        "abcdef1", ("api", "db", "api", "api", "alerts", "web", "key-session"), strict=True
    ):
        records.append(
            dict(
                id=ident * 64,
                name="/hybrid-windows-api-1" if ident == "a" else "/private",
                image="sha256:" + ("9" if ident == "b" else "8") * 64,
                labels={
                    "com.docker.compose.project": "hybrid-windows",
                    "com.docker.compose.service": role,
                },
                mounts=[runtime.copy()]
                if ident in "acd"
                else ([database.copy()] if ident == "b" else []),
                running=False,
                paused=False,
                dead=False,
                restarting=False,
                restart=dict(Name="no", MaximumRetryCount=0),
                networks={},
            )
        )
    state = dict(
        protocol="STOPPED_UNCLEAN_PRESERVATION_V1",
        source_reference="7" * 40,
        pgdata_relative="18/docker",
        key_session_excluded=True,
        automatic_resume_authorized=False,
        canonical_images=["sha256:" + "8" * 64, "sha256:" + "9" * 64],
        database_volume="database",
        runtime_volume="runtime",
        source_snapshots=[
            {k: r[k] for k in ("id", "image", "restart", "mounts")}
            | dict(service=r["labels"]["com.docker.compose.service"])
            for r in records[:6]
        ],
    )
    (backup / "BACKUP_STATE.json").write_text(json.dumps(state))
    for name in ("database.tar", "runtime.tar", "images.tar", "manifest.json"):
        (backup / name).write_bytes(b"SYNTHETIC_PRIVATE_DATA")

    def digest(name):
        return hashlib.sha256((backup / name).read_bytes()).hexdigest()

    receipt = dict(
        status="STOPPED_RAW_COPY_VERIFIED_NOT_RECOVERY_PROOF",
        source_reference="7" * 40,
        manifest_sha256=digest("manifest.json"),
        state_sha256=digest("BACKUP_STATE.json"),
        image_archive_sha256=digest("images.tar"),
        archives={
            g: dict(bytes=(backup / (g + ".tar")).stat().st_size, sha256=digest(g + ".tar"))
            for g in ("database", "runtime")
        },
        helper_container_ids=["2" * 64],
        output_volume="output",
        source_services_observed_stopped=True,
        actual_http_attempts=0,
        order_writes=0,
    )
    for flag in (
        "source_containers_started",
        "source_clean_shutdown_verified",
        "account_pause_rechecked",
        "source_data_archive_restore_tested",
        "offline_handoff_performed",
        "automatic_resume",
        "automatic_retry",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        receipt[flag] = False
    (backup / "BACKUP_RECEIPT.json").write_text(json.dumps(receipt))
    config = dict(
        source_reference="6" * 40,
        preservation_source_reference="7" * 40,
        api_container_id="a" * 64,
        database_container_id="b" * 64,
        manifest_sha256=digest("manifest.json"),
        receipt_sha256=digest("BACKUP_RECEIPT.json"),
    )
    helper = dict(
        records[2],
        id="2" * 64,
        labels={},
        mounts=[
            dict(database, Destination="/source/database", RW=False),
            dict(runtime, Destination="/source/runtime", RW=False),
            dict(
                Type="volume", Name="output", Source="/vol/output", Destination="/output", RW=True
            ),
        ],
    )
    records.append(helper)
    if case.endswith("-tamper"):
        name = {
            "receipt-tamper": "BACKUP_RECEIPT.json",
            "archive-tamper": "database.tar",
            "state-tamper": "BACKUP_STATE.json",
            "images-tamper": "images.tar",
            "manifest-tamper": "manifest.json",
        }[case]
        (backup / name).write_bytes(b"tampered")
    if case == "running":
        records[0]["running"] = True
    if case == "restart":
        records[0]["restart"]["Name"] = "always"
    if case == "helper-rw":
        helper["mounts"][0]["RW"] = True
    if case == "helper-running":
        helper["running"] = True
    if case == "external":
        records.append(dict(records[2], id="3" * 64, labels={}))
    if case == "state-scope":
        records[0]["image"] = "sha256:" + "4" * 64
    (tmp_path / "CONFIG.json").write_text(json.dumps(config))
    (tmp_path / "records.json").write_text(json.dumps(records))
    mode = {"no-optin": "", "both-modes": "-ExecuteRestore -CheckOnly"}.get(case, "-CheckOnly")
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        """
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/CONTEXT.ps1"
. "$PSScriptRoot/COMMON.ps1"
. "$PSScriptRoot/RESTORE_CORE.ps1"
function Find-RetainedBackup { return "$PSScriptRoot/backup" }
function Assert-PrivateBackupDirectory { }
function Assert-PrivateBackupFiles { }
$script:records=@(Get-Content -Raw "$PSScriptRoot/records.json" | ConvertFrom-Json)
function Container-Ids { return @($script:records | ForEach-Object {$_.id} | Sort-Object) }
function Read-Container([string]$Id) { return ($script:records | Where-Object {$_.id -ceq $Id} | ConvertTo-Json -Depth 12 | ConvertFrom-Json) }
function Docker-Lines([string[]]$Arguments,[int]$Maximum=128) {
 if ($Arguments[0] -ceq 'volume' -and $Arguments[1] -ceq 'inspect') {return @('"local"','"local"','null')}
 throw 'UNEXPECTED_DOCKER_CALL'
}
function Backup-Docker([string[]]$Arguments,[int]$Maximum=128) {
 if ($Arguments[0] -ceq 'image' -and $Arguments[1] -ceq 'inspect') {return $Arguments[-1]}
 throw 'UNEXPECTED_DOCKER_CALL'
}
Invoke-IsolatedRestore -Root $PSScriptRoot """
        + mode
        + """ | ForEach-Object {if ($_ -is [int]) {exit $_} else {Write-Output $_}}
"""
    )
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(harness)],
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
    assert not result.stderr, result.stderr
    assert result.returncode == (2 if reason else 0), result.stdout
    assert (
        "SYNTHETIC_PRIVATE_DATA" not in result.stdout
        and "UNEXPECTED_DOCKER_CALL" not in result.stdout
    )
    if reason:
        assert json.loads(result.stdout.splitlines()[-1])["failure_category"] == reason, (
            result.stdout
        )
    else:
        assert "RESTORE_CHECK_PASSED_NO_CLONE_NO_SOURCE_START_NO_ORDERS" in result.stdout


@pytest.mark.parametrize(
    "case,reason",
    [
        ("pid", None),
        ("no-pid", None),
        ("replica", "RESTORE_REPLICA_LAYOUT_UNSUPPORTED"),
        ("bad-version", "RESTORE_PG_LAYOUT_INVALID"),
        ("large-pid", "RESTORE_CLONE_PID_INVALID"),
        ("corrupt", "COLD_ARCHIVE_HASH_MISMATCH"),
    ],
)
def test_restore_worker_only_adjusts_clone_pid_after_exact_restore(tmp_path, case, reason):
    from tools.cold_backup_candidate import create_bundle

    db = tmp_path / "original-db"
    runtime = tmp_path / "original-runtime"
    bundle = tmp_path / "bundle"
    pg = db / "18/docker"
    pg.mkdir(parents=True)
    runtime.mkdir()
    bundle.mkdir()
    (pg / "PG_VERSION").write_bytes(b"17\n" if case == "bad-version" else b"18\n")
    if case != "no-pid":
        (pg / "postmaster.pid").write_bytes(
            b"1\nSYNTHETIC_PID\n" if case != "large-pid" else b"x" * 8193
        )
    if case == "replica":
        (pg / "standby.signal").write_bytes(b"")
    (runtime / "data").write_bytes(b"SYNTHETIC_RUNTIME")
    digest = create_bundle(
        {"database": db, "runtime": runtime}, bundle, enabled=True, sources_frozen_by_caller=True
    )
    if case == "corrupt":
        (bundle / "database.tar").write_bytes(b"bad")
    clone_db = tmp_path / "clone-db"
    clone_runtime = tmp_path / "clone-runtime"
    clone_db.mkdir()
    clone_runtime.mkdir()
    source = package()["restore_worker.py"].decode()
    for old, new in (
        ("/bundle", bundle),
        ("/clone/database", clone_db),
        ("/clone/runtime", clone_runtime),
    ):
        source = source.replace(old, str(new))
    source = source.replace(
        'os.statvfs("/clone/" + group)',
        'os.statvfs(str({"database": '
        + repr(str(clone_db))
        + ', "runtime": '
        + repr(str(clone_runtime))
        + "}[group]))",
    )
    namespace = {"__name__": "restore_test"}
    exec(compile(source, "<test-fixed-clone-mounts>", "exec"), namespace)
    before = {str(p.relative_to(db)): p.read_bytes() for p in db.rglob("*") if p.is_file()}
    if reason:
        with pytest.raises(ValueError, match=reason):
            namespace["restore"]({"manifest_sha256": digest}, package()["cold_archive.py"])
    else:
        value = namespace["restore"]({"manifest_sha256": digest}, package()["cold_archive.py"])
        assert value["clone_pid_removed"] is (case == "pid")
        assert not (clone_db / "18/docker/postmaster.pid").exists()
        assert (clone_runtime / "data").read_bytes() == b"SYNTHETIC_RUNTIME"
    assert before == {str(p.relative_to(db)): p.read_bytes() for p in db.rglob("*") if p.is_file()}


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,reason",
    [
        ("owned", None),
        ("original", "RESTORE_CLONE_ID_INVALID"),
        ("wrong-mount", "RESTORE_CLONE_MOUNT_INVALID"),
        ("network", "RESTORE_CLONE_NETWORK_INVALID"),
    ],
)
def test_only_new_networkless_clone_can_be_signalled(tmp_path, case, reason):
    materialize(tmp_path)
    script = tmp_path / "stop-test.ps1"
    script.write_text(
        """
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/RESTORE_CORE.ps1"
$script:stopped=$false
$script:calls=@()
function Read-Container([string]$Id) { return [pscustomobject]@{restart=[pscustomobject]@{Name='no'};mounts=@([pscustomobject]@{Type='volume';Name="""
        + ("'original-volume'" if case == "wrong-mount" else "'fresh-clone-volume'")
        + """;Destination='/var/lib/postgresql'})} }
function Backup-Docker([string[]]$Arguments,[int]$Maximum=128) {
 $script:calls+=,($Arguments -join '|')
 if ($Arguments[0] -ceq 'kill') { $script:stopped=$true; return $Arguments[-1] }
 if ($Arguments[2] -ceq '{{.HostConfig.NetworkMode}}') { return """
        + ("'bridge'" if case == "network" else "'none'")
        + """ }
 if ($Arguments[2] -ceq '{{.State.Running}}') { if ($script:stopped) {return 'false'} else {return 'true'} }
 throw 'UNEXPECTED_CALL'
}
try {
 Stop-OwnedClone -Id """
        + ("('a'*64)" if case == "original" else "('b'*64)")
        + """ -Volume 'fresh-clone-volume' -Original @([pscustomobject]@{id=('a'*64)})
 [pscustomobject]@{status='OK';calls=$script:calls} | ConvertTo-Json -Compress
} catch { [pscustomobject]@{status=$_.Exception.Message;calls=$script:calls} | ConvertTo-Json -Compress }
"""
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
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == (reason or "OK")
    kills = [x for x in report["calls"] if x.startswith("kill|")]
    assert kills == ([] if reason else ["kill|--signal=SIGINT|" + "b" * 64])


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "gid-zero",
        "uid-zero",
        "bad-count",
        "extra-private-field",
        "source-changed",
        "bad-pid-type",
    ],
)
def test_worker_report_rejects_unsafe_or_extra_fields(tmp_path, case):
    materialize(tmp_path)
    worker = dict(
        status="CLONE_BYTES_AND_METADATA_RESTORED",
        file_counts=dict(database=3, runtime=1),
        pg_uid=70,
        pg_gid=0 if case == "gid-zero" else 70,
        clone_pid_removed=True,
        source_changed=False,
    )
    if case == "uid-zero":
        worker["pg_uid"] = 0
    if case == "bad-count":
        worker["file_counts"]["database"] = 2
    if case == "extra-private-field":
        worker["private"] = "PRIVATE_SENTINEL"
    if case == "source-changed":
        worker["source_changed"] = True
    if case == "bad-pid-type":
        worker["clone_pid_removed"] = "true"
    (tmp_path / "worker.json").write_text(json.dumps(worker))
    script = tmp_path / "worker-test.ps1"
    script.write_text("""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/RESTORE_CORE.ps1"
try {
 $worker=Get-Content -Raw "$PSScriptRoot/worker.json" | ConvertFrom-Json
 Assert-RestoredWorkerReport $worker ([pscustomobject]@{file_counts=[pscustomobject]@{database=3;runtime=1}})
 Write-Output 'OK'
} catch { Write-Output $_.Exception.Message }
""")
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
    assert result.returncode == 0 and not result.stderr
    assert result.stdout.strip() == (
        "OK" if case in {"valid", "gid-zero"} else "RESTORE_WORKER_OUTPUT_INVALID"
    )
