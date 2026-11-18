"""Read-only full JSONB period-change and audit draft on a fresh networkless copy."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_financial_bundle import package_files as financial_files
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_recovery_preview_bundle import package_files as preview_files

FOLDER = "windows-demo-period-draft-20261118"
PIN = "c5cc61073e1ac159317b20af13aa106414f80960"
PREVIEW = "0137d8e0975aab18ca3d3e0c2424d0189c0c31dd"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        n: b
        for n, b in financial_files(PIN).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    preview = preview_files(PREVIEW)
    for n in (
        "ARRAY_CORE.ps1",
        "PRIVATE_READ.ps1",
        "PURE_HELPERS.ps1",
        "SAVED_INCOME_CORE.ps1",
        "SAVED_TRADE_CORE.ps1",
        "PREVIEW_CORE.ps1",
    ):
        files[n] = preview[n]
    files["DRAFT_EXT.ps1"] = git(
        "show", revision + ":tools/windows_demo_period_draft/DRAFT_EXT.ps1"
    )
    files["README_KO.md"] = git("show", revision + ":tools/windows_demo_period_draft/README_KO.md")
    cte = git("show", revision + ":tools/windows_demo_period_draft/draft_cte.sql").decode()
    sql = once(
        files["account_check.sql"].decode(),
        "'last_reconciled',state->>'last_reconciled')",
        "'last_reconciled',state->>'last_reconciled', 'transition_draft',transition.report)",
    )
    sql = once(
        sql,
        " FROM public.demo_accounts WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1';",
        " FROM public.demo_accounts CROSS JOIN transition WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1';",
    )
    sql = once(sql, "SELECT json_build_object(", cte + "\nSELECT json_build_object(")
    files["account_check.sql"] = sql.encode()
    files["COMMON.ps1"] = files["COMMON.ps1"].replace(
        b"copy-financial-20261118-", b"period-draft-20261118-"
    )
    ext = files["FINANCIAL_EXT.ps1"].decode()
    ext = once(
        ext,
        "$script:FinancialPlan=Invoke-IncomePlan $parent $Config.manifest_sha256",
        "$script:FinancialPlan=Invoke-IncomePlan $parent $Config.manifest_sha256\n  Initialize-PeriodDraftEvidence $script:FinancialPlan",
    )
    files["FINANCIAL_EXT.ps1"] = ext.encode()
    core = files["COPY_CORE.ps1"].decode()
    core = once(core, "Assert-CopyFinancial $account", "Assert-PeriodDraft $account")
    old = "-h /review-socket -U dev -d hybrid 2>$null)"
    params = "-h /review-socket -U dev -d hybrid -v ('draft_day='+$script:DraftEvidence.day) -v ('draft_week='+$script:DraftEvidence.week) -v ('draft_amount='+$script:DraftEvidence.amount) -v ('draft_evidence='+$script:DraftEvidence.sha256) 2>$null)"
    core = once(core, old, params)
    core = once(
        core,
        "$observed+=,($account | ConvertTo-Json -Compress)",
        "$observed+=,($account | ConvertTo-Json -Depth 12 -Compress)",
    )
    core = core.replace("COPY_FINANCIAL_RECEIPT.json", "PERIOD_DRAFT_RECEIPT.json").replace(
        "COPY_FINANCIAL_CHECK_PASSED_NO_CLONE_NO_SOURCE_START",
        "PERIOD_DRAFT_CHECK_PASSED_NO_CLONE_NO_SOURCE_START",
    )
    core = (
        core.replace(
            "COPY_FINANCIAL_READ_COMPLETE_ORIGINALS_UNCHANGED_COPY_STOPPED_NO_ORDERS",
            "PERIOD_DRAFT_REVIEW_COMPLETE_COPY_STOPPED_ORIGINALS_UNCHANGED_NO_APPLY",
        )
        .replace("copy_financial_review=", "period_draft_review=")
        .replace(
            "FRESH_COPY_SQL_FINANCIALS_MATCH_STORED_RECORDS_ORIGINALS_UNCHANGED",
            "FULL_JSONB_PERIOD_DRAFT_PRESERVATION_VERIFIED_ORIGINALS_UNCHANGED",
        )
    )
    core = once(
        core,
        "financial=$account.financial;",
        "transition_draft=$account.transition_draft;full_jsonb_draft_transition_verified=$true;commit_payload_verified=$false;apply_token_created=$false;financial=$account.financial;",
    )
    core = once(
        core,
        "financial_before_values_verified_on_copy=$true;full_ledger_transition_verified=$false;request_states_preserved=$true;",
        "full_jsonb_draft_transition_verified=$true;transition_draft=$account.transition_draft;financial_before_values_verified_on_copy=$true;full_ledger_transition_verified=$false;request_states_preserved=$true;",
    )
    core = core.replace(
        "^(COPY|HANDOFF|FINANCIAL|COMPARE|BASELINE|INSPECT)_",
        "^(COPY|HANDOFF|FINANCIAL|COMPARE|BASELINE|INSPECT|DRAFT|TRADES|CURRENT|INCOME|READ|REFERENCE)_",
    )
    core = core.replace(
        "REVIEW_SQL_FINANCIAL_DIFF_NO_BASELINE_ADOPTION_KEEP_STOPPED",
        "REVIEW_UNAPPROVED_JSONB_DRAFT_NO_COMMIT_NO_ADOPTION_KEEP_STOPPED",
    )
    files["COPY_CORE.ps1"] = core.encode()
    runner = files["RUN_FINANCIAL.ps1"].decode()
    runner = once(
        runner,
        "$oldOutput=$OutputEncoding;",
        "$script:DraftSource=$null\n$oldOutput=$OutputEncoding;",
    )
    runner = once(
        runner,
        "'CONFIG.json','README_KO.md')",
        "'CONFIG.json','README_KO.md','ARRAY_CORE.ps1','PRIVATE_READ.ps1','PURE_HELPERS.ps1','SAVED_INCOME_CORE.ps1','SAVED_TRADE_CORE.ps1','PREVIEW_CORE.ps1','DRAFT_EXT.ps1')",
    )
    runner = once(
        runner,
        "'PLAN_CORE.ps1','FINANCIAL_EXT.ps1')",
        "'PLAN_CORE.ps1','FINANCIAL_EXT.ps1','ARRAY_CORE.ps1','PRIVATE_READ.ps1','PURE_HELPERS.ps1','SAVED_INCOME_CORE.ps1','SAVED_TRADE_CORE.ps1','PREVIEW_CORE.ps1','DRAFT_EXT.ps1')",
    )
    runner = once(
        runner,
        "} finally {$OutputEncoding=",
        "} finally {if($null -ne $script:DraftSource){Trades-CloseSaved $script:DraftSource};$OutputEncoding=",
    )
    files["RUN_FINANCIAL.ps1"] = runner.encode()
    config = json.loads(files["CONFIG.json"])
    config["source_reference"] = revision
    files["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_REVIEW_DRAFT.cmd", "ExecuteRead")]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_FINANCIAL.ps1" -ManifestSHA256 '
            + hashlib.sha256(manifest).hexdigest()
            + " -"
            + mode
            + "\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return files


def build(ref, output):
    files = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as out:
        out.write(archive({FOLDER + "/" + n: b for n, b in files.items()}))
    print(
        json.dumps(
            dict(artifact=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.ref, args.output)
