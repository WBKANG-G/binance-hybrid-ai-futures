"""Read copy ownership/fence and render an unapproved original recovery design."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_financial_bundle import package_files as financial_files
from scripts.build_windows_copy_transition_bundle import once
from services.execution.demo_conditional_source_binding import verify_conditional_acceptance

FOLDER = "windows-demo-owner-review-20261124"
PIN = "c5cc61073e1ac159317b20af13aa106414f80960"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    f = {
        n: b
        for n, b in financial_files(PIN).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    for n, path in [
        ("OWNER_EXT.ps1", "tools/windows_demo_owner_review/OWNER_EXT.ps1"),
        ("README_KO.md", "tools/windows_demo_owner_review/README_KO.md"),
        (
            "ORIGINAL_RECOVERY_PROTOCOL_KO.md",
            "tools/windows_demo_owner_review/ORIGINAL_RECOVERY_PROTOCOL_KO.md",
        ),
        ("SOURCE_PROPOSAL.json", "docs/reviews/20261124/SOURCE_ACCEPTANCE_REVIEW.json"),
        ("SOURCE_ACCEPTANCE.json", "docs/reviews/20261124/SOURCE_ACCEPTANCE_RECEIPT.json"),
        (
            "CONDITIONAL_CURRENT_OBSERVATION.json",
            "docs/reviews/20261124/CONDITIONAL_CURRENT_WINDOWS_OBSERVATION.json",
        ),
    ]:
        f[n] = git("show", revision + ":" + path)
    verify_conditional_acceptance(
        json.loads(f["SOURCE_PROPOSAL.json"]), json.loads(f["SOURCE_ACCEPTANCE.json"])
    )
    projection = git(
        "show", revision + ":tools/windows_demo_owner_review/owner_projection.sql"
    ).decode()
    sql = f["account_check.sql"].decode()
    sql = once(
        sql,
        "SELECT json_build_object(",
        "SET LOCAL timezone='UTC';\nWITH source AS (\n SELECT *,jsonb_build_object('daily_key','2026-11-24','weekly_key','2026-11-23','daily_base','4998.74183608','weekly_base','4998.74183608','reconciled',false,'stream_ready',false) AS proposed_fields FROM public.demo_accounts WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1'\n), candidate AS (SELECT *,state || proposed_fields AS proposed FROM source)\nSELECT json_build_object(",
    )
    sql = once(sql, "state->>'last_reconciled')", "state->>'last_reconciled'\n" + projection + ")")
    sql = once(
        sql,
        "FROM public.demo_accounts WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1';",
        "FROM candidate;",
    )
    f["account_check.sql"] = sql.encode()
    f["COMMON.ps1"] = f["COMMON.ps1"].replace(
        b"copy-financial-20261118-", b"owner-review-20261124-"
    )
    config = json.loads(f["CONFIG.json"])
    config["source_reference"] = revision
    f["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    core = f["COPY_CORE.ps1"].decode()
    core = once(
        core,
        "        $result=[ordered]@{financial_before_values_verified_on_copy=",
        "        Write-BackupJson (Join-Path $directory 'OWNER_PROTOCOL_DRAFT.json') $script:OwnerProtocolReview\n        $result=[ordered]@{owner_protocol_review=$script:OwnerProtocolReview;financial_before_values_verified_on_copy=",
    )
    core = core.replace("COPY_FINANCIAL_RECEIPT.json", "OWNER_REVIEW_RECEIPT.json")
    core = once(
        core,
        "copy_financial_review='FRESH_COPY_SQL_FINANCIALS_MATCH_STORED_RECORDS_ORIGINALS_UNCHANGED';",
        "owner_review='FRESH_COPY_OWNER_FENCE_AND_PERIOD_DRAFT_REVIEWED_ORIGINALS_UNCHANGED';ownership=$account.owner_review;ownership_classification=$script:OwnerProtocolReview.classification;period_candidate=$account.period_candidate;proposed_offline_recovery_fence=111;original_owner_verified=$false;original_row_lock_acquired=$false;current_original_fence_verified=$false;original_database_write_authorized=$false;original_apply_ready=$false;protocol_draft_file=(Join-Path $directory 'OWNER_PROTOCOL_DRAFT.json');",
    )
    core = core.replace(
        "COPY_FINANCIAL_CHECK_PASSED_NO_CLONE_NO_SOURCE_START",
        "OWNER_REVIEW_CHECK_PASSED_NO_CLONE_NO_ORIGINAL_START",
    )
    core = core.replace(
        "COPY_FINANCIAL_READ_COMPLETE_ORIGINALS_UNCHANGED_COPY_STOPPED_NO_ORDERS",
        "OWNER_REVIEW_COMPLETE_COPY_STOPPED_NO_ORIGINAL_START_NO_APPLY_NO_ORDERS",
    )
    core = core.replace(
        "REVIEW_SQL_FINANCIAL_DIFF_NO_BASELINE_ADOPTION_KEEP_STOPPED",
        "REVIEW_UNAPPROVED_OWNER_PROTOCOL_AND_ORIGINAL_START_BOUNDARY_KEEP_STOPPED",
    )
    core = core.replace(
        "^(COPY|HANDOFF|FINANCIAL|COMPARE|BASELINE|INSPECT)_",
        "^(COPY|HANDOFF|FINANCIAL|COMPARE|BASELINE|INSPECT|OWNER)_",
    )
    f["COPY_CORE.ps1"] = core.encode()
    lines = f["RUN_FINANCIAL.ps1"].decode().splitlines()
    i = next(i for i, s in enumerate(lines) if s.lstrip().startswith("$required=@("))
    lines[i] = " $required=@(" + ",".join("'" + n + "'" for n in f) + ")"
    i = next(i for i, s in enumerate(lines) if "foreach($name in @(" in s)
    lines[i] = once(lines[i], "'FINANCIAL_EXT.ps1')", "'FINANCIAL_EXT.ps1','OWNER_EXT.ps1')")
    f["RUN_FINANCIAL.ps1"] = ("\n".join(lines) + "\n").encode()
    m = (
        json.dumps(dict(source_reference=revision, files=hashes(f)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    f["TRANSFER_MANIFEST.json"] = m
    for n, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_READ_OWNER.cmd", "ExecuteRead")]:
        f[n] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_FINANCIAL.ps1" -ManifestSHA256 '
            + hashlib.sha256(m).hexdigest()
            + " -"
            + mode
            + "\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return f


def build(ref, output):
    f = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    p = output / (FOLDER + ".zip")
    with p.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + n: b for n, b in f.items()}))
    print(json.dumps(dict(artifact=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.ref, a.output)
