"""Pinned source consent, real PowerShell GET mocks, and changed-period separation."""

import hashlib
import json
import os
import zipfile
from datetime import datetime
from unittest.mock import patch

import pytest

from scripts import build_windows_preapply_account_bundle as builder
from services.execution.demo_source_acceptance import (
    ACCEPTED_PROPOSAL_SHA256,
    ACCEPTED_RECEIPT_SHA256,
    SourceAcceptanceBlocked,
    verify_acceptance,
)
from tests.unit import test_current_read_bundle as prior
from tests.unit.test_cold_backup_executor import PWSH, ROOT
from tests.unit.test_evidence_receipt_review import launch


def package():
    release = os.getenv("PREAPPLY_ACCOUNT_RELEASE_ZIP")
    if release:
        with zipfile.ZipFile(release) as z:
            return {n[len(builder.FOLDER) + 1 :]: z.read(n) for n in z.namelist()}
    real = builder.git
    paths = {
        "docs/reviews/20261118/SOURCE_ACCEPTANCE_REVIEW.json",
        "docs/reviews/20261118/SOURCE_ACCEPTANCE_RECEIPT.json",
        "tools/windows_demo_preapply_account/ACCEPTANCE_CORE.ps1",
        "tools/windows_demo_preapply_account/README_KO.md",
    }

    def read(*args):
        if args[0] == "show" and args[1].split(":", 1)[-1] in paths:
            return (ROOT / args[1].split(":", 1)[1]).read_bytes()
        return real(*args)

    with patch.object(builder, "git", read):
        return builder.package_files("HEAD")


def verify(files):
    return verify_acceptance(
        json.loads(files["SOURCE_PROPOSAL.json"]),
        json.loads(files["SOURCE_ACCEPTANCE.json"]),
        trusted_proposal_sha256=ACCEPTED_PROPOSAL_SHA256,
        trusted_receipt_sha256=ACCEPTED_RECEIPT_SHA256,
    )


def test_actual_receipt_binds_user_reply_to_exact_proposal_without_write_authority():
    files = package()
    value = verify(files)
    assert value["source_accepted"] is True
    assert value["candidate"]["day_kst"] == "2026-11-18"
    assert not value["original_database_write_authorized"] and not value["original_apply_ready"]
    receipt = json.loads(files["SOURCE_ACCEPTANCE.json"])
    assert receipt["operator_reply"] == "응 알겠어 다음단계 진행해"
    assert receipt["exact_message_timestamp_available"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_accepted", False),
        ("source_accepted", 1),
        ("original_database_write_authorized", True),
        ("operator_reply", "다른 답변"),
        ("candidate", {}),
        ("acknowledged_unverified_facts", []),
    ],
)
def test_receipt_tamper_cannot_reuse_trusted_digest(field, value):
    files = package()
    receipt = json.loads(files["SOURCE_ACCEPTANCE.json"])
    receipt[field] = value
    with pytest.raises(SourceAcceptanceBlocked, match="SOURCE_RECEIPT_MISMATCH"):
        verify_acceptance(
            json.loads(files["SOURCE_PROPOSAL.json"]),
            receipt,
            trusted_proposal_sha256=ACCEPTED_PROPOSAL_SHA256,
            trusted_receipt_sha256=ACCEPTED_RECEIPT_SHA256,
        )


def test_proposal_changes_cannot_expand_acceptance_to_november23():
    files = package()
    envelope = json.loads(files["SOURCE_PROPOSAL.json"])
    envelope["proposal"]["candidate"]["day_kst"] = "2026-11-23"
    with pytest.raises(SourceAcceptanceBlocked, match="SOURCE_PROPOSAL_MISMATCH"):
        verify_acceptance(
            envelope,
            json.loads(files["SOURCE_ACCEPTANCE.json"]),
            trusted_proposal_sha256=ACCEPTED_PROPOSAL_SHA256,
            trusted_receipt_sha256=ACCEPTED_RECEIPT_SHA256,
        )


