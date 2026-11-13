"""Real PG financial commits and two fsynced files, synthetic responder only."""

# ruff: noqa: F401,F811
import copy
import hashlib
import json
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, event, insert, select, text, update
from sqlalchemy.exc import DBAPIError

from integrations.binance_usdm.rest import RESTResponse, prepare_signed_query
from packages.persistence.demo import accounts, outbox
from packages.persistence.demo import events as financial_events
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_wire_checkpoint_candidate import (
    FINANCIAL_KIND,
    PREFIX,
    AnchoredSyntheticWireJournal,
    CoupledDemoStore,
    WireCheckpointStore,
    bindings,
    checkpoints,
    installations,
)
from services.execution.demo_wire_session_contract_candidate import (
    CredentialSession,
    SyntheticResponder,
)
from tests.integration.test_demo_read_audit import audited
from tests.integration.test_response_evidence_candidate import evidenced
from tests.integration.test_runtime_read_accounting import financial
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.integration.test_submission_link_candidate import REQUEST, intent
from tests.unit.test_quota_startup_read_only import files
from tests.unit.test_wire_session_contract_candidate import KEY, SECRET, params

SCOPE = "DEMO-wire-scope"


@pytest.fixture
def coupled(evidenced, tmp_path_factory):
    a = evidenced[0]
    existing, coordinator = a[-2:]
    existing.change(
        coordinator.lease, "SYNTHETIC_PAUSE", lambda ledger: setattr(ledger, "paused", True)
    )
    store = CoupledDemoStore(existing.engine, clock=existing.now)
    anchors = WireCheckpointStore(store.engine)
    anchors.provision(apply=True)
    root = tmp_path_factory.mktemp("wire-anchored")
    journal = AnchoredSyntheticWireJournal.create_anchored(
        root, SCOPE, store, coordinator.lease, anchors, enable_synthetic_contract=True
    )
    owner = CredentialSession("DEMO-test", SCOPE, KEY, SECRET, enable_synthetic_contract=True)
    return evidenced, store, anchors, journal, owner, root


def capture(case, role="ENTRY"):
    original, _, _, _, owner, _ = case
    pin = intent(original, role)
    path, values = params(role, REQUEST, "0.0003" if role == "EXIT" else "0.0007")
    signed = prepare_signed_query(values, api_key=KEY, secret=SECRET, timestamp_ms=17)
    return owner.prepare("POST", path, signed.query, signed.api_key, intent=pin)


def reply():
    return SyntheticResponder(RESTResponse(200, b'{"synthetic":true}', {}))


def run(case, prepared, response=None):
    return case[3].exercise(case[4], prepared, response or reply(), enable_synthetic_dispatch=True)


def review(case):
    return case[2].review(case[5], SCOPE, case[3].journal_id, "DEMO-test")


def pg(case):
    with case[1].engine.connect() as c:
        return (
            c.execute(
                select(checkpoints)
                .where(checkpoints.c.scope == SCOPE)
                .order_by(checkpoints.c.sequence)
            )
            .mappings()
            .all()
        )


