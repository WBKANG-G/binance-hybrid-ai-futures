"""Fresh synthetic PG via local Unix socket; never uses actual Windows archives."""

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select, text

from packages.persistence.demo import accounts, events, metadata
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_copy_event_review import ACCOUNT, review_copy_request_events
from tests.unit.test_copy_event_bundle import fixture_state, package

pytestmark = pytest.mark.skipif(
    not os.getenv("COPY_EVENT_TEST_DATABASE_URL"), reason="Synthetic PG required"
)


@pytest.fixture
def case(tmp_path):
    engine = create_engine(os.environ["COPY_EVENT_TEST_DATABASE_URL"], hide_parameters=True)
    metadata.create_all(engine)
    state, plan = fixture_state()
    now = datetime(2026, 11, 13, 7, tzinfo=UTC)
    with engine.begin() as c:
        c.execute(events.delete())
        c.execute(accounts.delete())
        c.execute(
            insert(accounts).values(
                account_id=ACCOUNT, owner=None, fence=1, lease_until=None, state=state
            )
        )
        for i in range(4):
            payload = copy.deepcopy(state)
            if i == 0:
                payload["requests"] = {}
            if i == 1:
                payload["requests"] = {k: "SUBMITTING" for k in state["requests"]}
            if i == 3:
                payload = {"non_ledger": "PRIVATE_NOT_TO_OUTPUT"}
            c.execute(
                insert(events).values(
                    event_id=str(uuid4()),
                    account_id=ACCOUNT,
                    fence=i + 1,
                    as_of=now + timedelta(seconds=i),
                    kind="DEMO_SL_EFFECT",
                    payload=json.dumps(payload),
                )
            )
    yield engine, state, plan, tmp_path
    engine.dispose()


def snapshot(engine):
    with engine.connect() as c:
        return [
            (r.event_id, r.payload) for r in c.execute(select(events).order_by(events.c.event_id))
        ], c.execute(select(accounts.c.state)).scalar_one()


def test_readonly_projection_preserves_data_and_keeps_wire_proof_false(case):
    e, s, p, _ = case
    before = snapshot(e)
    report = review_copy_request_events(e, p, copy_only_review=True)
    assert report["copy_event_review"] == "LOCAL_EVENT_TIMELINES_READ_KEEP_STOPPED"
    assert (
        report["summary"]["events_scanned"] == 4
        and report["summary"]["non_ledger_payloads_skipped"] == 1
    )
    assert report["summary"]["source_row_coverage_verified"] is True
    assert len(report["summary"]["requests"]) == 4
    assert all(r["stored_transition_count"] >= 2 for r in report["summary"]["requests"])
    assert all(r["not_sent_with_reason_observed"] is False for r in report["summary"]["requests"])
    for flag in [
        "request_resolution_authorized",
        "resend_authorized",
        "request_states_changed",
        "financial_ledger_changed",
        "period_baselines_changed",
        "clock_rebased",
        "wire_submission_or_non_submission_verified",
        "full_event_content_validated",
        "strict_execution_order_verified",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ]:
        assert report[flag] is False
    assert snapshot(e) == before
    body = json.dumps(report)
    assert (
        "PRIVATE_CLIENT_SENTINEL" not in body
        and "PRIVATE_NOT_TO_OUTPUT" not in body
        and "NEVER_OUTPUT" not in body
    )


def test_known_not_sent_reason_is_only_a_stored_transition(case):
    e, s, p, _ = case
    payload = copy.deepcopy(s)
    payload["requests"]["PRIVATE_CLIENT_SENTINEL_3"] = "NOT_SENT"
    payload["request_no_send_evidence"]["PRIVATE_CLIENT_SENTINEL_3"] = "POSITION_ALREADY_FLAT"
    with e.begin() as c:
        c.execute(
            insert(events).values(
                event_id=str(uuid4()),
                account_id=ACCOUNT,
                fence=99,
                as_of=datetime(2026, 11, 13, 8, tzinfo=UTC),
                kind="DEMO_REDUCE_ONLY_EFFECT",
                payload=json.dumps(payload),
            )
        )
    report = review_copy_request_events(e, p, copy_only_review=True)
    row = next(r for r in report["summary"]["requests"] if r["role"] == "EXIT")
    assert row["not_sent_with_reason_observed"] is True
    assert row["backup_request_state"] == "SUBMITTING"
    assert report["request_resolution_authorized"] is False


def test_invalid_event_json_is_not_skipped(case):
    e, s, p, _ = case
    with e.begin() as c:
        c.execute(
            insert(events).values(
                event_id=str(uuid4()),
                account_id=ACCOUNT,
                fence=9,
                as_of=datetime(2026, 11, 13, 8, tzinfo=UTC),
                kind="OTHER",
                payload="PRIVATE_INVALID_JSON",
            )
        )
    with pytest.raises(LocalQuotaStopped, match="LOCAL_PROJECTION_SOURCE_JSON_INVALID"):
        review_copy_request_events(e, p, copy_only_review=True)


