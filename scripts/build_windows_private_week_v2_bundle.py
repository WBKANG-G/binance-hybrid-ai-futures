"""Seal fresh-copy late-lease repair; never resume the failed private copy."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_private_week_bundle import package_files as old_package

FOLDER = "windows-demo-private-week-v2-20261116"
PREPARATION = "services/execution/demo_copy_private_preparation.py"
PRIOR_PRIVATE_IDS = (
    "539539d5123a13c4d4c02e00c6f2657e194de31f47e968737f3de34f9b68f28e",
    "a3a0df9c36ff2905e71568937fee4b853cb2f9dcad242eea14fe14020a64858c",
    "60f4e1db9830453bafcf5947ea8689187efe87aaa49b81d6fc33704c02e0b70d",
    "7f9e9c5e9d65cb6219dab1e606238af9dc49452c10ca71bf2ca55dda2728ba16",
    "9317632f1f16034a97da930d10183878278bfd69226a91c9c41f62c9b18c7a95",
)


def package_files(ref):
    rev = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()

    def read(p):
        return git("show", rev + ":" + p)

    files = {
        p: b
        for p, b in old_package(ref).items()
        if p not in {"TRANSFER_MANIFEST.json", "RUN_CHECK.cmd", "RUN_PRIVATE_WEEK.cmd"}
    }
    for p, source in [
        ("PRIVATE_EXT.ps1", "PRIVATE_EXT.ps1"),
        ("private_worker.py", "worker.py"),
        ("README_KO.md", "README_KO.md"),
    ]:
        files[p] = read("tools/windows_demo_private_week_v2/" + source)
    files["payload/" + PREPARATION] = read(PREPARATION)
    files["COMMON.ps1"] = once(
        files["COMMON.ps1"].decode(),
        "copy-private-week-20261116-",
        "copy-private-week-v2-20261116-",
    ).encode()
    core = (
        files["COPY_CORE.ps1"]
        .decode()
        .replace("COPY_PRIVATE_WEEK_RECEIPT.json", "COPY_PRIVATE_WEEK_V2_RECEIPT.json")
        .replace(
            "PRIVATE_WEEK_SYNTHETIC_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS",
            "PRIVATE_WEEK_V2_SYNTHETIC_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS",
        )
    )
    core = core.replace(
        "diagnostic_failure_category=$script:CopyDiagnosticFailure;",
        "diagnostic_failure_category=$script:CopyDiagnosticFailure;private_timing_reasons=$script:PrivateTimingReasons;",
    )
    core = core.replace(
        "copy_mounts_and_network_verified=$true;",
        "copy_mounts_and_network_verified=$true;prior_private_resources_stopped_verified=$script:PriorPrivateStoppedVerified;",
    )
    files["COPY_CORE.ps1"] = core.encode()
    launcher = files["RUN_COPY.ps1"].decode()
    launcher = once(
        launcher,
        "'payload/services/execution/demo_copy_private_week_review.py')",
        "'payload/services/execution/demo_copy_private_week_review.py','payload/"
        + PREPARATION
        + "')",
    )
    files["RUN_COPY.ps1"] = launcher.encode()
    cfg = json.loads(files["CONFIG.json"])
    cfg["candidate_files"][PREPARATION] = hashlib.sha256(
        files["payload/" + PREPARATION]
    ).hexdigest()
    cfg["prior_private_helper_ids"] = PRIOR_PRIVATE_IDS
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
    with target.open("xb") as f:
        f.write(archive({FOLDER + "/" + p: b for p, b in files.items()}))
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