@pytest.mark.parametrize("role", ["ENTRY", "STOP_LOSS", "EXIT"])
def test_financial_marker_event_outbox_and_checkpoint_commit_before_boundary(
    coupled, role, monkeypatch
):
    prepared = capture(coupled, role)
    original, store, _, _, owner, root = coupled
    before = store.read("DEMO-test").model_dump(mode="json")
    prior = financial(original[0])
    calls = []
    original_respond = SyntheticResponder._respond

    def check(self):
        rows = pg(coupled)
        assert [r["wire"]["kind"] for r in rows] == ["ROOT", "SESSION", "PREPARE", "DISPATCH_BEGIN"]
        prepare = rows[2]
        assert prepare["financial_event"] and prepare["finance"] and prepare["source"]
        with store.engine.connect() as c:
            event_row = (
                c.execute(
                    select(financial_events).where(
                        financial_events.c.event_id == prepare["financial_event"]
                    )
                )
                .mappings()
                .one()
            )
            topic = c.execute(
                select(outbox.c.topic).where(outbox.c.event_id == prepare["financial_event"])
            ).scalar_one()
        assert event_row["kind"] == FINANCIAL_KIND and topic == "DEMO_AUDIT"
        wire = prepare["wire"]["data"]
        assert wire["session"] == owner.descriptor()["session"]
        if role == "EXIT":
            assert wire["params"]["quantity"] == "0.0003"
        assert review(coupled)["financial_prepares"] == 1
        calls.append(True)
        return original_respond(self)

    monkeypatch.setattr(SyntheticResponder, "_respond", check)
    result = run(coupled, prepared)
    assert result.status == 200 and calls == [True]
    after = store.read("DEMO-test").model_dump(mode="json")
    marker = PREFIX + str(prepared.operation)
    assert after["operations"].pop(marker)
    assert after == before
    current = financial(original[0])
    assert len(current[1]) == len(prior[1]) + 1 and len(current[2]) == len(prior[2]) + 1
    report = review(coupled)
    assert report["pg_file_checkpoint_matches"] and report["financial_prepare_transaction_coupled"]
    assert report["pending_operations"] == 0
    assert not any(
        report[k]
        for k in (
            "whole_system_rollback_protection_verified",
            "original_historical_intent_authenticated",
            "remote_account_identity_verified",
            "real_wire_submission_verified",
            "execution_approved",
            "orders_enabled",
        )
    )
    disk, current = files(root), financial(original[0])
    assert review(coupled) == report
    assert files(root) == disk and financial(original[0]) == current


def test_default_denial_does_not_access_paths_or_database():
    with pytest.raises(LocalQuotaStopped, match="DISABLED"):
        AnchoredSyntheticWireJournal.create_anchored(None, None, None, None, None)
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_PROVISION"):
        object.__new__(WireCheckpointStore).provision()


def test_same_scope_cannot_create_a_second_writer_or_backfill_old_file(coupled, tmp_path_factory):
    _, store, anchors, journal, _, _ = coupled
    other = tmp_path_factory.mktemp("second-writer")
    with pytest.raises(LocalQuotaStopped, match="ALREADY_BOUND"):
        AnchoredSyntheticWireJournal.create_anchored(
            other, SCOPE, store, journal.lease, anchors, enable_synthetic_contract=True
        )
    assert not list(other.iterdir()) and review(coupled)["records"] == 1


@pytest.mark.parametrize("phase", ["SESSION", "PREPARE", "DISPATCH_BEGIN", "OBSERVED"])
def test_pg_commit_failure_never_returns_or_retries_and_detects_file_gap(coupled, phase):
    prepared = capture(coupled)
    response = reply()
    engine = coupled[1].engine
    before = financial(coupled[0][0])

    def fail(connection):
        found = connection.execute(
            select(checkpoints.c.sequence).where(checkpoints.c.wire["kind"].astext == phase)
        ).first()
        if found:
            raise RuntimeError("SYNTHETIC_PG_COMMIT_FAILURE")

    event.listen(engine, "commit", fail)
    try:
        with pytest.raises(LocalQuotaStopped, match="COMMIT_FAILED"):
            run(coupled, prepared, response)
    finally:
        event.remove(engine, "commit", fail)
    assert response.calls == (phase == "OBSERVED")
    if phase in {"SESSION", "PREPARE"}:
        assert financial(coupled[0][0]) == before
    else:
        assert len(financial(coupled[0][0])[1]) == len(before[1]) + 1
    with pytest.raises(LocalQuotaStopped):
        review(coupled)
    with pytest.raises(LocalQuotaStopped):
        run(coupled, prepared, response)
    assert response.calls == (phase == "OBSERVED")


@pytest.mark.parametrize("stage", ["checkpoint-insert", "financial-outbox"])
def test_sql_failure_rolls_back_financial_state_event_outbox_and_anchor(coupled, stage):
    prepared, response = capture(coupled), reply()
    engine = coupled[1].engine
    before = financial(coupled[0][0])

    def fail(c, cursor, statement, params, context, many):
        financial_context = coupled[1]._wire_stage.get() is not None
        if financial_context and (
            (
                stage == "checkpoint-insert"
                and statement.startswith("INSERT INTO wire_checkpoint_events_v2")
            )
            or (stage == "financial-outbox" and statement.startswith("INSERT INTO demo_outbox"))
        ):
            raise RuntimeError("SYNTHETIC_SQL_FAILURE")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(LocalQuotaStopped, match="COMMIT_FAILED"):
            run(coupled, prepared, response)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert response.calls == 0 and financial(coupled[0][0]) == before
    assert [r["wire"]["kind"] for r in pg(coupled)] == ["ROOT", "SESSION"]
    assert review(coupled)["financial_prepares"] == 0
    assert coupled[1]._wire_stage.get() is None


