"""Fresh networkless-copy SQL financial projection; source services remain stopped."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_copy_transition_bundle import package_files as transition_files
from scripts.build_windows_recovery_preview_bundle import package_files as preview_files

FOLDER = "windows-demo-copy-financial-20261118"
PIN = "68283b4e4d62fc491e26547b2cec28676128cdba"
PREVIEW_PIN = "0137d8e0975aab18ca3d3e0c2424d0189c0c31dd"
COMPARE_PIN = "a1a8435a963a01b9b9d9f08f40cf8180b4d9188c"
COMPARISON_HELPER = "521b898bc3181998ab713728aa1a00910ae9059923beea520420cf268b4e9956"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    old = transition_files(PIN)
    names = (
        "CONTEXT.ps1",
        "COMMON.ps1",
        "SUPPORT.ps1",
        "SOURCE_SCOPE.ps1",
        "COPY_CORE.ps1",
        "restore_worker.py",
        "cold_archive.py",
        "CONFIG.json",
    )
    files = {n: old[n] for n in names}
    files["CONTEXT.ps1"] = once(
        files["CONTEXT.ps1"].decode(),
        "function Container-Ids {",
        "function Financial-AllContainerIds {",
    ).encode()
    files["COMMON.ps1"] = files["COMMON.ps1"].replace(
        b"copy-review-20261115-", b"copy-financial-20261118-"
    )
    ext = old["COPY_EXT.ps1"].decode()
    files["COPY_EXT.ps1"] = ext[: ext.index("function Invoke-CopyDiagnostic(")].encode()
    compare = git(
        "show", COMPARE_PIN + ":tools/windows_demo_source_compare/COMPARE_CORE.ps1"
    ).decode()
    files["COMPARE_ADMISSION.ps1"] = compare[
        : compare.index("function Invoke-SourceCompare(")
    ].encode()
    for name in ("FINANCIAL_EXT.ps1", "RUN_FINANCIAL.ps1", "account_check.sql", "README_KO.md"):
        files[name] = git("show", revision + ":tools/windows_demo_copy_financial/" + name)
    preview = preview_files(PREVIEW_PIN)
    for name in ("READ_CORE.ps1", "BASELINE_CORE.ps1", "PLAN_CORE.ps1"):
        files[name] = preview[name]
    core = files["COPY_CORE.ps1"].decode()
    line = next(
        line
        for line in core.splitlines()
        if "'account_state_sha256|daily_key|orders|paused|" in line
    )
    core = once(core, line, "            Assert-CopyFinancial $account")
    core = once(
        core,
        "([string]$lines[0]).Length -gt 2048) { throw 'RESTORE_CLONE_READ_FAILED' }",
        "([string]$lines[0]).Length -gt 8192) { throw 'RESTORE_CLONE_READ_FAILED' }",
    )
    core = once(
        core,
        "        $stage='READ_ONLY_CLOCK_PERIOD_DIAGNOSTIC'; Write-Output ('COPY_REVIEW_STAGE='+$stage)\n        $helpers+=,(Invoke-CopyDiagnostic $Root $config $state $volumes[1] $volumes[2] $original $helpers $directory)\n",
        "",
    )
    core = once(
        core,
        "copy_transition_diagnostic=$script:CopyDiagnostic;",
        "financial_before_values_verified_on_copy=$true;full_ledger_transition_verified=$false;request_states_preserved=$true;",
    )
    core = once(
        core,
        "diagnostic=$script:CopyDiagnostic;",
        "financial=$account.financial;request_states=$account.request_states;account_state_sha256=$account.account_state_sha256;financial_before_values_verified_on_copy=$true;current_original_sql_queried=$false;full_ledger_transition_verified=$false;baseline_adopted=$false;",
    )
    core = core.replace("copy_transition_review=", "copy_financial_review=").replace(
        "FRESH_COPY_DIAGNOSED_ORIGINALS_UNCHANGED",
        "FRESH_COPY_SQL_FINANCIALS_MATCH_STORED_RECORDS_ORIGINALS_UNCHANGED",
    )
    core = core.replace("COPY_REVIEW_RECEIPT.json", "COPY_FINANCIAL_RECEIPT.json").replace(
        "COPY_REVIEW_COMPLETE_ORIGINALS_UNCHANGED_NEW_COPY_STOPPED_NO_ORDERS",
        "COPY_FINANCIAL_READ_COMPLETE_ORIGINALS_UNCHANGED_COPY_STOPPED_NO_ORDERS",
    )
    core = core.replace(
        "RESTORE_CHECK_PASSED_NO_CLONE_NO_SOURCE_START_NO_ORDERS",
        "COPY_FINANCIAL_CHECK_PASSED_NO_CLONE_NO_SOURCE_START",
    )
    core = core.replace("^(COPY|HANDOFF)_", "^(COPY|HANDOFF|FINANCIAL|COMPARE|BASELINE|INSPECT)_")
    core = core.replace(
        "| ConvertTo-Json -Compress | Write-Output",
        "| ConvertTo-Json -Depth 8 -Compress | Write-Output",
    )
    core = core.replace(
        "REVIEW_COPY_DIAGNOSTIC_NO_REBASE_NO_DAY_ADOPTION_KEEP_STOPPED",
        "REVIEW_SQL_FINANCIAL_DIFF_NO_BASELINE_ADOPTION_KEEP_STOPPED",
    )
    files["COPY_CORE.ps1"] = core.encode()
    config = json.loads(files["CONFIG.json"])
    for key in ("baseline_files", "candidate_files", "worker_report_keys"):
        config.pop(key, None)
    config.update(source_reference=revision, comparison_helper_id=COMPARISON_HELPER)
    assert len(COMPARISON_HELPER) == 64
    files["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in (("RUN_CHECK.cmd", "CheckOnly"), ("RUN_READ_FINANCIAL.cmd", "ExecuteRead")):
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
    with target.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + n: b for n, b in files.items()}))
    print(
        json.dumps(
            dict(artifact=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.ref, args.output)