def test_projection_shape_errors_cannot_be_mistaken_for_missing_no_send(case):
    e, s, p, _ = case
    payload = copy.deepcopy(s)
    payload["request_no_send_evidence"] = "PRIVATE_INVALID_MAP"
    with e.begin() as c:
        c.execute(
            insert(events).values(
                event_id=str(uuid4()),
                account_id=ACCOUNT,
                fence=9,
                as_of=datetime(2026, 11, 13, 8, tzinfo=UTC),
                kind="OTHER",
                payload=json.dumps(payload),
            )
        )
    with pytest.raises(LocalQuotaStopped, match="LOCAL_PROJECTION_FIELD_SHAPE_INVALID"):
        review_copy_request_events(e, p, copy_only_review=True)


def test_more_than_4096_events_refused_before_projection(case):
    e, s, p, _ = case
    with e.begin() as c:
        c.execute(
            text(
                "INSERT INTO demo_events SELECT md5(g::text)::uuid::text,:account,1,now(),'OTHER','{}' FROM generate_series(1,4093) g"
            ),
            dict(account=ACCOUNT),
        )
    with pytest.raises(LocalQuotaStopped, match="LOCAL_PROJECTION_ROW_COUNT_LIMIT"):
        review_copy_request_events(e, p, copy_only_review=True)


def test_actual_networkless_readonly_worker_uses_cached_image_and_leaves_data_unchanged(case):
    import base64
    import subprocess

    from tests.integration.test_cold_backup_docker_drill import HELPER_IMAGE

    e, s, p, tmp_path = case
    f = package()
    cfg = json.loads(f["CONFIG.json"])
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o755)
    runtime.chmod(0o755)
    folder = (
        runtime
        / "demo-runtime-20261111"
        / "historical-request-evidence-11111111-1111-1111-1111-111111111111"
    )
    folder.mkdir(parents=True)
    for path in [folder, folder.parent]:
        path.chmod(0o755)
    (folder / "intent.json").write_text(json.dumps(p))
    (folder / "intent.json").chmod(0o644)
    context = json.loads(Path("/tmp/copy-events-pg-context.json").read_text())
    sources = {
        name: base64.b64encode(f["payload/" + name]).decode() for name in cfg["candidate_files"]
    }
    config = {k: cfg[k] for k in ["baseline_files", "candidate_files"]}
    code = (
        "import base64,json\nCONFIG=json.loads(base64.b64decode("
        + repr(base64.b64encode(json.dumps(config).encode()).decode())
        + "))\nSOURCES=json.loads(base64.b64decode("
        + repr(base64.b64encode(json.dumps(sources).encode()).decode())
        + "))\nexec(compile(base64.b64decode("
        + repr(base64.b64encode(f["copy_worker.py"]).decode())
        + '),"<synthetic-copy-event-worker>","exec"))\n'
    )
    name = "copy-events-worker-" + uuid4().hex[:12]
    created = subprocess.run(
        [
            "docker",
            "create",
            "--name",
            name,
            "--interactive",
            "--network",
            "none",
            "--restart",
            "no",
            "--read-only",
            "--memory",
            "768m",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001:10001",
            "--mount",
            "type=bind,source=" + str(runtime) + ",target=/review-runtime,readonly",
            "--mount",
            "type=bind,source=" + context["socket"] + ",target=/review-socket,readonly",
            "--entrypoint",
            "python",
            HELPER_IMAGE,
            "-I",
            "-B",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    identifier = created.stdout.strip()
    try:
        before = snapshot(e)
        r = subprocess.run(
            ["docker", "start", "--attach", "--interactive", identifier],
            input=code,
            capture_output=True,
            text=True,
            timeout=75,
        )
        assert r.returncode == 0 and not r.stderr, r.stdout + r.stderr
        v = json.loads(r.stdout)
        assert v["copy_event_review"] == "LOCAL_EVENT_TIMELINES_READ_KEEP_STOPPED"
        assert v["baseline_files_verified"] == 89 and v["candidate_files_verified"] == 2
        assert set(v) == set(cfg["worker_report_keys"])
        assert v["summary"]["events_scanned"] == 4 and v["copy_session_read_only"] is True
        assert "PRIVATE_CLIENT_SENTINEL" not in r.stdout and "PRIVATE_NOT_TO_OUTPUT" not in r.stdout
        assert snapshot(e) == before
        inspected = json.loads(
            subprocess.check_output(["docker", "inspect", identifier], text=True)
        )[0]
        assert inspected["HostConfig"]["NetworkMode"] == "none"
        assert inspected["HostConfig"]["ReadonlyRootfs"] is True
        assert all(not m["RW"] for m in inspected["Mounts"])
        assert inspected["State"]["Running"] is False and inspected["State"]["ExitCode"] == 0
    finally:
        subprocess.run(["docker", "rm", "-f", identifier], capture_output=True, check=True)
