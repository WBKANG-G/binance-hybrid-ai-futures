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


def docker(*args, stdin=None, timeout=60):
    result = subprocess.run(
        ["docker", *args], input=stdin, text=True, capture_output=True, timeout=timeout
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell unavailable")
def test_complete_executor_on_fresh_real_docker_with_windows_acl_mocked(tmp_path):
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
    output = tmp_path / "private backup output"
    source_runtime = prefix + "-runtime"
    source_database = prefix + "-database"
    original_volume_names = set(docker("volume", "ls", "--format", "{{.Name}}").splitlines())
    try:
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
INSERT INTO demo_accounts SELECT 'DEMO-WINDOWS-ISOLATED-BTC-V1',jsonb_build_object('paused',true,'daily_key','2026-11-13','orders',(SELECT jsonb_object_agg(i::text,'CLOSED') FROM generate_series(1,34) i),'requests','{"a":"UNKNOWN","b":"UNKNOWN","c":"UNKNOWN","d":"SUBMITTING"}'::jsonb),NULL;
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
            HELPER_IMAGE,
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
                HELPER_IMAGE,
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
                    HELPER_IMAGE,
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
            HELPER_IMAGE,
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
        materialize(tmp_path)
        config = json.loads((tmp_path / "BACKUP_CONFIG.json").read_text())
        config.update(api_container_id=api, database_container_id=db)
        (tmp_path / "BACKUP_CONFIG.json").write_text(json.dumps(config))
        core = tmp_path / "BACKUP_CORE.ps1"
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
        safe_script = str(tmp_path / "RUN_BACKUP.ps1").replace("'", "''")
        launcher.write_text(
            f"$PSNativeCommandArgumentPassing='Legacy'\n& '{safe_script}' -ManifestSHA256 '{hashlib.sha256(manifest).hexdigest()}' -ExecuteColdBackup\nexit $LASTEXITCODE\n"
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
            and "COLD_BACKUP_CHECK_PASSED_NO_STOP_NO_BACKUP_NO_ORDERS" in check.stdout
        )
        assert not output.exists()
        assert docker("inspect", "--format", "{{.State.Running}}", api) == "true"
        assert docker("inspect", "--format", "{{.State.Running}}", db) == "true"
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
        assert "COLD_BACKUP_SAVED_LOCAL_VERIFIED_KEEP_STOPPED_NO_ORDERS" in result.stdout
        receipt = json.loads((output / "BACKUP_RECEIPT.json").read_text())
        assert receipt["source_services_stopped"] and not receipt["automatic_resume"]
        assert all(
            docker("inspect", "--format", "{{.State.Running}}", c) == "false"
            for c in containers[:-1]
        )
        assert docker("inspect", "--format", "{{.State.Running}}", key) == "true"
        assert receipt["source_data_archive_restore_tested"] is False
        # Independently restore the EXPORTED host archives into fresh volumes.
        archive_source = base64.b64encode(package()["cold_archive.py"]).decode()
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
            HELPER_IMAGE,
            "-I",
            "-B",
            "-",
            stdin=payload,
            timeout=120,
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
        report = dict(
            scope="FRESH_SYNTHETIC_PG_AND_SIX_PARTICIPANTS_NOT_ACTUAL_WINDOWS",
            windows_ntfs_acl_mocked=True,
            production_acl_executed=False,
            check_only_verified_without_stopping_sources=True,
            in_process_plan_transport=True,
            legacy_argument_mode=True,
            real_native_launcher_and_docker=True,
            graceful_source_stop_verified=True,
            source_restart_policies_unchanged=True,
            key_session_kept_running=True,
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
        target = os.environ.get("COLD_EXECUTOR_DOCKER_REPORT")
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
        if network:
            subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=20)
