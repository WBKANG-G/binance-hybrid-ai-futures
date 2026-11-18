"""Completion-schema COPY-ONLY rehearsal on a fresh read-only networkless backup copy."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_typed_draft_bundle import package_files as typed_files

FOLDER = "windows-demo-adoption-model-20261118"
PIN = "effbee73c2e1f7ab2cbedac8cd597ef1c17fec1a"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        n: b
        for n, b in typed_files(PIN).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    # The worker loads the old module name in memory. Importing the already-defined
    # draft reviewer here captures that function before the completion wrapper replaces it.
    files["model_review.py"] += b"\n" + git(
        "show", revision + ":services/execution/demo_period_adoption_model.py"
    )
    files["README_KO.md"] = git(
        "show", revision + ":tools/windows_demo_adoption_model/README_KO.md"
    )
    ext = files["MODEL_EXT.ps1"].decode()
    ext = once(
        ext,
        " $script:TypedReview=$value",
        " if($value.completion_rehearsal_context -cne 'COPY_REHEARSAL_ONLY'){throw 'MODEL_COMPLETION_CONTEXT_INVALID'}\n"
        " foreach($flag in @('completion_audit_model_validated','completion_candidate_model_validated','completion_protected_state_preserved','rehearsal_receipt_rejected_for_real_application')){if($value.$flag -isnot [bool] -or -not $value.$flag){throw 'MODEL_COMPLETION_OUTPUT_INVALID'}}\n"
        " foreach($flag in @('completion_payload_returned','source_acceptance_receipt_created')){if($value.$flag -isnot [bool] -or $value.$flag){throw 'MODEL_COMPLETION_OUTPUT_INVALID'}}\n"
        " $script:TypedReview=$value",
    )
    files["MODEL_EXT.ps1"] = (
        ext.replace("TYPED_DRAFT_RESOURCE.json", "ADOPTION_MODEL_RESOURCE.json")
        .replace("TYPED_DRAFT_REVIEW.json", "ADOPTION_MODEL_REVIEW.json")
        .encode()
    )
    files["COMMON.ps1"] = files["COMMON.ps1"].replace(
        b"typed-draft-20261118-", b"adoption-model-20261118-"
    )
    files["COPY_CORE.ps1"] = (
        files["COPY_CORE.ps1"]
        .replace(b"typed_period_draft_review=", b"adoption_model_rehearsal=")
        .replace(
            b"TYPED_LEDGER_AND_DRAFT_AUDIT_REVIEWED_ORIGINALS_UNCHANGED",
            b"COMPLETION_MODEL_COPY_REHEARSAL_REVIEWED_ORIGINALS_UNCHANGED",
        )
        .replace(
            b"READ_ONLY_TYPED_MODEL_AND_AUDIT_REVIEW",
            b"READ_ONLY_COMPLETION_MODEL_COPY_REHEARSAL",
        )
        .replace(
            b"REVIEW_UNAPPROVED_JSONB_DRAFT_NO_COMMIT_NO_ADOPTION_KEEP_STOPPED",
            b"REVIEW_COMPLETION_MODEL_NO_SOURCE_ACCEPTANCE_NO_COMMIT_KEEP_STOPPED",
        )
        .replace(b"TYPED_PERIOD_DRAFT_RECEIPT.json", b"ADOPTION_MODEL_RECEIPT.json")
        .replace(
            b"TYPED_DRAFT_CHECK_PASSED_NO_CLONE_NO_SOURCE_START",
            b"ADOPTION_MODEL_CHECK_PASSED_NO_CLONE_NO_SOURCE_START",
        )
        .replace(
            b"TYPED_PERIOD_DRAFT_REVIEW_COMPLETE_COPY_STOPPED_NO_ADOPTION_NO_ORDERS",
            b"ADOPTION_MODEL_REHEARSAL_COMPLETE_COPY_STOPPED_NO_ADOPTION_NO_ORDERS",
        )
    )
    config = json.loads(files["CONFIG.json"])
    config.update(
        source_reference=revision,
        candidate_sha256=hashlib.sha256(files["model_review.py"]).hexdigest(),
    )
    files["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_REHEARSE_MODEL.cmd", "ExecuteRead")]:
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
