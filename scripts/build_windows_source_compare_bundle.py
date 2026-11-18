"""Build a pinned stopped-source byte comparator without starting the source database."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_isolated_restore_bundle import (
    API,
    DB,
    MANIFEST,
    PRESERVATION_SOURCE,
    RECEIPT,
)

FOLDER = "windows-demo-source-compare-20261118"
PIN = "0137d8e0975aab18ca3d3e0c2424d0189c0c31dd"


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        n: git("show", revision + ":tools/windows_demo_source_compare/" + n)
        for n in ("RUN_COMPARE.ps1", "COMPARE_CORE.ps1", "worker.py", "README_KO.md")
    }

    def old(path):
        return git("show", PIN + ":" + path).decode()

    plan = old("tools/windows_demo_backup_plan/RUN_PLAN.ps1")
    files["CONTEXT.ps1"] = plan[
        plan.index("function Digest(") : plan.index("\ntry {\n    if ($ManifestSHA256")
    ].encode()
    core = old("tools/windows_demo_cold_backup/BACKUP_CORE.ps1")
    files["SUPPORT.ps1"] = (
        core[core.index("function Backup-Docker(") : core.index("function Write-BackupJson(")]
        + core[
            core.index("function Assert-PrivateBackupDirectory(") : core.index(
                "function Sync-BackupFile("
            )
        ]
    ).encode()
    scope = old("tools/windows_demo_isolated_restore/RESTORE_CORE.ps1")
    files["SOURCE_SCOPE.ps1"] = scope[: scope.index("function Assert-NewClone(")].encode()
    lib = old("tools/cold_backup_candidate.py")
    files["READ_LIBRARY.py"] = (
        lib[: lib.index("class HashReader:")]
        + lib[lib.index("def validate_manifest(") : lib.index("def restore_bundle(")]
    ).encode()
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
        )
        + "\n"
    ).encode()
    manifest = (
        json.dumps(dict(source_reference=revision, files=hashes(files)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_COMPARE.cmd", "CompareOnce")]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_COMPARE.ps1" -ManifestSHA256 '
            + hashlib.sha256(manifest).hexdigest()
            + " -"
            + mode
            + "\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return files


def build(ref, output):
    files = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + n: b for n, b in files.items()}))
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
