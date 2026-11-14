"""Sealed fresh-copy diagnostic runner; reuse pinned restore checks, no rebase."""

import argparse
import ast
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_response_candidate_review_bundle import package_files as image_package

FOLDER = "windows-demo-copy-transition-review-20261115"
CANDIDATE = (
    "packages/persistence/demo_response_evidence.py",
    "services/execution/demo_evidence_snapshot.py",
    "services/execution/demo_copy_transition_review.py",
)


def once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError("COPY_RESTORE_TEMPLATE_CHANGED")
    return source.replace(old, new)


def derive_core(source: str) -> str:
    # Every adaptation is exact and fails on template drift. Original restore
    # bytes/metadata validation, source snapshot checks and graceful stop remain.
    s = once(
        source,
        "$original=Read-RestoreSources $config $receipt $state",
        "$original=Read-CopySources $config $receipt $state; $handles+=,$script:CopyRestoreHandle",
    )
    s = once(
        s,
        "        foreach ($group in @('database','runtime')) {",
        "        foreach ($group in @('database','runtime','socket')) {",
    )
    s = once(
        s, "$name='demo-restore-'+$group+'-'+$token", "$name='demo-copy-review-'+$group+'-'+$token"
    )
    s = once(
        s,
        "if ($backup.Contains(',')",
        "$script:CopySocket=$volumes[2]\n        if ($backup.Contains(',')",
    )
    s = once(
        s,
        "'--entrypoint','python',$state.canonical_images[0]",
        "'--mount',('type=volume,source='+$volumes[2]+',target=/review-socket,volume-nocopy'),'--label','com.docker.compose.project=demo-copy-review','--label','com.docker.compose.service=archive-restore','--entrypoint','python',$state.canonical_images[0]",
    )
    s = once(
        s,
        "'--entrypoint','postgres',$state.canonical_images[1]",
        "'--mount',('type=volume,source='+$volumes[2]+',target=/review-socket,volume-nocopy'),'--label','com.docker.compose.project=demo-copy-review','--label','com.docker.compose.service=copy-db','--entrypoint','postgres',$state.canonical_images[1]",
    )
    s = once(s, "'unix_socket_directories=/tmp'", "'unix_socket_directories=/review-socket'")
    s = s.replace("-h /tmp", "-h /review-socket")
    needle = "        $stage='STOP_COPY_AND_REVERIFY_INPUTS';"
    s = once(
        s,
        needle,
        "        $stage='READ_ONLY_CLOCK_PERIOD_DIAGNOSTIC'; Write-Output ('COPY_REVIEW_STAGE='+$stage)\n        $helpers+=,(Invoke-CopyDiagnostic $Root $config $state $volumes[1] $volumes[2] $original $helpers $directory)\n"
        + needle,
    )
    s = once(
        s,
        "$result=[ordered]@{isolated_restore=",
        "$result=[ordered]@{copy_transition_diagnostic=$script:CopyDiagnostic;copy_mounts_and_network_verified=$true;isolated_restore=",
    )
    s = s.replace("ISOLATED_RESTORE_RECEIPT.json", "COPY_REVIEW_RECEIPT.json")
    s = once(
        s,
        "isolated_restore='LOCAL_COPY_RECOVERY_VERIFIED_ORIGINALS_STOPPED'",
        "copy_transition_review='FRESH_COPY_DIAGNOSED_ORIGINALS_UNCHANGED';diagnostic=$script:CopyDiagnostic;copy_mounts_and_network_verified=$true",
    )
    s = s.replace("private_restore_report_directory=", "private_copy_report_directory=").replace(
        "restore_receipt_sha256=(Restore-Hash", "copy_review_receipt_sha256=(Restore-Hash"
    )
    s = once(
        s,
        "next_action='KEEP_ORIGINALS_AND_COPY_STOPPED_REVIEW_RECEIPT_BEFORE_OFFLINE_HANDOFF'",
        "next_action='REVIEW_COPY_DIAGNOSTIC_NO_REBASE_NO_DAY_ADOPTION_KEEP_STOPPED'",
    )
    s = s.replace(
        "ISOLATED_COPY_RECOVERED_READ_CHECKED_STOPPED_NO_ORDERS",
        "COPY_REVIEW_COMPLETE_ORIGINALS_UNCHANGED_NEW_COPY_STOPPED_NO_ORDERS",
    )
    s = once(
        s,
        "if ($allowed -ccontains $_.Exception.Message)",
        "if ($allowed -ccontains $_.Exception.Message -or $_.Exception.Message -cmatch '^(COPY|HANDOFF)_[A-Z0-9_]{1,100}$')",
    )
    s = once(s, "$volumes.Count -eq 2", "$volumes.Count -eq 3")
    s = once(
        s,
        "scope_probe=$script:RestoreScopeProbe;retained_helper_ids=",
        "diagnostic_failure_category=$script:CopyDiagnosticFailure;retained_diagnostic_helper_id=$script:CopyDiagnosticId;scope_probe=$script:RestoreScopeProbe;retained_helper_ids=",
    )
    s = once(
        s,
        "@{stage=$stage;failure_category=$reason;retained_helper_ids=$helpers",
        "@{stage=$stage;failure_category=$reason;diagnostic_failure_category=$script:CopyDiagnosticFailure;retained_diagnostic_helper_id=$script:CopyDiagnosticId;retained_helper_ids=$helpers",
    )
    return s


