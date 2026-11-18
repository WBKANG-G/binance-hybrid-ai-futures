"""Receipt-bound restored-helper admission for original byte comparison."""

import hashlib
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_source_compare_v2_bundle as builder
from tests.unit import test_offline_handoff as handoff
from tests.unit import test_source_compare_bundle as comparison
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch


def package():
    release = os.getenv("SOURCE_COMPARE_V2_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        if args[0] == "show" and args[1].startswith(ref + ":tools/windows_demo_source_compare_v2/"):
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


# Exercise the existing full comparator safety suite against this version's exact bytes.
@pytest.fixture(autouse=True)
def versioned_package(monkeypatch):
    monkeypatch.setattr(comparison, "package", package)


test_matching_two_passes_preserve_bytes_metadata_and_no_raw_output = (
    comparison.test_matching_two_passes_preserve_bytes_metadata_and_no_raw_output
)
test_mismatch_and_unsupported_files_fail_closed_without_writes = (
    comparison.test_mismatch_and_unsupported_files_fail_closed_without_writes
)
test_helper_must_be_new_stopped_exact_image_and_readonly_isolated = (
    comparison.test_helper_must_be_new_stopped_exact_image_and_readonly_isolated
)
test_real_docker_readonly_source_mounts_and_worker = (
    comparison.test_real_docker_readonly_source_mounts_and_worker
)
test_real_docker_new_helper_admission_and_isolation = (
    comparison.test_real_docker_new_helper_admission_and_isolation
)
test_orchestration_before_after_guards_and_no_source_start = (
    comparison.test_orchestration_before_after_guards_and_no_source_start
)
test_transfer_seal_blocks_before_docker_or_input_access = (
    comparison.test_transfer_seal_blocks_before_docker_or_input_access
)


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
def test_existing_receipt_bound_guard_accepts_nine_project_members_but_not_unknowns(
    tmp_path, case, expected
):
    files = package()
    mapped = {
        "CONTEXT.ps1": files["CONTEXT.ps1"],
        "COMMON.ps1": files["SUPPORT.ps1"],
        "SUPPORT.ps1": files["SOURCE_SCOPE.ps1"],
        "HANDOFF_CORE.ps1": files["RESTORE_EVIDENCE.ps1"],
    }
    with patch.object(handoff, "package", lambda: mapped):
        handoff.test_real_scope_validator_rejects_unreviewed_or_running_resources(
            tmp_path, case, expected
        )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "case,expected",
    [
        ("valid", "ACCEPTED"),
        ("wrong-bind", "HANDOFF_RESTORE_BIND_SOURCE_CHANGED"),
        ("rw-bind", "HANDOFF_RESTORE_BIND_SOURCE_CHANGED"),
        ("wrong-id", "HANDOFF_HELPER_CONFIGURATION_CHANGED"),
        ("receipt-hash", "HANDOFF_RESTORE_HASH_MISMATCH"),
        ("receipt-link", "HANDOFF_RESTORE_RECEIPT_INVALID"),
    ],
)
def test_restore_identity_and_bind_are_bound_to_pinned_receipt_and_backup(tmp_path, case, expected):
    backup, proof = handoff.synthetic_inputs(tmp_path)
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    # synthetic_inputs config is intentionally separate from the production sealed config.
    recovery = json.loads(proof.read_text())
    config = dict(
        restore_receipt_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),
        restore_source_reference=recovery["source_reference"],
        manifest_sha256=recovery["backup_manifest_sha256"],
        receipt_sha256=recovery["backup_receipt_sha256"],
    )
    if case == "receipt-hash":
        config["restore_receipt_sha256"] = "0" * 64
    if case == "receipt-link":
        config["restore_source_reference"] = "0" * 40
    worker = dict(
        id="d" * 64,
        mounts=[
            dict(
                Type="bind",
                Destination="/bundle",
                Source=str(backup) if case != "wrong-bind" else "/OTHER",
                RW=(case == "rw-bind"),
            )
        ],
    )
    if case == "wrong-id":
        worker["id"] = "c" * 64
    (tmp_path / "input.json").write_text(json.dumps(dict(config=config, worker=worker)))
    body = f"""
. '{tmp_path}/CONTEXT.ps1'
. '{tmp_path}/SUPPORT.ps1'
. '{tmp_path}/SOURCE_SCOPE.ps1'
. '{tmp_path}/RESTORE_EVIDENCE.ps1'
. '{tmp_path}/REVIEWED_SCOPE.ps1'
$data=Get-Content -Raw '{tmp_path}/input.json'|ConvertFrom-Json
function Find-RestoreEvidence {{param($Config) return '{proof}'}}
function Find-RetainedBackup {{param($Config) return '{backup}'}}
function Assert-HandoffStoppedScope {{param($Config,$Receipt,$State,$Recovery) return ,@($data.worker)}}
try{{$null=Read-RestoreSources $data.config $null $null;'ACCEPTED'}}catch{{$_.Exception.Message}}finally{{if($null -ne $script:ReviewedRestoreHandle){{$script:ReviewedRestoreHandle.Dispose()}}}}
"""
    value = launch(tmp_path, body)
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert value.stdout.strip() == expected


