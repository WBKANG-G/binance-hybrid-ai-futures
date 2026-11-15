"""Native PowerShell reproduces console truncation and reviews files without mutation."""

import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest

from scripts import build_windows_evidence_receipt_review_bundle as builder
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_copy_evidence_bundle import package as pipeline_package

pytestmark = pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")


def package():
    release = os.getenv("RECEIPT_REVIEW_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    ref = real("rev-parse", "HEAD").decode().strip()

    def read(*args):
        return (
            (ROOT / args[1].split(":", 1)[1]).read_bytes()
            if args[0] == "show" and args[1].startswith(ref + ":")
            else real(*args)
        )

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def launch(tmp_path, body):
    script = tmp_path / "test.ps1"
    script.write_text("$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n" + body)
    return subprocess.run(
        [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(script)],
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


def fixture_data():
    cfg = json.loads(package()["CONFIG.json"])
    required = [
        "REMOTE_ACCOUNT_IDENTITY_AND_CREDENTIAL_CONTEXT_PROOF",
        "ACCOUNT_AND_SYMBOL_CONFIGURATION_PROOF",
        "REGULAR_AND_ALGO_OPEN_ORDER_PROOF",
        "COMPLETE_TRADE_INCOME_TRANSFER_COVERAGE",
        "DAY_BOUNDARY_VALUATION_OR_RECONSTRUCTION_PROOF",
        "EXPLICIT_OPERATOR_STATEMENT_AND_SOURCE_ACCEPTANCE",
        "HISTORICAL_REQUESTS_REQUIRE_INDEPENDENT_REMOTE_PROOF",
        "CURRENT_PERIOD_AND_LOSS_HIGH_WATER_RECHECK",
    ]
    content = dict(
        account_content_review="NARROW_FLAT_USDT_ARITHMETIC_CONSISTENT",
        asset_rows_checked=1,
        position_rows_checked=1,
        positive_usdt_wallet_observed=True,
        remaining_requirements=required,
        account_receipt_sha256="a" * 64,
    )
    for field in (
        "account_configuration_verified",
        "open_orders_verified",
        "remote_account_identity_verified",
        "baseline_amount_proposed",
        "baseline_source_accepted",
        "baseline_adoption_approved",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
        "proposal_input_shape_complete",
        "caller_claims_present",
        "operator_statement_independently_verified",
        "referenced_proofs_verified",
        "whole_history_coverage_verified",
        "midnight_valuation_verified",
        "stored_periods_verified_against_ledger",
    ):
        content[field] = False
    pipeline = dict(
        content_review=content,
        audit_events_before=56,
        audit_events_after=58,
        synthetic_sender_calls=1,
        exchange_http_attempts=0,
        order_writes=0,
        synthetic_copy_evidence_rehearsal="PIPELINE_VERIFIED_SYNTHETIC_ONLY_KEEP_STOPPED",
        archive_receipt_sha256="a" * 64,
    )
    for field in (
        "startup_ready_observed",
        "evidence_binding_created",
        "protected_rows_and_files_preserved",
        "shared_journal_changed",
        "resource_checkpoint_changed",
    ):
        pipeline[field] = True
    for field in (
        "copy_isolation_independently_verified",
        "exchange_credentials_accessed",
        "financial_ledger_changed",
        "period_baselines_changed",
        "request_states_changed",
        "automatic_retry",
        "installation_ready",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        pipeline[field] = False
    clock = dict(
        synthetic_evidence_rehearsal=pipeline,
        prior_audit_events=55,
        audit_events_after=56,
        prior_revision=58,
        revision_after=59,
        cumulative_weight_before=284,
        cumulative_weight_after=284,
    )
    receipt = dict(
        copy_evidence_result=clock,
        source_reference=cfg["expected_source_reference"],
        prior_clock_receipt_sha256=cfg["prior_clock_receipt_sha256"],
        prior_copy_receipt_sha256=cfg["prior_copy_receipt_sha256"],
        backup_manifest_sha256=cfg["manifest_sha256"],
        backup_receipt_sha256=cfg["receipt_sha256"],
        isolated_restore="COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED",
        clone_network="none",
    )
    for field in (
        "copy_mounts_and_network_verified",
        "input_hashes_reverified",
        "clone_stopped",
        "clone_clean_exit_verified",
        "archive_bytes_and_metadata_verified_before_clone_recovery",
    ):
        receipt[field] = True
    for field in (
        "original_services_started",
        "original_volume_mounted_in_restore_helpers",
        "financial_write_attempted",
        "installation_ready",
        "automatic_retry",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        receipt[field] = False
    return cfg, receipt, clock, pipeline


def test_old_console_stringifies_but_saved_and_fixed_console_preserve_objects(tmp_path):
    cfg, receipt, clock, pipeline = fixture_data()
    (tmp_path / "clock.json").write_text(json.dumps(clock))
    old_zip = (
        ROOT
        / "artifacts/windows-demo-copy-evidence-rehearsal-20261115/windows-demo-copy-evidence-rehearsal-20261115.zip"
    )
    with zipfile.ZipFile(old_zip) as z:
        prefix = old_zip.stem + "/"
        old_core = z.read(prefix + "COPY_CORE.ps1").decode()
        common = z.read(prefix + "COMMON.ps1")
    old_line = next(
        line
        for line in old_core.splitlines()
        if "copy_evidence_rehearsal='FRESH_COPY_SYNTHETIC_EVIDENCE_VERIFIED_ORIGINALS_UNCHANGED'"
        in line
    )
    fixed_line = next(
        line
        for line in pipeline_package()["COPY_CORE.ps1"].decode().splitlines()
        if "copy_evidence_rehearsal='FRESH_COPY_SYNTHETIC_EVIDENCE_VERIFIED_ORIGINALS_UNCHANGED'"
        in line
    )
    assert "ConvertTo-Json -Depth 16" in fixed_line
    (tmp_path / "COMMON.ps1").write_bytes(common)
    result = launch(
        tmp_path,
        f"""
$WarningPreference='SilentlyContinue'
. '{tmp_path / "COMMON.ps1"}'
$script:CopyDiagnostic=Get-Content -Raw '{tmp_path / "clock.json"}' | ConvertFrom-Json
$directory='{tmp_path}'
function Restore-Hash([string]$Path) {{return ('0'*64)}}
$old=& {{ {old_line} }}
$new=& {{ {fixed_line} }}
Write-BackupJson '{tmp_path / "saved.json"}' ([pscustomobject]@{{copy_evidence_result=$script:CopyDiagnostic}})
$disk=Get-Content -Raw '{tmp_path / "saved.json"}'|ConvertFrom-Json
$a=$old|ConvertFrom-Json;$b=$new|ConvertFrom-Json
[pscustomobject]@{{old_is_string=($a.diagnostic.synthetic_evidence_rehearsal.content_review -is [string]);new_is_object=($b.diagnostic.synthetic_evidence_rehearsal.content_review -is [pscustomobject]);disk_is_object=($disk.copy_evidence_result.synthetic_evidence_rehearsal.content_review -is [pscustomobject]);requirements_count=@($disk.copy_evidence_result.synthetic_evidence_rehearsal.content_review.remaining_requirements).Count}}|ConvertTo-Json -Compress
""",
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert json.loads(result.stdout) == dict(
        old_is_string=True, new_is_object=True, disk_is_object=True, requirements_count=8
    )


@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "string",
        "array_string",
        "authority",
        "counter_string",
        "lineage",
        "companion",
        "missing",
        "two_matches",
        "symlink",
        "oversized",
        "invalid_json",
        "requirements",
        "open_originals",
    ],
)
def test_read_only_inspector_preserves_files_and_rejects_invalid_receipts(tmp_path, fault):
    files = package()
    cfg, receipt, clock, pipeline = fixture_data()
    base = tmp_path / "backups"
    base.mkdir()
    folder = base / ("copy-evidence-20261115-" + "a" * 32)
    folder.mkdir()
    if fault == "string":
        pipeline["content_review"] = "@{baseline_adoption_approved=False}"
    if fault == "array_string":
        pipeline["content_review"]["remaining_requirements"] = "System.Object[]"
    if fault == "authority":
        pipeline["content_review"]["baseline_adoption_approved"] = True
    if fault == "counter_string":
        pipeline["audit_events_after"] = "58"
    if fault == "lineage":
        receipt["prior_clock_receipt_sha256"] = "0" * 64
    if fault == "requirements":
        pipeline["content_review"]["remaining_requirements"][-1] = "invented"
    if fault == "open_originals":
        receipt["original_services_started"] = True
    for name, obj in (
        ("COPY_EVIDENCE_RECEIPT.json", receipt),
        ("COPY_CLOCK_RESULT.json", clock),
        ("COPY_EVIDENCE_RESULT.json", pipeline),
    ):
        (folder / name).write_text(json.dumps(obj))
    if fault == "companion":
        (folder / "COPY_EVIDENCE_RESULT.json").write_text("{}")
    if fault == "missing":
        (folder / "COPY_CLOCK_RESULT.json").unlink()
    if fault == "oversized":
        (folder / "COPY_EVIDENCE_RECEIPT.json").write_bytes(b" " * (1048576 + 1))
    if fault == "invalid_json":
        (folder / "COPY_EVIDENCE_RECEIPT.json").write_text("{")
    if fault == "two_matches":
        other = base / ("copy-evidence-20261115-" + "b" * 32)
        other.mkdir()
        (other / "COPY_EVIDENCE_RECEIPT.json").write_text(json.dumps(receipt))
    if fault == "symlink":
        other = tmp_path / "outside.json"
        (folder / "COPY_EVIDENCE_RESULT.json").rename(other)
        (folder / "COPY_EVIDENCE_RESULT.json").symlink_to(other)
    (tmp_path / "REVIEW_CORE.ps1").write_bytes(files["REVIEW_CORE.ps1"])
    (tmp_path / "config.json").write_text(json.dumps(cfg))

    def snapshot():
        return {
            str(p.relative_to(base)): (
                p.lstat().st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
            for p in base.rglob("*")
            if p.is_file()
        }

    before = snapshot()
    result = launch(
        tmp_path,
        f"""
. '{tmp_path / "REVIEW_CORE.ps1"}'
function docker {{throw 'DOCKER_MUST_NOT_BE_CALLED'}}
try {{Review-EvidenceReceipts '{base}' (Get-Content -Raw '{tmp_path / "config.json"}'|ConvertFrom-Json)|ConvertTo-Json -Depth 16 -Compress}}
catch {{Write-Output 'DENIED';exit 2}}
""",
    )
    assert result.returncode == (0 if fault == "valid" else 2) and not result.stderr, (
        result.stdout + result.stderr
    )
    assert snapshot() == before
    if fault == "valid":
        output = json.loads(result.stdout)
        assert output["nested_content_review_is_object"] and output["companion_json_matches"]
        assert (
            not output["receipt_hash_compared_to_external_pin"]
            and not output["docker_used"]
            and not output["files_changed"]
        )


def test_transfer_tamper_never_reads_user_receipts(tmp_path):
    files = package()
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    manifest_pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    (tmp_path / "REVIEW_CORE.ps1").write_text("PRIVATE_SENTINEL")
    result = launch(
        tmp_path,
        f"& '{tmp_path / 'REVIEW.ps1'}' -ManifestSHA256 '{manifest_pin}'\nexit $LASTEXITCODE\n",
    )
    assert result.returncode == 2 and not result.stderr and "PRIVATE_SENTINEL" not in result.stdout
    output = json.loads(result.stdout)
    assert output["failure_category"] == "RECEIPT_TRANSFER_INVALID" and not output["files_changed"]
