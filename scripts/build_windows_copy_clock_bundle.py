"""Fresh-copy V1 clock rehearsal pinned to the latest successful copy diagnostic."""

import argparse
import ast
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_copy_transition_bundle import package_files as copy_package

FOLDER = "windows-demo-copy-clock-rehearsal-20261115"
PRIOR_RECEIPT = "df50d9b96a9cc67b2d46110eeb75858c2ad6057eb95c796b31a881a6c2937365"
PRIOR_SOURCE = "dce468d351738b92ef4651a395b7b8f47265baad"
CANDIDATE = (
    "packages/persistence/demo_read_recovery.py",
    "packages/persistence/demo_response_evidence.py",
    "services/execution/demo_evidence_snapshot.py",
    "services/execution/demo_copy_transition_review.py",
    "services/execution/demo_copy_clock_rehearsal.py",
)


def package_files(ref: str) -> dict[str, bytes]:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()

    def read(p: str) -> bytes:
        return git("show", revision + ":" + p)

    old = copy_package(ref)
    files = {
        p: b
        for p, b in old.items()
        if not p.startswith("payload/")
        and p not in {"TRANSFER_MANIFEST.json", "RUN_CHECK.cmd", "RUN_COPY_REVIEW.cmd"}
    }
    files["COMMON.ps1"] = once(
        files["COMMON.ps1"].decode(), "copy-review-20261115-", "copy-clock-20261115-"
    ).encode()
    core = files["COPY_CORE.ps1"].decode()
    core = once(
        core,
        "$original=Read-CopySources $config $receipt $state; $handles+=,$script:CopyRestoreHandle",
        "$original=Read-ClockSources $config $receipt $state; $handles+=,$script:CopyRestoreHandle; $handles+=,$script:ClockPriorHandle",
    )
    core = once(core, "Invoke-CopyDiagnostic $Root", "Invoke-ClockDiagnostic $Root")
    core = once(
        core,
        "$stage='READ_ONLY_CLOCK_PERIOD_DIAGNOSTIC'",
        "$stage='COPY_ONLY_V1_CLOCK_REHEARSAL_AND_STARTUP_HOLD'",
    )
    core = once(
        core,
        "isolated_restore='BLOCKED_KEEP_ORIGINALS_STOPPED_NO_RETRY'",
        "copy_clock_rehearsal='BLOCKED_COPY_MAY_HAVE_CHANGED_NO_RETRY';copy_may_have_changed=($null -ne $clone);clock_application_may_have_completed=$script:ClockApplyAttempted",
    )
    core = once(
        core,
        "@{stage=$stage;failure_category=$reason;diagnostic_failure_category=",
        "@{stage=$stage;failure_category=$reason;copy_may_have_changed=($null -ne $clone);clock_application_may_have_completed=$script:ClockApplyAttempted;diagnostic_failure_category=",
    )
    core = core.replace("COPY_REVIEW_RECEIPT.json", "COPY_CLOCK_RECEIPT.json")
    core = once(
        core,
        "copy_transition_review='FRESH_COPY_DIAGNOSED_ORIGINALS_UNCHANGED'",
        "copy_clock_rehearsal='FRESH_COPY_CLOCK_REHEARSED_ORIGINALS_UNCHANGED'",
    )
    core = core.replace(
        "copy_transition_diagnostic=$script:CopyDiagnostic",
        "copy_clock_result=$script:CopyDiagnostic",
    )
    core = core.replace(
        "private_copy_report_directory=", "private_clock_report_directory="
    ).replace("copy_review_receipt_sha256=", "copy_clock_receipt_sha256=")
    core = once(
        core,
        "next_action='REVIEW_COPY_DIAGNOSTIC_NO_REBASE_NO_DAY_ADOPTION_KEEP_STOPPED'",
        "next_action='REVIEW_COPY_CLOCK_RECEIPT_NO_DAY_ADOPTION_KEEP_STOPPED'",
    )
    core = core.replace(
        "COPY_REVIEW_COMPLETE_ORIGINALS_UNCHANGED_NEW_COPY_STOPPED_NO_ORDERS",
        "COPY_CLOCK_REHEARSAL_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS",
    )
    core = once(core, "^(COPY|HANDOFF)_", "^(COPY|HANDOFF|CLOCK)_")
    core = once(
        core,
        "foreach ($handle in $handles) { $handle.Dispose() }",
        "foreach ($handle in $handles) { $handle.Dispose() }; if ($null -ne $script:CopyRestoreHandle) { $script:CopyRestoreHandle.Dispose() }; if ($null -ne $script:ClockPriorHandle) { $script:ClockPriorHandle.Dispose() }",
    )
    core = once(
        core,
        "        $result=[ordered]@{",
        "        if ((Restore-Hash $script:ClockPriorPath) -cne $config.prior_copy_receipt_sha256) { throw 'CLOCK_PRIOR_HASH_MISMATCH' }\n        $result=[ordered]@{prior_copy_receipt_sha256=$config.prior_copy_receipt_sha256;",
    )
    files["COPY_CORE.ps1"] = core.encode()
    launcher = files["RUN_COPY.ps1"].decode()
    launcher = once(
        launcher,
        "'payload/services/execution/demo_copy_transition_review.py')",
        "'payload/services/execution/demo_copy_transition_review.py','payload/packages/persistence/demo_read_recovery.py','payload/services/execution/demo_copy_clock_rehearsal.py','CLOCK_EXT.ps1')",
    )
    launcher = once(
        launcher,
        "    . (Join-Path $backupRoot 'COPY_EXT.ps1')",
        "    . (Join-Path $backupRoot 'COPY_EXT.ps1')\n    . (Join-Path $backupRoot 'CLOCK_EXT.ps1')",
    )
    files["RUN_COPY.ps1"] = launcher.encode()
    files["CLOCK_EXT.ps1"] = read("tools/windows_demo_copy_clock_rehearsal/CLOCK_EXT.ps1")
    files["copy_worker.py"] = read("tools/windows_demo_copy_clock_rehearsal/worker.py")
    files["README_KO.md"] = read("tools/windows_demo_copy_clock_rehearsal/README_KO.md")
    config = json.loads(files["CONFIG.json"])
    config["candidate_files"] = {p: hashlib.sha256(read(p)).hexdigest() for p in CANDIDATE}
    for p in CANDIDATE:
        files["payload/" + p] = read(p)
    config["prior_copy_source_reference"] = PRIOR_SOURCE
    config["prior_copy_receipt_sha256"] = PRIOR_RECEIPT
    tree = ast.parse(read(CANDIDATE[-1]))
    returns = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "dict"
    ]
    assert len(returns) == 1
    config["worker_report_keys"] = [k.arg for k in returns[0].keywords] + [
        "baseline_files_verified",
        "candidate_files_verified",
        "copy_read_sessions_read_only",
        "copy_write_session_explicit",
    ]
    files["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    manifest = (json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2) + "\n").encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [
        ("RUN_CHECK.cmd", "CheckOnly"),
        ("RUN_CLOCK_REHEARSAL.cmd", "ExecuteReview"),
    ]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_COPY.ps1" '
            + f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()} -{mode}\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return files


def build(ref: str, output: Path) -> Path:
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
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.ref, args.output)