def test_scope_guards_and_worker_remain_pinned_and_no_helper_name_allowlist():
    files = package()
    from scripts.build_windows_source_compare_bundle import package_files as old

    previous = old(builder.PIN)
    assert files["worker.py"] == previous["worker.py"]
    assert files["SOURCE_SCOPE.ps1"] == previous["SOURCE_SCOPE.ps1"]
    assert b"agitated_williams" not in files["REVIEWED_SCOPE.ps1"]
    assert b"brave_wing" not in files["REVIEWED_SCOPE.ps1"]
    assert b"Assert-HandoffStoppedScope" in files["REVIEWED_SCOPE.ps1"]
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    for n, h in manifest["files"].items():
        assert hashlib.sha256(files[n]).hexdigest() == h
    assert b"Read-RestoreSources" in files["REVIEWED_SCOPE.ps1"]
    assert (
        b"'RESTORE_EVIDENCE.ps1','REVIEWED_SCOPE.ps1','COMPARE_CORE.ps1'"
        in files["RUN_COMPARE.ps1"]
    )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_sealed_v2_launcher_loads_receipt_scope_before_check_and_creates_nothing(tmp_path):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    backup = tmp_path / "backup"
    backup.mkdir()
    for name in (
        "BACKUP_RECEIPT.json",
        "BACKUP_STATE.json",
        "manifest.json",
        "database.tar",
        "runtime.tar",
        "images.tar",
    ):
        (backup / name).write_bytes(b"{}")
    ext = tmp_path / "REVIEWED_SCOPE.ps1"
    ext.write_text(
        ext.read_text()
        + f"""
function Read-RestoreJson {{param($Path) return [pscustomobject]@{{}}}}
function Find-RetainedBackup {{param($Config) return '{backup}'}}
function Assert-InputHashes {{param($Directory,$Config,$Receipt)}}
function Read-RestoreSources {{param($Config,$Receipt,$State) return ,@([pscustomobject]@{{id=('b'*64)}})}}
function docker {{throw 'NO_DOCKER_COMMAND_ALLOWED'}}
"""
    )
    m = json.loads(files["TRANSFER_MANIFEST.json"])
    m["files"][ext.name] = hashlib.sha256(ext.read_bytes()).hexdigest()
    raw = json.dumps(m).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    value = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_COMPARE.ps1' -ManifestSHA256 '{hashlib.sha256(raw).hexdigest()}' -CheckOnly\nexit $LASTEXITCODE",
    )
    assert value.returncode == 0 and not value.stderr, value.stdout + value.stderr
    assert "SOURCE_COMPARE_CHECK_PASSED_NO_SOURCE_START" in value.stdout
    assert "NO_DOCKER_COMMAND_ALLOWED" not in value.stdout