def test_partial_independent_witness_does_not_commit_financial_receipt(coupled, monkeypatch):
    import packages.persistence.demo_wire_checkpoint_candidate as module

    prepared, response = capture(coupled), reply()
    before = financial(coupled[0][0])
    original = module._append_line

    def partial(path, previous, line):
        if path.name == "wire-checkpoint-v2.log" and json.loads(line)["sequence"] == 2:
            with path.open("ab") as stream:
                stream.write(line[:7])
            raise OSError("SYNTHETIC_WITNESS_PARTIAL_WRITE")
        original(path, previous, line)

    monkeypatch.setattr(module, "_append_line", partial)
    with pytest.raises(LocalQuotaStopped):
        run(coupled, prepared, response)
    assert financial(coupled[0][0]) == before and response.calls == 0
    disk = files(coupled[5])
    with pytest.raises(LocalQuotaStopped):
        review(coupled)
    assert files(coupled[5]) == disk


def test_lease_expiry_after_file_fsync_rolls_back_financial_commit(coupled, monkeypatch):
    import packages.persistence.demo_wire_checkpoint_candidate as module

    prepared, response = capture(coupled), reply()
    before = financial(coupled[0][0])
    original = module._append_line
    future = coupled[1].now() + timedelta(hours=1)

    def expire(path, previous, line):
        original(path, previous, line)
        if path.name == "wire-checkpoint-v2.log" and json.loads(line)["sequence"] == 2:
            coupled[1].now = lambda: future

    monkeypatch.setattr(module, "_append_line", expire)
    with pytest.raises(LocalQuotaStopped, match="COMMIT_FAILED"):
        run(coupled, prepared, response)
    assert response.calls == 0 and financial(coupled[0][0]) == before
    with pytest.raises(LocalQuotaStopped):
        review(coupled)


@pytest.mark.parametrize("kind", ["wire-prefix", "witness-prefix", "pg-prefix"])
def test_single_store_rollback_is_blocked_in_fresh_readonly_review(coupled, kind):
    prepared = capture(coupled)
    wire_root = coupled[3].path.read_bytes()
    witness_path = coupled[2].witness(coupled[5])
    witness_root = witness_path.read_bytes()
    run(coupled, prepared)
    if kind == "wire-prefix":
        coupled[3].path.write_bytes(wire_root)
    elif kind == "witness-prefix":
        witness_path.write_bytes(witness_root)
    else:
        with coupled[1].engine.begin() as c:
            c.execute(
                text(
                    "ALTER TABLE wire_checkpoint_events_v2 DISABLE TRIGGER wire_checkpoint_immutable_v2"
                )
            )
            c.execute(delete(checkpoints).where(checkpoints.c.sequence > 0))
            c.execute(
                text(
                    "ALTER TABLE wire_checkpoint_events_v2 ENABLE TRIGGER wire_checkpoint_immutable_v2"
                )
            )
    disk = files(coupled[5])
    with pytest.raises(LocalQuotaStopped):
        review(coupled)
    assert files(coupled[5]) == disk


@pytest.mark.parametrize(
    "table,operation",
    [(t, op) for t in (installations, bindings, checkpoints) for op in ("update", "delete")],
)
def test_anchor_tables_are_append_only(coupled, table, operation):
    with pytest.raises(DBAPIError):
        with coupled[1].engine.begin() as c:
            c.execute(
                delete(table)
                if operation == "delete"
                else update(table).values(
                    **({"identity": "a" * 36} if table is installations else {"hash": "a" * 64})
                )
            )
    assert review(coupled)["records"] == 1


