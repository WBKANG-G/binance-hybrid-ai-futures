"""Real Docker inspect/printenv/version through PowerShell native argument modes.

Only fresh sleeping containers, no database initialization or app startup.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from tests.unit.test_cold_backup_plan_powershell import PWSH, package

API_IMAGE = "hybrid-read-code:fb371aeabcc9-ea6d3f52e6bb"
PG_IMAGE = "sha256:c293117fcecda7344b5480222e813b9f673d7abd69b1dd95eff239b768b04f59"


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(not PWSH.exists(), reason="PowerShell 7 unavailable")
@pytest.mark.parametrize("argument_mode", ["Standard", "Legacy"])
def test_real_docker_read_only_plan_and_old_probe_regression(tmp_path, argument_mode):
    for image in (API_IMAGE, PG_IMAGE):
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
            pytest.skip("Cached test image missing")
    if (
        subprocess.run(
            ["docker", "container", "inspect", "hybrid-windows-api-1"], capture_output=True
        ).returncode
        == 0
    ):
        pytest.skip("Reserved API name already in use; never touch existing container")
    prefix = "cold-plan-v2-test-" + uuid4().hex[:12]
    volumes = []
    containers = []
    try:
        for name in ("runtime", "database"):
            volumes.append(docker("volume", "create", prefix + "-" + name))
        api = docker(
            "run",
            "-d",
            "--name",
            "hybrid-windows-api-1",
            "--network",
            "none",
            "--restart",
            "no",
            "--read-only",
            "--label",
            "com.docker.compose.project=hybrid-windows",
            "--label",
            "com.docker.compose.service=api",
            "--mount",
            f"type=volume,source={volumes[0]},target=/app/demo-export-state",
            "--entrypoint",
            "python",
            API_IMAGE,
            "-I",
            "-B",
            "-c",
            "import time; time.sleep(240)",
        )
        containers.append(api)
        db = docker(
            "run",
            "-d",
            "--name",
            prefix + "-db",
            "--network",
            "none",
            "--restart",
            "no",
            "--read-only",
            "--label",
            "com.docker.compose.project=hybrid-windows",
            "--label",
            "com.docker.compose.service=db",
            "--env",
            "POSTGRES_USER=dev",
            "--env",
            "POSTGRES_DB=hybrid",
            "--mount",
            f"type=volume,source={volumes[1]},target=/var/lib/postgresql",
            "--entrypoint",
            "sleep",
            PG_IMAGE,
            "240",
        )
        containers.append(db)
        files = package()
        for name, body in files.items():
            (tmp_path / name).write_bytes(body)
        safe = str(tmp_path / "PLAN.ps1").replace("'", "''")
        digest = hashlib.sha256(files["TRANSFER_MANIFEST.json"]).hexdigest()
        launcher = tmp_path / "launch.ps1"
        launcher.write_text(
            f"$PSNativeCommandArgumentPassing='{argument_mode}'\n& '{safe}' -ManifestSHA256 '{digest}'\nexit $LASTEXITCODE\n"
        )
        environment = dict(
            os.environ,
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
            POWERSHELL_TELEMETRY_OPTOUT="1",
        )
        result = subprocess.run(
            [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(launcher)],
            capture_output=True,
            text=True,
            timeout=45,
            env=environment,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not result.stderr
        report = json.loads(
            next(line for line in result.stdout.splitlines() if line.startswith("{"))
        )
        assert report["backup_plan"] == "READ_ONLY_COLD_BACKUP_PLAN_OBSERVED_KEEP_PAUSED"
        assert report["postgresql_major"] == 18
        assert {r["container_id"] for r in report["participants"]} == {api, db}
        assert report["postgres_probe"]["control_version"] == "MATCHING_VERSION"
        assert not report["source_services_stopped"] and not report["backup_created"]
        assert all(
            docker("inspect", "--format", "{{.State.Running}}", c) == "true" for c in containers
        )
        # Reproduce the exact v1 shell-format hazard with real native Docker argv.
        probe = tmp_path / "old-probe.ps1"
        probe.write_text(
            "$PSNativeCommandArgumentPassing='"
            + argument_mode
            + "'\n"
            + '$command=\'printf "%s\\n%s\\n%s\\n" "$PGDATA" "$POSTGRES_USER" "$POSTGRES_DB"\'\n'
            + "& docker exec "
            + db
            + " sh -c $command\n"
        )
        old = subprocess.run(
            [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(probe)],
            capture_output=True,
            text=True,
            timeout=15,
            env=environment,
        )
        assert old.returncode == 0 and not old.stderr
        expected = ["/var/lib/postgresql/18/docker", "dev", "hybrid"]
        if argument_mode == "Legacy":
            assert old.stdout.splitlines() != expected
        else:
            assert old.stdout.splitlines() == expected
        # New native format contains no literal quote characters; every output row is strict JSON.
        paths = [
            ".Id",
            ".Name",
            ".Image",
            ".Config.Labels",
            ".Mounts",
            ".State.Running",
            ".State.Restarting",
            ".State.Paused",
            ".State.Dead",
            ".HostConfig.RestartPolicy",
            ".NetworkSettings.Networks",
        ]
        native_format = "{{println}}".join("{{json " + p + "}}" for p in paths)
        framed = tmp_path / "framed.ps1"
        framed.write_text(
            "$PSNativeCommandArgumentPassing='"
            + argument_mode
            + "'\n$format='"
            + native_format
            + "'\n& docker inspect --format $format "
            + api
            + "\n"
        )
        frame_output = subprocess.run(
            [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(framed)],
            capture_output=True,
            text=True,
            timeout=15,
            env=environment,
        )
        assert frame_output.returncode == 0 and not frame_output.stderr
        assert len(frame_output.stdout.splitlines()) == 11
        decoded = [json.loads(row) for row in frame_output.stdout.splitlines()]
        assert decoded[0] == api and isinstance(decoded[4], list) and len(decoded[4]) == 1
        old_json = tmp_path / "old-json.ps1"
        old_json.write_text(
            "$PSNativeCommandArgumentPassing='"
            + argument_mode
            + '\'\n$format=\'{"id":{{json .Id}},"name":{{json .Name}}}\'\n& docker inspect --format $format '
            + api
            + "\n"
        )
        old_json_output = subprocess.run(
            [str(PWSH), "-NoProfile", "-NonInteractive", "-File", str(old_json)],
            capture_output=True,
            text=True,
            timeout=15,
            env=environment,
        )
        assert old_json_output.returncode == 0 and not old_json_output.stderr
        old_json_valid = True
        try:
            json.loads(old_json_output.stdout)
        except json.JSONDecodeError:
            old_json_valid = False
        assert old_json_valid == (argument_mode == "Standard")
        target = os.environ.get("COLD_PLAN_NATIVE_REPORT_DIR")
        if target:
            path = Path(target)
            path.mkdir(exist_ok=True, parents=True)
            (path / (argument_mode + ".json")).write_text(
                json.dumps(
                    dict(
                        scope="FRESH_SLEEPING_SYNTHETIC_CONTAINERS_REAL_DOCKER_NOT_ACTUAL_WINDOWS",
                        argument_mode=argument_mode,
                        postgresql_major=18,
                        framed_fields_strict_json_valid=True,
                        legacy_custom_json_strict_valid=old_json_valid,
                        postgres_fields_validated=True,
                        source_containers_kept_running=True,
                        database_server_initialized=False,
                        sql_executed=False,
                        old_shell_probe_returned_expected_fields=old.stdout.splitlines()
                        == expected,
                        backup_created=False,
                        execution_approved=False,
                        orders_enabled=False,
                        live_trading_enabled=False,
                    ),
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            )
    finally:
        for c in reversed(containers):
            subprocess.run(["docker", "rm", "--force", c], capture_output=True, timeout=15)
        for v in reversed(volumes):
            subprocess.run(["docker", "volume", "rm", v], capture_output=True, timeout=15)
