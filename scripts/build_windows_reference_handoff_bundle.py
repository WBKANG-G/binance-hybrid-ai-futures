"""Copy validated hash-only references to a new D USB directory without overwriting backups."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_private_receipt_review_v2_bundle import package_files as review_package

FOLDER = "windows-demo-reference-handoff-20261116"
TOOLS = "tools/windows_demo_reference_handoff/"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    previous = review_package(ref)
    files = {
        p: git("show", revision + ":" + TOOLS + p)
        for p in (
            "RUN_HANDOFF.ps1",
            "HANDOFF_CORE.ps1",
            "README_KO.md",
            "MEDIA.ps1",
        )
    }
    files["REVIEW_CORE.ps1"] = previous["REVIEW_CORE.ps1"]
    cfg = json.loads(previous["CONFIG.json"])
    cfg["handoff_source_reference"] = revision
    cfg["expected_reference_source_reference"] = "ee8aa302e90ec2b9227e03c557b2a226b78f0289"
    cfg["destination_drive"] = "D"
    files["CONFIG.json"] = (json.dumps(cfg, indent=2, sort_keys=True) + "\n").encode()
    manifest = (json.dumps(dict(files=hashes(files)), indent=2, sort_keys=True) + "\n").encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [
        ("RUN_CHECK.cmd", "CheckOnly"),
        ("RUN_HANDOFF.cmd", "CopyReference"),
    ]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_HANDOFF.ps1" '
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
