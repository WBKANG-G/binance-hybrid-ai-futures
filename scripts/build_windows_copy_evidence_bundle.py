"""Seal the synthetic-only Windows copy evidence rehearsal with an explicit prior pin."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_clock_bundle import CANDIDATE as CLOCK_CANDIDATE
from scripts.build_windows_copy_clock_bundle import package_files as clock_package
from scripts.build_windows_copy_transition_bundle import once

FOLDER = "windows-demo-copy-evidence-rehearsal-20261115"
PRIOR_CLOCK_SOURCE = "4d1d90e8815e405d8f338a8e972ae1acff10b7e6"
EXTRAS = (
    "services/execution/demo_response_evidence_transport.py",
    "services/execution/demo_copy_baseline_evidence.py",
    "services/execution/demo_copy_response_archive.py",
    "services/execution/demo_copy_account_content.py",
    "services/execution/demo_copy_evidence_rehearsal.py",
)
CANDIDATE = CLOCK_CANDIDATE + EXTRAS


def package_files(ref: str, prior_clock_sha256: str) -> dict[str, bytes]:
    if re.fullmatch(r"[0-9a-f]{64}", prior_clock_sha256) is None:
        raise ValueError("EVIDENCE_EXPLICIT_PRIOR_CLOCK_PIN_REQUIRED")
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()

    def read(path: str) -> bytes:
        return git("show", revision + ":" + path)

    old = clock_package(ref)
    files = {
        p: b
        for p, b in old.items()
        if p not in {"TRANSFER_MANIFEST.json", "RUN_CHECK.cmd", "RUN_CLOCK_REHEARSAL.cmd"}
    }
    files["COMMON.ps1"] = once(
        files["COMMON.ps1"].decode(), "copy-clock-20261115-", "copy-evidence-20261115-"
    ).encode()
    core = files["COPY_CORE.ps1"].decode()
    core = once(
        core,
        "Read-ClockSources $config $receipt $state",
        "Read-EvidenceSources $config $receipt $state",
    )
    core = once(
        core,
        "$handles+=,$script:ClockPriorHandle",
        "$handles+=,$script:ClockPriorHandle; $handles+=,$script:EvidencePriorHandle",
    )
    core = once(core, "Invoke-ClockDiagnostic $Root", "Invoke-EvidenceDiagnostic $Root")
    core = core.replace("COPY_CLOCK_RECEIPT.json", "COPY_EVIDENCE_RECEIPT.json")
    core = core.replace("copy_clock_rehearsal=", "copy_evidence_rehearsal=")
    core = core.replace(
        "FRESH_COPY_CLOCK_REHEARSED_ORIGINALS_UNCHANGED",
        "FRESH_COPY_SYNTHETIC_EVIDENCE_VERIFIED_ORIGINALS_UNCHANGED",
    )
    core = core.replace(
        "copy_clock_result=$script:CopyDiagnostic", "copy_evidence_result=$script:CopyDiagnostic"
    )
    core = core.replace(
        "private_clock_report_directory=", "private_evidence_report_directory="
    ).replace("copy_clock_receipt_sha256=", "copy_evidence_receipt_sha256=")
    core = once(
        core,
        "$stage='COPY_ONLY_V1_CLOCK_REHEARSAL_AND_STARTUP_HOLD'",
        "$stage='SYNTHETIC_COPY_EVIDENCE_PIPELINE_AND_STARTUP_HOLD'",
    )
    core = once(
        core,
        "next_action='REVIEW_COPY_CLOCK_RECEIPT_NO_DAY_ADOPTION_KEEP_STOPPED'",
        "next_action='REVIEW_SYNTHETIC_EVIDENCE_RECEIPT_NO_REAL_GET_NO_DAY_ADOPTION_KEEP_STOPPED'",
    )
    core = once(
        core,
        "COPY_CLOCK_REHEARSAL_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS",
        "COPY_SYNTHETIC_EVIDENCE_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS",
    )
    core = once(core, "^(COPY|HANDOFF|CLOCK)_", "^(COPY|HANDOFF|CLOCK|EVIDENCE)_")
    core = once(
        core,
        "        $result=[ordered]@{prior_copy_receipt_sha256=",
        "        if ((Restore-Hash $script:EvidencePriorPath) -cne $config.prior_clock_receipt_sha256) { throw 'EVIDENCE_PRIOR_HASH_MISMATCH' }\n        $result=[ordered]@{prior_clock_receipt_sha256=$config.prior_clock_receipt_sha256;prior_copy_receipt_sha256=",
    )
    core = once(
        core,
        "if ($null -ne $script:ClockPriorHandle) { $script:ClockPriorHandle.Dispose() }",
        "if ($null -ne $script:ClockPriorHandle) { $script:ClockPriorHandle.Dispose() }; if ($null -ne $script:EvidencePriorHandle) { $script:EvidencePriorHandle.Dispose() }",
    )
    # Default depth 2 turns nested content_review into a PSCustomObject string.
    # The persisted receipts already use Write-BackupJson depth 16.
    marker = "} | ConvertTo-Json -Compress | Write-Output"
    lines = core.splitlines(keepends=True)
    matches = [
        i
        for i, line in enumerate(lines)
        if "copy_evidence_rehearsal='FRESH_COPY_SYNTHETIC_EVIDENCE_VERIFIED_ORIGINALS_UNCHANGED'"
        in line
    ]
    if len(matches) != 1:
        raise ValueError("EVIDENCE_CONSOLE_TEMPLATE_CHANGED")
    i = matches[0]
    lines[i] = once(lines[i], marker, "} | ConvertTo-Json -Depth 16 -Compress | Write-Output")
    core = "".join(lines)
    files["COPY_CORE.ps1"] = core.encode()
    extension = files["CLOCK_EXT.ps1"].decode()
    extension = extension.replace("'demo-copy-clock'", "'demo-copy-evidence'")
    extension = once(
        extension,
        "com.docker.compose.project=demo-copy-clock",
        "com.docker.compose.project=demo-copy-evidence",
    )
    extension = once(
        extension, "$value.candidate_files_verified -ne 5", "$value.candidate_files_verified -ne 10"
    )
    extension = once(
        extension,
        "if ($line -cmatch '^COPY_CLOCK_HOLD remaining_seconds=[0-9]{1,2}$')",
        "if ($line -cmatch '^COPY_CLOCK_HOLD remaining_seconds=[0-9]{1,2}$' -or $line -cmatch '^COPY_EVIDENCE_STAGE=(VERIFY_REHEARSED_COPY|CONFIGURE_COPY_AUXILIARY_ONLY|SYNTHETIC_GET_AND_ARCHIVE|READ_ONLY_CONTENT_AND_PRESERVATION_CHECK)$')",
    )
    files["CLOCK_EXT.ps1"] = extension.encode()
    files["EVIDENCE_EXT.ps1"] = read("tools/windows_demo_copy_evidence/EVIDENCE_EXT.ps1")
    files["copy_worker.py"] = read("tools/windows_demo_copy_evidence/worker.py")
    files["README_KO.md"] = read("tools/windows_demo_copy_evidence/README_KO.md")
    launcher = files["RUN_COPY.ps1"].decode()
    marker = "'payload/services/execution/demo_copy_clock_rehearsal.py','CLOCK_EXT.ps1')"
    additions = ",".join("'payload/" + p + "'" for p in EXTRAS)
    launcher = once(
        launcher,
        marker,
        "'payload/services/execution/demo_copy_clock_rehearsal.py','CLOCK_EXT.ps1','EVIDENCE_EXT.ps1',"
        + additions
        + ")",
    )
    launcher = once(
        launcher,
        "    . (Join-Path $backupRoot 'CLOCK_EXT.ps1')",
        "    . (Join-Path $backupRoot 'CLOCK_EXT.ps1')\n    . (Join-Path $backupRoot 'EVIDENCE_EXT.ps1')",
    )
    files["RUN_COPY.ps1"] = launcher.encode()
    cfg = json.loads(files["CONFIG.json"])
    cfg["candidate_files"] = {p: hashlib.sha256(read(p)).hexdigest() for p in CANDIDATE}
    cfg["worker_report_keys"].append("synthetic_evidence_rehearsal")
    cfg["prior_clock_receipt_sha256"] = prior_clock_sha256
    cfg["prior_clock_source_reference"] = PRIOR_CLOCK_SOURCE
    files["CONFIG.json"] = (json.dumps(cfg, sort_keys=True) + "\n").encode()
    for p in EXTRAS:
        files["payload/" + p] = read(p)
    manifest = (json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2) + "\n").encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [
        ("RUN_CHECK.cmd", "CheckOnly"),
        ("RUN_EVIDENCE_REHEARSAL.cmd", "ExecuteReview"),
    ]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_COPY.ps1" '
            + f"-ManifestSHA256 {hashlib.sha256(manifest).hexdigest()} -{mode}\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return files


def build(ref: str, output: Path, prior_clock_sha256: str) -> Path:
    files = package_files(ref, prior_clock_sha256)
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
    parser.add_argument("--prior-clock-sha256", required=True)
    args = parser.parse_args()
    build(args.ref, args.output, args.prior_clock_sha256)
