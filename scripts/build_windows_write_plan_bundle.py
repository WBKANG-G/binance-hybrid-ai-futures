"""Offline proposal from locally retained original inspection evidence; no DB startup."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_conditional_current_bundle import package_files as source_files

FOLDER = "windows-demo-write-plan-20261124"
PIN = "a26d8ac50bb8220b824eb1e97c91c6493eda6f7d"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    old = source_files(PIN)
    f = {n: old[n] for n in ("READ_CORE.ps1", "PRIVATE_STORAGE.ps1")}
    f["PRIVATE_STORAGE.ps1"] = (
        f["PRIVATE_STORAGE.ps1"]
        .replace(b"WBKANG-DEMO-ConditionalReads", b"WBKANG-DEMO-WritePlans")
        .replace(b"conditional-current-20261124-", b"write-plan-20261124-")
    )
    for n, path in [
        ("RUN_PREPARE.ps1", "tools/windows_demo_write_plan/RUN_PREPARE.ps1"),
        ("PLAN_CORE.ps1", "tools/windows_demo_write_plan/PLAN_CORE.ps1"),
        ("README_KO.md", "tools/windows_demo_write_plan/README_KO.md"),
        ("transaction_candidate.py", "services/execution/demo_period_transaction_candidate.py"),
    ]:
        f[n] = git("show", revision + ":" + path)
    f["RUN_PREPARE.ps1"] = f["RUN_PREPARE.ps1"].replace(
        b"'__FILES__'", ",".join("'" + n + "'" for n in f).encode()
    )
    m = (
        json.dumps(dict(source_reference=revision, files=hashes(f)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    f["TRANSFER_MANIFEST.json"] = m
    for n, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_PREPARE.cmd", "Prepare")]:
        f[n] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_PREPARE.ps1" -ManifestSHA256 '
            + hashlib.sha256(m).hexdigest()
            + " -"
            + mode
            + "\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return f


def build(ref, output):
    f = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    p = output / (FOLDER + ".zip")
    with p.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + n: b for n, b in f.items()}))
    print(json.dumps(dict(artifact=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.ref, a.output)
