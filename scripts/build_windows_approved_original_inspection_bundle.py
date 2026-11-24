"""Seal the exact reviewed inspection code with the now-recorded one-attempt consent."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_original_inspection_bundle import package_files as review_files
from services.execution.demo_original_inspection_acceptance import (
    REVIEW_RELEASE,
    REVIEW_SOURCE,
    REVIEW_ZIP_SHA256,
    verify_inspection_acceptance,
)

FOLDER = "windows-demo-original-inspection-20261124"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    f = {
        n: b
        for n, b in review_files(REVIEW_SOURCE).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    review_zip = git(
        "show",
        REVIEW_RELEASE
        + ":artifacts/windows-demo-original-inspection-review-20261124/windows-demo-original-inspection-review-20261124.zip",
    )
    if hashlib.sha256(review_zip).hexdigest() != REVIEW_ZIP_SHA256:
        raise ValueError("INSPECTION_REVIEW_ARTIFACT_MISMATCH")
    pending = json.loads(
        git(
            "show",
            REVIEW_RELEASE + ":docs/reviews/20261124/ORIGINAL_INSPECTION_PENDING_QUESTION.json",
        )
    )
    accepted = git(
        "show", revision + ":docs/reviews/20261124/ORIGINAL_START_ACCEPTANCE_RECEIPT.json"
    )
    verify_inspection_acceptance(pending, f["INSPECTION_PROPOSAL.json"], json.loads(accepted))
    p = json.loads(f["INSPECTION_PROPOSAL.json"])
    if not all(
        hashlib.sha256(f[n]).hexdigest() == h for n, h in p["execution_file_sha256"].items()
    ):
        raise ValueError("INSPECTION_REVIEWED_EXECUTION_FILES_CHANGED")
    f["ORIGINAL_START_ACCEPTANCE.json"] = accepted
    f["README_KO.md"] = git(
        "show", revision + ":tools/windows_demo_approved_original_inspection/README_KO.md"
    )
    config = json.loads(f["CONFIG.json"])
    config.update(
        source_reference=revision,
        inspection_approval_receipt_file_sha256=hashlib.sha256(accepted).hexdigest(),
    )
    f["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    m = (
        json.dumps(dict(source_reference=revision, files=hashes(f)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    f["TRANSFER_MANIFEST.json"] = m
    for n, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_INSPECT.cmd", "InspectOnce")]:
        f[n] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\necho ORIGINAL_INSPECTION_START_SCOPE_APPROVED_SINGLE_ATTEMPT_ONLY\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_INSPECT.ps1" -ManifestSHA256 '
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
