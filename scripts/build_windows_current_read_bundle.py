"""Build a sealed offline reader for a saved-evidence-linked current account observation tool without changes."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes

FOLDER = "windows-demo-current-read-20261118"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        name: git("show", revision + ":tools/windows_demo_current_read/" + name)
        for name in (
            "RUN_CURRENT.ps1",
            "CURRENT_CORE.ps1",
            "SAVED_TRADE_CORE.ps1",
            "TRADE_HELPERS.ps1",
            "PRIVATE_STORAGE.ps1",
            "READ_TRANSPORT.ps1",
            "README_KO.md",
        )
    }
    files["READ_CORE.ps1"] = git(
        "show",
        "f3f8e44d11bb4696fe06fc38aed4da14f52837e0:tools/windows_demo_request_reference/INSPECT_CORE.ps1",
    )
    files["ARRAY_CORE.ps1"] = git(
        "show", "2db93a7:tools/windows_demo_history_review/ARRAY_CORE.ps1"
    )
    files["PLAN_CORE.ps1"] = git("show", "56dd3f5:tools/windows_demo_income_read/PLAN_CORE.ps1")
    files["SAVED_INCOME_CORE.ps1"] = git(
        "show", "83823a6:tools/windows_demo_trade_read/SAVED_CORE.ps1"
    )
    files["INCOME_HELPERS.ps1"] = git(
        "show", "83823a6:tools/windows_demo_trade_read/INCOME_HELPERS.ps1"
    )
    manifest = (
        json.dumps({"source_reference": revision, "files": hashes(files)}, indent=2, sort_keys=True)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in (("RUN_CHECK.cmd", "CheckOnly"), ("RUN_READ_CURRENT.cmd", "CollectOnce")):
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
    files = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + p: b for p, b in files.items()}))
    print(
        json.dumps(
            {"artifact": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.ref, a.output)
