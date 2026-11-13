"""Separate CLI process with a PostgreSQL connection defaulting to READ ONLY."""

# ruff: noqa: F401,F811
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_wire_restore_reference_candidate import export_reference_copy
from tests.integration.test_demo_read_audit import audited
from tests.integration.test_response_evidence_candidate import evidenced
from tests.integration.test_runtime_read_accounting import financial
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.integration.test_wire_checkpoint_candidate import SCOPE, capture, coupled, run
from tests.unit.test_quota_startup_read_only import files
from tests.unit.test_wire_session_contract_candidate import KEY, SECRET


@pytest.mark.parametrize(
    "mode",
    [
        "inspect",
        "compare-match",
        "compare-mismatch",
        "compare-tamper",
        "missing-dedicated-env",
        "bad-argument",
    ],
)
def test_cli_process_is_readonly_sanitized_and_cannot_resume(coupled, tmp_path_factory, mode):
    run(coupled, capture(coupled))
    root = tmp_path_factory.mktemp("cli-offline-reference")
    saved = export_reference_copy(
        coupled[2], coupled[5], root, SCOPE, coupled[3].journal_id, "DEMO-test", apply=True
    )
    with coupled[1].engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    url = make_url(os.environ["TEST_DATABASE_URL"]).update_query_dict(
        {"options": f"-csearch_path={schema} -cdefault_transaction_read_only=on"}
    )
    env = dict(
        os.environ,
        WIRE_REVIEW_DATABASE_URL=url.render_as_string(hide_password=False),
        DATABASE_URL="PRIVATE_DO_NOT_USE_FALLBACK",
    )
    command = [
        sys.executable,
        "-m",
        "scripts.review_wire_restore_reference",
        "--reference-file",
        str(root / saved["filename"]),
        "--retained-sha256",
        saved["retained_sha256"],
    ]
    if mode.startswith("compare") or mode == "missing-dedicated-env":
        command += [
            "--mode",
            "compare",
            "--enable-synthetic-review",
            "--source-directory",
            str(coupled[5]),
            "--scope",
            SCOPE,
            "--journal",
            str(coupled[3].journal_id),
            "--account",
            "DEMO-test",
        ]
    if mode == "inspect":
        env["WIRE_REVIEW_DATABASE_URL"] = "PRIVATE_INVALID_DATABASE_CONFIGURATION"
    elif mode == "missing-dedicated-env":
        del env["WIRE_REVIEW_DATABASE_URL"]
    elif mode == "compare-mismatch":
        coupled[1].change(
            coupled[3].lease,
            "SYNTHETIC_REFERENCE_CLI_NOTE",
            lambda ledger: ledger.operations.update(REFERENCE_CLI_NOTE="synthetic"),
        )
    elif mode == "compare-tamper":
        coupled[3].path.write_bytes(coupled[3].path.read_bytes() + b"{}\n")
    elif mode == "bad-argument":
        command += ["--api-key", "PRIVATE_KEY_DO_NOT_ECHO"]
    before, disk = financial(coupled[0][0]), files(coupled[5])
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == (0 if mode in {"inspect", "compare-match"} else 2)
    assert not result.stderr
    report = json.loads(result.stdout)
    assert not any(
        report[k]
        for k in (
            "restore_performed",
            "storage_restore_authorized",
            "process_restart_resumption_supported",
            "credential_session_resumed",
            "reference_is_latest_proven",
            "execution_approved",
            "orders_enabled",
            "live_trading_enabled",
        )
    )
    if mode == "inspect":
        assert report["database_accessed"] is False
    elif mode in {"compare-match", "compare-mismatch"}:
        assert report["matches_supplied_retained_reference"] is (mode == "compare-match")
    elif mode == "missing-dedicated-env":
        assert report["failure_code"] == "WIRE_REVIEW_DATABASE_ENV_REQUIRED"
    for private in (
        KEY.get_secret_value(),
        SECRET.get_secret_value(),
        "PRIVATE_KEY_DO_NOT_ECHO",
        "PRIVATE_DO_NOT_USE_FALLBACK",
        "PRIVATE_INVALID_DATABASE_CONFIGURATION",
        "postgresql+psycopg://",
    ):
        assert private not in result.stdout + result.stderr
    assert financial(coupled[0][0]) == before and files(coupled[5]) == disk
