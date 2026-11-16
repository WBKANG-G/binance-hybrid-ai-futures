"""Seal PowerShell 5.1 Decimal compatibility without changing the signing lease."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_private_week_v2_bundle import package_files as v2_package

FOLDER = "windows-demo-private-week-v3-20261116"
PRIOR_V2_READER = "eb09666ae4235987563455557aca103bbc38018d0776f553fbb7cd0e2b8a0b43"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()

    def read(p):
        return git("show", revision + ":" + p)

    files = {
        p: b
        for p, b in v2_package(ref).items()
        if p not in {"TRANSFER_MANIFEST.json", "RUN_CHECK.cmd", "RUN_PRIVATE_WEEK.cmd"}
    }
    ext = files["PRIVATE_EXT.ps1"].decode()
    lines = ext.splitlines(keepends=True)
    found = [
        i for i, line in enumerate(lines) if "{throw 'PRIVATE_LATE_LEASE_RESULT_INVALID'}" in line
    ]
    if len(found) != 1:
        raise ValueError("V3_LATE_VALIDATOR_TEMPLATE_CHANGED")
    lines[found[0]] = "    Assert-PrivateLateLeaseResult $pipeline\n"
    ext = once("".join(lines), "function Read-PrivateSources(", "function Read-PrivateSourcesV2(")
    files["PRIVATE_EXT.ps1"] = (
        ext.encode() + b"\n" + read("tools/windows_demo_private_week_v3/EXTENSION.ps1")
    )
    files["README_KO.md"] = read("tools/windows_demo_private_week_v3/README_KO.md")
    files["COMMON.ps1"] = once(
        files["COMMON.ps1"].decode(),
        "copy-private-week-v2-20261116-",
        "copy-private-week-v3-20261116-",
    ).encode()
    core = (
        files["COPY_CORE.ps1"]
        .decode()
        .replace("COPY_PRIVATE_WEEK_V2_RECEIPT.json", "COPY_PRIVATE_WEEK_V3_RECEIPT.json")
        .replace(
            "PRIVATE_WEEK_V2_SYNTHETIC_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS",
            "PRIVATE_WEEK_V3_SYNTHETIC_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS",
        )
    )
    core = core.replace(
        "prior_private_resources_stopped_verified=$script:PriorPrivateStoppedVerified;",
        "prior_private_resources_stopped_verified=$script:PriorPrivateStoppedVerified;prior_private_v2_stopped_verified=$script:PriorPrivateV2StoppedVerified;late_lease_result_diagnostic=$script:PrivateLateResultDiagnostic;",
    )
    core = core.replace(
        "diagnostic_failure_category=$script:CopyDiagnosticFailure;",
        "diagnostic_failure_category=$script:CopyDiagnosticFailure;late_lease_result_diagnostic=$script:PrivateLateResultDiagnostic;",
    )
    files["COPY_CORE.ps1"] = core.encode()
    cfg = json.loads(files["CONFIG.json"])
    cfg["prior_private_v2_reader_id"] = PRIOR_V2_READER
    files["CONFIG.json"] = (json.dumps(cfg, sort_keys=True) + "\n").encode()
    manifest = (json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2) + "\n").encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_PRIVATE_WEEK.cmd", "ExecuteReview")]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_COPY.ps1" '
            + f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()} -{mode}\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return files


def build(ref, output):
    files = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + p: b for p, b in files.items()}))
    print(
        json.dumps(
            dict(artifact=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        )
    )
    return target


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.ref, a.output)