def test_transport_plan_and_prior_evidence_readers_are_byte_identical():
    files = package()
    old = builder.current_files(builder.PIN)
    for n in (
        "READ_TRANSPORT.ps1",
        "PLAN_CORE.ps1",
        "SAVED_INCOME_CORE.ps1",
        "SAVED_TRADE_CORE.ps1",
        "READ_CORE.ps1",
        "ARRAY_CORE.ps1",
        "TRADE_HELPERS.ps1",
        "INCOME_HELPERS.ps1",
    ):
        assert files[n] == old[n]
    assert b"WBKANG-DEMO-CurrentReads" not in files["PRIVATE_STORAGE.ps1"]
    assert b"WBKANG-DEMO-PreapplyReads" in files["PRIVATE_STORAGE.ps1"]
    assert b"604800000L" in files["CURRENT_CORE.ps1"]
    manifest = json.loads(files["TRANSFER_MANIFEST.json"])
    assert all(hashlib.sha256(files[n]).hexdigest() == h for n, h in manifest["files"].items())


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "field,value",
    [("day_key", "2026-11-23"), ("week_key", "2026-11-23"), ("wallet", "5000.00000000")],
)
def test_candidate_mismatch_is_refused_before_key_input(tmp_path, field, value):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    source = dict(day_key="2026-11-18", week_key="2026-11-16", wallet="4998.74183608")
    source[field] = value
    script = f"""
. '{tmp_path}/ACCEPTANCE_CORE.ps1'
function Export-Credentials {{throw 'TEST_KEY_ACCESS_FORBIDDEN'}}
$source=ConvertFrom-Json -InputObject '{json.dumps(source)}'
try{{Assert-AcceptedSource '{tmp_path}' $source;throw 'TEST_NOT_BLOCKED'}}catch{{if($_.Exception.Message -cne 'CURRENT_ACCEPTANCE_CANDIDATE_MISMATCH'){{throw}};'CANDIDATE_REFUSED'}}
"""
    result = launch(tmp_path, script)
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert result.stdout.strip() == "CANDIDATE_REFUSED"


def setup(tmp_path):
    with patch.object(prior, "package", return_value=package()):
        return prior.setup(tmp_path)


