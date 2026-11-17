"""Build a sealed offline reader for a sealed standalone DEMO order-export request tool."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes

FOLDER = "windows-demo-history-export-20261117"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        name: git("show", revision + ":tools/windows_demo_history_export/" + name)
        for name in (
            "RUN_EXPORT.ps1",
            "EXPORT_CORE.ps1",
            "PLAN_CORE.ps1",
            "PRIVATE_STORAGE.ps1",
            "README_KO.md",
        )
    }
    files["READ_CORE.ps1"] = git(
        "show",
        "f3f8e44d11bb4696fe06fc38aed4da14f52837e0:tools/windows_demo_request_reference/INSPECT_CORE.ps1",
    )
    manifest = (
        json.dumps({"source_reference": revision, "files": hashes(files)}, indent=2, sort_keys=True)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in (
        ("RUN_CHECK.cmd", "CheckOnly"),
        ("RUN_REQUEST_EXPORT.cmd", "RequestExportOnce"),
        ("RUN_STATUS.cmd", "StatusOnce"),
    ):
        interaction = " -NonInteractive" if mode == "CheckOnly" else ""
        files[name] = (
            "@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile"
            + interaction
            + ' -ExecutionPolicy Bypass -File "%~dp0RUN_EXPORT.ps1" -ManifestSHA256 '
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
