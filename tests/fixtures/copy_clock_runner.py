"""Extend the real Docker pipeline with one clock rehearsal on a NEW copy."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from tests.unit.test_cold_backup_executor import PWSH
from tests.unit.test_copy_clock_bundle import materialize


def exercise(root, backup, restore_report, pins, prior_directory, containers, volumes):
    target = root / "copy clock package"
    materialize(target)
    prior_path = prior_directory / "COPY_REVIEW_RECEIPT.json"
    prior = json.loads(prior_path.read_text())
    config = json.loads((target / "CONFIG.json").read_text())
    for k in (
        "api_container_id",
        "database_container_id",
        "preservation_source_reference",
        "restore_source_reference",
        "receipt_sha256",
        "manifest_sha256",
        "restore_receipt_sha256",
    ):
        config[k] = pins[k]
    config["prior_copy_source_reference"] = prior["source_reference"]
    config["prior_copy_receipt_sha256"] = hashlib.sha256(prior_path.read_bytes()).hexdigest()
    (target / "CONFIG.json").write_text(json.dumps(config))
    output = root / "copy clock output"
    safe = str(backup).replace("'", "''")
    proof = str(restore_report).replace("'", "''")
    dest = str(output).replace("'", "''")
    previous = str(prior_path).replace("'", "''")
    ext = target / "CLOCK_EXT.ps1"
    ext.write_text(
        ext.read_text()
        + f"""
# TEST ONLY: local path discovery and Windows NTFS ACL checks substituted on Linux.
function Find-RetainedBackup {{return '{safe}'}}
function Find-RestoreEvidence {{return '{proof}/ISOLATED_RESTORE_RECEIPT.json'}}
function Find-PriorCopyReceipt {{return '{previous}'}}
function Assert-PrivateBackupDirectory {{}}
function Assert-PrivateBackupFiles {{}}
function New-PrivateBackupDirectory {{$null=[IO.Directory]::CreateDirectory('{dest}');return '{dest}'}}
"""
    )
    manifest = json.loads((target / "TRANSFER_MANIFEST.json").read_text())
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

    before_resources = resources()
    check = launch("CheckOnly")
    assert check.returncode == 0 and not check.stderr, check.stdout + check.stderr
    assert before_resources == resources() and not output.exists()
    result = launch("ExecuteReview")
    for name in (
        "RESTORE_RESOURCES.json",
        "CLONE_CREATED.json",
        "FAILURE.json",
        "COPY_CLOCK_RESOURCE.json",
        "COPY_CLOCK_RECEIPT.json",
    ):
        path = output / name
        if path.exists():
            value = json.loads(path.read_text())
            containers.extend(value.get("owned_helper_ids", value.get("retained_helper_ids", [])))
            for key in ("clone_container_id", "helper_id", "retained_diagnostic_helper_id"):
                if value.get(key):
                    containers.append(value[key])
            volumes.extend(value.get("clone_volumes", value.get("retained_clone_volumes", [])))
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    assert "COPY_CLOCK_REHEARSAL_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS" in result.stdout
    assert "COPY_CLOCK_HOLD remaining_seconds=" in result.stdout
    receipt = json.loads((output / "COPY_CLOCK_RECEIPT.json").read_text())
    clock = receipt["copy_clock_result"]
    assert clock["prior_audit_events"] == prior["copy_transition_diagnostic"]["prior_audit_events"]
    assert clock["audit_events_after"] == clock["prior_audit_events"] + 1
    assert clock["cumulative_weight_before"] == clock["cumulative_weight_after"]
    assert clock["startup_hold_verified"] and clock["diagnostic_after"]["clock_matches"]
    assert (
        clock["diagnostic_after"]["historical_unresolved_requests"] == 4
        and not clock["diagnostic_after"]["period_keys_match"]
    )
    assert (
        receipt["clone_stopped"]
        and receipt["clone_clean_exit_verified"]
        and receipt["input_hashes_reverified"]
    )
    assert (
        hashlib.sha256(prior_path.read_bytes()).hexdigest() == config["prior_copy_receipt_sha256"]
    )
    Path(os.environ["COPY_CLOCK_DOCKER_REPORT"]).write_text(
        json.dumps(
            dict(
                real_docker_lifecycle=True,
                actual_70_second_hold=True,
                windows_acl_and_input_discovery_mocked=True,
                actual_windows_touched=False,
                originals_and_prior_copy_not_started=True,
                copy_clock_result=clock,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
