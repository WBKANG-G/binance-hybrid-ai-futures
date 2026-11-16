"""Create a fresh networkless-copy event projection runner; originals remain stopped."""

import argparse
import ast
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import package_files as transition_package

FOLDER = "windows-demo-copy-event-review-20261116"
CANDIDATE = (
    "scripts/review_demo_historical_receipts.py",
    "services/execution/demo_copy_event_review.py",
)


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()

    def read(p):
        return git("show", revision + ":" + p)

    old = transition_package(ref)
    files = {
        p: b
        for p, b in old.items()
        if not p.startswith("payload/") and p != "TRANSFER_MANIFEST.json" and not p.endswith(".cmd")
    }
    files["COMMON.ps1"] = files["COMMON.ps1"].replace(
        b"copy-review-20261115-", b"copy-event-review-20261116-"
    )
    core = (
        files["COPY_CORE.ps1"]
        .decode()
        .replace("COPY_REVIEW_RECEIPT.json", "COPY_EVENT_REVIEW_RECEIPT.json")
        .replace(
            "copy_transition_review='FRESH_COPY_DIAGNOSED_ORIGINALS_UNCHANGED'",
            "copy_event_review='FRESH_COPY_EVENT_TIMELINES_REVIEWED_ORIGINALS_UNCHANGED'",
        )
        .replace(
            "diagnostic=$script:CopyDiagnostic;copy_mounts",
            "diagnostic=$script:CopyDiagnostic.summary;copy_mounts",
        )
        .replace("copy_transition_diagnostic=", "event_transition_diagnostic=")
        .replace("private_copy_report_directory=", "private_event_report_directory=")
        .replace("copy_review_receipt_sha256=", "event_review_receipt_sha256=")
        .replace(
            "COPY_REVIEW_COMPLETE_ORIGINALS_UNCHANGED_NEW_COPY_STOPPED_NO_ORDERS",
            "COPY_EVENT_REVIEW_COMPLETE_ORIGINALS_UNCHANGED_NEW_COPY_STOPPED_NO_ORDERS",
        )
        .replace("READ_ONLY_CLOCK_PERIOD_DIAGNOSTIC", "READ_ONLY_REQUEST_EVENT_PROJECTION")
        .replace(
            "REVIEW_COPY_DIAGNOSTIC_NO_REBASE_NO_DAY_ADOPTION_KEEP_STOPPED",
            "REVIEW_EVENT_TIMELINES_NO_REQUEST_RESOLUTION_NO_RESEND_KEEP_STOPPED",
        )
        .replace(
            "| ConvertTo-Json -Compress | Write-Output",
            "| ConvertTo-Json -Depth 10 -Compress | Write-Output",
        )
    )
    if "copy_event_review=" not in core or "diagnostic=$script:CopyDiagnostic.summary" not in core:
        raise ValueError("COPY_EVENTS_TEMPLATE_CHANGED")
    files["COPY_CORE.ps1"] = core.encode()
    for target, source in [
        ("COPY_EXT.ps1", "COPY_EXT.ps1"),
        ("copy_worker.py", "worker.py"),
        ("RUN_COPY.ps1", "RUN_COPY.ps1"),
        ("README_KO.md", "README_KO.md"),
    ]:
        files[target] = read("tools/windows_demo_copy_event_review/" + source)
    config = json.loads(old["CONFIG.json"])
    config["candidate_files"] = {p: hashlib.sha256(read(p)).hexdigest() for p in CANDIDATE}
    tree = ast.parse(read(CANDIDATE[-1]))
    keys = ast.literal_eval(
        next(
            n.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "REPORT_KEYS" for t in n.targets)
        )
    )
    config["worker_report_keys"] = list(keys) + [
        "baseline_files_verified",
        "candidate_files_verified",
        "copy_session_read_only",
    ]
    for p in CANDIDATE:
        files["payload/" + p] = read(p)
    files["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    manifest = (json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2) + "\n").encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_EVENT_REVIEW.cmd", "ExecuteReview")]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_COPY.ps1" '
            + f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()} -{mode}\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return files


def build(ref, output):
    files = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (FOLDER + ".zip")
    with target.open("xb") as f:
        f.write(archive({FOLDER + "/" + p: b for p, b in files.items()}))
    print(
        json.dumps(
            dict(artifact=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.ref, a.output)