def collect(tmp_path, root, pin, day="2026-11-23", fault=""):
    real = prior.launch
    at = int(datetime.fromisoformat(day + "T06:40:00+00:00").timestamp() * 1000)

    def reader(path, script):
        script = script.replace("1794978000000L", str(at) + "L").replace(
            "WBKANG-DEMO-CurrentReads/current-read-20261118-",
            "WBKANG-DEMO-PreapplyReads/preapply-account-20261123-",
        )
        return real(path, script)

    extra = f". '{tmp_path}/ACCEPTANCE_CORE.ps1'\nAssert-AcceptedSource '{tmp_path}' $source"
    with patch.object(prior, "launch", reader):
        return prior.run(tmp_path, root, pin, extra=extra, fault=fault)


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("day,same", [("2026-11-18", True), ("2026-11-23", False)])
def test_new_period_observation_does_not_rebase_accepted_candidate(tmp_path, day, same):
    root, parent, pin = setup(tmp_path)
    before = (
        prior.snapshot(root),
        prior.snapshot(parent),
        prior.snapshot(tmp_path / "data/WBKANG-DEMO-IncomeReads"),
    )
    value = collect(tmp_path, root, pin, day)
    r = value["result"]
    assert r["current_read"] == "CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES", r
    assert r["current_account_profile_matches_saved_reference"] is True
    assert r["accepted_period_matches_observed_period"] is same
    assert r["preapply_account_conditions_matched"] is same
    assert r["accepted_day_key_kst"] == "2026-11-18" and r["accepted_week_key_kst"] == "2026-11-16"
    assert r["observed_day_key_kst"] == day
    assert r["source_accepted"] is True and not r["original_apply_ready"]
    assert r["actual_http_attempts"] == 18 and not r["docker_used"] and not r["services_started"]
    assert before == (
        prior.snapshot(root),
        prior.snapshot(parent),
        prior.snapshot(tmp_path / "data/WBKANG-DEMO-IncomeReads"),
    )
    assert not (tmp_path / "data/WBKANG-DEMO-CurrentReads").exists()
    assert list((tmp_path / "data/WBKANG-DEMO-PreapplyReads").rglob("RECEIPT.json"))


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "fault",
    ["regular", "algo", "position", "wallet_change", "income_gap", "trade_gap", "mode_change"],
)
def test_mismatch_never_marks_preapply_ready(tmp_path, fault):
    root, _, pin = setup(tmp_path)
    r = collect(tmp_path, root, pin, fault=fault)["result"]
    assert r["preapply_account_conditions_matched"] is False
    assert r["current_account_profile_matches_saved_reference"] is False
    assert (
        not r["original_apply_ready"]
        and not r["settings_changed_by_tool"]
        and not r["orders_enabled"]
    )


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("day", ["2026-11-18", "2026-11-26"])
def test_clock_must_advance_and_reference_gap_is_bounded(tmp_path, day):
    root, _, pin = setup(tmp_path)
    if day == "2026-11-18":
        real = prior.launch

        def old_clock(path, script):
            return real(path, script)

        with patch.object(prior, "launch", old_clock):
            extra = (
                f". '{tmp_path}/ACCEPTANCE_CORE.ps1'\nAssert-AcceptedSource '{tmp_path}' $source"
            )
            value = prior.run(tmp_path, root, pin, extra=extra)
    else:
        value = collect(tmp_path, root, pin, day)
    assert value["result"]["current_read"] == "STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY"
    assert value["calls"] == ["/fapi/v1/time"]


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "file", ["SOURCE_ACCEPTANCE.json", "SOURCE_PROPOSAL.json", "ACCEPTANCE_CORE.ps1"]
)
def test_seal_refuses_tampering_before_credentials(tmp_path, file):
    files = package()
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    (tmp_path / file).write_text("PRIVATE_MUST_NOT_RUN")
    pin = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
    result = launch(
        tmp_path,
        f"& '{tmp_path}/RUN_CURRENT.ps1' -ManifestSHA256 '{pin}' -CollectOnce\nexit $LASTEXITCODE",
    )
    assert result.returncode == 2 and not result.stderr
    assert "PRIVATE_MUST_NOT_RUN" not in result.stdout
    assert json.loads(result.stdout)["failure_category"] == "INSPECT_TRANSFER_INVALID"


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_sealed_offline_check_does_not_start_network_or_create_outputs(tmp_path):
    real = prior.launch

    def check(path, script):
        result = real(path, script)
        marker = "PREAPPLY_ACCOUNT_CHECK_PASSED_NO_HTTP"
        assert marker in result.stdout, result.stdout + result.stderr
        result.stdout = result.stdout.replace(marker, "CURRENT_READ_CHECK_PASSED_NO_HTTP")
        return result

    with (
        patch.object(prior, "package", return_value=package()),
        patch.object(prior, "launch", check),
    ):
        prior.test_sealed_offline_check_links_both_old_runs_and_writes_nothing(tmp_path)
    assert not (tmp_path / "data/WBKANG-DEMO-PreapplyReads").exists()


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize(
    "profile,same,code,marker",
    [
        (True, True, 0, "PREAPPLY_ACCOUNT_MATCHED_NO_ORIGINAL_WRITE_NO_ORDERS"),
        (True, False, 0, "CURRENT_ACCOUNT_MATCHED_NEW_PERIOD_REVIEW_REQUIRED_NO_ORDERS"),
        (False, False, 2, "PREAPPLY_ACCOUNT_NOT_MATCHED_KEEP_STOPPED_NO_RETRY"),
    ],
)
def test_launcher_exit_status_separates_saved_mismatch_from_success(
    tmp_path, profile, same, code, marker
):
    root, _, pin = setup(tmp_path)
    runner = tmp_path / "RUN_CURRENT.ps1"
    runner.write_text(
        runner.read_text().replace(
            "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d", pin
        )
    )
    (tmp_path / "PRIVATE_STORAGE.ps1").write_text(
        "function Reference-AssertPrivateDirectory {param($Path)}"
    )
    core = tmp_path / "CURRENT_CORE.ps1"
    p = "$true" if profile else "$false"
    s = "$true" if same else "$false"
    core.write_text(
        core.read_text()
        + f"\nfunction Invoke-CurrentRead {{param($Plan,$Source) return [pscustomobject]@{{current_read='CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES';current_account_profile_matches_saved_reference={p};accepted_period_matches_observed_period={s};preapply_account_conditions_matched=({p} -and {s})}}}}"
    )
    manifest = json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_text())
    for n in ("RUN_CURRENT.ps1", "PRIVATE_STORAGE.ps1", "CURRENT_CORE.ps1"):
        manifest["files"][n] = hashlib.sha256((tmp_path / n).read_bytes()).hexdigest()
    raw = json.dumps(manifest).encode()
    (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(raw)
    result = launch(
        tmp_path,
        f"function tar.exe {{& tar @args}}\n& '{runner}' -ManifestSHA256 '{hashlib.sha256(raw).hexdigest()}' -CollectOnce\nexit $LASTEXITCODE",
    )
    assert result.returncode == code and not result.stderr, result.stdout + result.stderr
    assert marker in result.stdout
