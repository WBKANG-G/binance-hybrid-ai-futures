"""Real installed API image, stream-only Python -I -B, root filesystem read-only."""

# ruff: noqa: F401,F811
import base64
import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from tests.integration.test_demo import system as demo_system
from tests.integration.test_runtime_inventory_worker import ACCOUNT, financial_state, inventory
from tests.unit.test_runtime_inventory_bundle import package

IMAGE = "hybrid-read-code:fb371aeabcc9-ea6d3f52e6bb"


@pytest.mark.parametrize("database_available", [True, False])
def test_streamed_inventory_on_installed_image_has_no_rootfs_writes_or_exchange_requests(
    inventory, monkeypatch, database_available
):
    exists = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE], capture_output=True, text=True
    )
    if exists.returncode:
        pytest.skip("Installed synthetic API image unavailable")
    with inventory[0].engine.connect() as c:
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
    config = base64.b64encode(files["INVENTORY_CONFIG.json"]).decode()
    payload = f"import base64,json\nCONFIG=json.loads(base64.b64decode('{config}'))\nexec(compile(base64.b64decode('{code}'),'<runtime-inventory>','exec'))\n"
    env = dict(
        os.environ,
        DATABASE_URL=url.render_as_string(hide_password=False),
        BINANCE_DEMO_API_KEY="PRIVATE_CREDENTIAL_SENTINEL",
    )
    before = financial_state(inventory[0])
    command = [
        "docker",
        "run",
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
    ]
    # stdin must stay open; no payload copying, extraction, application startup or TTY.
    command.insert(2, "-i")
    result = subprocess.run(
        command, input=payload, text=True, capture_output=True, env=env, timeout=25
    )
    assert result.returncode == (0 if database_available else 2), result.stderr
    report = json.loads(result.stdout)
    assert report["runtime_inventory"] == (
        "READ_ONLY_INVENTORY_COMPLETE_KEEP_PAUSED" if database_available else "BLOCKED_KEEP_PAUSED"
    )
    assert report["candidate_files_total"] == 9 and report["baseline_files_total"] == 89
    assert (
        report["exchange_credentials_accessed"] is False
        and report["shared_journal_opened"] is False
    )
    assert report["actual_http_attempts"] == 0 and report["order_writes"] == 0
    assert report["database_changed"] is False and report["source_files_changed"] is False
    assert "PRIVATE_CREDENTIAL_SENTINEL" not in result.stdout + result.stderr
    assert "postgresql+" not in result.stdout + result.stderr
    if database_available:
        assert (
            report["request_state_counts"]["UNKNOWN"] == 3
            and report["request_state_counts"]["SUBMITTING"] == 1
        )
        assert report["candidate_table_names_present"] is False
        assert report["account_found"] and report["paused"]
    assert financial_state(inventory[0]) == before
    output = os.environ.get("INVENTORY_DOCKER_REPORT")
    if output and database_available:
        Path(output).write_text(
            json.dumps(
                dict(
                    scope="SYNTHETIC_DOCKER_NOT_ACTUAL_WINDOWS",
                    image=IMAGE,
                    rootfs_read_only=True,
                    user="10001:10001",
                    payload_streamed_to_stdin=True,
                    worker_report=report,
                ),
                indent=2,
            )
            + "\n"
        )
