"""Strict completion schema, authority separation, and real read-only copy worker."""

import copy
import hashlib
import json
import os
import subprocess
import zipfile
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from scripts import build_windows_adoption_model_bundle as builder
from services.execution import demo_period_adoption_model as model
from tests.unit import test_typed_draft_bundle as prior
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch


def package():
    release = os.getenv("ADOPTION_MODEL_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git

    def read(*args):
        if args[0] == "show" and args[1].split(":", 1)[-1] in {
            "services/execution/demo_period_adoption_model.py",
            "tools/windows_demo_adoption_model/README_KO.md",
        }:
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def rehearsal():
    before, _, report, expected = prior.draft()
    expected = expected | {
        "candidate_financial_state_sha256": report["candidate_financial_state_sha256"]
    }
    original = copy.deepcopy(before)
    with patch.object(
        model.PeriodCompletionAudit,
        "model_validate",
        wraps=model.PeriodCompletionAudit.model_validate,
    ) as validated:
        result = model.completion_rehearsal(before, expected)
        receipt = copy.deepcopy(validated.call_args_list[0].args[0])
    assert before == original
    return result, receipt


def test_rehearsal_validates_completion_candidate_without_exporting_or_authorizing_it():
    result, receipt = rehearsal()
    assert result["completion_audit_model_validated"]
    assert result["completion_candidate_model_validated"]
    assert result["completion_protected_state_preserved"]
    assert result["rehearsal_receipt_rejected_for_real_application"]
    assert result["completion_rehearsal_context"] == "COPY_REHEARSAL_ONLY"
    assert not result["completion_payload_returned"]
    assert not result["source_acceptance_receipt_created"]
    assert "PRIVATE_" not in json.dumps(result)
    with pytest.raises(model.DraftModelBlocked, match="MODEL_REHEARSAL_CANNOT_AUTHORIZE"):
        model.validate_real_completion(
            receipt, verified_acceptance_sha256=receipt["source_acceptance_receipt_sha256"]
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("applied", 1),
        ("source_accepted", "true"),
        ("execution_approved", 0),
        ("midnight_equity_independently_verified", True),
        ("account_wide_history_verified", True),
        ("initial_funding_verified", True),
        ("committed_fence", 110),
        ("expected_fence", True),
        ("operation_id", "not-a-uuid"),
        ("recorded_at_utc", "2026-11-18T05:40:00"),
        ("recorded_at_utc", "2026-11-18T14:40:00+09:00"),
        ("extra_claim", True),
    ],
)
def test_completion_schema_refuses_invalid_or_overstated_claims(key, value):
    _, receipt = rehearsal()
    receipt[key] = value
    with pytest.raises((ValidationError, ValueError)):
        model.PeriodCompletionAudit.model_validate(receipt)


def test_real_shape_still_requires_separately_verified_acceptance_binding():
    _, receipt = rehearsal()
    receipt["context"] = "ACCEPTED_APPLICATION"
    for missing in (None, "0" * 64):
        with pytest.raises(model.DraftModelBlocked, match="MODEL_VERIFIED_ACCEPTANCE_REQUIRED"):
            model.validate_real_completion(receipt, verified_acceptance_sha256=missing)


def test_old_unapproved_draft_is_not_completion_and_real_flags_remain_false():
    args = prior.draft()
    result = model.review_models(*args)
    assert result["completion_audit_model_validated"]
    for key in (
        "source_accepted",
        "database_changed",
        "audit_record_written",
        "baseline_adopted",
        "apply_token_created",
        "commit_payload_verified",
        "orders_enabled",
        "live_trading_enabled",
    ):
        assert result[key] is False
    with pytest.raises(ValidationError):
        model.PeriodCompletionAudit.model_validate(args[2]["audit_draft"])


