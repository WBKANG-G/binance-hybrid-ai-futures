"""Approval-disabled review package for a bounded original DB startup/read/stop."""

import argparse
import hashlib
import json
from pathlib import Path

from scripts.build_demo_read_review_bundle import archive, git, hashes
from scripts.build_windows_copy_transition_bundle import once
from scripts.build_windows_owner_review_bundle import package_files as owner_files
from scripts.build_windows_source_refresh_bundle import package_files as refresh_files

FOLDER = "windows-demo-original-inspection-review-20261124"
REFRESH = "4b441c0fbc9c1356a0d5c0dfa2404d546d51a0cb"
OWNER = "39c1f2ede044b5fac12406d2d7dc270e37f1d89f"


def proposal():
    return dict(
        policy="ORIGINAL_DATABASE_INSPECTION_ONLY_PROPOSAL_V1",
        review_id="original-db-inspection-only-20261124-v1",
        account_id="DEMO-WINDOWS-ISOLATED-BTC-V1",
        database_volume="hybrid-windows_postgres_data",
        runtime_volume="hybrid-windows_demo_export_state",
        runtime_volume_mounted_in_temporary_server=False,
        postgres_image="EXACT_CACHED_CANONICAL_IMAGE_IN_HASH_PINNED_BACKUP_STATE",
        pgdata="/var/lib/postgresql/18/docker",
        source_manifest_sha256="2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d",
        prestart_byte_metadata_comparison_passes=2,
        prestart_comparison_helper_id="521b898bc3181998ab713728aa1a00910ae9059923beea520420cf268b4e9956",
        temporary_postgres_containers=1,
        new_volumes=0,
        network="none",
        tcp_listen_addresses="",
        socket_directory="/tmp",
        restart_policy="no",
        readonly_rootfs=True,
        sql_default_read_only=True,
        sql_repeatable_read_read_only=True,
        sql_read_count=2,
        startup_ready_loop_seconds=60,
        shutdown_signal="SIGINT",
        shutdown_observation_loop_seconds=30,
        individual_docker_cli_timeout_seconds=15,
        postgresql_startup_shutdown_can_write_original_storage=True,
        custom_pid_deletion=False,
        force_kill=False,
        container_or_volume_deletion=False,
        automatic_retry=False,
        existing_original_service_containers_started=False,
        original_ledger_update=False,
        owner_lease_takeover=False,
        fence_increment=False,
        baseline_adoption=False,
        request_resolution=False,
        resend=False,
        exchange_http_attempts=0,
        orders_enabled=False,
        live_trading_enabled=False,
        approval_received=False,
        execution_enabled=False,
    )


