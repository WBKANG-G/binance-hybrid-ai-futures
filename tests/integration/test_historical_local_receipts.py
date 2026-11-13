"""Native read-only receipt comparison after immutable prior HTTP evidence."""

# ruff: noqa: F401,F811
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import DBAPIError

from packages.persistence.demo import accounts, events
from scripts import adopt_demo_accounted_day13 as adoption
from scripts import probe_demo_accounted_reads as common
from scripts import review_demo_historical_receipts as receipts
from scripts import review_demo_historical_requests as history
from scripts import review_demo_post_adoption as gate
from scripts import review_demo_read_code_install as fingerprint
from scripts import review_demo_read_deployment as deployment
from tests.integration.test_historical_exit_evidence import (
    adopted,
    anchored,
    base_installation,
    candidate,
    day12,
    day_sender,
    demo_system,
    execute,
    exit_wire,
    installation,
    invoke,
    predecessor,
    prepare,
    ready,
    reviewed,
    run,
    sender,
    wire,
)
from tests.integration.test_runtime_read_accounting import financial, settings
from tests.unit.test_quota_startup_read_only import files

pytestmark = pytest.mark.parametrize("installation", ["local_receipts"], indirect=True)


@pytest.fixture
def finished(predecessor):
    execute(
        predecessor,
        exit_followup=True,
        sender=exit_wire(predecessor, [], "missing"),
        source=receipts.EXIT_SOURCE,
    )
    return predecessor


def review(case):
    a = case[0]
    return receipts.run(
        a[1].engine,
        deployment.STORAGE,
        a[0].scope,
        a[4],
        a[1].installation_id(),
        "DEMO-test",
        settings(a),
        fingerprint,
        adoption,
        gate,
        history,
        common,
        json.loads(Path("docs/request_cost_evidence/PINNED_RUNTIME_COST_PROOF.json").read_text()),
        clocks=case[3],
    )


def test_receipt_relations_and_intent_effect_history_never_resolve_or_write(finished, monkeypatch):
    from integrations.binance_usdm.demo import DemoClient
    from scripts import demo_key_session

    def forbidden(*a, **kw):
        pytest.fail("credential or HTTP access")

    monkeypatch.setattr(DemoClient, "request", forbidden)
    monkeypatch.setattr(demo_key_session, "credentials", forbidden)
    before = (
        financial(finished[0]),
        files(deployment.STORAGE.parent),
        common.resource_rows(finished[0][1].engine, finished[0][0].scope),
    )
    result = review(finished)
    assert result["local_historical_receipt_review"] == "COMPLETED_KEEP_PAUSED"
    assert (
        result["http_attempts"] == result["modeled_weight_charged"] == 0
        and not result["credential_session_accessed"]
    )
    assert result["all_reviewed_state_unchanged"] and result["previous_exit_evidence_verified"]
    rows = {
        r["role"]: r for r in result["local_receipt_relations"]["requests"] if r["role"] != "ENTRY"
    }
    for role in ["STOP_LOSS", "EXIT"]:
        row = rows[role]
        assert row["cancelled_exposure_correction_receipts"][0][
            "receipt_structure_matches_current_order"
        ]
        assert row["cancelled_exposure_correction_receipts"][0][
            "original_cancelled_zero_fill_shape"
        ]
        assert not row["remote_resolution_verified"] and not row["automatically_resolvable"]
    assert rows["STOP_LOSS"]["same_order_request_references"] == [
        rows["EXIT"]["request_reference_sha256"]
    ]
    timeline = result["local_event_history"]["request_local_state_timelines"][
        rows["STOP_LOSS"]["request_reference_sha256"]
    ]
    states = [r["local_fields"]["request_state"] for r in timeline]
    assert "SUBMITTING" in states and "UNKNOWN" in states
    assert all(not r["remote_proof_verified"] for r in timeline)
    assert not result["local_receipt_relations"]["flat_emergency_acknowledgements"][0][
        "request_resolution_authorized"
    ]
    assert not result["capacity_assumptions"]["proof_fits_empty_window"]
    assert not result["capacity_shortfall_resolved"] and not result["execution_approved"]
    assert before == (
        financial(finished[0]),
        files(deployment.STORAGE.parent),
        common.resource_rows(finished[0][1].engine, finished[0][0].scope),
    )
    assert review(finished) == result


def test_changed_prior_exit_result_is_not_accepted(finished):
    _, folder = history.paths(deployment.STORAGE, finished[0][4], exit_followup=True)
    p = folder / "complete.json"
    data = json.loads(p.read_text())
    data["observations"][0]["observed_status"] = "FILLED"
    p.write_text(json.dumps(data))
    with pytest.raises(common.ProbeBlocked, match="SAVED_OBSERVATION_CHANGED"):
        review(finished)


