"""Build committed, pinned Windows backup-scope preflight sources only."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.build_demo_read_review_bundle import archive, git, hashes

FOLDER = "windows-demo-backup-scope-preflight-20261113"
PREFIX = "tools/windows_demo_backup_preflight/"


def package_files(ref: str) -> dict[str, bytes]:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    worker = git("show", f"{revision}:{PREFIX}worker.py")
    namespace: dict[str, Any] = {"__name__": "preflight_schema"}
    exec(compile(worker, "<committed-preflight-worker>", "exec"), namespace)
    config = dict(
        source_reference=revision, account=namespace["ACCOUNT"], storage_root=namespace["STORAGE"]
    )
    files = {
        "worker.py": worker,
        "PREFLIGHT.ps1": git("show", f"{revision}:{PREFIX}RUN_PREFLIGHT.ps1"),
        "README_KO.md": git("show", f"{revision}:{PREFIX}README_KO.md"),
        "PREFLIGHT_CONFIG.json": json.dumps(config, sort_keys=True, indent=2).encode() + b"\n",
        "REPORT_SCHEMA.json": json.dumps(
            namespace["report_schema"](), sort_keys=True, indent=2
        ).encode()
        + b"\n",
    }
    manifest = json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2).encode() + b"\n"
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_PREFLIGHT.cmd"] = (
        "@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
        'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0PREFLIGHT.ps1" '
        f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()}\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    return files


def build(ref: str, output: Path) -> Path:
    files = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + name: body for name, body in files.items()}))
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
