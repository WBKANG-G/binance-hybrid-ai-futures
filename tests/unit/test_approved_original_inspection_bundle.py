"""The exact approved start scope cannot expand reviewed execution or be replayed."""

import copy
import hashlib
import json
import os
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_approved_original_inspection_bundle as builder
from services.execution.demo_original_inspection_acceptance import (
    RECEIPT_SHA256,
    InspectionAcceptanceBlocked,
    verify_inspection_acceptance,
)
from services.execution.demo_source_acceptance import digest
from tests.unit import test_original_inspection_bundle as reviewed
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_copy_commit_bundle import database as database
from tests.unit.test_evidence_receipt_review import launch


def package():
    release = os.getenv("APPROVED_ORIGINAL_INSPECTION_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    def read(*args):
        if args[0] == "show" and args[1].split(":", 1)[-1] in {
            "docs/reviews/20261124/ORIGINAL_START_ACCEPTANCE_RECEIPT.json",
            "tools/windows_demo_approved_original_inspection/README_KO.md",
        }:
            return (ROOT / args[1].split(":", 1)[-1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def evidence():
    f = package()
    p = json.loads(
        builder.git(
            "show",
            builder.REVIEW_RELEASE
            + ":docs/reviews/20261124/ORIGINAL_INSPECTION_PENDING_QUESTION.json",
        )
    )
    return p, f["INSPECTION_PROPOSAL.json"], json.loads(f["ORIGINAL_START_ACCEPTANCE.json"])


def test_exact_bound_reply_grants_one_physical_start_and_no_logical_writes():
    p, b, r = evidence()
    before = copy.deepcopy((p, b, r))
    v = verify_inspection_acceptance(p, b, r)
    assert (
        r["operator_reply"] == "다음 단계 진행해줘" and not r["exact_message_timestamp_available"]
    )
    assert digest(r) == RECEIPT_SHA256
    assert (
        v["original_database_start_authorized"]
        and v["postgresql_startup_shutdown_storage_writes_authorized"]
    )
    assert not v["original_ledger_write_authorized"] and not v["original_apply_ready"]
    assert (p, b, r) == before


@pytest.mark.parametrize(
    "field,value",
    [
        (k, True)
        for k in (
            "original_ledger_write_authorized",
            "owner_lease_takeover_authorized",
            "baseline_adoption_authorized",
            "request_resolution_authorized",
            "resend_authorized",
            "execution_approved",
            "orders_enabled",
            "live_trading_enabled",
        )
    ]
    + [
        ("original_database_start_authorized", False),
        ("postgresql_startup_shutdown_storage_writes_authorized", False),
        ("single_attempt_only", False),
        ("operator_reply", "다른 답변"),
        ("question_text_sha256", "f" * 64),
        ("proposal_file_sha256", "f" * 64),
        ("reviewed_source_reference", "f" * 40),
        ("approval_id", "f" * 64),
        ("exact_message_timestamp_available", True),
    ],
)
def test_receipt_mutation_cannot_keep_trusted_scope(field, value):
    p, b, r = evidence()
    r[field] = value
    with pytest.raises(InspectionAcceptanceBlocked):
        verify_inspection_acceptance(p, b, r)


def test_proposal_cannot_change_even_if_someone_recomputes_its_own_hash():
    p, b, r = evidence()
    q = json.loads(b)
    q["original_ledger_update"] = True
    altered = json.dumps(q).encode()
    p["proposal_file_sha256"] = hashlib.sha256(altered).hexdigest()
    r["proposal_file_sha256"] = p["proposal_file_sha256"]
    with pytest.raises(InspectionAcceptanceBlocked):
        verify_inspection_acceptance(p, altered, r)


def test_all_reviewed_execution_bytes_and_historical_consents_are_unchanged():
    f = package()
    old = builder.review_files(builder.REVIEW_SOURCE)
    p = json.loads(f["INSPECTION_PROPOSAL.json"])
    assert f["INSPECTION_PROPOSAL.json"] == old["INSPECTION_PROPOSAL.json"]
    for n, h in p["execution_file_sha256"].items():
        assert f[n] == old[n] and hashlib.sha256(f[n]).hexdigest() == h
    for n in ("SOURCE_ACCEPTANCE.json", "SOURCE_PROPOSAL.json", "OWNER_WINDOWS_OBSERVATION.json"):
        assert f[n] == old[n]
    allowed = {
        "CONFIG.json",
        "ORIGINAL_START_ACCEPTANCE.json",
        "README_KO.md",
        "TRANSFER_MANIFEST.json",
        "RUN_CHECK.cmd",
        "RUN_INSPECT.cmd",
    }
    assert {n for n in f if f[n] != old[n]} <= allowed
    c = json.loads(f["CONFIG.json"])
    r = f["ORIGINAL_START_ACCEPTANCE.json"]
    assert c["inspection_approval_receipt_file_sha256"] == hashlib.sha256(r).hexdigest()
    assert json.loads(f["SOURCE_ACCEPTANCE.json"])["original_database_start_authorized"] is False
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    assert all(hashlib.sha256(f[n]).hexdigest() == h for n, h in m["files"].items())


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_real_record_passes_native_unchanged_acceptance_gate_without_start(tmp_path):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    v = launch(
        tmp_path,
        f"""
. '{tmp_path}/INSPECTION_CORE.ps1'
function Restore-Hash {{param($Path) return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()}}
function Read-RestoreJson {{param($Path) return Get-Content -Raw $Path|ConvertFrom-Json}}
$c=Read-RestoreJson '{tmp_path}/CONFIG.json'
$id=Assert-InspectionApproval '{tmp_path}' $c
[pscustomobject]@{{id=$id;approved=$script:InspectionApproved;volume=$script:InspectionVolume}}|ConvertTo-Json -Compress
""",
    )
    assert not v.stderr and v.returncode == 0, v.stdout + v.stderr
    r = json.loads(v.stdout)
    assert (
        r["approved"] and r["id"] == json.loads(f["ORIGINAL_START_ACCEPTANCE.json"])["approval_id"]
    )
    assert r["volume"] == "hybrid-windows_postgres_data"


def stage_fixture(tmp_path, fault="none"):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    backup = tmp_path / "backup"
    backup.mkdir()
    for n in (
        "BACKUP_RECEIPT.json",
        "BACKUP_STATE.json",
        "database.tar",
        "runtime.tar",
        "images.tar",
    ):
        (backup / n).write_text("{}")
    (backup / "manifest.json").write_text(
        json.dumps(
            dict(
                groups={
                    g: dict(entries=[dict(kind="file")], logical_bytes=16)
                    for g in ("database", "runtime")
                }
            )
        )
    )
    approval_id = json.loads(f["ORIGINAL_START_ACCEPTANCE.json"])["approval_id"]
    ext = tmp_path / "FINANCIAL_EXT.ps1"
    ext.write_text(
        ext.read_text()
        + f"""
function Find-RetainedBackup {{param($Config) return '{backup}'}}
function Assert-InputHashes {{param($Directory,$Config,$Receipt)}}
function Read-CopySources {{param($Config,$Receipt,$State) $script:FinancialPrior=[pscustomobject]@{{account_state_sha256=('a'*64)}};return ,@([pscustomobject]@{{id=('b'*64)}})}}
function Assert-RestoreSourceSnapshot {{param($Original,$Helpers,$Frozen) if($Frozen -or $script:RefreshHelperVisible -ne ($Helpers.Count -eq 1)){{throw 'ORIGINAL_TEST_SNAPSHOT_ORDER_INVALID'}}}}
function New-PrivateBackupDirectory {{param($Token) if($Token -cne '{approval_id[:32]}'){{throw 'ORIGINAL_TEST_ATTEMPT_ID_INVALID'}};$d=Join-Path '{tmp_path}' ('attempt-'+$Token);if(Test-Path -LiteralPath $d){{throw 'COLD_DESTINATION_EXISTS'}};$null=New-Item -ItemType Directory $d;return $d}}
"""
    )
    refresh = tmp_path / "REFRESH_CORE.ps1"
    refresh.write_text(
        refresh.read_text()
        + f"""
function Assert-RefreshReader {{param($Config,$State,$Original)}}
function Invoke-RefreshPayload {{param($Config,$State,$Original,$Payload) if('{fault}' -ceq 'compare'){{throw 'COMPARE_READER_RUN_FAILED'}};return [pscustomobject]@{{status='STOPPED_SOURCE_TREES_MATCH_PINNED_BACKUP';failure_category='NONE';passes=2;file_counts=[pscustomobject]@{{database=1;runtime=1}};logical_bytes=[pscustomobject]@{{database=16;runtime=16}};source_data_written=$false;logical_account_row_queried=$false}}}}
"""
    )
    core = tmp_path / "INSPECTION_CORE.ps1"
    core.write_text(
        core.read_text()
        + f"""
function Invoke-OriginalInspection {{param($Root,$Config,$State,$Original,$Directory,$Manifest) if(-not $script:InspectionApproved){{throw 'ORIGINAL_TEST_APPROVAL_NOT_ESTABLISHED'}};[IO.File]::AppendAllText('{tmp_path}/original-calls','once');$script:InspectionStarted=$true;$script:InspectionEverRunning=$true;$script:InspectionStopped=$true;Write-Output 'ORIGINAL_TEST_BOUND_INSPECTION_CALLED'}}
"""
    )
    if fault == "approval-hash":
        c = json.loads((tmp_path / "CONFIG.json").read_bytes())
        c["inspection_approval_receipt_file_sha256"] = "f" * 64
        (tmp_path / "CONFIG.json").write_text(json.dumps(c))
    m = json.loads(f["TRANSFER_MANIFEST.json"])
    for n in ("FINANCIAL_EXT.ps1", "REFRESH_CORE.ps1", "INSPECTION_CORE.ps1", "CONFIG.json"):
        m["files"][n] = hashlib.sha256((tmp_path / n).read_bytes()).hexdigest()
    raw = json.dumps(m).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def invoke(tmp_path, pin, mode="InspectOnce"):
    return launch(
        tmp_path,
        f"function docker {{throw 'ORIGINAL_TEST_REAL_DOCKER_FORBIDDEN'}}\n& '{tmp_path}/RUN_INSPECT.ps1' -ManifestSHA256 '{pin}' -{mode}\nexit $LASTEXITCODE",
    )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("fault", ["none", "compare", "approval-hash", "check"])
def test_authorized_launcher_reaches_inspection_only_after_binding_comparison_and_claim(
    tmp_path, fault
):
    pin = stage_fixture(tmp_path, fault)
    v = invoke(tmp_path, pin, "CheckOnly" if fault == "check" else "InspectOnce")
    assert not v.stderr, v.stdout + v.stderr
    assert v.returncode == (0 if fault in {"none", "check"} else 2), v.stdout
    assert (tmp_path / "original-calls").exists() == (fault == "none")
    if fault in {"check", "approval-hash"}:
        assert not list(tmp_path.glob("attempt-*"))
    if fault == "compare":
        assert "COMPARE_READER_RUN_FAILED" in v.stdout
    if fault == "approval-hash":
        assert "ORIGINAL_APPROVAL_HASH_MISMATCH" in v.stdout
    assert "ORIGINAL_TEST_REAL_DOCKER_FORBIDDEN" not in v.stdout


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("first_fault", ["none", "compare"])
def test_same_approval_cannot_repeat_after_success_or_comparison_failure(tmp_path, first_fault):
    pin = stage_fixture(tmp_path, first_fault)
    first = invoke(tmp_path, pin)
    assert first.returncode == (0 if first_fault == "none" else 2), first.stdout
    path = tmp_path / "original-calls"
    before = path.read_bytes() if path.exists() else b""
    second = invoke(tmp_path, pin)
    assert second.returncode == 2 and not second.stderr, second.stdout + second.stderr
    assert "COLD_DESTINATION_EXISTS" in second.stdout
    assert (path.read_bytes() if path.exists() else b"") == before


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "name",
    [
        "ORIGINAL_START_ACCEPTANCE.json",
        "CONFIG.json",
        "original_read.sql",
        "INSPECTION_PROPOSAL.json",
    ],
)
def test_approved_packet_tamper_rejected_before_source_access(tmp_path, name):
    f = package()
    for n, b in f.items():
        (tmp_path / n).write_bytes(b)
    (tmp_path / name).write_text("PRIVATE_DO_NOT_EXECUTE")
    v = invoke(tmp_path, hashlib.sha256(f["TRANSFER_MANIFEST.json"]).hexdigest())
    assert v.returncode == 2 and not v.stderr
    assert "PRIVATE_DO_NOT_EXECUTE" not in v.stdout and "SCOPE_TRANSFER_INVALID" in v.stdout


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault", ["valid", "rw", "extra-mount", "original-id", "network", "command", "ports"]
)
def test_reviewed_host_admission_still_holds_in_approved_packet(tmp_path, fault):
    with patch.object(reviewed, "package", package):
        reviewed.test_host_admission_rejects_identity_mount_command_or_isolation_drift(
            tmp_path, fault
        )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("fault", ["none", "scope", "copy-claim", "readonly", "fence"])
def test_reviewed_original_sql_validation_is_unchanged(tmp_path, fault):
    with patch.object(reviewed, "package", package):
        reviewed.test_original_projection_validator_requires_original_scope_and_original_guards(
            tmp_path, fault
        )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("fault", ["none", "query"])
def test_approved_packet_real_synthetic_start_stop_and_failure_cleanup(tmp_path, database, fault):
    with patch.object(reviewed, "package", package):
        reviewed.test_synthetic_volume_actual_start_read_twice_stop_without_account_changes(
            tmp_path, database, fault
        )