def test_privileged_rehash_of_entire_pg_anchor_still_disagrees_with_private_witness(coupled):
    from uuid import uuid4

    from packages.persistence.demo_read_audit import digest

    run(coupled, capture(coupled))
    engine = coupled[1].engine
    with engine.begin() as c:
        for table in (installations, bindings, checkpoints):
            c.execute(
                text(f"ALTER TABLE {table.name} DISABLE TRIGGER wire_checkpoint_immutable_v2")
            )
        identity = str(uuid4())
        c.execute(update(installations).values(identity=identity))
        binding = dict(c.execute(select(bindings)).mappings().one())
        binding["installation"] = identity
        binding["hash"] = digest({k: v for k, v in binding.items() if k != "hash"})
        c.execute(update(bindings).values(installation=identity, hash=binding["hash"]))
        previous = binding["hash"]
        for old in c.execute(select(checkpoints).order_by(checkpoints.c.sequence)).mappings().all():
            row = dict(old)
            row["previous_hash"] = previous
            row["hash"] = digest({k: v for k, v in row.items() if k != "hash"})
            c.execute(
                update(checkpoints)
                .where(checkpoints.c.sequence == row["sequence"])
                .values(previous_hash=previous, hash=row["hash"])
            )
            previous = row["hash"]
        for table in (installations, bindings, checkpoints):
            c.execute(text(f"ALTER TABLE {table.name} ENABLE TRIGGER wire_checkpoint_immutable_v2"))
    with pytest.raises(LocalQuotaStopped, match="WITNESS_MISMATCH"):
        review(coupled)


@pytest.mark.parametrize("kind", ["receipt-payload", "source-payload", "outbox", "current-marker"])
def test_financial_or_original_intent_tamper_is_rejected(coupled, kind):
    prepared = capture(coupled)
    run(coupled, prepared)
    row = pg(coupled)[2]
    engine = coupled[1].engine
    with engine.begin() as c:
        if kind in {"receipt-payload", "source-payload"}:
            identity = (
                row["financial_event"] if kind == "receipt-payload" else row["source"]["event_id"]
            )
            original = c.execute(
                select(financial_events.c.payload).where(financial_events.c.event_id == identity)
            ).scalar_one()
            c.execute(
                update(financial_events)
                .where(financial_events.c.event_id == identity)
                .values(payload=original + " ")
            )
        elif kind == "outbox":
            c.execute(delete(outbox).where(outbox.c.event_id == row["financial_event"]))
        else:
            state = copy.deepcopy(c.execute(select(accounts.c.state)).scalar_one())
            state["operations"].pop(PREFIX + str(prepared.operation))
            c.execute(update(accounts).values(state=state))
    with pytest.raises(LocalQuotaStopped):
        review(coupled)


def test_missing_financial_hook_cannot_silently_leave_an_unanchored_receipt(coupled, monkeypatch):
    prepared, response = capture(coupled), reply()
    monkeypatch.setattr(coupled[1], "_stage_change_audit", lambda *args: None)
    with pytest.raises(LocalQuotaStopped, match="COMMIT_FAILED"):
        run(coupled, prepared, response)
    assert response.calls == 0
    with pytest.raises(LocalQuotaStopped, match="MARKER_COVERAGE_MISMATCH"):
        review(coupled)


@pytest.mark.parametrize("change", ["unpaused", "fenced", "expired"])
def test_nonlive_or_unpaused_financial_account_denies_before_dispatch(coupled, change):
    prepared, response = capture(coupled), reply()
    with coupled[1].engine.begin() as c:
        if change == "unpaused":
            state = copy.deepcopy(c.execute(select(accounts.c.state)).scalar_one())
            state["paused"] = False
            c.execute(update(accounts).values(state=state))
        elif change == "fenced":
            c.execute(update(accounts).values(fence=accounts.c.fence + 1))
        else:
            c.execute(update(accounts).values(lease_until=coupled[1].now() - timedelta(seconds=1)))
    with pytest.raises(LocalQuotaStopped):
        run(coupled, prepared, response)
    assert response.calls == 0 and len(pg(coupled)) == 1


def test_uncertain_synthetic_outcome_preserves_committed_receipt_and_never_resumes(coupled):
    prepared = capture(coupled)
    response = SyntheticResponder(RESTResponse(200, b"{}", {}), fail=True)
    with pytest.raises(LocalQuotaStopped, match="UNCERTAIN_NO_RETRY"):
        run(coupled, prepared, response)
    report = review(coupled)
    assert report["uncertain_outcome"] and report["pending_operations"] == 1
    assert report["financial_prepares"] == 1 and response.calls == 1
    with pytest.raises(LocalQuotaStopped):
        run(coupled, prepared, response)
    assert response.calls == 1


