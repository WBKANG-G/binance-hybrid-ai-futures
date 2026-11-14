"""Pinned offline copy: no overwrite, private errors, no source/control mutations."""

import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_offline_handoff_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT


def package():
    release = os.environ.get("OFFLINE_HANDOFF_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    def source(*args):
        return (ROOT / args[1].split(":", 1)[1]).read_bytes() if args[0] == "show" else real(*args)

    with patch.object(builder, "git", source):
        return builder.package_files("HEAD")


def materialize(root):
    root.mkdir(parents=True, exist_ok=True)
    for n, b in package().items():
        (root / n).write_bytes(b)


def test_pins_restore_success_and_only_d_drive():
    files = package()
    assert len(files) == 13
    config = json.loads(files["CONFIG.json"])
    assert config["destination_drive"] == "D"
    assert (
        config["restore_receipt_sha256"]
        == "26395438e08500da24252eac7b11dfe76b22e0d660c86bb1c0d236b6b9e1da08"
    )
    for n, h in json.loads(files["TRANSFER_MANIFEST.json"])["files"].items():
        assert hashlib.sha256(files[n]).hexdigest() == h
    core = files["HANDOFF_CORE.ps1"].decode()
    for forbidden in (
        "'start'",
        "'kill'",
        "'rm'",
        "'exec'",
        "'update'",
        "Format-Volume",
        "Set-Disk",
        "Config.Env",
    ):
        assert forbidden not in core
    assert "[IO.FileMode]::CreateNew" in core and "Flush($true)" in core
    assert "physical_disconnect_verified=$false" in core


def synthetic_inputs(root):
    backup = root / "backup"
    backup.mkdir()
    proof = root / "proof"
    proof.mkdir()
    for n in ("database.tar", "runtime.tar", "images.tar", "manifest.json"):
        (backup / n).write_bytes(b"SYNTHETIC_PRIVATE_BACKUP" + n.encode())
    (backup / "BACKUP_STATE.json").write_text("{}")

    def h(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    receipt = dict(
        status="STOPPED_RAW_COPY_VERIFIED_NOT_RECOVERY_PROOF",
        source_reference="a" * 40,
        manifest_sha256=h(backup / "manifest.json"),
        state_sha256=h(backup / "BACKUP_STATE.json"),
        image_archive_sha256=h(backup / "images.tar"),
        source_services_observed_stopped=True,
        order_writes=0,
        actual_http_attempts=0,
        archives={
            g: dict(bytes=(backup / (g + ".tar")).stat().st_size, sha256=h(backup / (g + ".tar")))
            for g in ("database", "runtime")
        },
    )
    for f in (
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
        receipt[f] = False
    (backup / "BACKUP_RECEIPT.json").write_text(json.dumps(receipt))
    recovery = dict(
        isolated_restore="COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED",
        source_reference="b" * 40,
        backup_manifest_sha256=h(backup / "manifest.json"),
        backup_receipt_sha256=h(backup / "BACKUP_RECEIPT.json"),
        clone_network="none",
        account=dict(
            paused=True,
            orders=34,
            unresolved_requests=4,
            daily_key="2026-11-13",
            weekly_key="2026-11-09",
            transaction_read_only="on",
            account_state_sha256="c" * 64,
        ),
        retained_helper_ids=["d" * 64, "e" * 64],
        clone_container_id="e" * 64,
        retained_clone_volumes=[
            "demo-restore-database-" + "f" * 32,
            "demo-restore-runtime-" + "f" * 32,
        ],
        order_writes=0,
        actual_http_attempts=0,
    )
    for f in (
        "input_hashes_reverified",
        "archive_bytes_and_metadata_verified_before_clone_recovery",
        "clone_stopped",
        "clone_clean_exit_verified",
    ):
        recovery[f] = True
    for f in (
        "original_services_started",
        "original_volume_mounted_in_restore_helpers",
        "financial_write_attempted",
        "full_financial_proof_verified",
        "offline_handoff_performed",
        "installation_ready",
        "automatic_retry",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        recovery[f] = False
    path = proof / "ISOLATED_RESTORE_RECEIPT.json"
    path.write_text(json.dumps(recovery))
    config = dict(
        source_reference="9" * 40,
        preservation_source_reference="a" * 40,
        restore_source_reference="b" * 40,
        manifest_sha256=h(backup / "manifest.json"),
        receipt_sha256=h(backup / "BACKUP_RECEIPT.json"),
        restore_receipt_sha256=h(path),
        destination_drive="D",
    )
    (root / "CONFIG.json").write_text(json.dumps(config))
    return backup, path


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,reason",
    [
        ("valid", None),
        ("check", None),
        ("no-optin", "HANDOFF_EXPLICIT_MODE_REQUIRED"),
        ("both", "HANDOFF_EXPLICIT_MODE_REQUIRED"),
        ("restore-tamper", "HANDOFF_RESTORE_HASH_MISMATCH"),
        ("manifest-tamper", "RESTORE_RETAINED_HASH_MISMATCH"),
        ("running", "HANDOFF_CONTAINER_NOT_STOPPED"),
        ("internal", "HANDOFF_EXTERNAL_USB_REQUIRED"),
        ("exfat", None),
        ("fat32", "HANDOFF_FILESYSTEM_UNSUPPORTED_NO_FORMAT_PERFORMED"),
        ("exfat-check", None),
        ("exfat-source-acl", "COLD_BACKUP_ACL_INVALID"),
        ("filesystem-swap", "HANDOFF_MEDIA_CHANGED"),
        ("space", "HANDOFF_SPACE_INSUFFICIENT"),
        ("swap", "HANDOFF_MEDIA_CHANGED"),
        ("corrupt", "HANDOFF_FINAL_HASH_MISMATCH"),
        ("final-scope", "COLD_SOURCE_RUNNING_STATE_CHANGED"),
        ("acl", "COLD_BACKUP_ACL_INVALID"),
        ("platform", "HANDOFF_WINDOWS_D_DRIVE_REQUIRED"),
    ],
)
def test_copy_lifecycle(tmp_path, case, reason):
    materialize(tmp_path)
    backup, proof = synthetic_inputs(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    dest = external / "new-run"
    if case == "restore-tamper":
        proof.write_bytes(b"tampered")
    if case == "manifest-tamper":
        (backup / "manifest.json").write_bytes(b"tampered")
    original = {str(p): p.read_bytes() for p in [*backup.iterdir(), proof]}
    source = """
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/CONTEXT.ps1"
. "$PSScriptRoot/COMMON.ps1"
. "$PSScriptRoot/SUPPORT.ps1"
. "$PSScriptRoot/HANDOFF_CORE.ps1"
function docker {throw 'DOCKER_MUTATION_FORBIDDEN'}
function Find-RetainedBackup {return "$PSScriptRoot/backup"}
function Find-RestoreEvidence {return "$PSScriptRoot/proof/ISOLATED_RESTORE_RECEIPT.json"}
function Assert-PrivateBackupDirectory {if ($env:HANDOFF_CASE -ceq 'exfat-source-acl') {throw 'COLD_BACKUP_ACL_INVALID'}}
function Assert-PrivateBackupFiles { }
function Assert-HandoffStoppedScope {
 if ($env:HANDOFF_CASE -ceq 'running') {throw 'HANDOFF_CONTAINER_NOT_STOPPED'}
 return ,@([pscustomobject]@{id=('1'*64)})
}
function Assert-RestoreSourceSnapshot {if ($env:HANDOFF_CASE -ceq 'final-scope') {throw 'COLD_SOURCE_RUNNING_STATE_CHANGED'}}
$script:mediaCalls=0
function New-HandoffDirectory {
 if ($env:HANDOFF_CASE -ceq 'acl') {throw 'COLD_BACKUP_ACL_INVALID'}
 $path="$PSScriptRoot/external/new-run"
 if ([IO.Directory]::Exists($path)) {throw 'HANDOFF_DESTINATION_EXISTS'}
 $null=[IO.Directory]::CreateDirectory($path);return $path
}
"""
    if case != "platform":
        source += """
function Read-HandoffMedia {
 $script:mediaCalls++
 if ($env:HANDOFF_CASE -ceq 'internal') {throw 'HANDOFF_EXTERNAL_USB_REQUIRED'}
 if ($env:HANDOFF_CASE -ceq 'fat32') {throw 'HANDOFF_FILESYSTEM_UNSUPPORTED_NO_FORMAT_PERFORMED'}
 $fs='NTFS';if ($env:HANDOFF_CASE.StartsWith('exfat')) {$fs='exFAT'}
 if ($env:HANDOFF_CASE -ceq 'filesystem-swap' -and $script:mediaCalls -gt 1) {$fs='exFAT'}
 $identity='same';if ($env:HANDOFF_CASE -ceq 'swap' -and $script:mediaCalls -gt 1) {$identity='different'}
 $free=100000000000L;if ($env:HANDOFF_CASE -ceq 'space') {$free=0L}
 return [pscustomobject]@{root="$PSScriptRoot/external";identity_sha256=$identity;free_bytes=$free;filesystem=$fs;bus='USB'}
}
"""
    if case == "corrupt":
        source += """
$script:RealCopy=${function:Copy-HandoffFile}
function Copy-HandoffFile([string]$Source,[string]$Destination,[long]$Length,[string]$Hash) {
 & $script:RealCopy $Source $Destination $Length $Hash
 [IO.File]::WriteAllBytes($Destination,[byte[]]@(1,2,3))
}
"""
    mode = {
        "check": "-CheckOnly",
        "exfat-check": "-CheckOnly",
        "no-optin": "",
        "both": "-ExecuteHandoff -CheckOnly",
    }.get(case, "-ExecuteHandoff")
    source += (
        "Invoke-OfflineHandoff -Root $PSScriptRoot "
        + mode
        + " | ForEach-Object {if ($_ -is [int]) {exit $_} else {Write-Output $_}}\n"
    )
    script = tmp_path / "harness.ps1"
    script.write_text(source)
    result = subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        env=dict(
            os.environ,
            HANDOFF_CASE=case,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
        ),
    )
    assert not result.stderr, result.stderr
    assert result.returncode == (2 if reason else 0), result.stdout
    assert (
        "SYNTHETIC_PRIVATE_BACKUP" not in result.stdout
        and "DOCKER_MUTATION_FORBIDDEN" not in result.stdout
    )
    assert original == {str(p): p.read_bytes() for p in [*backup.iterdir(), proof]}
    report = json.loads(next(x for x in result.stdout.splitlines() if x.startswith("{")))
    if reason:
        assert report["failure_category"] == reason, result.stdout
        assert not (dest / "HANDOFF_RECEIPT.json").exists()
    elif case in {"check", "exfat-check"}:
        assert (
            not dest.exists() and "OFFLINE_HANDOFF_CHECK_PASSED_NO_COPY_NO_ORDERS" in result.stdout
        )
    else:
        assert len(list(dest.iterdir())) == 13
        ref = json.loads((dest / "OFFLINE_REFERENCE.json").read_text())
        assert len(ref["files"]) == 10 and ref["physical_offline_custody_verified"] is False
        for item in ref["files"]:
            assert hashlib.sha256((dest / item["name"]).read_bytes()).hexdigest() == item["sha256"]
        assert (
            report["physical_disconnect_verified"] is False
            and report["separate_device_verification_performed"] is False
        )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_copy_create_new_never_overwrites_existing_file(tmp_path):
    materialize(tmp_path)
    (tmp_path / "src").write_bytes(b"new")
    (tmp_path / "dst").write_bytes(b"KEEP_EXISTING")
    script = tmp_path / "copy-test.ps1"
    script.write_text(
        '. "$PSScriptRoot/HANDOFF_CORE.ps1"\ntry {Copy-HandoffFile "$PSScriptRoot/src" "$PSScriptRoot/dst" 3 "unused";exit 9} catch {exit 0}'
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
    assert result.returncode == 0 and (tmp_path / "dst").read_bytes() == b"KEEP_EXISTING"


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,expected",
    [
        ("valid", "OK"),
        ("internal", "HANDOFF_EXTERNAL_USB_REQUIRED"),
        ("boot", "HANDOFF_EXTERNAL_USB_REQUIRED"),
        ("system", "HANDOFF_EXTERNAL_USB_REQUIRED"),
        ("readonly", "HANDOFF_EXTERNAL_USB_REQUIRED"),
        ("offline", "HANDOFF_EXTERNAL_USB_REQUIRED"),
        ("exfat", "OK"),
        ("fat32", "HANDOFF_FILESYSTEM_UNSUPPORTED_NO_FORMAT_PERFORMED"),
        ("wrong-drive", "HANDOFF_WINDOWS_D_DRIVE_REQUIRED"),
        ("missing-identity", "HANDOFF_MEDIA_IDENTITY_UNVERIFIED"),
    ],
)
def test_media_policy_facts(tmp_path, case, expected):
    materialize(tmp_path)
    value = dict(
        root="D:\\",
        identity_sha256="f" * 64,
        free_bytes=1000000000,
        filesystem="NTFS",
        bus="USB",
        is_boot=False,
        is_system=False,
        is_offline=False,
        is_readonly=False,
    )
    if case in {"boot", "system", "readonly", "offline"}:
        value["is_" + case] = True
    if case == "internal":
        value["bus"] = "NVMe"
    if case in {"exfat", "fat32"}:
        value["filesystem"] = "exFAT" if case == "exfat" else "FAT32"
    if case == "wrong-drive":
        value["root"] = "C:\\"
    if case == "missing-identity":
        value["identity_sha256"] = ""
    (tmp_path / "facts.json").write_text(json.dumps(value))
    script = tmp_path / "media-test.ps1"
    script.write_text(
        '. "$PSScriptRoot/HANDOFF_CORE.ps1"\ntry {Assert-HandoffMediaFacts (Get-Content -Raw "$PSScriptRoot/facts.json" | ConvertFrom-Json);Write-Output "OK"} catch {Write-Output $_.Exception.Message}'
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
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == expected, (
        result.stdout + result.stderr
    )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,reason",
    [
        ("valid", None),
        ("corrupt", "OFFLINE_FILE_HASH_MISMATCH"),
        ("reference-tampered", "OFFLINE_REFERENCE_HASH_MISMATCH"),
        ("traversal", "OFFLINE_REFERENCE_INVALID"),
    ],
)
def test_portable_verifier_needs_no_docker_and_writes_nothing(tmp_path, case, reason):
    verifier = tmp_path / "VERIFY_OFFLINE.ps1"
    verifier.write_bytes(package()["VERIFY_OFFLINE.ps1"])
    names = [
        "BACKUP_RECEIPT.json",
        "BACKUP_STATE.json",
        "manifest.json",
        "database.tar",
        "runtime.tar",
        "images.tar",
        "ISOLATED_RESTORE_RECEIPT.json",
        "PRESERVATION_TOOL.zip",
        "RESTORE_TOOL.zip",
        "VERIFY_OFFLINE.ps1",
    ]
    records = []
    for n in names:
        p = tmp_path / n
        if n != "VERIFY_OFFLINE.ps1":
            p.write_bytes(b"SYNTHETIC_PRIVATE")
        records.append(
            dict(name=n, bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        )
    if case == "traversal":
        records[0]["name"] = "../PRIVATE_SENTINEL"
    ref = tmp_path / "OFFLINE_REFERENCE.json"
    ref.write_text(json.dumps(dict(protocol="DEMO_EXTERNAL_COPY_REFERENCE_V1", files=records)))
    pin = hashlib.sha256(ref.read_bytes()).hexdigest()
    if case == "reference-tampered":
        ref.write_bytes(b"tampered")
    if case == "corrupt":
        (tmp_path / "database.tar").write_bytes(b"corrupt")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    result = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(verifier),
            "-ReferenceSHA256",
            pin,
        ],
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
    assert result.returncode == (2 if reason else 0) and not result.stderr, (
        result.stdout + result.stderr
    )
    assert "PRIVATE_SENTINEL" not in result.stdout and "SYNTHETIC_PRIVATE" not in result.stdout
    report = json.loads(result.stdout.splitlines()[0])
    if reason:
        assert report["failure_category"] == reason
    else:
        assert report["file_count"] == 10 and report["docker_used"] is False
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,expected",
    [
        ("valid", "OK"),
        ("unknown-project", "HANDOFF_UNREVIEWED_PROJECT_CONTAINER"),
        ("source-running", "HANDOFF_CONTAINER_NOT_STOPPED"),
        ("clone-running", "HANDOFF_CONTAINER_NOT_STOPPED"),
        ("reader-writable", "HANDOFF_HELPER_CONFIGURATION_CHANGED"),
        ("clone-network", "HANDOFF_HELPER_CONFIGURATION_CHANGED"),
        ("clone-exit", "HANDOFF_CLONE_EXIT_CHANGED"),
        ("external-source", "HANDOFF_EXTERNAL_SOURCE_CONSUMER"),
        ("source-field-added", "HANDOFF_ORIGINAL_CONFIGURATION_CHANGED"),
    ],
)
def test_real_scope_validator_rejects_unreviewed_or_running_resources(tmp_path, case, expected):
    materialize(tmp_path)
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
    for c, role in zip(
        "abcdef1", ("api", "db", "api", "api", "alerts", "web", "key-session"), strict=True
    ):
        records.append(
            dict(
                id=c * 64,
                name="/private",
                image="sha256:" + ("9" if c == "b" else "8") * 64,
                labels={
                    "com.docker.compose.project": "hybrid-windows",
                    "com.docker.compose.service": role,
                },
                mounts=[runtime.copy()] if c in "acd" else ([database.copy()] if c == "b" else []),
                running=False,
                restarting=False,
                paused=False,
                dead=False,
                restart=dict(Name="no", MaximumRetryCount=0),
                networks={},
            )
        )
    originals = json.loads(json.dumps(records))
    dbcopy = "demo-restore-database-" + "f" * 32
    rtcopy = "demo-restore-runtime-" + "f" * 32
    state = dict(
        protocol="STOPPED_UNCLEAN_PRESERVATION_V1",
        source_reference="a" * 40,
        database_volume="database",
        runtime_volume="runtime",
        canonical_images=["sha256:" + "8" * 64, "sha256:" + "9" * 64],
        source_snapshots=[
            {k: r[k] for k in ("id", "image", "restart", "mounts")}
            | dict(service=r["labels"]["com.docker.compose.service"])
            for r in originals[:6]
        ],
    )
    backup = dict(helper_container_ids=["2" * 64], output_volume="output")
    recovery = dict(
        retained_helper_ids=["3" * 64, "4" * 64],
        clone_container_id="4" * 64,
        retained_clone_volumes=[dbcopy, rtcopy],
    )
    preserve = dict(
        records[0],
        id="2" * 64,
        mounts=[
            dict(database, Destination="/source/database", RW=False),
            dict(runtime, Destination="/source/runtime", RW=False),
            dict(
                Type="volume", Name="output", Source="/vol/output", Destination="/output", RW=True
            ),
        ],
    )
    restore = dict(
        records[0],
        id="3" * 64,
        mounts=[
            dict(Type="bind", Source="/private/backup", Destination="/bundle", RW=False),
            dict(database, Name=dbcopy, Source="/vol/" + dbcopy, Destination="/clone/database"),
            dict(runtime, Name=rtcopy, Source="/vol/" + rtcopy, Destination="/clone/runtime"),
        ],
    )
    clone = dict(
        records[1],
        id="4" * 64,
        labels={},
        mounts=[dict(database, Name=dbcopy, Source="/vol/" + dbcopy)],
    )
    records.extend([preserve, restore, clone])
    if case == "unknown-project":
        records.append(dict(records[0], id="5" * 64, mounts=[]))
    if case == "source-running":
        records[0]["running"] = True
    if case == "clone-running":
        clone["running"] = True
    if case == "reader-writable":
        preserve["mounts"][0]["RW"] = True
    if case == "external-source":
        records.append(dict(records[0], id="5" * 64, labels={}))
    if case == "source-field-added":
        records[0]["mounts"][0]["Unexpected"] = "PRIVATE_SENTINEL"
    config = dict(
        preservation_source_reference="a" * 40,
        api_container_id="a" * 64,
        database_container_id="b" * 64,
    )
    (tmp_path / "scope.json").write_text(
        json.dumps(
            dict(records=records, state=state, backup=backup, recovery=recovery, config=config)
        )
    )
    script = tmp_path / "scope-test.ps1"
    script.write_text(
        """
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/CONTEXT.ps1"
. "$PSScriptRoot/COMMON.ps1"
. "$PSScriptRoot/SUPPORT.ps1"
. "$PSScriptRoot/HANDOFF_CORE.ps1"
$script:scope=Get-Content -Raw "$PSScriptRoot/scope.json" | ConvertFrom-Json
function Container-Ids {return @($script:scope.records | ForEach-Object {$_.id} | Sort-Object)}
function Read-Container([string]$Id) {return ($script:scope.records | Where-Object {$_.id -ceq $Id} | ConvertTo-Json -Depth 12 | ConvertFrom-Json)}
function Docker-Lines {return @('"local"','"local"','null')}
function Backup-Docker([string[]]$Arguments,[int]$Maximum=128) {
 if ($Arguments[0] -ceq 'image') {return $Arguments[-1]}
 if ($Arguments[2] -ceq '{{.State.ExitCode}} {{.State.OOMKilled}}') {return """
        + ("'137 false'" if case == "clone-exit" else "'0 false'")
        + """ }
 if ($Arguments[2] -ceq '{{.HostConfig.NetworkMode}}') {return """
        + ("'bridge'" if case == "clone-network" else "'none'")
        + """ }
 throw 'UNEXPECTED_DOCKER_CALL'
}
try {$null=Assert-HandoffStoppedScope $script:scope.config $script:scope.backup $script:scope.state $script:scope.recovery; Write-Output 'OK'} catch {Write-Output $_.Exception.Message}
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
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == expected, (
        result.stdout + result.stderr
    )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,expected",
    [
        ("create", "OK"),
        ("existing", "HANDOFF_DESTINATION_EXISTS"),
        ("extra-file", "HANDOFF_DESTINATION_CONTENT_INVALID"),
        ("nested", "HANDOFF_DESTINATION_CONTENT_INVALID"),
        ("link", "HANDOFF_DESTINATION_CONTENT_INVALID"),
    ],
)
def test_exfat_directory_path_never_uses_ntfs_acl_and_keeps_existing_content(
    tmp_path, case, expected
):
    materialize(tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    token = "a" * 32
    target = media / ("WBKANG-DEMO-Offline-20261114-" + token)
    if case in {"existing", "extra-file", "nested", "link"}:
        target.mkdir()
    if case == "existing":
        (target / "keep").write_bytes(b"KEEP")
    if case == "extra-file":
        (target / "UNEXPECTED").write_bytes(b"PRIVATE_SENTINEL")
    if case == "nested":
        (target / "nested").mkdir()
    if case == "link":
        (target / "database.tar").symlink_to(tmp_path / "README_KO.md")
    script = tmp_path / "exfat-directory-test.ps1"
    script.write_text(
        """
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. "$PSScriptRoot/HANDOFF_CORE.ps1"
function Assert-PrivateBackupDirectory {throw 'NTFS_ACL_MUST_NOT_RUN'}
function Assert-PrivateBackupFiles {throw 'NTFS_ACL_MUST_NOT_RUN'}
try {
"""
        + (
            " $null=New-HandoffDirectory \"$PSScriptRoot/media\" ('a'*32) 'exFAT'\n"
            if case in {"create", "existing"}
            else ' Assert-HandoffDestination "$PSScriptRoot/media/WBKANG-DEMO-Offline-20261114-'
            + token
            + "\" 'exFAT'\n"
        )
        + """ Write-Output 'OK'
} catch {Write-Output $_.Exception.Message}
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
    assert result.returncode == 0 and not result.stderr and result.stdout.strip() == expected, (
        result.stdout + result.stderr
    )
    if case == "create":
        assert target.is_dir() and not list(target.iterdir())
    if case == "existing":
        assert (target / "keep").read_bytes() == b"KEEP"