def test_database_refuses_write_inside_timeline_transaction(finished):
    engine = finished[0][1].engine
    before = financial(finished[0])

    def inject(c, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT demo_events.event_id"):
            cursor.execute("UPDATE demo_accounts SET fence=fence+1")

    event.listen(engine, "before_cursor_execute", inject)
    try:
        with pytest.raises(DBAPIError):
            review(finished)
    finally:
        event.remove(engine, "before_cursor_execute", inject)
    assert financial(finished[0]) == before


def test_non_ledger_events_are_counted_and_not_mistaken_for_missing_requests(finished):
    # Directly test the scanner; injecting after adoption would correctly fail preserved-history validation.
    engine = finished[0][1].engine
    with engine.begin() as c:
        c.execute(
            events.insert().values(
                event_id=str(uuid4()),
                account_id="DEMO-test",
                fence=0,
                as_of=finished[0][2].utc(),
                kind="OTHER",
                payload=json.dumps({"synthetic": "private"}),
            )
        )
    ledger = finished[0][6].read("DEMO-test")
    result = receipts.timelines(engine, "DEMO-test", history.plan_requests(ledger, common), common)
    assert result["non_ledger_payloads_skipped"] >= 1
    assert "private" not in str(result)


def test_oversized_event_refuses_instead_of_returning_partial_history(finished):
    engine = finished[0][1].engine
    with engine.begin() as c:
        c.execute(
            events.insert().values(
                event_id=str(uuid4()),
                account_id="DEMO-test",
                fence=0,
                as_of=finished[0][2].utc(),
                kind="OTHER",
                payload=json.dumps({"synthetic": "X" * (2 * 1024 * 1024)}),
            )
        )
    before = financial(finished[0])
    ledger = finished[0][6].read("DEMO-test")
    with pytest.raises(common.ProbeBlocked, match="EVENT_ROW_LIMIT"):
        receipts.timelines(engine, "DEMO-test", history.plan_requests(ledger, common), common)
    assert financial(finished[0]) == before


def test_scan_timeout_refuses_without_partial_success(finished, monkeypatch):
    before = financial(finished[0])
    samples = iter([0, 61])
    from types import SimpleNamespace

    monkeypatch.setattr(receipts, "time", SimpleNamespace(monotonic=lambda: next(samples, 61)))
    ledger = finished[0][6].read("DEMO-test")
    with pytest.raises(common.ProbeBlocked, match="EVENT_TIME_LIMIT"):
        receipts.timelines(
            finished[0][1].engine, "DEMO-test", history.plan_requests(ledger, common), common
        )
    monkeypatch.undo()
    assert financial(finished[0]) == before


def scan_account(
    engine, ledger, count, *, payload_expression="json_build_object('synthetic',true)::text"
):
    # Separate synthetic account so metadata limits can be tested without changing adopted receipts.
    from sqlalchemy import text

    with engine.begin() as c:
        c.execute(
            accounts.insert().values(
                account_id="DEMO-scan",
                owner=None,
                fence=0,
                lease_until=None,
                state=ledger.model_dump(mode="json"),
            )
        )
        c.execute(
            text(
                "INSERT INTO demo_events(event_id,account_id,fence,as_of,kind,payload) SELECT md5('scan-'||n::text)::uuid::text,'DEMO-scan',0,now(),'SYNTHETIC',"
                + payload_expression
                + " FROM generate_series(1,:count) n"
            ),
            dict(count=count),
        )


def test_full_4096_rows_use_one_ordered_stream_without_omitting_rows(finished):
    engine = finished[0][1].engine
    ledger = finished[0][6].read("DEMO-test")
    scan_account(engine, ledger, 4096)
    queries = []

    def record(c, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT demo_events.event_id"):
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = receipts.timelines(
            engine, "DEMO-scan", history.plan_requests(ledger, common), common
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(queries) == 1
    assert result["events_scanned"] == result["non_ledger_payloads_skipped"] == 4096
    assert (
        result["event_metadata_counts_verified"]
        and result["query_strategy"] == "SINGLE_READ_ONLY_STREAM"
    )


def test_row_count_limit_is_diagnosed_before_transferring_payloads(finished):
    engine = finished[0][1].engine
    ledger = finished[0][6].read("DEMO-test")
    scan_account(engine, ledger, 4097)
    state = {}
    with pytest.raises(common.ProbeBlocked, match="EVENT_ROW_COUNT_LIMIT"):
        receipts.timelines(
            engine, "DEMO-scan", history.plan_requests(ledger, common), common, state=state
        )
    assert state["local_receipt_scan"]["planned_event_rows"] == 4097
    assert state["local_receipt_scan"]["events_scanned"] == 0
    assert state["local_receipt_scan"]["row_limit"] == 4096


def test_byte_limit_is_diagnosed_before_transferring_payloads(finished):
    engine = finished[0][1].engine
    ledger = finished[0][6].read("DEMO-test")
    scan_account(
        engine,
        ledger,
        128,
        payload_expression="json_build_object('synthetic',repeat('X',1048576))::text",
    )
    state = {}
    with pytest.raises(common.ProbeBlocked, match="EVENT_BYTE_LIMIT"):
        receipts.timelines(
            engine, "DEMO-scan", history.plan_requests(ledger, common), common, state=state
        )
    assert state["local_receipt_scan"]["planned_event_rows"] == 128
    assert state["local_receipt_scan"]["planned_event_bytes"] > 128 * 1024 * 1024
    assert state["local_receipt_scan"]["events_scanned"] == 0
