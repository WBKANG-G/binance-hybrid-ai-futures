"""Publish inspect-only diagnostics for the stopped-source scope guard."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes

FOLDER = "windows-demo-source-scope-diagnostic-20261118"
PIN = "a1a8435a963a01b9b9d9f08f40cf8180b4d9188c"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    from scripts.build_windows_source_compare_bundle import package_files as prior

    old = prior(PIN)
    files = {n: old[n] for n in ("CONTEXT.ps1", "SUPPORT.ps1", "SOURCE_SCOPE.ps1", "CONFIG.json")}
    for n in ("DIAGNOSTIC_CORE.ps1", "RUN_DIAGNOSE.ps1", "README_KO.md"):
        files[n] = git("show", revision + ":tools/windows_demo_source_scope_diagnostic/" + n)
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_DIAGNOSE.cmd"] = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_DIAGNOSE.ps1" -ManifestSHA256 '
        + hashlib.sha256(manifest).hexdigest()
        + "\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    return files


def build(ref, output):
    files = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as f:
        f.write(archive({FOLDER + "/" + n: b for n, b in files.items()}))
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