def test_release_retains_readonly_worker_sql_and_restore_control_bytes():
    files = package()
    old = builder.typed_files(builder.PIN)
    for n in (
        "typed_worker.py",
        "account_check.sql",
        "restore_worker.py",
        "cold_archive.py",
        "COPY_EXT.ps1",
        "FINANCIAL_EXT.ps1",
        "DRAFT_EXT.ps1",
        "SOURCE_SCOPE.ps1",
        "SUPPORT.ps1",
    ):
        assert files[n] == old[n]
    config = json.loads(files["CONFIG.json"])
    assert hashlib.sha256(files["model_review.py"]).hexdigest() == config["candidate_sha256"]
    assert len(config["baseline_files"]) == 89
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    for n, digest in manifest["files"].items():
        assert hashlib.sha256(files[n]).hexdigest() == digest
    assert set(n for n in files if n.endswith(".cmd")) == {
        "RUN_CHECK.cmd",
        "RUN_REHEARSE_MODEL.cmd",
    }


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_all_powershell_parses_and_new_module_tamper_is_refused(tmp_path):
    with patch.object(prior, "package", return_value=package()):
        prior.test_all_ps_scripts_parse_and_transfer_tampering_refused(tmp_path)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_sealed_precheck_starts_no_resources(tmp_path):
    # Reuse the established no-mutation fixture. Only its final marker assertion
    # differs; check the actual new marker before adapting it for that assertion.
    real_launch = prior.launch
    checked = []

    def checked_launch(*args, **kwargs):
        value = real_launch(*args, **kwargs)
        marker = "ADOPTION_MODEL_CHECK_PASSED_NO_CLONE_NO_SOURCE_START"
        assert marker in value.stdout
        checked.append(True)
        return subprocess.CompletedProcess(
            value.args,
            value.returncode,
            value.stdout.replace(marker, "TYPED_DRAFT_CHECK_PASSED_NO_CLONE_NO_SOURCE_START"),
            value.stderr,
        )

    with (
        patch.object(prior, "package", return_value=package()),
        patch.object(prior, "launch", checked_launch),
    ):
        prior.test_sealed_check_loads_model_extension_and_cannot_start_resources(tmp_path)
    assert checked == [True]


@pytest.fixture(scope="module")
def database():
    yield from prior.isolated_model_worker_database.__wrapped__()


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("valid", None),
        ("module-hash", "MODEL_SOURCE_MISMATCH"),
        ("expected-digest", "MODEL_EXPECTED_DRAFT_MISMATCH"),
    ],
)
def test_actual_nonroot_readonly_worker_preserves_table(database, fault, expected):
    files = package()
    real_run = subprocess.run
    seen = []

    def run(*args, **kwargs):
        result = real_run(*args, **kwargs)
        if result.stdout and isinstance(result.stdout, str):
            try:
                value = json.loads(result.stdout)
            except ValueError:
                pass
            else:
                if isinstance(value, dict) and "typed_draft_review" in value:
                    seen.append(value)
        return result

    with patch.object(prior, "package", return_value=files), patch.object(subprocess, "run", run):
        prior.test_real_nonroot_networkless_worker_reads_models_and_sql_without_writes(
            database, fault, expected
        )
    assert len(seen) == 1
    if expected is None:
        assert seen[0]["completion_audit_model_validated"]
        assert seen[0]["completion_candidate_model_validated"]
        assert seen[0]["rehearsal_receipt_rejected_for_real_application"]
        assert seen[0]["completion_rehearsal_context"] == "COPY_REHEARSAL_ONLY"


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_actual_powershell_new_result_admission(database, tmp_path):
    socket, api, args = database
    files = package()
    for n, body in files.items():
        (tmp_path / n).write_bytes(body)
    params = dict(
        draft_day="2026-11-18",
        draft_week="2026-11-16",
        draft_amount="4998.74183608",
        draft_evidence="e" * 64,
    )
    cli = [part for k, v in params.items() for part in ("-v", k + "=" + v)]
    account = subprocess.run(
        args + cli,
        input=files["account_check.sql"].decode(),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert account.returncode == 0, account.stderr
    (tmp_path / "account.json").write_text(account.stdout)
    api_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", api], text=True
    ).strip()
    directory = tmp_path / "review"
    directory.mkdir()
    script = f"""
foreach($n in @('CONTEXT.ps1','COMMON.ps1','SUPPORT.ps1','MODEL_EXT.ps1')){{. (Join-Path '{tmp_path}' $n)}}
function Assert-RestoreSourceSnapshot {{param($Original,$Helpers,$Frozen) if($Frozen -or $Helpers.Count -ne 1){{throw 'MODEL_TEST_SCOPE'}}}}
$script:DraftEvidence=[pscustomobject]@{{day='2026-11-18';week='2026-11-16';amount='4998.74183608';sha256=('e'*64)}}
$config=Get-Content -Raw '{tmp_path}/CONFIG.json'|ConvertFrom-Json
$account=Get-Content -Raw '{tmp_path}/account.json'|ConvertFrom-Json
try{{$null=Invoke-TypedDraftReview '{tmp_path}' $config ([pscustomobject]@{{canonical_images=@('{api_id}')}}) '{socket}' @([pscustomobject]@{{id=('b'*64)}}) @() '{directory}' $account;$script:TypedReview|ConvertTo-Json -Depth 6 -Compress}}finally{{if($null -ne $script:CopyDiagnosticId){{$null=& docker rm $script:CopyDiagnosticId}}}}
"""
    result = launch(tmp_path, script)
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["completion_audit_model_validated"]
    assert value["rehearsal_receipt_rejected_for_real_application"]
    assert (directory / "ADOPTION_MODEL_REVIEW.json").exists()
    assert "PRIVATE_" not in result.stdout
