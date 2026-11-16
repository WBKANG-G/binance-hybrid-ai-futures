"""Create local hash-only reference files after locked read-only receipt validation."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_private_receipt_review_v2_bundle import package_files as review_package

FOLDER = "windows-demo-private-reference-20261116"
TOOLS = "tools/windows_demo_private_reference/"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    previous = review_package(ref)
    files = {
        p: git("show", revision + ":" + TOOLS + p)
        for p in (
            "RUN_REFERENCE.ps1",
            "REFERENCE_CORE.ps1",
            "README_KO.md",
            "EVIDENCE_REQUIREMENTS_KO.md",
        )
    }
    files["REVIEW_CORE.ps1"] = previous["REVIEW_CORE.ps1"]
    cfg = json.loads(previous["CONFIG.json"])
    cfg["reference_builder_source_reference"] = revision
    files["CONFIG.json"] = (json.dumps(cfg, indent=2, sort_keys=True) + "\n").encode()
    manifest = (json.dumps(dict(files=hashes(files)), indent=2, sort_keys=True) + "\n").encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [
        ("RUN_CHECK.cmd", "CheckOnly"),
        ("RUN_CREATE_REFERENCE.cmd", "CreateReference"),
    ]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_REFERENCE.ps1" '
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
