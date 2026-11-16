"""Actual production evidence bundle on fresh synthetic Docker inputs only."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from tests.unit.test_cold_backup_executor import PWSH
from tests.unit.test_copy_evidence_bundle import materialize


def exercise(
    root, backup, restore_report, pins, prior_directory, clock_directory, containers, volumes
):
    target = root / "evidence bundle package"
    materialize(target)
    prior_path = prior_directory / "COPY_REVIEW_RECEIPT.json"
    clock_path = clock_directory / "COPY_CLOCK_RECEIPT.json"
    prior = json.loads(prior_path.read_bytes())
    clock = json.loads(clock_path.read_bytes())
    config = json.loads((target / "CONFIG.json").read_bytes())
    for key in (
        "api_container_id",
        "database_container_id",
        "preservation_source_reference",
        "restore_source_reference",
        "receipt_sha256",
        "manifest_sha256",
        "restore_receipt_sha256",
    ):
        config[key] = pins[key]
    config.update(
        prior_copy_source_reference=prior["source_reference"],
        prior_copy_receipt_sha256=hashlib.sha256(prior_path.read_bytes()).hexdigest(),
        prior_clock_source_reference=clock["source_reference"],
        prior_clock_receipt_sha256=hashlib.sha256(clock_path.read_bytes()).hexdigest(),
    )
    (target / "CONFIG.json").write_text(json.dumps(config))
    output = root / "evidence bundle output"

    def quote(value):
        return str(value).replace("'", "''")

    ext = target / "EVIDENCE_EXT.ps1"
    ext.write_text(
        ext.read_text()
        + f"""
# TEST ONLY: Windows input discovery and ACL checks substituted on Linux.
function Find-RetainedBackup {{return '{quote(backup)}'}}
function Find-RestoreEvidence {{return '{quote(restore_report / "ISOLATED_RESTORE_RECEIPT.json")}'}}
function Find-PriorCopyReceipt {{return '{quote(prior_path)}'}}
function Find-PriorClockReceipt {{return '{quote(clock_path)}'}}
function Assert-PrivateBackupDirectory {{}}
function Assert-PrivateBackupFiles {{}}
function New-PrivateBackupDirectory {{$null=[IO.Directory]::CreateDirectory('{quote(output)}');return '{quote(output)}'}}
"""
    )
    manifest = json.loads((target / "TRANSFER_MANIFEST.json").read_bytes())
    manifest["files"] = {
        p: hashlib.sha256((target / p).read_bytes()).hexdigest() for p in manifest["files"]
    }
    body = json.dumps(manifest, sort_keys=True).encode()
    (target / "TRANSFER_MANIFEST.json").write_bytes(body)
    env = dict(
        os.environ,
        XDG_CACHE_HOME=str(root / "cache"),
        XDG_CONFIG_HOME=str(root / "config"),
        XDG_DATA_HOME=str(root / "data"),
    )

    def launch(mode):
        script = target / (mode + ".ps1")
        script.write_text(
            f"$PSNativeCommandArgumentPassing='Legacy'\n& '{target / 'RUN_COPY.ps1'}' -ManifestSHA256 '{hashlib.sha256(body).hexdigest()}' -{mode}\nexit $LASTEXITCODE\n"
        )
        return subprocess.run(
            [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(script)],
            text=True,
            capture_output=True,
            timeout=500,
            env=env,
        )

    def resources():
        return tuple(
            subprocess.check_output(["docker", *args], text=True)
            for args in (
                ["ps", "--all", "--quiet", "--no-trunc"],
                ["volume", "ls", "--format", "{{.Name}}"],
            )
        )

    before = resources()
    check = launch("CheckOnly")
    assert check.returncode == 0 and not check.stderr, check.stdout + check.stderr
    assert before == resources() and not output.exists()
    result = launch("ExecuteReview")
    for name in (
        "RESTORE_RESOURCES.json",
        "CLONE_CREATED.json",
        "FAILURE.json",
        "COPY_CLOCK_RESOURCE.json",
        "COPY_EVIDENCE_RECEIPT.json",
    ):
        path = output / name
        if path.exists():
            value = json.loads(path.read_bytes())
            containers.extend(value.get("owned_helper_ids", value.get("retained_helper_ids", [])))
            for key in ("clone_container_id", "helper_id", "retained_diagnostic_helper_id"):
                if value.get(key):
                    containers.append(value[key])
            volumes.extend(value.get("clone_volumes", value.get("retained_clone_volumes", [])))
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert "COPY_SYNTHETIC_EVIDENCE_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS" in result.stdout
    assert "COPY_EVIDENCE_STAGE=SYNTHETIC_GET_AND_ARCHIVE" in result.stdout
    receipt = json.loads((output / "COPY_EVIDENCE_RECEIPT.json").read_bytes())
    pipeline = receipt["copy_evidence_result"]["synthetic_evidence_rehearsal"]
    assert pipeline["synthetic_sender_calls"] == 1 and pipeline["exchange_http_attempts"] == 0
    assert pipeline["audit_events_after"] == pipeline["audit_events_before"] + 2
    assert (
        receipt["clone_stopped"]
        and receipt["clone_clean_exit_verified"]
        and receipt["input_hashes_reverified"]
    )
    assert (
        hashlib.sha256(clock_path.read_bytes()).hexdigest() == config["prior_clock_receipt_sha256"]
    )
    if os.getenv("PRIVATE_WEEK_BUNDLE_DOCKER_REPORT"):
        from tests.fixtures.private_week_bundle_runner import exercise as private_exercise

        private_exercise(
            root,
            backup,
            restore_report,
            pins,
            prior_directory,
            clock_directory,
            output,
            containers,
            volumes,
        )
    Path(os.environ["COPY_EVIDENCE_BUNDLE_DOCKER_REPORT"]).write_text(
        json.dumps(
            dict(
                actual_windows_touched=False,
                windows_acl_and_input_discovery_mocked=True,
                real_docker_lifecycle=True,
                real_70_second_hold=True,
                prior_clock_receipt_and_resources_verified=True,
                read_only_preflight_no_resources=True,
                new_copy_stopped=True,
                input_hashes_reverified=True,
                candidate_files_verified=10,
                baseline_files_verified=89,
                pipeline=pipeline,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
