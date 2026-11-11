"""Actual fallback installation and rollback, isolated synthetic PostgreSQL state."""

# ruff: noqa: F811
import hashlib
import importlib.util
import json
import shutil
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from packages.persistence.demo import accounts, events, outbox

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "apply_fixture",
    BASE / "manual-close-day-apply-20261111/test_manual_close_day_apply_20261111.py",
)
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
setup = prior.setup
closed = prior.closed
demo_system = prior.demo_system
spec = importlib.util.spec_from_file_location(
    "recovery_tests", Path(__file__).with_name("test_docker_recovery.py")
)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


@pytest.mark.parametrize("outcome", ["success", "concurrent_audit"])
def test_real_fallback_installer_preserves_pg_state_and_configuration(setup, tmp_path, outcome):
    store, *_ = setup
    prior.apply(setup)
    target = "hybrid-windows-api-1"
    assert (
        subprocess.run(
            ["docker", "inspect", target], env=r.ENV, capture_output=True, check=False
        ).returncode
        != 0
    )
    account = "DEMO-WINDOWS-ISOLATED-BTC-V1"
    with store.engine.begin() as c:
        row = dict(
            c.execute(select(accounts).where(accounts.c.account_id == "DEMO-test")).mappings().one()
        )
        row["account_id"] = account
        c.execute(insert(accounts).values(**row))
        schema = c.execute(text("SELECT current_schema()")).scalar_one()

    def snapshot():
        with store.engine.connect() as c:
            return [
                c.execute(select(t).order_by(*t.primary_key.columns)).all()
                for t in (accounts, events, outbox)
            ]

    before = snapshot()
    address = subprocess.check_output(
        [
            "docker",
            "inspect",
            "phase4-alert-pipeline-fresh-db",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        ],
        env=r.ENV,
        text=True,
    ).strip()
    volume = "phase4-recovery-pg-" + uuid4().hex[:12]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    packet = tmp_path / "installer"
    packet.mkdir()
    for name in ("INSTALL_CODE.ps1", "INSTALL_INSPECTION.ps1", "DOCKER_RECOVERY.ps1"):
        shutil.copy2(Path(__file__).with_name(name), packet / name)
    code = BASE / "runtime-code-install-v3-20261111"
    for name in ("guard_runtime_code_install.py", "Dockerfile"):
        shutil.copy2(code / name, packet / name)
    shutil.copytree(code / "payload", packet / "payload")
    files = {
        str(p.relative_to(packet)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in packet.rglob("*")
        if p.is_file()
    }
    (packet / "TRANSFER_MANIFEST.json").write_text(json.dumps({"files": files}))

    def docker(*args):
        result = subprocess.run(
            ["docker", *args], env=r.ENV, capture_output=True, text=True, check=False, timeout=40
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    docker("volume", "create", volume)
    backup = candidate = original_id = None
    worker = None
    stop_worker = threading.Event()
    audit_added = threading.Event()
    try:
        dsn = f"postgresql+psycopg://dev:dev@{address}:5432/hybrid?options=-csearch_path%3D{schema}"
        docker(
            "run",
            "-d",
            "--name",
            target,
            "--network",
            "bridge",
            "--user",
            "10001",
            "-e",
            "DUMMY_TOKEN=PRIVATE_한글",
            "-e",
            "LIVE_TRADING_ENABLED=false",
            "-e",
            "TRADING_MODE=PAPER",
            "-e",
            "DATABASE_URL=" + dsn,
            "--label",
            "com.docker.compose.project=hybrid-windows",
            "--label",
            "com.docker.compose.service=api",
            "--label",
            "com.docker.compose.project.working_dir=PRIVATE_MISSING_DIRECTORY",
            "--label",
            "com.docker.compose.project.config_files=PRIVATE_MISSING_FILE",
            "-v",
            volume + ":/app/demo-export-state",
            "-p",
            f"127.0.0.1:{port}:8000",
            "--health-cmd",
            "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=2)\"",
            "--health-interval",
            "1s",
            "--health-timeout",
            "3s",
            "--health-retries",
            "15",
            "--entrypoint",
            "python",
            "phase4-tp-budget-install-smoke:20261109",
            "-m",
            "http.server",
            "8000",
        )
        for _ in range(40):
            details = json.loads(docker("inspect", target))[0]
            if details["State"]["Health"]["Status"] == "healthy":
                break
            time.sleep(0.3)
        assert details["State"]["Health"]["Status"] == "healthy"
        original_id = details["Id"]
        if outcome == "concurrent_audit":

            def mutate_audit():
                while not stop_worker.is_set():
                    probe = subprocess.run(
                        ["docker", "inspect", target],
                        env=r.ENV,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if probe.returncode == 0 and json.loads(probe.stdout)[0]["Id"] != original_id:
                        with store.engine.begin() as connection:
                            connection.execute(
                                insert(events).values(
                                    event_id=str(uuid4()),
                                    account_id=account,
                                    fence=row["fence"],
                                    as_of=datetime.now(UTC),
                                    kind="SYNTHETIC_CONCURRENT_AUDIT",
                                    payload="SYNTHETIC_TEST_ONLY",
                                )
                            )
                        audit_added.set()
                        return
                    time.sleep(0.05)

            worker = threading.Thread(target=mutate_audit, daemon=True)
            worker.start()
        result = subprocess.run(
            [r.PWSH, "-NoProfile", "-File", str(packet / "INSTALL_CODE.ps1")],
            env=r.ENV,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        state = packet / "INSTALL_STATE.json"
        if state.exists():
            info = json.loads(state.read_text())
            backup = info.get("backup_container")
            candidate = info.get("candidate_container_id")
        assert "PRIVATE_" not in result.stdout + result.stderr
        if outcome == "success":
            assert result.returncode == 0, result.stdout + result.stderr
            assert "POSTCHECKED_CODE_ONLY_KEEP_PAUSED" in result.stdout
            assert "PRESERVED_CONTAINER_CONFIGURATION_VERIFIED_BACKUP_RETAINED" in result.stdout
            assert snapshot() == before
            after = json.loads(docker("inspect", target))[0]
            assert after["Id"] != original_id and after["Config"]["Env"] == details["Config"]["Env"]
            assert (
                after["HostConfig"] == details["HostConfig"]
                and after["Mounts"] == details["Mounts"]
            )
            old = json.loads(docker("inspect", backup))[0]
            assert old["Id"] == original_id and not old["State"]["Running"]
        else:
            assert audit_added.is_set()
            assert result.returncode == 2, result.stdout + result.stderr
            report = json.loads(result.stdout.splitlines()[-1])
            assert (
                report["rollback_status"] == "ORIGINAL_API_RESTORED"
                and not report["rollback_database_verified"]
            )
            restored = json.loads(docker("inspect", target))[0]
            assert (
                restored["Id"] == original_id and restored["State"]["Health"]["Status"] == "healthy"
            )
            after_rows = snapshot()
            assert after_rows[0] == before[0] and after_rows[2] == before[2]
            assert len(after_rows[1]) == len(before[1]) + 1
            assert not json.loads(docker("inspect", report["failed_container"]))[0]["State"][
                "Running"
            ]
    finally:
        stop_worker.set()
        if worker is not None:
            worker.join(timeout=5)
        for name in (target, backup, candidate, original_id):
            if name:
                subprocess.run(
                    ["docker", "rm", "-f", name], env=r.ENV, capture_output=True, check=False
                )
        subprocess.run(
            ["docker", "volume", "rm", volume], env=r.ENV, capture_output=True, check=True
        )
