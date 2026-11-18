"""Offline link of pinned restored-ledger report to saved recovery candidate evidence."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_offline_handoff_bundle import (
    MANIFEST,
    RECEIPT,
    RESTORE_RECEIPT,
    RESTORE_SOURCE,
)
from scripts.build_windows_recovery_preview_bundle import package_files as preview_files

FOLDER = "windows-demo-recovery-link-20261118"
PIN = "0137d8e0975aab18ca3d3e0c2424d0189c0c31dd"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        n: b
        for n, b in preview_files(PIN).items()
        if not n.endswith(".cmd")
        and n not in {"TRANSFER_MANIFEST.json", "RUN_PREVIEW.ps1", "README_KO.md"}
    }
    for n in ("LINK_CORE.ps1", "RUN_LINK.ps1", "README_KO.md"):
        files[n] = git("show", revision + ":tools/windows_demo_recovery_link/" + n)
    old = git("show", PIN + ":tools/windows_demo_offline_handoff/HANDOFF_CORE.ps1").decode()
    files["RESTORE_ASSERT.ps1"] = old[
        old.index("function Assert-RestoreEvidence(") : old.index(
            "function Assert-HandoffStoppedScope("
        )
    ].encode()
    files["CONFIG.json"] = (
        json.dumps(
            dict(
                manifest_sha256=MANIFEST,
                receipt_sha256=RECEIPT,
                restore_receipt_sha256=RESTORE_RECEIPT,
                restore_source_reference=RESTORE_SOURCE,
            ),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), indent=2, sort_keys=True)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_LINK.cmd"] = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_LINK.ps1" -ManifestSHA256 '
        + hashlib.sha256(manifest).hexdigest()
        + "\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    return files


def build(ref, output):
    f = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as out:
        out.write(archive({FOLDER + "/" + n: b for n, b in f.items()}))
    print(
        json.dumps(
            dict(artifact=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    build(a.ref, a.output)
