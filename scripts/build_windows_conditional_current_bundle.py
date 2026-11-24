"""Fresh DEMO GET observation with bounded, source-only conditional binding."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_preapply_account_bundle import package_files as prior_files
from services.execution.demo_conditional_source_binding import (
    ACCEPTANCE_SHA256,
    PROPOSAL_SHA256,
    verify_conditional_acceptance,
)

FOLDER = "windows-demo-conditional-current-20261124"
PIN = "5b57085847c0eb6e02275c8018ca85fc999f143b"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    f = {
        n: b
        for n, b in prior_files(PIN).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    for n, path in [
        ("SOURCE_PROPOSAL.json", "docs/reviews/20261124/SOURCE_ACCEPTANCE_REVIEW.json"),
        ("SOURCE_ACCEPTANCE.json", "docs/reviews/20261124/SOURCE_ACCEPTANCE_RECEIPT.json"),
        (
            "SOURCE_REFRESH_OBSERVATION.json",
            "docs/reviews/20261124/SOURCE_REFRESH_WINDOWS_OBSERVATION.json",
        ),
        ("ACCEPTANCE_CORE.ps1", "tools/windows_demo_conditional_current/ACCEPTANCE_CORE.ps1"),
        ("BINDING_CORE.ps1", "tools/windows_demo_conditional_current/BINDING_CORE.ps1"),
        ("README_KO.md", "tools/windows_demo_conditional_current/README_KO.md"),
    ]:
        f[n] = git("show", revision + ":" + path)
    e, a = json.loads(f["SOURCE_PROPOSAL.json"]), json.loads(f["SOURCE_ACCEPTANCE.json"])
    verify_conditional_acceptance(e, a)
    text = f["ACCEPTANCE_CORE.ps1"].decode()
    for marker, value in {
        "__RECEIPT_FILE_SHA__": hashlib.sha256(f["SOURCE_ACCEPTANCE.json"]).hexdigest(),
        "__PROPOSAL_FILE_SHA__": hashlib.sha256(f["SOURCE_PROPOSAL.json"]).hexdigest(),
        "__REVIEW_ID__": e["review_id"],
        "__PROPOSAL_SHA__": PROPOSAL_SHA256,
        "__RECEIPT_CANONICAL_SHA__": ACCEPTANCE_SHA256,
    }.items():
        text = text.replace(marker, value)
    f["ACCEPTANCE_CORE.ps1"] = text.encode()
    f["READ_TRANSPORT.ps1"] = once(
        f["READ_TRANSPORT.ps1"].decode(),
        "$Context.http_attempts -lt 18",
        "$Context.http_attempts -lt 19",
    ).encode()
    f["PRIVATE_STORAGE.ps1"] = (
        f["PRIVATE_STORAGE.ps1"]
        .replace(b"WBKANG-DEMO-PreapplyReads", b"WBKANG-DEMO-ConditionalReads")
        .replace(b"preapply-account-20261123-", b"conditional-current-20261124-")
    )
    core = f["CURRENT_CORE.ps1"].decode()
    core = once(
        core, "$context.server_time -gt 1794980400911L", "$context.server_time -gt 1795490093574L"
    )
    core = once(
        core,
        "$observationEnd=$null;$keys=$null",
        "$observationEnd=$null;$keys=$null;$bindingNow=$null;$observationStart=$null",
    )
    core = once(
        core,
        "$keys=Income-Boundaries $context.server_time",
        "$observationStart=$context.server_time\n  $keys=Income-Boundaries $context.server_time",
    )
    # Retain all existing 7-day guards, and reject outside consent before private GETs.
    core = once(
        core,
        "  Export-RateLimit $context",
        "  Inspect-Require ($keys.day_key -cge '2026-11-24' -and $keys.day_key -cle '2026-11-29' -and $keys.week_key -ceq '2026-11-23') 'CURRENT_REUSE_PERIOD_INVALID'\n  Export-RateLimit $context",
    )
    core = once(
        core,
        "  $last=Income-Boundaries",
        "  Export-Clock $context;$bindingNow=$context.server_time\n  $last=Income-Boundaries",
    )
    core = once(
        core,
        "accepted_day_key_kst=$Source.day_key;accepted_week_key_kst=$Source.week_key;",
        "accepted_day_key_kst=$script:AcceptedSource.day_key;accepted_week_key_kst=$script:AcceptedSource.week_key;observation_started_server_ms=$observationStart;",
    )
    core = once(
        core,
        "day_key_kst=$Source.day_key;week_key_kst=$Source.week_key;saved_wallet_candidate_usdt=",
        "saved_reference_day_key_kst=$Source.day_key;saved_reference_week_key_kst=$Source.week_key;saved_wallet_candidate_usdt=",
    )
    start = core.index(
        " $result|Add-Member -NotePropertyName current_account_profile_matches_saved_reference"
    )
    end = core.index(" if($null -ne $directory", start)
    core = core[:start] + " Add-ConditionalBinding $result $Source $bindingNow\n" + core[end:]
    core = once(
        core,
        "$result.failure_category='CURRENT_RECEIPT_WRITE_FAILED'",
        "$result.failure_category='CURRENT_RECEIPT_WRITE_FAILED';$result.conditional_source_bound=$false;$result.source_accepted=$false;$result.conditional_binding_failure_category='CURRENT_RECEIPT_WRITE_FAILED'",
    )
    core = once(
        core,
        "policy='DEMO_PREAPPLY_ACCOUNT_READ_20261123_V1'",
        "policy='DEMO_CONDITIONAL_CURRENT_READ_20261124_V1'",
    )
    f["CURRENT_CORE.ps1"] = core.encode()
    runner = f["RUN_CURRENT.ps1"].decode()
    runner = once(
        runner,
        "'ACCEPTANCE_CORE.ps1','CURRENT_CORE.ps1'",
        "'ACCEPTANCE_CORE.ps1','BINDING_CORE.ps1','CURRENT_CORE.ps1'",
    )
    # Replace exactly the generated manifest-name expression, not initializers.
    lines = runner.splitlines()
    i = next(
        i for i, s in enumerate(lines) if "if((@($manifest.files.PSObject.Properties.Name" in s
    )
    lines[i] = (
        " if((@($manifest.files.PSObject.Properties.Name|Sort-Object)-join '|') -cne ((@("
        + ",".join("'" + n + "'" for n in f)
        + ")|Sort-Object)-join '|')){throw 'INSPECT_TRANSFER_INVALID'}"
    )
    runner = "\n".join(lines) + "\n"
    runner = runner.replace(
        "PREAPPLY_ACCOUNT_CHECK_PASSED_NO_HTTP", "CONDITIONAL_CURRENT_CHECK_PASSED_NO_HTTP"
    )
    runner = once(
        runner,
        "if($CheckOnly){[pscustomobject]@{source_accepted=$true;",
        "if($CheckOnly){[pscustomobject]@{source_acceptance_recorded=$true;conditional_reuse_approved=$true;conditional_reuse_evaluated=$false;conditional_source_bound=$false;fresh_account_evidence_checked=$false;accepted_day_key_kst=$script:AcceptedSource.day_key;accepted_week_key_kst=$script:AcceptedSource.week_key;source_accepted=$false;",
    )
    runner = once(
        runner,
        "day_key_kst=$source.day_key;week_key_kst=$source.week_key;",
        "saved_reference_day_key_kst=$source.day_key;saved_reference_week_key_kst=$source.week_key;",
    )
    start = runner.index(" if($result.preapply_account_conditions_matched)")
    end = runner.index(" exit 2", start)
    runner = (
        runner[:start]
        + " if($result.current_read -ceq 'CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES' -and $result.conditional_source_bound){Write-Output 'CONDITIONAL_SOURCE_BOUND_NO_ORIGINAL_START_NO_APPLY_NO_ORDERS';exit 0}\n Write-Output 'CONDITIONAL_SOURCE_NOT_BOUND_KEEP_STOPPED_NO_RETRY'\n"
        + runner[end:]
    )
    f["RUN_CURRENT.ps1"] = runner.encode()
    m = (
        json.dumps(dict(source_reference=revision, files=hashes(f)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    f["TRANSFER_MANIFEST.json"] = m
    for n, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_READ_AND_BIND.cmd", "CollectOnce")]:
        interaction = " -NonInteractive" if mode == "CheckOnly" else ""
        f[n] = (
            "@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile"
            + interaction
            + ' -ExecutionPolicy Bypass -File "%~dp0RUN_CURRENT.ps1" -ManifestSHA256 '
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
