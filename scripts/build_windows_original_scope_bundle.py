"""Read-only original stop/identity/backup checks, without clone or service creation."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_financial_bundle import package_files as financial_files
from services.execution.demo_conditional_source_binding import (
    ACCEPTANCE_SHA256,
    PROPOSAL_SHA256,
    verify_conditional_acceptance,
)

PIN = "c5cc61073e1ac159317b20af13aa106414f80960"
FOLDER = "windows-demo-original-scope-v2-20261124"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    old = financial_files(PIN)
    names = (
        "CONTEXT.ps1",
        "COMMON.ps1",
        "SUPPORT.ps1",
        "SOURCE_SCOPE.ps1",
        "COPY_CORE.ps1",
        "COPY_EXT.ps1",
        "COMPARE_ADMISSION.ps1",
        "FINANCIAL_EXT.ps1",
        "READ_CORE.ps1",
        "BASELINE_CORE.ps1",
        "PLAN_CORE.ps1",
        "CONFIG.json",
    )
    files = {n: old[n] for n in names}
    files["COPY_CORE.ps1"] = files["COPY_CORE.ps1"].split(b"function Assert-NewClone(", 1)[0]
    files["COPY_EXT.ps1"] = files["COPY_EXT.ps1"].split(b"function Assert-NewClone(", 1)[0]
    for n, path in [
        ("RUN_SCOPE.ps1", "tools/windows_demo_original_scope/RUN_SCOPE.ps1"),
        ("README_KO.md", "tools/windows_demo_original_scope/README_KO.md"),
        ("SOURCE_ACCEPTANCE.json", "docs/reviews/20261124/SOURCE_ACCEPTANCE_RECEIPT.json"),
        ("SOURCE_PROPOSAL.json", "docs/reviews/20261124/SOURCE_ACCEPTANCE_REVIEW.json"),
    ]:
        files[n] = git("show", revision + ":" + path)
    envelope = json.loads(files["SOURCE_PROPOSAL.json"])
    accepted = json.loads(files["SOURCE_ACCEPTANCE.json"])
    verify_conditional_acceptance(envelope, accepted)
    config = json.loads(files["CONFIG.json"])
    config.update(
        source_reference=revision,
        accepted_review_id=envelope["review_id"],
        accepted_proposal_sha256=PROPOSAL_SHA256,
        accepted_receipt_sha256=ACCEPTANCE_SHA256,
        source_acceptance_file_sha256=hashlib.sha256(files["SOURCE_ACCEPTANCE.json"]).hexdigest(),
    )
    files["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    files["RUN_SCOPE.ps1"] = files["RUN_SCOPE.ps1"].replace(
        b"'__FILES__'", ",".join("'" + n + "'" for n in files).encode()
    )
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_SCOPE_CHECK.cmd"] = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_SCOPE.ps1" -ManifestSHA256 '
        + hashlib.sha256(manifest).hexdigest()
        + "\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    return files


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
