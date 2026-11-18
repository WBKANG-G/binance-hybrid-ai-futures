"""Typed model and unapproved-audit review in memory on a networkless copy."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_copy_transition_bundle import package_files as old_copy_files
from scripts.build_windows_period_draft_bundle import package_files as draft_files

FOLDER = "windows-demo-typed-draft-20261118"
PIN = "6f23989570bb8148df4202fec46e7e9297c75021"
COPY_PIN = "68283b4e4d62fc491e26547b2cec28676128cdba"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        n: b
        for n, b in draft_files(PIN).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    for target, path in [
        ("MODEL_EXT.ps1", "tools/windows_demo_typed_draft/MODEL_EXT.ps1"),
        ("typed_worker.py", "tools/windows_demo_typed_draft/worker.py"),
        ("model_review.py", "services/execution/demo_period_draft_model_review.py"),
        ("README_KO.md", "tools/windows_demo_typed_draft/README_KO.md"),
    ]:
        files[target] = git("show", revision + ":" + path)
    files["COMMON.ps1"] = files["COMMON.ps1"].replace(
        b"period-draft-20261118-", b"typed-draft-20261118-"
    )
    core = files["COPY_CORE.ps1"].decode()
    core = once(
        core,
        "        $stage='STOP_COPY_AND_REVERIFY_INPUTS';",
        "        $stage='READ_ONLY_TYPED_MODEL_AND_AUDIT_REVIEW'; Write-Output ('TYPED_REVIEW_STAGE='+$stage)\n        $helpers+=,(Invoke-TypedDraftReview $Root $config $state $volumes[2] $original $helpers $directory $account)\n        $stage='STOP_COPY_AND_REVERIFY_INPUTS';",
    )
    core = once(
        core,
        "full_jsonb_draft_transition_verified=$true;transition_draft=",
        "typed_model_review=$script:TypedReview;typed_model_validation_performed=$true;full_jsonb_draft_transition_verified=$true;transition_draft=",
    )
    core = once(
        core,
        "transition_draft=$account.transition_draft;full_jsonb_draft_transition_verified=$true;",
        "typed_model_review=$script:TypedReview;typed_model_validation_performed=$true;transition_draft=$account.transition_draft;full_jsonb_draft_transition_verified=$true;",
    )
    core = core.replace("period_draft_review=", "typed_period_draft_review=").replace(
        "FULL_JSONB_PERIOD_DRAFT_PRESERVATION_VERIFIED_ORIGINALS_UNCHANGED",
        "TYPED_LEDGER_AND_DRAFT_AUDIT_REVIEWED_ORIGINALS_UNCHANGED",
    )
    core = (
        core.replace("PERIOD_DRAFT_RECEIPT.json", "TYPED_PERIOD_DRAFT_RECEIPT.json")
        .replace(
            "PERIOD_DRAFT_CHECK_PASSED_NO_CLONE_NO_SOURCE_START",
            "TYPED_DRAFT_CHECK_PASSED_NO_CLONE_NO_SOURCE_START",
        )
        .replace(
            "PERIOD_DRAFT_REVIEW_COMPLETE_COPY_STOPPED_ORIGINALS_UNCHANGED_NO_APPLY",
            "TYPED_PERIOD_DRAFT_REVIEW_COMPLETE_COPY_STOPPED_NO_ADOPTION_NO_ORDERS",
        )
    )
    core = core.replace(
        "^(COPY|HANDOFF|FINANCIAL|COMPARE|BASELINE|INSPECT|DRAFT|TRADES|CURRENT|INCOME|READ|REFERENCE)_",
        "^(COPY|HANDOFF|FINANCIAL|COMPARE|BASELINE|INSPECT|DRAFT|TRADES|CURRENT|INCOME|READ|REFERENCE|MODEL)_",
    )
    files["COPY_CORE.ps1"] = core.encode()
    runner = files["RUN_FINANCIAL.ps1"].decode()
    # Use literal required/load blocks to avoid ambiguous substitutions between the two.
    lines = runner.splitlines()
    required = next(i for i, line in enumerate(lines) if "$required=@(" in line)
    required_names = [n for n in files if n not in {"TRANSFER_MANIFEST.json"}]
    lines[required] = " $required=@(" + ",".join("'" + n + "'" for n in required_names) + ")"
    load = next(i for i, line in enumerate(lines) if "foreach($name in @(" in line)
    if "'MODEL_EXT.ps1'" not in lines[load]:
        lines[load] = lines[load].replace("'DRAFT_EXT.ps1')", "'DRAFT_EXT.ps1','MODEL_EXT.ps1')")
    files["RUN_FINANCIAL.ps1"] = ("\n".join(lines) + "\n").encode()
    config = json.loads(files["CONFIG.json"])
    config.update(
        source_reference=revision,
        baseline_files=json.loads(old_copy_files(COPY_PIN)["CONFIG.json"])["baseline_files"],
        candidate_sha256=hashlib.sha256(files["model_review.py"]).hexdigest(),
        sql_sha256=hashlib.sha256(files["account_check.sql"]).hexdigest(),
    )
    files["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_REVIEW_MODEL.cmd", "ExecuteRead")]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_FINANCIAL.ps1" -ManifestSHA256 '
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
    with target.open("xb") as out:
        out.write(archive({FOLDER + "/" + n: b for n, b in f.items()}))
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