def package_files(ref):
    revision = git("rev-parse", "--verify", ref + "^{commit}").decode().strip()
    f = {
        n: b
        for n, b in refresh_files(REFRESH).items()
        if not n.endswith(".cmd") and n != "TRANSFER_MANIFEST.json"
    }
    owned = owner_files(OWNER)
    sql = (
        owned["account_check.sql"]
        .replace(
            b"COPY_DATABASE_STATEMENT_TIMESTAMP", b"ISOLATED_ORIGINAL_DATABASE_STATEMENT_TIMESTAMP"
        )
        .replace(b"'original_owner_verified',false", b"'original_owner_verified',true")
    )
    f["original_read.sql"] = sql
    f["ORIGINAL_START_ACCEPTANCE.json"] = (
        json.dumps(
            dict(
                policy="ORIGINAL_DATABASE_INSPECTION_ONLY_ACCEPTANCE_PENDING",
                approval_received=False,
                original_database_start_authorized=False,
                postgresql_startup_shutdown_storage_writes_authorized=False,
                operator_reply=None,
            ),
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()
    validation = (
        owned["OWNER_EXT.ps1"].decode().split(" $classification='UNOWNED_NO_LEASE'", 1)[0] + "}\n"
    )
    validation = (
        validation.replace("function Assert-CopyFinancial(", "function Assert-OriginalRead(")
        .replace(
            "'COPY_DATABASE_STATEMENT_TIMESTAMP'",
            "'ISOLATED_ORIGINAL_DATABASE_STATEMENT_TIMESTAMP'",
        )
        .replace(
            "Owner-Bool $o.original_owner_verified $false",
            "Owner-Bool $o.original_owner_verified $true",
        )
    )
    f["INSPECTION_RESULT.ps1"] = validation.encode()
    for n, path in [
        ("INSPECTION_CORE.ps1", "tools/windows_demo_original_inspection/INSPECTION_CORE.ps1"),
        ("README_KO.md", "tools/windows_demo_original_inspection/README_KO.md"),
        (
            "OWNER_WINDOWS_OBSERVATION.json",
            "docs/reviews/20261124/OWNER_REVIEW_WINDOWS_OBSERVATION.json",
        ),
    ]:
        f[n] = git("show", revision + ":" + path)
    f["INSPECTION_PROPOSAL.json"] = (
        json.dumps(proposal(), sort_keys=True, indent=2) + "\n"
    ).encode()
    c = json.loads(f["CONFIG.json"])
    c.update(
        source_reference=revision,
        inspection_proposal_file_sha256=hashlib.sha256(f["INSPECTION_PROPOSAL.json"]).hexdigest(),
        inspection_approval_receipt_file_sha256=None,
    )
    f["CONFIG.json"] = (json.dumps(c, sort_keys=True) + "\n").encode()
    f["COMMON.ps1"] = f["COMMON.ps1"].replace(
        b"source-refresh-20261124-", b"original-inspection-20261124-"
    )
    core = f.pop("RUN_COMPARE.ps1").decode().replace("$CompareOnce", "$InspectOnce")
    core = once(
        core,
        " . (Join-Path $PSScriptRoot 'REFRESH_CORE.ps1')",
        " . (Join-Path $PSScriptRoot 'REFRESH_CORE.ps1')\n . (Join-Path $PSScriptRoot 'INSPECTION_CORE.ps1')\n . (Join-Path $PSScriptRoot 'INSPECTION_RESULT.ps1')",
    )
    core = once(
        core,
        " $stage='ACQUIRE_EXCLUSIVE_COMPARE'",
        " $stage='ORIGINAL_START_APPROVAL'\n $inspectionApprovalId=Assert-InspectionApproval $PSScriptRoot $config\n $stage='ACQUIRE_EXCLUSIVE_COMPARE'",
    )
    core = once(
        core,
        "$directory=New-PrivateBackupDirectory ([Guid]::NewGuid().ToString('N'))",
        "$directory=New-PrivateBackupDirectory ($inspectionApprovalId.Substring(0,32))",
    )
    core = once(
        core,
        " Write-Output 'SOURCE_REFRESH_MATCHED_HELPER_STOPPED_NO_ORIGINAL_START_NO_ORDERS'",
        " $stage='ISOLATED_ORIGINAL_DATABASE_READ_ONLY_INSPECTION'\n Invoke-OriginalInspection $PSScriptRoot $config $state $original $directory $m",
    )
    core = core.replace(
        "SOURCE_REFRESH_CHECK_PASSED_NO_HELPER_START",
        "ORIGINAL_INSPECTION_REVIEW_CHECK_PASSED_NO_START_NO_APPROVAL_GRANTED",
    )
    core = core.replace(
        "^(SCOPE|RESTORE|COLD|HANDOFF|FINANCIAL|COMPARE|BACKUP_PLAN|BASELINE|INSPECT|READ|REFERENCE|INCOME)_",
        "^(SCOPE|RESTORE|COLD|HANDOFF|FINANCIAL|COMPARE|BACKUP_PLAN|BASELINE|INSPECT|READ|REFERENCE|INCOME|ORIGINAL|OWNER)_",
    )
    core = core.replace(
        "original_database_started=$false;original_database_changed=$false;",
        "original_database_started=$(if($script:InspectionEverRunning){$true}elseif($script:InspectionStarted){$null}else{$false});original_database_files_may_have_changed=$script:InspectionStarted;temporary_original_server_id=$script:InspectionId;temporary_original_server_clean_stop_verified=$script:InspectionStopped;",
    )
    # Initialize reporting flags before seal validation, even if modules never load.
    core = once(
        core,
        "$handles=@();",
        "$script:InspectionStarted=$false;$script:InspectionEverRunning=$false;$script:InspectionStopped=$false;$script:InspectionId=$null;$handles=@();",
    )
    core = core.replace("'RUN_COMPARE.ps1'", "'RUN_INSPECT.ps1'")
    core = core.replace(
        "start_attempted=$script:RefreshStartAttempted;",
        "comparison_start_attempted=$script:RefreshStartAttempted;original_start_attempted=$script:InspectionStarted;original_ever_running=$script:InspectionEverRunning;temporary_original_server_id=$script:InspectionId;temporary_original_server_clean_stop_verified=$script:InspectionStopped;",
    )
    f["RUN_INSPECT.ps1"] = core.encode()
    lines = core.splitlines()
    i = next(i for i, s in enumerate(lines) if s.lstrip().startswith("$required=@('"))
    lines[i] = " $required=@(" + ",".join("'" + n + "'" for n in f) + ")"
    f["RUN_INSPECT.ps1"] = ("\n".join(lines) + "\n").encode()
    p = proposal()
    p["execution_file_sha256"] = hashes(
        {
            n: f[n]
            for n in (
                "INSPECTION_CORE.ps1",
                "INSPECTION_RESULT.ps1",
                "original_read.sql",
                "REFRESH_CORE.ps1",
                "worker.py",
                "READ_LIBRARY.py",
                "DOCKER_READ.ps1",
                "CONTEXT.ps1",
                "COMMON.ps1",
                "SOURCE_SCOPE.ps1",
                "COPY_CORE.ps1",
                "COPY_EXT.ps1",
                "FINANCIAL_EXT.ps1",
                "COMPARE_ADMISSION.ps1",
                "RUN_INSPECT.ps1",
            )
        }
    )
    f["INSPECTION_PROPOSAL.json"] = (json.dumps(p, sort_keys=True, indent=2) + "\n").encode()
    c["inspection_proposal_file_sha256"] = hashlib.sha256(f["INSPECTION_PROPOSAL.json"]).hexdigest()
    f["CONFIG.json"] = (json.dumps(c, sort_keys=True) + "\n").encode()
    m = (
        json.dumps(dict(source_reference=revision, files=hashes(f)), sort_keys=True, indent=2)
        + "\n"
    ).encode()
    f["TRANSFER_MANIFEST.json"] = m
    for n, mode in [("RUN_CHECK.cmd", "CheckOnly"), ("RUN_INSPECT.cmd", "InspectOnce")]:
        f[n] = (
            '@echo off\r\nsetlocal DisableDelayedExpansion\r\npowershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0RUN_INSPECT.ps1" -ManifestSHA256 '
            + hashlib.sha256(m).hexdigest()
            + " -"
            + mode
            + "\r\nexit /b %errorlevel%\r\n"
        ).encode("ascii")
    return f


def build(ref, output):
    f = package_files(ref)
    output.mkdir(parents=True, exist_ok=True)
    p = output / (FOLDER + ".zip")
    with p.open("xb") as stream:
        stream.write(archive({FOLDER + "/" + n: b for n, b in f.items()}))
    print(json.dumps(dict(artifact=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    build(a.ref, a.output)
