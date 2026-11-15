"""Read-only companion receipt review: never Docker, replay, write or repair."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes

FOLDER = "windows-demo-evidence-receipt-review-20261115"
TOOLS = "tools/windows_demo_evidence_receipt_review/"


def package_files(ref: str) -> dict[str, bytes]:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    files = {
        p: git("show", revision + ":" + TOOLS + p)
        for p in ("REVIEW.ps1", "REVIEW_CORE.ps1", "README_KO.md")
    }
    config = dict(
        expected_source_reference="3ff5bc4358aee59797f5b25f259b6437cec7844e",
        prior_clock_receipt_sha256="8bd24518a93cb977479dbcaed2670236c256386ac400e9b129f35965a167d6f5",
        prior_copy_receipt_sha256="df50d9b96a9cc67b2d46110eeb75858c2ad6057eb95c796b31a881a6c2937365",
        manifest_sha256="2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d",
        receipt_sha256="4aff6c186597d9ea97a12928101961f5143c93dfae36b5b647109d11676b6778",
    )
    files["CONFIG.json"] = (json.dumps(config, indent=2, sort_keys=True) + "\n").encode()
    manifest = (json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2) + "\n").encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    files["RUN_RECEIPT_CHECK.cmd"] = (
        '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0REVIEW.ps1" '
        + f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()}\r\nexit /b %errorlevel%\r\n"
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.ref, args.output)
