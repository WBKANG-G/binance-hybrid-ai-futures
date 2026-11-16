"""Seal the November 16 synthetic private-tmpfs/fresh-copy Windows rehearsal."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_evidence_bundle import CANDIDATE as EVIDENCE_CANDIDATE
from scripts.build_windows_copy_evidence_bundle import package_files as evidence_package
from scripts.build_windows_copy_transition_bundle import once

FOLDER = "windows-demo-private-week-rehearsal-20261116"
PRIOR_CLOCK_PIN = "8bd24518a93cb977479dbcaed2670236c256386ac400e9b129f35965a167d6f5"
# Consistent 64-character value in the user's success and subsequent checker screenshots.
# This is a local lineage pin, not independent external custody/account proof.
PRIOR_EVIDENCE_PIN = "0dc12ec08a82e708d31ce90e1846228016c5120a0ec24517ad282ab81eabe37e"
PRIOR_EVIDENCE_SOURCE = "3ff5bc4358aee59797f5b25f259b6437cec7844e"
EXTRAS = (
    "scripts/demo_key_session.py",
    "services/execution/demo_read_credential_context.py",
    "services/execution/demo_copy_volatile_credentials.py",
    "services/execution/demo_private_read_scope.py",
    "services/execution/demo_copy_private_week_review.py",
)
CANDIDATE = EVIDENCE_CANDIDATE + EXTRAS


def package_files(ref: str) -> dict[str, bytes]:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()

    def read(path):
        return git("show", revision + ":" + path)

    files = {
        p: b
        for p, b in evidence_package(ref, PRIOR_CLOCK_PIN).items()
        if p not in {"TRANSFER_MANIFEST.json", "RUN_CHECK.cmd", "RUN_EVIDENCE_REHEARSAL.cmd"}
    }
    files["COMMON.ps1"] = once(
        files["COMMON.ps1"].decode(), "copy-evidence-20261115-", "copy-private-week-20261116-"
    ).encode()
    core = files["COPY_CORE.ps1"].decode()
    core = once(
        core,
        "Read-EvidenceSources $config $receipt $state",
        "Read-PrivateSources $config $receipt $state",
    )
    core = once(
        core,
        "$handles+=,$script:EvidencePriorHandle",
        "$handles+=,$script:EvidencePriorHandle; $handles+=,$script:PrivatePriorHandle",
    )
    core = once(core, "Invoke-EvidenceDiagnostic $Root", "Invoke-PrivateDiagnostic $Root")
    # All private roles must be in the exact container-set comparison, also after a failure.
    core = core.replace(
        "Assert-RestoreSourceSnapshot $original $helpers $false",
        "Assert-RestoreSourceSnapshot $original @(@($helpers+$script:PrivateHelperIDs) | Select-Object -Unique) $false",
    )
    core = once(
        core,
        "$stage='STOP_COPY_AND_REVERIFY_INPUTS';",
        "$helpers=@(@($helpers+$script:PrivateHelperIDs) | Select-Object -Unique)\n        $stage='STOP_COPY_AND_REVERIFY_INPUTS';",
    )
    core = core.replace("COPY_EVIDENCE_RECEIPT.json", "COPY_PRIVATE_WEEK_RECEIPT.json")
    core = core.replace("copy_evidence_rehearsal=", "private_week_rehearsal=")
    core = core.replace(
        "FRESH_COPY_SYNTHETIC_EVIDENCE_VERIFIED_ORIGINALS_UNCHANGED",
        "FRESH_COPY_PRIVATE_WEEK_VERIFIED_ORIGINALS_UNCHANGED",
    )
    core = core.replace(
        "copy_evidence_result=$script:CopyDiagnostic", "private_week_result=$script:CopyDiagnostic"
    )
    core = core.replace(
        "private_evidence_report_directory=", "private_week_report_directory="
    ).replace("copy_evidence_receipt_sha256=", "private_week_receipt_sha256=")
    core = once(
        core,
        "$stage='SYNTHETIC_COPY_EVIDENCE_PIPELINE_AND_STARTUP_HOLD'",
        "$stage='SYNTHETIC_PRIVATE_COPY_AND_WEEK_OBSERVATION'",
    )
    core = core.replace(
        "REVIEW_SYNTHETIC_EVIDENCE_RECEIPT_NO_REAL_GET_NO_DAY_ADOPTION_KEEP_STOPPED",
        "REVIEW_PRIVATE_WEEK_RECEIPT_NO_REAL_GET_NO_BASELINE_ADOPTION_KEEP_STOPPED",
    )
    core = core.replace(
        "COPY_SYNTHETIC_EVIDENCE_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS",
        "PRIVATE_WEEK_SYNTHETIC_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS",
    )
    core = once(core, "^(COPY|HANDOFF|CLOCK|EVIDENCE)_", "^(COPY|HANDOFF|CLOCK|EVIDENCE|PRIVATE)_")
    core = once(
        core,
        "$result=[ordered]@{prior_clock_receipt_sha256=",
        "if ((Restore-Hash $script:PrivatePriorPath) -cne $config.prior_evidence_receipt_sha256) { throw 'PRIVATE_PRIOR_HASH_MISMATCH' }\n        $result=[ordered]@{prior_evidence_receipt_sha256=$config.prior_evidence_receipt_sha256;prior_clock_receipt_sha256=",
    )
    core = core.replace(
        "retained_clone_volumes=$volumes;",
        "retained_clone_volumes=$volumes;private_helper_ids=$script:PrivateHelperIDs;private_key_volume=$script:PrivateKeyVolume;private_key_session_destroyed=$script:PrivateEmptyVerified;",
    )
    core = once(
        core,
        "if ($null -ne $script:EvidencePriorHandle) { $script:EvidencePriorHandle.Dispose() }",
        "if ($null -ne $script:EvidencePriorHandle) { $script:EvidencePriorHandle.Dispose() }; if ($null -ne $script:PrivatePriorHandle) { $script:PrivatePriorHandle.Dispose() }",
    )
    core = once(
        core,
        "private_week_report_directory=$directory;",
        "private_week_report_directory=$directory;private_key_session_destroyed=$script:PrivateEmptyVerified;",
    )
    files["COPY_CORE.ps1"] = core.encode()
    files["PRIVATE_EXT.ps1"] = read("tools/windows_demo_private_week/PRIVATE_EXT.ps1")
    files["private_worker.py"] = read("tools/windows_demo_private_week/worker.py")
    files["README_KO.md"] = read("tools/windows_demo_private_week/README_KO.md")
    launcher = files["RUN_COPY.ps1"].decode()
    additions = ",".join("'payload/" + p + "'" for p in EXTRAS)
    launcher = once(
        launcher,
        "'payload/services/execution/demo_copy_evidence_rehearsal.py')",
        "'payload/services/execution/demo_copy_evidence_rehearsal.py','PRIVATE_EXT.ps1','private_worker.py',"
        + additions
        + ")",
    )
    launcher = once(
        launcher,
        "    . (Join-Path $backupRoot 'EVIDENCE_EXT.ps1')",
        "    . (Join-Path $backupRoot 'EVIDENCE_EXT.ps1')\n    . (Join-Path $backupRoot 'PRIVATE_EXT.ps1')",
    )
    files["RUN_COPY.ps1"] = launcher.encode()
    cfg = json.loads(files["CONFIG.json"])
    cfg["candidate_files"] = {p: hashlib.sha256(read(p)).hexdigest() for p in CANDIDATE}
    cfg["worker_report_keys"] += [
        "private_week_review",
        "private_scope_policy",
        "synthetic_key_file_unchanged",
    ]
    cfg.update(
        prior_evidence_receipt_sha256=PRIOR_EVIDENCE_PIN,
        prior_evidence_source_reference=PRIOR_EVIDENCE_SOURCE,
        review_day_kst="2026-11-16",
        review_week_kst="2026-11-16",
    )
    files["CONFIG.json"] = (json.dumps(cfg, sort_keys=True) + "\n").encode()
    for p in EXTRAS:
        files["payload/" + p] = read(p)
    manifest = (json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2) + "\n").encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for name, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_PRIVATE_WEEK.cmd", "ExecuteReview")]:
        files[name] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_COPY.ps1" '
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.ref, args.output)
