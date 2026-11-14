"""Build a forced-check-only scope diagnostic; no restore command is distributed."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_isolated_restore_bundle import package_files as restore_files

FOLDER = "windows-demo-restore-scope-diagnostic-20261114"


def package_files(ref: str) -> dict[str, bytes]:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = restore_files(ref)
    for name in ("TRANSFER_MANIFEST.json", "RUN_RESTORE.cmd", "RUN_CHECK.cmd"):
        del files[name]
    invocation = "Invoke-IsolatedRestore -Root $backupRoot -ExecuteRestore:$ExecuteRestore -CheckOnly:$CheckOnly"
    source = files["RUN_RESTORE.ps1"].decode()
    assert source.count(invocation) == 1
    files["RUN_RESTORE.ps1"] = (
        source.replace(invocation, "Invoke-IsolatedRestore -Root $backupRoot -CheckOnly")
        .replace(
            "source_state_may_have_changed=$backupInvoked", "source_state_may_have_changed=$false"
        )
        .encode()
    )
    files["README_KO.md"] = git(
        "show", revision + ":tools/windows_demo_isolated_restore/DIAGNOSTIC_README_KO.md"
    )
    manifest = json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2).encode() + b"\n"
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_CHECK.cmd"] = (
        "@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
        'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_RESTORE.ps1" '
        f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()} -CheckOnly\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    return files


def build(ref: str, output: Path) -> Path:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.ref, args.output)
