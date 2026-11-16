"""Build a sealed offline reader for prior local no-send records without resolving or resending requests."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes

FOLDER = "windows-demo-no-send-reference-20261116"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        name: git("show", revision + ":tools/windows_demo_no_send_reference/" + name)
        for name in ("RUN_NO_SEND_CHECK.ps1", "NO_SEND_CORE.ps1", "README_KO.md")
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
    files["RUN_NO_SEND_CHECK.cmd"] = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_NO_SEND_CHECK.ps1" -ManifestSHA256 '
        + hashlib.sha256(manifest).hexdigest()
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
