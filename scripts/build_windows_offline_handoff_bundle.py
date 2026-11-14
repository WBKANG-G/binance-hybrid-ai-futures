"""Build D: external-media handoff with pinned backup and successful restore evidence."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_cold_backup_bundle import API, DB
from scripts.build_windows_isolated_restore_bundle import MANIFEST, PRESERVATION_SOURCE, RECEIPT

FOLDER = "windows-demo-offline-handoff-v2-20261114"
PREFIX = "tools/windows_demo_offline_handoff/"
RESTORE_RECEIPT = "26395438e08500da24252eac7b11dfe76b22e0d660c86bb1c0d236b6b9e1da08"
RESTORE_SOURCE = "ef24f29a44fe58bfcc23099b81a1ca9ca59801d7"


def package_files(ref: str) -> dict[str, bytes]:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        n: git("show", revision + ":" + PREFIX + n)
        for n in ("RUN_HANDOFF.ps1", "HANDOFF_CORE.ps1", "README_KO.md", "VERIFY_OFFLINE.ps1")
    }
    plan = git("show", revision + ":tools/windows_demo_backup_plan/RUN_PLAN.ps1").decode()
    files["CONTEXT.ps1"] = plan[
        plan.index("function Digest(") : plan.index("\ntry {\n    if ($ManifestSHA256")
    ].encode()
    common = git("show", revision + ":tools/windows_demo_cold_backup/BACKUP_CORE.ps1").decode()
    files["COMMON.ps1"] = common[: common.index("function Read-BackupPlan(")].encode()
    support = git(
        "show", revision + ":tools/windows_demo_isolated_restore/RESTORE_CORE.ps1"
    ).decode()
    files["SUPPORT.ps1"] = support[: support.index("function Read-RestoreSources(")].encode()
    for name, path, expected in (
        (
            "PRESERVATION_TOOL.zip",
            "artifacts/windows-demo-stopped-preservation-20261114/windows-demo-stopped-preservation-20261114.zip",
            "74244ad8c1f0affaeb401b283d28c32a226aa3019e5f1ac35e5d52f88e7a39f3",
        ),
        (
            "RESTORE_TOOL.zip",
            "artifacts/windows-demo-isolated-restore-v2-20261114/windows-demo-isolated-restore-v2-20261114.zip",
            "bbcc0ba69dc37a0198d9384cc24c0b75b520cfce152f162e70abd334a6220d60",
        ),
    ):
        files[name] = git("show", revision + ":" + path)
        assert hashlib.sha256(files[name]).hexdigest() == expected
    files["CONFIG.json"] = (
        json.dumps(
            dict(
                source_reference=revision,
                api_container_id=API,
                database_container_id=DB,
                manifest_sha256=MANIFEST,
                receipt_sha256=RECEIPT,
                preservation_source_reference=PRESERVATION_SOURCE,
                restore_receipt_sha256=RESTORE_RECEIPT,
                restore_source_reference=RESTORE_SOURCE,
                destination_drive="D",
            ),
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    manifest = json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2).encode() + b"\n"
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in (("RUN_CHECK.cmd", "CheckOnly"), ("RUN_HANDOFF.cmd", "ExecuteHandoff")):
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_HANDOFF.ps1" '
            + f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()} -{mode}\r\nexit /b %errorlevel%\r\n"
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
