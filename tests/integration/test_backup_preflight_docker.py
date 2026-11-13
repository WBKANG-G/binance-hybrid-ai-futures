"""Installed API image smoke; read-only rootfs and storage, no app startup."""

# ruff: noqa: F401,F811
import base64
import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from tests.integration.test_backup_scope_preflight_worker import scope_case
from tests.integration.test_demo import system as demo_system
from tests.integration.test_runtime_inventory_worker import financial_state, inventory
from tests.unit.test_backup_scope_preflight import package

IMAGE = "hybrid-read-code:fb371aeabcc9-ea6d3f52e6bb"


@pytest.mark.parametrize("database_available", [True, False])
def test_packaged_worker_on_installed_image_is_streamed_and_rootfs_read_only(
    scope_case, database_available
):
    store, storage = scope_case
    if subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE], capture_output=True
    ).returncode:
        pytest.skip("Installed synthetic API image unavailable")
    with store.engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    address = subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            "phase4-alert-pipeline-fresh-db",
        ],
        text=True,
    ).strip()
    url = (
        make_url(os.environ["TEST_DATABASE_URL"])
        .set(host=address, port=5432 if database_available else 1)
        .update_query_dict({"options": "-csearch_path=" + schema})
    )
    files = package()
    code = base64.b64encode(files["worker.py"]).decode()
    cfg = base64.b64encode(files["PREFLIGHT_CONFIG.json"]).decode()
    payload = f"import base64,json\nCONFIG=json.loads(base64.b64decode('{cfg}'))\nexec(compile(base64.b64decode('{code}'),'<backup-scope-preflight>','exec'))\n"
    # Host test UID differs from container UID; expose only synthetic directory metadata.
    storage.chmod(0o755)
    for path in storage.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
    before = financial_state(store)
    before_files = {
        str(p.relative_to(storage)): p.read_bytes() for p in storage.rglob("*") if p.is_file()
    }
    result = subprocess.run(
        [
            "docker",
            "run",
            "-i",
            "--rm",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001:10001",
            "--network",
            "bridge",
            "--mount",
            f"type=bind,src={storage},dst=/app/demo-export-state,readonly",
            "--env",
            "DATABASE_URL",
            "--env",
            "BINANCE_DEMO_API_KEY",
            "--entrypoint",
            "python",
            IMAGE,
            "-I",
            "-B",
            "-",
        ],
        input=payload,
        text=True,
        capture_output=True,
        timeout=25,
        env=dict(
            os.environ,
            DATABASE_URL=url.render_as_string(hide_password=False),
            BINANCE_DEMO_API_KEY="PRIVATE_CREDENTIAL_SENTINEL",
        ),
    )
    assert result.returncode == (0 if database_available else 2), result.stdout + result.stderr
    assert not result.stderr
    report = json.loads(result.stdout)
    assert report["actual_http_attempts"] == report["order_writes"] == 0
    assert report["database_changed"] is False and report["files_changed"] is False
    assert report["backup_ready"] is False and report["backup_created"] is False
    assert report["exchange_credentials_accessed"] is False
    assert "PRIVATE" not in result.stdout + result.stderr
    assert financial_state(store) == before
    assert {
        str(p.relative_to(storage)): p.read_bytes() for p in storage.rglob("*") if p.is_file()
    } == before_files
    if database_available:
        assert report["backup_scope_preflight"] == "READ_ONLY_SCOPE_OBSERVED_KEEP_PAUSED"
        assert report["storage"]["files"] == 1 and report["storage"]["sqlite_wal_files"] == 1
        assert "read_response_evidence_events_v2" in {r["name"] for r in report["tables"]}
        output = os.environ.get("PREFLIGHT_DOCKER_REPORT")
        if output:
            Path(output).write_text(
                json.dumps(
                    dict(
                        scope="SYNTHETIC_DOCKER_NOT_ACTUAL_WINDOWS",
                        image=IMAGE,
                        rootfs_read_only=True,
                        storage_mount_read_only=True,
                        user="10001:10001",
                        payload_streamed_to_stdin=True,
                        worker_report=report,
                    ),
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            )
    else:
        assert report["backup_scope_preflight"] == "BLOCKED_KEEP_PAUSED"
