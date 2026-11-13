"""Build a minimal, pinned, stream-only Windows read-only runtime inventory ZIP."""

import argparse
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes

FOLDER = "windows-demo-runtime-inventory-20261113"
BASELINE = "fb371aeabcc9c4611d7b7d20def1a4e3edeaa41d"
INSTALLED_ADDITIONS = (
    "packages/persistence/demo_read_audit.py",
    "packages/persistence/demo_read_recovery.py",
    "services/execution/demo_read_audit_bootstrap.py",
    "services/execution/demo_unused_read_clock.py",
    "services/execution/demo_runtime_read_transport.py",
    "scripts/review_demo_read_journal.py",
)
CANDIDATE = (
    "packages/persistence/demo.py",
    "packages/persistence/demo_response_evidence.py",
    "services/execution/demo_response_evidence_transport.py",
    "services/execution/demo_request_outcome_candidate.py",
    "services/execution/demo_submission_link_candidate.py",
    "services/execution/demo_wire_session_contract_candidate.py",
    "packages/persistence/demo_wire_checkpoint_candidate.py",
    "services/execution/demo_wire_restore_reference_candidate.py",
    "scripts/review_wire_restore_reference.py",
)


def read_source(ref: str, name: str) -> bytes:
    return git("show", f"{ref}:{name}")


def package_files(
    revision: str, *, read: Callable[[str, str], bytes] = read_source
) -> dict[str, bytes]:
    original = json.loads(
        read(BASELINE, "tools/windows-demo-read-review/INSTALLED_SOURCE_20261111.json")
    )
    baseline = {name: value for name, value in original.items() if name.endswith(".py")}
    baseline.update(
        {name: hashlib.sha256(read(BASELINE, name)).hexdigest() for name in INSTALLED_ADDITIONS}
    )
    config = dict(
        source_reference=revision,
        baseline_reference=BASELINE,
        account="DEMO-WINDOWS-ISOLATED-BTC-V1",
        expected_day_kst="2026-11-13",
        baseline_files=baseline,
        candidate_files={
            name: hashlib.sha256(read(revision, name)).hexdigest() for name in CANDIDATE
        },
    )
    prefix = "tools/windows_demo_runtime_inventory/"
    files = {
        "worker.py": read(revision, prefix + "worker.py"),
        "INVENTORY.ps1": read(revision, prefix + "RUN_INVENTORY.ps1"),
        "README_KO.md": read(revision, prefix + "README_KO.md"),
        "INVENTORY_CONFIG.json": json.dumps(config, indent=2, sort_keys=True).encode() + b"\n",
    }
    manifest = json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2).encode() + b"\n"
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_INVENTORY.cmd"] = (
        "@echo off\r\nsetlocal DisableDelayedExpansion\r\n"
        'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0INVENTORY.ps1" '
        f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()}\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    return files


def build(ref: str, output: Path) -> Path:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = package_files(revision)
    target = output / (FOLDER + ".zip")
    output.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + name: body for name, body in files.items()}))
    print(
        json.dumps(
            dict(
                artifact=str(target),
                source_reference=revision,
                sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            )
        )
    )
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.ref, args.output)
