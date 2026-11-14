"""Build isolated restore package, pinned to the user's retained preservation hashes."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_cold_backup_bundle import API, DB

FOLDER = "windows-demo-isolated-restore-v2-20261114"
PREFIX = "tools/windows_demo_isolated_restore/"
RECEIPT = "4aff6c186597d9ea97a12928101961f5143c93dfae36b5b647109d11676b6778"
MANIFEST = "2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d"
PRESERVATION_SOURCE = "349831e2c1a70c9caa7ed8dedf172641eb1878e4"


def package_files(ref: str) -> dict[str, bytes]:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        name: git("show", revision + ":" + PREFIX + name)
        for name in (
            "RUN_RESTORE.ps1",
            "RESTORE_CORE.ps1",
            "restore_worker.py",
            "account_check.sql",
            "README_KO.md",
        )
    }
    plan = git("show", revision + ":tools/windows_demo_backup_plan/RUN_PLAN.ps1").decode()
    files["CONTEXT.ps1"] = plan[
        plan.index("function Digest(") : plan.index("\ntry {\n    if ($ManifestSHA256")
    ].encode()
    core = git("show", revision + ":tools/windows_demo_cold_backup/BACKUP_CORE.ps1").decode()
    files["COMMON.ps1"] = (
        core[: core.index("function Read-BackupPlan(")]
        + core[
            core.index("function Assert-SourceSnapshot(") : core.index(
                "function Request-GracefulSourceStop("
            )
        ]
    ).encode()
    files["cold_archive.py"] = git("show", revision + ":tools/cold_backup_candidate.py")
    files["CONFIG.json"] = (
        json.dumps(
            dict(
                source_reference=revision,
                preservation_source_reference=PRESERVATION_SOURCE,
                api_container_id=API,
                database_container_id=DB,
                receipt_sha256=RECEIPT,
                manifest_sha256=MANIFEST,
            ),
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    manifest = json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2).encode() + b"\n"
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in (("RUN_CHECK.cmd", "CheckOnly"), ("RUN_RESTORE.cmd", "ExecuteRestore")):
        files[name] = (
            "@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
            'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_RESTORE.ps1" '
            f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()} -{mode}\r\nexit /b %errorlevel%\r\n"
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
