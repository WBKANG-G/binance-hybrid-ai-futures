"""Build the reviewed Windows cold backup executor from committed sources."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_cold_backup_plan_bundle import package_files as plan_files

FOLDER = "windows-demo-cold-backup-v2-20261114"
PREFIX = "tools/windows_demo_cold_backup/"
API = "4a8c061be784c3d73bd808a2da39d2374ed49f69294d1c64a2487db828e96026"
DB = "42bb757768213d35c4322c15e68982aef690b572fc061615cbdc27649dadb041"


def package_files(ref: str) -> dict[str, bytes]:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    plan = plan_files(ref)
    context = plan["PLAN.ps1"].decode().split("\ntry {\n    if ($ManifestSHA256", 1)[0]
    context = context[context.index("function Digest(") :]
    files = {
        name: git("show", revision + ":" + PREFIX + source)
        for name, source in (
            ("RUN_BACKUP.ps1", "RUN_BACKUP.ps1"),
            ("BACKUP_CORE.ps1", "BACKUP_CORE.ps1"),
            ("account_header.py", "account_header.py"),
            ("backup_driver.py", "backup_driver.py"),
            ("README_KO.md", "README_KO.md"),
        )
    }
    files["CONTEXT.ps1"] = context.encode()
    files["cold_archive.py"] = git("show", revision + ":tools/cold_backup_candidate.py")
    files["BACKUP_CONFIG.json"] = (
        json.dumps(
            dict(source_reference=revision, api_container_id=API, database_container_id=DB),
            sort_keys=True,
            indent=2,
        ).encode()
        + b"\n"
    )
    files.update({"plan/" + name: body for name, body in plan.items()})
    manifest = json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2).encode() + b"\n"
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_BACKUP.cmd"] = (
        "@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
        'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_BACKUP.ps1" '
        f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()} -ExecuteColdBackup\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    files["RUN_CHECK.cmd"] = (
        "@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
        'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_BACKUP.ps1" '
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