def package_files(ref: str) -> dict[str, bytes]:
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()

    def read(p: str) -> bytes:
        return git("show", revision + ":" + p)

    old = image_package(ref)
    files = {n: old[n] for n in ("CONTEXT.ps1", "COMMON.ps1", "SUPPORT.ps1", "SOURCE_SCOPE.ps1")}
    files["COMMON.ps1"] = files["COMMON.ps1"].replace(b"cold-20261114-", b"copy-review-20261115-")
    files["COMMON.ps1"] = files["COMMON.ps1"].replace(
        b"[IO.Directory]::SetAccessControl($parent,$acl)", b"Assert-PrivateBackupDirectory $parent"
    )
    files["COPY_CORE.ps1"] = derive_core(
        read("tools/windows_demo_isolated_restore/RESTORE_CORE.ps1").decode()
    ).encode()
    files["COPY_EXT.ps1"] = read("tools/windows_demo_copy_transition_review/COPY_EXT.ps1")
    files["copy_worker.py"] = read("tools/windows_demo_copy_transition_review/worker.py")
    worker = read("tools/windows_demo_isolated_restore/restore_worker.py").decode()
    worker = once(
        worker,
        '    return dict(\n        status="CLONE_BYTES_AND_METADATA_RESTORED",',
        '    os.chown("/review-socket", meta["uid"], meta["gid"])\n    os.chmod("/review-socket", 0o777)\n    return dict(\n        status="CLONE_BYTES_AND_METADATA_RESTORED",',
    )
    files["restore_worker.py"] = worker.encode()
    files["cold_archive.py"] = read("tools/cold_backup_candidate.py")
    files["account_check.sql"] = read("tools/windows_demo_isolated_restore/account_check.sql")
    files["README_KO.md"] = read("tools/windows_demo_copy_transition_review/README_KO.md")
    files["RUN_COPY.ps1"] = read("tools/windows_demo_copy_transition_review/RUN_COPY.ps1")
    config = json.loads(old["CONFIG.json"])
    config["candidate_files"] = {p: hashlib.sha256(read(p)).hexdigest() for p in CANDIDATE}
    for p in CANDIDATE:
        files["payload/" + p] = read(p)
    tree = ast.parse(read(CANDIDATE[-1]))
    returns = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "dict"
    ]
    assert len(returns) == 1
    result_call = returns[0].value
    assert isinstance(result_call, ast.Call)
    assert all(k.arg is not None for k in result_call.keywords)
    config["worker_report_keys"] = [k.arg for k in result_call.keywords] + [
        "baseline_files_verified",
        "candidate_files_verified",
        "copy_session_read_only",
    ]
    files["CONFIG.json"] = (json.dumps(config, sort_keys=True) + "\n").encode()
    manifest = (json.dumps(dict(files=hashes(files)), sort_keys=True, indent=2) + "\n").encode()
    files["TRANSFER_MANIFEST.json"] = manifest
    for n, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_COPY_REVIEW.cmd", "ExecuteReview")]:
        files[n] = (
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
