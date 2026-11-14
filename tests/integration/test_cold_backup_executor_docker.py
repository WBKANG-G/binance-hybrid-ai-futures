"""Real PG/API six-container stop, image export, cold archive and local restore.

Windows ACL calls are replaced in a COPY of the test bundle on Linux; this never
claims real Windows ACL verification. All source containers/volumes are fresh.
"""

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.build_demo_read_review_bundle import hashes
from tests.integration.test_cold_backup_docker_drill import HELPER_IMAGE, PG_IMAGE, ready, sql
from tests.unit.test_cold_backup_executor import PWSH, materialize, package
from tests.unit.test_isolated_restore import materialize as restore_materialize
from tests.unit.test_offline_handoff import materialize as handoff_materialize
from tests.unit.test_restore_scope_diagnostic import materialize as diagnostic_materialize
from tests.unit.test_stopped_preservation import (
    materialize as preserve_materialize,
)
from tests.unit.test_stopped_preservation import (
    package as preserve_package,
)


def docker(*args, stdin=None, timeout=60, env=None):
    result = subprocess.run(
        ["docker", *args], input=stdin, text=True, capture_output=True, timeout=timeout, env=env
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
@pytest.mark.parametrize("preserve_stopped", [False, True])
def test_complete_executor_on_fresh_real_docker_with_windows_acl_mocked(tmp_path, preserve_stopped):
    for image in (PG_IMAGE, HELPER_IMAGE):
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
            pytest.skip("Cached image unavailable")
    if (
        subprocess.run(
            ["docker", "container", "inspect", "hybrid-windows-api-1"], capture_output=True
        ).returncode
        == 0
    ):
        pytest.skip("Existing API name is reserved; never touch an existing container")
    prefix = "cold-exec-test-" + uuid4().hex[:12]
    containers = []
    volumes = []
    network = None
    helper_image = HELPER_IMAGE
    owned_image = None
    output = tmp_path / "private backup output"
    source_runtime = prefix + "-runtime"
    source_database = prefix + "-database"
    original_volume_names = set(docker("volume", "ls", "--format", "{{.Name}}").splitlines())
    try:
        if preserve_stopped:
            owned_image = prefix + ":compose-labelled"
            docker(
                "build",
                "--pull=false",
                "--network",
                "none",
                "-t",
                owned_image,
                "-",
                stdin="FROM "
                + HELPER_IMAGE
                + "\nLABEL com.docker.compose.project=hybrid-windows com.docker.compose.service=api\n",
                timeout=60,
                env=dict(
                    os.environ,
                    DOCKER_CONFIG=str(tmp_path / "docker-config"),
                    BUILDX_CONFIG=str(tmp_path / "buildx"),
                ),
            )
            helper_image = owned_image
        for name in (
            source_runtime,
            source_database,
            prefix + "-restore-db",
            prefix + "-restore-runtime",
        ):
            volumes.append(docker("volume", "create", name))
        network = docker("network", "create", "--internal", prefix + "-network")
        db = docker(
            "run",
            "-d",
            "--name",
            prefix + "-db",
            "--network",
            network,
            "--network-alias",
            "db",
            "--restart",
            "no",
            "--label",
            "com.docker.compose.project=hybrid-windows",
            "--label",
            "com.docker.compose.service=db",
            "--env",
            "POSTGRES_USER=dev",
            "--env",
            "POSTGRES_DB=hybrid",
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "--mount",
            f"type=volume,source={source_database},target=/var/lib/postgresql",
            PG_IMAGE,
        )
        containers.append(db)
        ready(db)
        sql(
            db,
            """
CREATE TABLE demo_accounts (account_id text PRIMARY KEY,state jsonb NOT NULL,lease_until timestamptz);
INSERT INTO demo_accounts SELECT 'DEMO-WINDOWS-ISOLATED-BTC-V1',jsonb_build_object('paused',true,'daily_key','2026-11-13','weekly_key','2026-11-09','orders',(SELECT jsonb_object_agg(i::text,'CLOSED') FROM generate_series(1,34) i),'requests','{"a":"UNKNOWN","b":"UNKNOWN","c":"UNKNOWN","d":"SUBMITTING"}'::jsonb),NULL;
CREATE TABLE read_response_evidence_events_v2 (id int PRIMARY KEY,body text);
INSERT INTO read_response_evidence_events_v2 VALUES (1,'SYNTHETIC_PRIVATE_EVIDENCE');
""",
        )
        expected = sql(
            db, "SELECT encode(sha256(convert_to(state::text,'UTF8')),'hex') FROM demo_accounts;"
        )
        ip = docker(
            "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", db
        )
        url = "postgresql+psycopg://dev:synthetic@" + ip + ":5432/hybrid"
        api = docker(
            "run",
            "-d",
            "--name",
            "hybrid-windows-api-1",
            "--network",
            network,
            "--restart",
            "no",
            "--label",
            "com.docker.compose.project=hybrid-windows",
            "--label",
            "com.docker.compose.service=api",
            "--env",
            "DATABASE_URL=" + url,
            "--mount",
            f"type=volume,source={source_runtime},target=/app/demo-export-state",
            "--entrypoint",
            "python",
            helper_image,
            "-I",
            "-B",
            "-c",
            "import signal,sys,time; signal.signal(signal.SIGTERM,lambda *a:sys.exit(0)); time.sleep(600)",
        )
        containers.append(api)
        for index in range(2):
            ident = docker(
                "create",
                "--name",
                prefix + "-retained-" + str(index),
                "--network",
                "none",
                "--restart",
                "no",
                "--label",
                "com.docker.compose.project=hybrid-windows",
                "--label",
                "com.docker.compose.service=api",
                "--mount",
                f"type=volume,source={source_runtime},target=/app/demo-export-state",
                "--entrypoint",
                "python",
                helper_image,
                "-I",
                "-B",
                "-c",
                "pass",
            )
            containers.append(ident)
        for role in ("alerts", "web"):
            containers.append(
                docker(
                    "create",
                    "--name",
                    prefix + "-" + role,
                    "--network",
                    "none",
                    "--restart",
                    "no",
                    "--label",
                    "com.docker.compose.project=hybrid-windows",
                    "--label",
                    "com.docker.compose.service=" + role,
                    "--entrypoint",
                    "python",
                    helper_image,
                    "-I",
                    "-B",
                    "-c",
                    "pass",
                )
            )
        key = docker(
            "run",
            "-d",
            "--name",
            prefix + "-key-session",
            "--network",
            "none",
            "--restart",
            "no",
            "--read-only",
            "--label",
            "com.docker.compose.project=hybrid-windows",
            "--label",
            "com.docker.compose.service=key-session",
            "--entrypoint",
            "python",
            helper_image,
            "-I",
            "-B",
            "-c",
            "import time; time.sleep(600)",
        )
        containers.append(key)
        docker(
            "exec",
            api,
            "python",
            "-I",
            "-B",
            "-c",
            "from pathlib import Path; import sqlite3; p=Path('/app/demo-export-state'); c=sqlite3.connect(p/'state.db'); c.execute('CREATE TABLE test(value text)'); c.execute(\"INSERT INTO test VALUES ('SYNTHETIC_RUNTIME')\"); c.commit(); c.close()",
        )
        if preserve_stopped:
            # Abrupt death is confined to this fresh synthetic fixture, never originals.
            docker("kill", "--signal=SIGKILL", db)
            docker("kill", "--signal=SIGKILL", api)
            docker("kill", "--signal=SIGKILL", key)
            assert docker("inspect", "--format", "{{.State.ExitCode}}", db) == "137"
            control = docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--mount",
                f"type=volume,source={source_database},target=/var/lib/postgresql,readonly,volume-nocopy",
                "--entrypoint",
                "pg_controldata",
                PG_IMAGE,
                "/var/lib/postgresql/18/docker",
            )
            assert "in production" in control
            preserve_materialize(tmp_path)
        else:
            materialize(tmp_path)
        config_name = "CONFIG.json" if preserve_stopped else "BACKUP_CONFIG.json"
        config = json.loads((tmp_path / config_name).read_text())
        config.update(api_container_id=api, database_container_id=db)
        (tmp_path / config_name).write_text(json.dumps(config))
        core = tmp_path / ("COMMON.ps1" if preserve_stopped else "BACKUP_CORE.ps1")
        safe = str(output).replace("'", "''")
        core.write_text(
            core.read_text()
            + f"""
# TEST-ONLY LINUX ACL SUBSTITUTION; production bundle does not contain this.
function New-PrivateBackupDirectory([string]$Token) {{ $null=[IO.Directory]::CreateDirectory('{safe}'); return '{safe}' }}
function Assert-PrivateBackupDirectory([string]$Path) {{ if ($Path -ne '{safe}') {{ throw 'COLD_BACKUP_ACL_INVALID' }} }}
function Assert-PrivateBackupFiles([string]$Directory) {{ }}
function Backup-FreeBytes([string]$Directory) {{ return 100000000000L }}
"""
        )
        files = {
            p: (tmp_path / p).read_bytes()
            for p in json.loads((tmp_path / "TRANSFER_MANIFEST.json").read_text())["files"]
        }
        manifest = json.dumps(dict(files=hashes(files)), sort_keys=True).encode()
        (tmp_path / "TRANSFER_MANIFEST.json").write_bytes(manifest)
        launcher = tmp_path / "test-launch.ps1"
        safe_script = str(
            tmp_path / ("RUN_PRESERVE.ps1" if preserve_stopped else "RUN_BACKUP.ps1")
        ).replace("'", "''")
        execute_mode = "ExecutePreservation" if preserve_stopped else "ExecuteColdBackup"
        launcher.write_text(
            f"$PSNativeCommandArgumentPassing='Legacy'\n& '{safe_script}' -ManifestSHA256 '{hashlib.sha256(manifest).hexdigest()}' -{execute_mode}\nexit $LASTEXITCODE\n"
        )
        check_script = tmp_path / "check-launch.ps1"
        check_script.write_text(
            f"$PSNativeCommandArgumentPassing='Legacy'\n& '{safe_script}' -ManifestSHA256 '{hashlib.sha256(manifest).hexdigest()}' -CheckOnly\nexit $LASTEXITCODE\n"
        )
        check = subprocess.run(
            [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(check_script)],
            capture_output=True,
            text=True,
            timeout=90,
            env=dict(
                os.environ,
                XDG_CACHE_HOME=str(tmp_path / "cache"),
                XDG_CONFIG_HOME=str(tmp_path / "config"),
                XDG_DATA_HOME=str(tmp_path / "data"),
                POWERSHELL_TELEMETRY_OPTOUT="1",
            ),
        )
        assert check.returncode == 0, check.stdout + check.stderr
        assert (
            not check.stderr
            and (
                "STOPPED_SCOPE_CHECKED_NO_SOURCE_START_NO_BACKUP_NO_ORDERS"
                if preserve_stopped
                else "COLD_BACKUP_CHECK_PASSED_NO_STOP_NO_BACKUP_NO_ORDERS"
            )
            in check.stdout
        )
        assert not output.exists()
        assert docker("inspect", "--format", "{{.State.Running}}", api) == (
            "false" if preserve_stopped else "true"
        )
        assert docker("inspect", "--format", "{{.State.Running}}", db) == (
            "false" if preserve_stopped else "true"
        )
        result = subprocess.run(
            [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(launcher)],
            capture_output=True,
            text=True,
            timeout=240,
            env=dict(
                os.environ,
                XDG_CACHE_HOME=str(tmp_path / "cache"),
                XDG_CONFIG_HOME=str(tmp_path / "config"),
                XDG_DATA_HOME=str(tmp_path / "data"),
                POWERSHELL_TELEMETRY_OPTOUT="1",
            ),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not result.stderr and "SYNTHETIC_PRIVATE_EVIDENCE" not in result.stdout
        assert (
            "STOPPED_RAW_COPY_SAVED_NOT_RECOVERY_VERIFIED_NO_ORDERS"
            if preserve_stopped
            else "COLD_BACKUP_SAVED_LOCAL_VERIFIED_KEEP_STOPPED_NO_ORDERS"
        ) in result.stdout
        receipt = json.loads((output / "BACKUP_RECEIPT.json").read_text())
        assert (
            receipt[
                "source_services_observed_stopped"
                if preserve_stopped
                else "source_services_stopped"
            ]
            and not receipt["automatic_resume"]
        )
        assert all(
            docker("inspect", "--format", "{{.State.Running}}", c) == "false"
            for c in containers[:-1]
        )
        assert docker("inspect", "--format", "{{.State.Running}}", key) == (
            "false" if preserve_stopped else "true"
        )
        assert receipt["source_data_archive_restore_tested"] is False
        if preserve_stopped:
            restore_root = tmp_path / "isolated restore package"
            restore_materialize(restore_root)
            restore_output = tmp_path / "private restore report"
            restore_config = json.loads((restore_root / "CONFIG.json").read_text())
            restore_config.update(
                api_container_id=api,
                database_container_id=db,
                preservation_source_reference=receipt["source_reference"],
                receipt_sha256=hashlib.sha256(
                    (output / "BACKUP_RECEIPT.json").read_bytes()
                ).hexdigest(),
                manifest_sha256=receipt["manifest_sha256"],
            )
            (restore_root / "CONFIG.json").write_text(json.dumps(restore_config))
            safe_report = str(restore_output).replace("'", "''")
            restore_core = restore_root / "RESTORE_CORE.ps1"
            restore_core.write_text(
                restore_core.read_text()
                + f"""
# TEST-ONLY Linux substitutes; production ZIP retains real NTFS ACL and folder discovery.
function Find-RetainedBackup {{ return '{safe}' }}
function New-PrivateBackupDirectory([string]$Token) {{ $null=[IO.Directory]::CreateDirectory('{safe_report}'); return '{safe_report}' }}
function Assert-PrivateBackupDirectory([string]$Path) {{ if (@('{safe}','{safe_report}') -cnotcontains $Path) {{ throw 'COLD_BACKUP_ACL_INVALID' }} }}
function Assert-PrivateBackupFiles {{ }}

"""
            )
            restore_files = {
                p: (restore_root / p).read_bytes()
                for p in json.loads((restore_root / "TRANSFER_MANIFEST.json").read_text())["files"]
            }
            restore_manifest = json.dumps(
                dict(files=hashes(restore_files)), sort_keys=True
            ).encode()
            (restore_root / "TRANSFER_MANIFEST.json").write_bytes(restore_manifest)
            restore_script = restore_root / "test-launch.ps1"
            restore_script.write_text(
                f"$PSNativeCommandArgumentPassing='Legacy'\n& '{str(restore_root / 'RUN_RESTORE.ps1')}' -ManifestSHA256 '{hashlib.sha256(restore_manifest).hexdigest()}' -ExecuteRestore\nexit $LASTEXITCODE\n"
            )
            diagnostic_root = tmp_path / "scope diagnostic package"
            diagnostic_materialize(diagnostic_root)
            (diagnostic_root / "CONFIG.json").write_text(json.dumps(restore_config))
            (diagnostic_root / "RESTORE_CORE.ps1").write_bytes(restore_core.read_bytes())
            diagnostic_files = {
                p: (diagnostic_root / p).read_bytes()
                for p in json.loads((diagnostic_root / "TRANSFER_MANIFEST.json").read_text())[
                    "files"
                ]
            }
            diagnostic_manifest = json.dumps(
                dict(files=hashes(diagnostic_files)), sort_keys=True
            ).encode()
            (diagnostic_root / "TRANSFER_MANIFEST.json").write_bytes(diagnostic_manifest)
            diagnostic_script = diagnostic_root / "test-diagnostic.ps1"
            diagnostic_script.write_text(
                f"$PSNativeCommandArgumentPassing='Legacy'\n& '{str(diagnostic_root / 'RUN_RESTORE.ps1')}' -ManifestSHA256 '{hashlib.sha256(diagnostic_manifest).hexdigest()}' -ExecuteRestore\nexit $LASTEXITCODE\n"
            )
            ids_before = docker("ps", "--all", "--quiet", "--no-trunc")
            volumes_before = docker("volume", "ls", "--format", "{{.Name}}")
            diagnostic = subprocess.run(
                [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(diagnostic_script)],
                capture_output=True,
                text=True,
                timeout=90,
                env=dict(
                    os.environ,
                    XDG_CACHE_HOME=str(tmp_path / "cache"),
                    XDG_CONFIG_HOME=str(tmp_path / "config"),
                    XDG_DATA_HOME=str(tmp_path / "data"),
                ),
            )
            assert diagnostic.returncode == 0 and not diagnostic.stderr, (
                diagnostic.stdout + diagnostic.stderr
            )
            assert "RESTORE_CHECK_PASSED_NO_CLONE_NO_SOURCE_START_NO_ORDERS" in diagnostic.stdout
            assert (
                '"step":"COMPLETE"' in diagnostic.stdout
                and '"labelled_project_count":8' in diagnostic.stdout
                and '"source_project_count":7' in diagnostic.stdout
            )
            assert docker("ps", "--all", "--quiet", "--no-trunc") == ids_before
            assert docker("volume", "ls", "--format", "{{.Name}}") == volumes_before
            assert not restore_output.exists()
            recovered = subprocess.run(
                [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(restore_script)],
                capture_output=True,
                text=True,
                timeout=240,
                env=dict(
                    os.environ,
                    XDG_CACHE_HOME=str(tmp_path / "cache"),
                    XDG_CONFIG_HOME=str(tmp_path / "config"),
                    XDG_DATA_HOME=str(tmp_path / "data"),
                    POWERSHELL_TELEMETRY_OPTOUT="1",
                ),
            )
            # Register only freshly owned resources even when a restore assertion fails.
            for record in (
                "RESTORE_RESOURCES.json",
                "CLONE_CREATED.json",
                "FAILURE.json",
                "ISOLATED_RESTORE_RECEIPT.json",
            ):
                path = restore_output / record
                if path.exists():
                    value = json.loads(path.read_text())
                    containers.extend(
                        value.get("owned_helper_ids", value.get("retained_helper_ids", []))
                    )
                    if value.get("clone_container_id"):
                        containers.append(value["clone_container_id"])
                    volumes.extend(
                        value.get("clone_volumes", value.get("retained_clone_volumes", []))
                    )
            assert recovered.returncode == 0, recovered.stdout + recovered.stderr
            assert not recovered.stderr and "SYNTHETIC_PRIVATE_EVIDENCE" not in recovered.stdout
            assert "ISOLATED_COPY_RECOVERED_READ_CHECKED_STOPPED_NO_ORDERS" in recovered.stdout
            restore_receipt = json.loads(
                (restore_output / "ISOLATED_RESTORE_RECEIPT.json").read_text()
            )
            assert (
                restore_receipt["input_hashes_reverified"]
                and restore_receipt["clone_stopped"]
                and restore_receipt["clone_clean_exit_verified"]
            )
            assert (
                not restore_receipt["original_services_started"]
                and not restore_receipt["original_volume_mounted_in_restore_helpers"]
            )
            assert restore_receipt["account"]["account_state_sha256"] == expected
            assert (
                restore_receipt["account"]["orders"] == 34
                and restore_receipt["account"]["unresolved_requests"] == 4
            )
            assert restore_receipt["clone_pid_removed"]
            handoff_root = tmp_path / "handoff package"
            handoff_materialize(handoff_root)
            handoff_config = json.loads((handoff_root / "CONFIG.json").read_text())
            handoff_config.update(
                api_container_id=api,
                database_container_id=db,
                preservation_source_reference=receipt["source_reference"],
                restore_source_reference=restore_receipt["source_reference"],
                receipt_sha256=hashlib.sha256(
                    (output / "BACKUP_RECEIPT.json").read_bytes()
                ).hexdigest(),
                manifest_sha256=receipt["manifest_sha256"],
                restore_receipt_sha256=hashlib.sha256(
                    (restore_output / "ISOLATED_RESTORE_RECEIPT.json").read_bytes()
                ).hexdigest(),
            )
            (handoff_root / "CONFIG.json").write_text(json.dumps(handoff_config))
            external_root = tmp_path / "external media"
            external_root.mkdir()
            safe_external = str(external_root).replace("'", "''")
            handoff_core = handoff_root / "HANDOFF_CORE.ps1"
            handoff_core.write_text(
                handoff_core.read_text()
                + f"""
# TEST ONLY: disk discovery and NTFS ACL replaced on Linux. No production bypass.
function Find-RetainedBackup {{return '{safe}'}}
function Find-RestoreEvidence {{return '{safe_report}/ISOLATED_RESTORE_RECEIPT.json'}}
function Assert-PrivateBackupDirectory {{}}
function Assert-PrivateBackupFiles {{}}
function Read-HandoffMedia {{return [pscustomobject]@{{root='{safe_external}';identity_sha256=('a'*64);free_bytes=100000000000L;filesystem='NTFS';bus='USB'}}}}
function New-HandoffDirectory {{ $path=Join-Path '{safe_external}' 'new-handoff'; $null=[IO.Directory]::CreateDirectory($path); return $path }}
"""
            )
            handoff_files = {
                p: (handoff_root / p).read_bytes()
                for p in json.loads((handoff_root / "TRANSFER_MANIFEST.json").read_text())["files"]
            }
            handoff_manifest = json.dumps(
                dict(files=hashes(handoff_files)), sort_keys=True
            ).encode()
            (handoff_root / "TRANSFER_MANIFEST.json").write_bytes(handoff_manifest)
            handoff_launch = handoff_root / "test-handoff.ps1"
            handoff_launch.write_text(
                f"$PSNativeCommandArgumentPassing='Legacy'\n& '{str(handoff_root / 'RUN_HANDOFF.ps1')}' -ManifestSHA256 '{hashlib.sha256(handoff_manifest).hexdigest()}' -ExecuteHandoff\nexit $LASTEXITCODE\n"
            )
            copied = subprocess.run(
                [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(handoff_launch)],
                capture_output=True,
                text=True,
                timeout=180,
                env=dict(
                    os.environ,
                    XDG_CACHE_HOME=str(tmp_path / "cache"),
                    XDG_CONFIG_HOME=str(tmp_path / "config"),
                    XDG_DATA_HOME=str(tmp_path / "data"),
                ),
            )
            assert copied.returncode == 0 and not copied.stderr, copied.stdout + copied.stderr
            assert (
                "EXTERNAL_COPY_VERIFIED_SAFE_EJECT_AND_SEPARATE_CUSTODY_PENDING_NO_ORDERS"
                in copied.stdout
            )
            external = external_root / "new-handoff"
            handoff_receipt = json.loads((external / "HANDOFF_RECEIPT.json").read_text())
            assert (
                handoff_receipt["file_count"] == 10
                and not handoff_receipt["physical_disconnect_verified"]
            )
            assert len(list(external.iterdir())) == 13
            verified = subprocess.run(
                [
                    str(PWSH),
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(external / "VERIFY_OFFLINE.ps1"),
                    "-ReferenceSHA256",
                    handoff_receipt["reference_sha256"],
                ],
                capture_output=True,
                text=True,
                timeout=90,
                env=dict(
                    os.environ,
                    XDG_CACHE_HOME=str(tmp_path / "cache"),
                    XDG_CONFIG_HOME=str(tmp_path / "config"),
                    XDG_DATA_HOME=str(tmp_path / "data"),
                ),
            )
            assert verified.returncode == 0 and not verified.stderr, (
                verified.stdout + verified.stderr
            )
            assert "OFFLINE_FILES_HASH_VERIFIED_NO_DOCKER_NO_WRITES" in verified.stdout
            handoff_target = os.environ.get("OFFLINE_HANDOFF_DOCKER_REPORT")
            if handoff_target:
                Path(handoff_target).write_text(
                    json.dumps(
                        dict(
                            scope="FRESH_SYNTHETIC_PG_BACKUP_AND_RESTORE_NOT_ACTUAL_WINDOWS",
                            actual_windows_touched=False,
                            windows_media_discovery_and_acl_mocked=True,
                            native_legacy_launcher_used=True,
                            real_docker_metadata_stopped_scope_check=True,
                            source_and_recovery_receipts_bound=True,
                            all_ten_files_copied_and_hashes_match=True,
                            portable_verifier_passed_without_docker=True,
                            source_files_unchanged=True,
                            originals_and_copy_kept_stopped=True,
                            physical_disconnect_verified=False,
                            orders_enabled=False,
                            live_trading_enabled=False,
                        ),
                        sort_keys=True,
                        indent=2,
                    )
                    + "\n"
                )
            report_target = os.environ.get("ISOLATED_RESTORE_DOCKER_REPORT")
            if report_target:
                Path(report_target).write_text(
                    json.dumps(
                        dict(
                            scope="FRESH_SYNTHETIC_CRASHED_PG_NOT_ACTUAL_WINDOWS",
                            actual_windows_touched=False,
                            windows_acl_and_backup_discovery_mocked=True,
                            real_launcher_and_docker=True,
                            diagnostic_forced_check_only_with_execute_switch_verified=True,
                            diagnostic_created_no_containers_volumes_or_report_directory=True,
                            preservation_helper_inherits_compose_image_labels=True,
                            only_receipt_bound_verified_helper_excluded_from_source_count=True,
                            legacy_argument_mode=True,
                            receipt_and_all_backup_hashes_verified_before_and_after=True,
                            originals_kept_stopped=True,
                            original_volumes_not_mounted_by_restore_helpers=True,
                            new_clone_volumes_only=True,
                            clone_network_none=True,
                            source_pid_retained_clone_pid_removed=True,
                            readonly_account_state_hash_matches=True,
                            clone_gracefully_stopped_with_exit_zero=True,
                            orders_enabled=False,
                            live_trading_enabled=False,
                        ),
                        sort_keys=True,
                        indent=2,
                    )
                    + "\n"
                )
        # Independently restore the EXPORTED host archives into fresh volumes.
        archive_source = base64.b64encode(
            (preserve_package() if preserve_stopped else package())["cold_archive.py"]
        ).decode()
        payload = (
            "import base64\nexec(compile(base64.b64decode('"
            + archive_source
            + "'),'<archive>','exec'))\n"
            + f"print(restore_bundle(Path('/bundle'),{{'database':Path('/restore/database'),'runtime':Path('/restore/runtime')}},retained_manifest_sha256='{receipt['manifest_sha256']}',enabled=True))\n"
        )
        docker(
            "run",
            "-i",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--cap-add",
            "FSETID",
            "--user",
            "0:0",
            "--mount",
            f"type=bind,source={output},target=/bundle,readonly",
            "--mount",
            f"type=volume,source={volumes[2]},target=/restore/database,volume-nocopy",
            "--mount",
            f"type=volume,source={volumes[3]},target=/restore/runtime,volume-nocopy",
            "--entrypoint",
            "python",
            helper_image,
            "-I",
            "-B",
            "-",
            stdin=payload,
            timeout=120,
        )
        if preserve_stopped:
            # Verify exact source bytes still present after capture and before recovery.
            # Only clone PID is removed; retained archive and original remain unchanged.
            import tarfile

            with tarfile.open(output / "database.tar") as tar:
                pid_members = [m for m in tar.getmembers() if m.name.endswith("/postmaster.pid")]
                assert len(pid_members) == 1
                pid_data = tar.extractfile(pid_members[0]).read()
            check_pid = (
                "from pathlib import Path; import hashlib; p=Path('/copy/18/docker/postmaster.pid'); assert hashlib.sha256(p.read_bytes()).hexdigest()=='"
                + hashlib.sha256(pid_data).hexdigest()
                + "'; p.unlink()"
            )
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,source={volumes[2]},target=/copy,volume-nocopy",
                "--entrypoint",
                "python",
                helper_image,
                "-I",
                "-B",
                "-c",
                check_pid,
            )
        clone = docker(
            "run",
            "-d",
            "--name",
            prefix + "-restored-db",
            "--network",
            "none",
            "--read-only",
            "--restart",
            "no",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
            "--user",
            "70:70",
            "--mount",
            f"type=volume,source={volumes[2]},target=/var/lib/postgresql",
            "--entrypoint",
            "postgres",
            PG_IMAGE,
            "-D",
            "/var/lib/postgresql/18/docker",
            "-c",
            "listen_addresses=",
            "-c",
            "unix_socket_directories=/tmp",
        )
        containers.append(clone)
        ready(clone, "/tmp")
        assert (
            sql(
                clone,
                "BEGIN READ ONLY; SELECT encode(sha256(convert_to(state::text,'UTF8')),'hex') FROM demo_accounts; COMMIT;",
                "/tmp",
            )
            == expected
        )
        assert (
            sql(
                clone,
                "BEGIN READ ONLY; SELECT count(*) FROM read_response_evidence_events_v2; COMMIT;",
                "/tmp",
            )
            == "1"
        )
        for name, entry in receipt["archives"].items():
            assert (
                hashlib.sha256((output / (name + ".tar")).read_bytes()).hexdigest()
                == entry["sha256"]
            )
        if preserve_stopped:
            recovery_logs = subprocess.run(
                ["docker", "logs", clone], capture_output=True, text=True, timeout=10
            )
            assert recovery_logs.returncode == 0
            recovery_text = recovery_logs.stdout + recovery_logs.stderr
            assert (
                "automatic recovery in progress" in recovery_text and "redo done" in recovery_text
            )
            # Original PID/WAL contents remain readable and unchanged; source stays stopped.
            check_source = (
                "from pathlib import Path; import hashlib; p=Path('/source/18/docker/postmaster.pid'); assert hashlib.sha256(p.read_bytes()).hexdigest()=='"
                + hashlib.sha256(pid_data).hexdigest()
                + "'"
            )
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,source={source_database},target=/source,readonly,volume-nocopy",
                "--entrypoint",
                "python",
                helper_image,
                "-I",
                "-B",
                "-c",
                check_source,
            )
            source_entries = json.loads((output / "manifest.json").read_text())["groups"][
                "database"
            ]["entries"]
            source_check = "import json,hashlib; from pathlib import Path; entries=json.loads(input()); root=Path('/source'); assert all(hashlib.sha256((root/e['path']).read_bytes()).hexdigest()==e['sha256'] for e in entries if e['kind']=='file')"
            docker(
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--read-only",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,source={source_database},target=/source,readonly,volume-nocopy",
                "--entrypoint",
                "python",
                helper_image,
                "-I",
                "-B",
                "-c",
                source_check,
                stdin=json.dumps(source_entries) + "\n",
            )
            assert docker("inspect", "--format", "{{.State.ExitCode}}", db) == "137"
            assert receipt["source_clean_shutdown_verified"] is False
            assert receipt["account_pause_rechecked"] is False
            assert restore_receipt["clone_stopped"]
            inherited_labels = json.loads(
                docker(
                    "inspect",
                    "--format",
                    "{{json .Config.Labels}}",
                    receipt["helper_container_ids"][0],
                )
            )
            assert (
                inherited_labels["com.docker.compose.project"] == "hybrid-windows"
                and inherited_labels["com.docker.compose.service"] == "api"
            )
        report = dict(
            scope="FRESH_SYNTHETIC_PG_AND_SIX_PARTICIPANTS_NOT_ACTUAL_WINDOWS",
            windows_ntfs_acl_mocked=True,
            production_acl_executed=False,
            check_only_verified_without_stopping_sources=True,
            in_process_plan_transport=True,
            legacy_argument_mode=True,
            real_native_launcher_and_docker=True,
            graceful_source_stop_verified=not preserve_stopped,
            abrupt_stopped_source_preserved=preserve_stopped,
            recovery_only_on_clone=preserve_stopped,
            source_restart_policies_unchanged=True,
            key_session_kept_running=not preserve_stopped,
            source_images_saved=True,
            exported_archives_restored_to_fresh_volumes=True,
            restored_pg_account_hash_matches=True,
            non_demo_prefixed_table_preserved=True,
            source_period_key_preserved="2026-11-13",
            source_originals_not_restarted=True,
            actual_windows_touched=False,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
        )
        target = os.environ.get(
            "STOPPED_PRESERVATION_DOCKER_REPORT"
            if preserve_stopped
            else "COLD_EXECUTOR_DOCKER_REPORT"
        )
        if target:
            Path(target).write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    finally:
        # Remove only IDs created here and executor helpers recovered from its private receipt/failure record.
        for file in ("BACKUP_RECEIPT.json", "FAILURE.json"):
            p = output / file
            if p.exists():
                value = json.loads(p.read_text())
                containers.extend(
                    value.get("helper_container_ids", value.get("retained_helper_ids", []))
                )
                volume = value.get("output_volume", value.get("retained_output_volume"))
                if volume and volume not in original_volume_names:
                    volumes.append(volume)
        for c in reversed(list(dict.fromkeys(containers))):
            subprocess.run(["docker", "rm", "--force", c], capture_output=True, timeout=20)
        for v in reversed(list(dict.fromkeys(volumes))):
            subprocess.run(["docker", "volume", "rm", v], capture_output=True, timeout=20)
        if owned_image:
            subprocess.run(["docker", "image", "rm", owned_image], capture_output=True, timeout=20)
        if network:
            subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=20)
