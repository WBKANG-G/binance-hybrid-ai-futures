"""Build a sealed read-only saved-evidence recovery proposal without network or DB code."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes

FOLDER = "windows-demo-recovery-preview-20261118"
PIN = "2db0b6331eb79f59375823994aea137b5910614a"


def pinned(path):
    return git("show", PIN + ":" + path)


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        name: git("show", revision + ":tools/windows_demo_recovery_preview/" + name)
        for name in ("RUN_PREVIEW.ps1", "PREVIEW_CORE.ps1", "README_KO.md")
    }
    for name, path in {
        "READ_CORE.ps1": "tools/windows_demo_request_reference/INSPECT_CORE.ps1",
        "ARRAY_CORE.ps1": "tools/windows_demo_history_review/ARRAY_CORE.ps1",
        "PRIVATE_READ.ps1": "tools/windows_demo_history_review/PRIVATE_READ.ps1",
        "PLAN_CORE.ps1": "tools/windows_demo_income_read/PLAN_CORE.ps1",
        "BASELINE_CORE.ps1": "tools/windows_demo_baseline_reference/BASELINE_CORE.ps1",
        "SAVED_INCOME_CORE.ps1": "tools/windows_demo_trade_read/SAVED_CORE.ps1",
        "SAVED_TRADE_CORE.ps1": "tools/windows_demo_current_read/SAVED_TRADE_CORE.ps1",
    }.items():
        files[name] = pinned(path)
    # Extract only pure parsers: the bundle carries no transport, credentials, or writer.
    income = pinned("tools/windows_demo_trade_read/INCOME_HELPERS.ps1").decode()
    pure = income[: income.index("function Income-Now(")]
    pure += income[
        income.index("function Income-Boundaries(") : income.index("function Income-Save(")
    ]
    pure += income[
        income.index("function Income-Account(") : income.index("function Income-AddRows(")
    ]
    current = pinned("tools/windows_demo_current_read/CURRENT_CORE.ps1").decode()
    pure += current[: current.index("function Current-Fetch(")]
    pure += """
function Export-Decode([string]$Body) {
 $value=Inspect-Json $Body
 Inspect-Require ($null -ne $value -and $value.GetType() -eq [System.Management.Automation.PSCustomObject]) 'PREVIEW_JSON_OBJECT_REQUIRED'
 return $value
}
"""
    files["PURE_HELPERS.ps1"] = pure.encode()
    manifest = (
        json.dumps({"source_reference": revision, "files": hashes(files)}, indent=2, sort_keys=True)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_PREVIEW.cmd"] = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_PREVIEW.ps1" -ManifestSHA256 '
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.ref, args.output)
