"""Source-consent-bound, fresh DEMO account GET observations; no original DB start."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_current_read_bundle import package_files as current_files
from services.execution.demo_source_acceptance import (
    ACCEPTED_PROPOSAL_SHA256,
    ACCEPTED_RECEIPT_SHA256,
    verify_acceptance,
)

FOLDER = "windows-demo-preapply-account-20261123"
PIN = "4422debcb2849a609d359f4b4f9516034de89477"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        n: b
        for n, b in current_files(PIN).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    for n, path in [
        ("SOURCE_PROPOSAL.json", "docs/reviews/20261118/SOURCE_ACCEPTANCE_REVIEW.json"),
        ("SOURCE_ACCEPTANCE.json", "docs/reviews/20261118/SOURCE_ACCEPTANCE_RECEIPT.json"),
        ("ACCEPTANCE_CORE.ps1", "tools/windows_demo_preapply_account/ACCEPTANCE_CORE.ps1"),
        ("README_KO.md", "tools/windows_demo_preapply_account/README_KO.md"),
    ]:
        files[n] = git("show", revision + ":" + path)
    envelope = json.loads(files["SOURCE_PROPOSAL.json"])
    receipt = json.loads(files["SOURCE_ACCEPTANCE.json"])
    verified = verify_acceptance(
        envelope,
        receipt,
        trusted_proposal_sha256=ACCEPTED_PROPOSAL_SHA256,
        trusted_receipt_sha256=ACCEPTED_RECEIPT_SHA256,
    )
    text = files["ACCEPTANCE_CORE.ps1"].decode()
    for marker, value in {
        "__RECEIPT_FILE_SHA__": hashlib.sha256(files["SOURCE_ACCEPTANCE.json"]).hexdigest(),
        "__PROPOSAL_FILE_SHA__": hashlib.sha256(files["SOURCE_PROPOSAL.json"]).hexdigest(),
        "__REVIEW_ID__": verified["review_id"],
        "__PROPOSAL_SHA__": envelope["proposal_sha256"],
        "__RECEIPT_CANONICAL_SHA__": verified["source_acceptance_receipt_sha256"],
    }.items():
        text = text.replace(marker, value)
    files["ACCEPTANCE_CORE.ps1"] = text.encode()
    files["PRIVATE_STORAGE.ps1"] = (
        files["PRIVATE_STORAGE.ps1"]
        .replace(b"WBKANG-DEMO-CurrentReads", b"WBKANG-DEMO-PreapplyReads")
        .replace(b"current-read-20261118-", b"preapply-account-20261123-")
    )
    core = files["CURRENT_CORE.ps1"].decode()
    source_ms = int(
        datetime.fromisoformat(
            envelope["proposal"]["source"]["observations_end_utc"].replace("Z", "+00:00")
        ).timestamp()
        * 1000
    )
    core = once(
        core,
        "$context.watch.Restart();Export-Clock $context",
        "$context.watch.Restart();Export-Clock $context\n  Inspect-Require ($context.server_time -gt "
        + str(source_ms)
        + "L) 'CURRENT_ACCEPTED_OBSERVATION_NOT_ADVANCED'",
    )
    core = once(core, "$observationEnd=$null", "$observationEnd=$null;$keys=$null")
    core = once(
        core,
        "$keys.day_key -ceq $Source.day_key -and $keys.week_key -ceq $Source.week_key -and $context.server_time -ge $Source.trade_end_ms+5000L",
        "$context.server_time -ge $Source.trade_end_ms+5000L -and $context.server_time-$Source.old_cutoff_ms -le 604800000L -and $context.server_time-$Source.trade_end_ms -le 604800000L",
    )
    core = once(
        core,
        "$last.day_key -ceq $Source.day_key -and $last.week_key -ceq $Source.week_key",
        "$last.day_key -ceq $keys.day_key -and $last.week_key -ceq $keys.week_key",
    )
    core = once(
        core,
        "$observationEnd=Income-Now $context",
        "$observationEnd=Income-Now $context\n  Inspect-Require ($observationEnd-$Source.old_cutoff_ms -le 604800000L -and $observationEnd-$Source.trade_end_ms -le 604800000L) 'CURRENT_REFERENCE_WINDOW_EXCEEDS_SEVEN_DAYS'",
    )
    core = core.replace(
        "policy='DEMO_CURRENT_ACCOUNT_READ_20261118_V1';",
        "policy='DEMO_PREAPPLY_ACCOUNT_READ_20261123_V1';source_acceptance_receipt_sha256=$script:AcceptedSource.receipt_sha256;",
    )
    core = once(
        core,
        "$result=[pscustomobject]@{current_read=",
        "$result=[pscustomobject]@{accepted_day_key_kst=$Source.day_key;accepted_week_key_kst=$Source.week_key;observed_day_key_kst=$(if($null -ne $keys){$keys.day_key}else{$null});observed_week_key_kst=$(if($null -ne $keys){$keys.week_key}else{$null});source_acceptance_review_id=$script:AcceptedSource.review_id;source_acceptance_receipt_sha256=$script:AcceptedSource.receipt_sha256;source_accepted=$true;original_database_write_authorized=$false;original_apply_ready=$false;current_read=",
    )
    core = once(
        core,
        " if($null -ne $directory -and [IO.Directory]::Exists($directory))",
        " $result|Add-Member -NotePropertyName current_account_profile_matches_saved_reference -NotePropertyValue (Test-CurrentReferenceProfile $result)\n $result|Add-Member -NotePropertyName accepted_period_matches_observed_period -NotePropertyValue ($null -ne $keys -and $keys.day_key -ceq $Source.day_key -and $keys.week_key -ceq $Source.week_key)\n $result|Add-Member -NotePropertyName preapply_account_conditions_matched -NotePropertyValue (Test-PreapplyAccount $result)\n if($null -ne $directory -and [IO.Directory]::Exists($directory))",
    )
    files["CURRENT_CORE.ps1"] = core.encode()
    runner = files["RUN_CURRENT.ps1"].decode()
    old = "ARRAY_CORE.ps1|CURRENT_CORE.ps1|INCOME_HELPERS.ps1|PLAN_CORE.ps1|PRIVATE_STORAGE.ps1|READ_CORE.ps1|READ_TRANSPORT.ps1|README_KO.md|RUN_CURRENT.ps1|SAVED_INCOME_CORE.ps1|SAVED_TRADE_CORE.ps1|TRADE_HELPERS.ps1"
    runner = once(
        runner,
        "'" + old + "'",
        "((@(" + ",".join("'" + n + "'" for n in files) + ")|Sort-Object)-join '|')",
    )
    runner = once(
        runner,
        "'TRADE_HELPERS.ps1','CURRENT_CORE.ps1'",
        "'TRADE_HELPERS.ps1','ACCEPTANCE_CORE.ps1','CURRENT_CORE.ps1'",
    )
    runner = once(
        runner, " if($CheckOnly){", " Assert-AcceptedSource $PSScriptRoot $source\n if($CheckOnly){"
    )
    runner = runner.replace(
        "current_read='SAVED_INCOME_AND_TRADE_LINKS_CHECKED_NO_HTTP';",
        "source_accepted=$true;source_acceptance_receipt_sha256=$script:AcceptedSource.receipt_sha256;original_database_write_authorized=$false;original_apply_ready=$false;current_read='ACCEPTED_SOURCE_AND_SAVED_LINKS_CHECKED_NO_HTTP';",
    )
    runner = runner.replace(
        "CURRENT_READ_CHECK_PASSED_NO_HTTP", "PREAPPLY_ACCOUNT_CHECK_PASSED_NO_HTTP"
    )
    runner = once(
        runner,
        "if($result.current_read -ceq 'CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES'){Write-Output 'CURRENT_ACCOUNT_SAVED_NO_CHANGES_NO_ORDERS';exit 0}",
        "if($result.preapply_account_conditions_matched){Write-Output 'PREAPPLY_ACCOUNT_MATCHED_NO_ORIGINAL_WRITE_NO_ORDERS';exit 0}\n if($result.current_account_profile_matches_saved_reference -and -not $result.accepted_period_matches_observed_period){Write-Output 'CURRENT_ACCOUNT_MATCHED_NEW_PERIOD_REVIEW_REQUIRED_NO_ORDERS';exit 0}\n Write-Output 'PREAPPLY_ACCOUNT_NOT_MATCHED_KEEP_STOPPED_NO_RETRY'",
    )
    files["RUN_CURRENT.ps1"] = runner.encode()
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_READ_PREAPPLY.cmd", "CollectOnce")]:
        interaction = " -NonInteractive" if mode == "CheckOnly" else ""
        files[name] = (
            "@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile"
            + interaction
            + ' -ExecutionPolicy Bypass -File "%~dp0RUN_CURRENT.ps1" -ManifestSHA256 '
            + hashlib.sha256(manifest).hexdigest()
            + " -"
            + mode
            + "\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return files


def build(ref, output):
    f = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + n: b for n, b in f.items()}))
    print(
        json.dumps(
            dict(artifact=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.ref, a.output)
