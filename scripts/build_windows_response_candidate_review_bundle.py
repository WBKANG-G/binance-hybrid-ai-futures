"""Build image-only compatibility review for exactly two GET evidence candidates."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_cold_backup_bundle import API, DB
from scripts.build_windows_isolated_restore_bundle import MANIFEST, PRESERVATION_SOURCE, RECEIPT
from scripts.build_windows_offline_handoff_bundle import RESTORE_RECEIPT, RESTORE_SOURCE
from scripts.build_windows_runtime_inventory_bundle import BASELINE, INSTALLED_ADDITIONS

FOLDER = "windows-demo-response-candidate-review-20261114"
CANDIDATE = (
    "packages/persistence/demo_response_evidence.py",
    "services/execution/demo_response_evidence_transport.py",
)


def package_files(ref: str) -> dict[str, bytes]:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()

    def read(name: str) -> bytes:
        return git("show", revision + ":" + name)

    files = {
        n: read("tools/windows_demo_response_candidate_review/" + n)
        for n in ("REVIEW.ps1", "REVIEW_CORE.ps1", "worker.py", "README_KO.md")
    }
    old = json.loads(
        git("show", BASELINE + ":tools/windows-demo-read-review/INSTALLED_SOURCE_20261111.json")
    )
    baseline = {n: h for n, h in old.items() if n.endswith(".py")}
    baseline.update(
        {
            n: hashlib.sha256(git("show", BASELINE + ":" + n)).hexdigest()
            for n in INSTALLED_ADDITIONS
        }
    )
    assert len(baseline) == 89
    context = read("tools/windows_demo_backup_plan/RUN_PLAN.ps1").decode()
    files["CONTEXT.ps1"] = context[
        context.index("function Digest(") : context.index("\ntry {\n    if ($ManifestSHA256")
    ].encode()
    common = read("tools/windows_demo_cold_backup/BACKUP_CORE.ps1").decode()
    files["COMMON.ps1"] = common[: common.index("function Read-BackupPlan(")].encode()
    support = read("tools/windows_demo_isolated_restore/RESTORE_CORE.ps1").decode()
    files["SUPPORT.ps1"] = support[: support.index("function Read-RestoreSources(")].encode()
    scope = read("tools/windows_demo_offline_handoff/HANDOFF_CORE.ps1").decode()
    files["SOURCE_SCOPE.ps1"] = scope[: scope.index("function Assert-HandoffMediaFacts(")].encode()
    for n in CANDIDATE:
        files["payload/" + n] = read(n)
    files["CONFIG.json"] = (
        json.dumps(
            dict(
                source_reference=revision,
                baseline_reference=BASELINE,
                baseline_files=baseline,
                candidate_files={
                    n: hashlib.sha256(files["payload/" + n]).hexdigest() for n in CANDIDATE
                },
                api_container_id=API,
                database_container_id=DB,
                manifest_sha256=MANIFEST,
                receipt_sha256=RECEIPT,
                preservation_source_reference=PRESERVATION_SOURCE,
                restore_receipt_sha256=RESTORE_RECEIPT,
                restore_source_reference=RESTORE_SOURCE,
            ),
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    manifest = json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2).encode() + b"\n"
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_REVIEW.cmd"] = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0REVIEW.ps1" '
        + f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()}\r\nexit /b %errorlevel%\r\n"
    ).encode("ascii")
    return files


def build(ref: str, output: Path) -> Path:
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
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.ref, args.output)