def test_get_in_same_session_is_anchored_without_an_extra_financial_prepare(coupled):
    run(coupled, capture(coupled))
    before = financial(coupled[0][0])
    path, values = params("READ", REQUEST)
    signed = prepare_signed_query(values, api_key=KEY, secret=SECRET, timestamp_ms=18)
    prepared = coupled[4].prepare("GET", path, signed.query, signed.api_key)
    run(coupled, prepared)
    rows = pg(coupled)
    assert rows[2]["wire"]["data"]["session"] == rows[5]["wire"]["data"]["session"]
    assert rows[5]["financial_event"] is None
    assert financial(coupled[0][0]) == before and review(coupled)["financial_prepares"] == 1


def test_baseline_change_hook_failure_rolls_back_all_financial_writes(coupled):
    store = coupled[1]
    before = financial(coupled[0][0])

    def fail(*args):
        raise RuntimeError("SYNTHETIC_AUDIT_HOOK_FAILURE")

    marker = store._wire_stage.set(fail)
    try:
        with pytest.raises(RuntimeError, match="AUDIT_HOOK_FAILURE"):
            store.change(
                coupled[3].lease,
                FINANCIAL_KIND,
                lambda ledger: ledger.operations.update(TEST_ONLY="unchanged-on-failure"),
            )
    finally:
        store._wire_stage.reset(marker)
    assert financial(coupled[0][0]) == before
    assert review(coupled)["records"] == 1


def test_disabled_anchor_trigger_prevents_dispatch(coupled):
    prepared, response = capture(coupled), reply()
    with coupled[1].engine.begin() as c:
        c.execute(
            text(
                "ALTER TABLE wire_checkpoint_events_v2 DISABLE TRIGGER wire_checkpoint_immutable_v2"
            )
        )
    with pytest.raises(LocalQuotaStopped):
        run(coupled, prepared, response)
    with pytest.raises(LocalQuotaStopped, match="SCHEMA_INVALID"):
        review(coupled)
    assert response.calls == 0


def test_changed_lease_configuration_is_rejected(coupled):
    prepared, response = capture(coupled), reply()
    coupled[3].lease.fence += 1
    with pytest.raises(LocalQuotaStopped):
        run(coupled, prepared, response)
    assert response.calls == 0 and len(pg(coupled)) == 1


@pytest.mark.parametrize("rollback", [False, True])
def test_new_process_readonly_review_detects_file_prefix_rollback(coupled, rollback):
    import os
    import subprocess
    import sys

    root = coupled[3].path.read_bytes()
    run(coupled, capture(coupled))
    if rollback:
        coupled[3].path.write_bytes(root)
    with coupled[1].engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    script = """
import json, os
from pathlib import Path
from uuid import UUID
from sqlalchemy import create_engine
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_wire_checkpoint_candidate import WireCheckpointStore
engine = create_engine(os.environ["TEST_DATABASE_URL"], connect_args={"options": "-csearch_path=" + os.environ["WIRE_TEST_SCHEMA"]})
try:
    result = WireCheckpointStore(engine).review(Path(os.environ["WIRE_TEST_DIRECTORY"]), "DEMO-wire-scope", UUID(os.environ["WIRE_TEST_JOURNAL"]), "DEMO-test")
    print(json.dumps({"verified": result["pg_file_checkpoint_matches"], "resume": result["process_restart_resumption_supported"]}))
except LocalQuotaStopped:
    print(json.dumps({"blocked": True}))
finally:
    engine.dispose()
"""
    env = dict(
        os.environ,
        WIRE_TEST_SCHEMA=schema,
        WIRE_TEST_DIRECTORY=str(coupled[5]),
        WIRE_TEST_JOURNAL=str(coupled[3].journal_id),
    )
    disk = files(coupled[5])
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert json.loads(result.stdout) == (
        {"blocked": True} if rollback else {"verified": True, "resume": False}
    )
    assert files(coupled[5]) == disk
