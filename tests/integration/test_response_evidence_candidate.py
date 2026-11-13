"""Real PG/SQLite/file commits; synthetic responses only, never real exchange access."""

# ruff: noqa: F401,F811
import copy
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import DBAPIError

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import AuditedDemoReadJournal, read_events
from packages.persistence.demo_response_evidence import (
    EvidenceReadJournal,
    ResponseEvidenceStore,
    bindings,
    events,
)
from services.execution.demo_response_evidence_transport import EvidenceReadTransport
from tests.integration.test_demo_read_audit import audited, reader, records
from tests.integration.test_runtime_read_accounting import financial, journal, settings, signed
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_quota_startup_read_only import files


@pytest.fixture
def evidenced(audited):
    a, audit, old = audited
    store = ResponseEvidenceStore(a[1].engine)
    store.provision(apply=True)
    store.bind(old, "DEMO-test", apply=True)
    shared = EvidenceReadJournal(
        a[3],
        old.scope,
        a[4],
        anchors=a[1],
        audit=audit,
        evidence=store,
        account="DEMO-test",
        enable_evidence_binding=True,
        read_only=False,
        **a[5],
    )
    return a, audit, store, shared


def transport(case, sender=None, **kw):
    a, _, _, shared = case
    return EvidenceReadTransport(
        settings(a),
        shared,
        enable_evidence_reads=True,
        monotonic=a[2].mono,
        sender=sender
        or (lambda *args: RESTResponse(200, b'{"ok":true}', {"x-mbx-used-weight-1m": "100"})),
        **kw,
    )


def review(case, **kw):
    a, _, store, shared = case
    return store.review(a[3], shared.scope, a[4], "DEMO-test", **kw)


def evidence_rows(case):
    with case[0][1].engine.connect() as c:
        return c.execute(select(events).order_by(events.c.sequence)).mappings().all()


def test_response_hash_is_in_same_transaction_as_finish_and_reopens_readonly(evidenced):
    a, audit, store, shared = evidenced
    before = financial(a)
    seen = []

    def sender(*args):
        rows = evidence_rows(evidenced)
        assert len(rows) == 1 and rows[0]["phase"] == "RESERVE" and rows[0]["response"] is None
        assert records(a)[-1]["action"]["kind"] == "RESERVE"
        seen.append(True)
        return RESTResponse(200, b'{"ok":true}', {"x-mbx-used-weight-1m": "100"})

    result = transport(evidenced, sender)(*signed())
    rows = evidence_rows(evidenced)
    assert len(seen) == 1 and [r["phase"] for r in rows] == ["RESERVE", "FINISH"]
    assert rows[-1]["response"]["body_sha256"] == hashlib.sha256(result.body).hexdigest()
    assert rows[-1]["audit_hash"] == records(a)[-1]["hash"]
    assert rows[-1]["checkpoint_digest"] == records(a)[-1]["checkpoint_digest"]
    assert financial(a) == before
    disk = files(a[3])
    old = records(a)
    report = review(evidenced, sequence=1, body=result.body)
    assert report["response_body_digest_matches"] and report["pending_requests"] == 0
    assert not report["archive_authorized"] and not report["remote_account_identity_verified"]
    assert not report["response_body_artifact_stored"] and not report["execution_approved"]
    EvidenceReadJournal(
        a[3],
        shared.scope,
        a[4],
        anchors=a[1],
        audit=audit,
        evidence=store,
        account="DEMO-test",
        enable_evidence_binding=True,
        **a[5],
    )
    assert files(a[3]) == disk and records(a) == old and financial(a) == before
    with pytest.raises(LocalQuotaStopped, match="BODY_MISMATCH"):
        review(evidenced, sequence=1, body=b'{"ok":false}')


def test_default_denial_precedes_access_to_settings_provider_or_files():
    with pytest.raises(LocalQuotaStopped, match="TRANSPORT_DISABLED"):
        EvidenceReadTransport(None, None)
    with pytest.raises(LocalQuotaStopped, match="EVIDENCE_DISABLED"):
        EvidenceReadJournal(None, evidence=None, account="DEMO-test")


def test_existing_audited_history_is_bound_without_backfill_or_old_changes(audited):
    a, audit, old = audited
    reader(audited)(*signed())
    before = records(a)
    disk = files(a[3])
    fin = financial(a)
    store = ResponseEvidenceStore(a[1].engine)
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_PROVISION"):
        store.provision()
    store.provision(apply=True)
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_BIND"):
        store.bind(old, "DEMO-test")
    store.bind(old, "DEMO-test", apply=True)
    report = store.review(a[3], old.scope, a[4], "DEMO-test")
    assert report["base_audit_sequence"] == 2 and report["evidence_events"] == 0
    assert records(a) == before and financial(a) == fin
    new = files(a[3])
    assert all(new[k] == v for k, v in disk.items()) and len(new) == len(disk) + 1
    store.bind(old, "DEMO-test", apply=True)
    assert files(a[3]) == new


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_evidence_rows_are_append_only(evidenced, operation):
    transport(evidenced)(*signed())
    engine = evidenced[0][1].engine
    with pytest.raises(DBAPIError):
        with engine.begin() as c:
            c.execute(
                update(events).values(response={}) if operation == "update" else delete(events)
            )
    assert review(evidenced)["evidence_events"] == 2


def test_keys_signature_and_body_are_not_persisted(evidenced):
    body = b"PRIVATE_RESPONSE_AND_KEY_MUST_NOT_BE_STORED"
    r = transport(
        evidenced,
        lambda *args: RESTResponse(
            200, body, {"x-mbx-used-weight-1m": "100", "set-cookie": "PRIVATE"}
        ),
    )
    r(*signed())
    value = str(evidence_rows(evidenced))
    assert "PRIVATE" not in value and "SYNTHETIC_KEY" not in value and "signature" not in value
    assert review(evidenced, sequence=1, body=body)["response_body_digest_matches"]


@pytest.mark.parametrize("kind", ["network", "missing-weight", "429", "http400"])
def test_failed_response_is_not_success_or_an_archive_certificate(evidenced, kind):
    seen = []

    def sender(*args):
        seen.append(True)
        if kind == "network":
            raise OSError("PRIVATE_IO_DETAIL")
        return RESTResponse(
            429 if kind == "429" else 400 if kind == "http400" else 200,
            b'{"code":-2013}',
            {} if kind == "missing-weight" else {"x-mbx-used-weight-1m": "100"},
        )

    r = transport(evidenced, sender)
    with pytest.raises(LocalQuotaStopped):
        r(*signed())
    assert len(seen) == 1
    report = review(evidenced)
    assert not report["archive_authorized"] and not report["execution_approved"]
    assert "PRIVATE_IO_DETAIL" not in str(evidence_rows(evidenced))
    if kind == "network":
        assert report["pending_requests"] == 1 and report["shared_stopped"]
        assert evidence_rows(evidenced)[-1]["response_state"] == "OUTCOME_UNCERTAIN"
    else:
        assert evidence_rows(evidenced)[-1]["response_state"] == "OBSERVED"
    with pytest.raises(LocalQuotaStopped):
        r(*signed())
    assert len(seen) == 1


def test_legacy_writer_after_binding_creates_a_gap_instead_of_being_backfilled(evidenced):
    a, audit, store, shared = evidenced
    old = AuditedDemoReadJournal(a[3], shared.scope, a[4], anchors=a[1], audit=audit, **a[5])
    from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport

    AccountedDemoReadTransport(
        settings(a),
        old,
        enable_reads=True,
        monotonic=a[2].mono,
        sender=lambda *args: RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"}),
    )(*signed())
    with pytest.raises(LocalQuotaStopped, match="COVERAGE_GAP"):
        review(evidenced)


def test_witness_tamper_blocks_even_when_pg_rows_are_intact(evidenced):
    transport(evidenced)(*signed())
    a, _, store, shared = evidenced
    p = store.witness(a[3], shared.scope)
    p.write_bytes(p.read_bytes() + b"{}\n")
    with pytest.raises(LocalQuotaStopped, match="WITNESS_MISMATCH"):
        review(evidenced)


def test_failure_in_evidence_insert_rolls_back_old_finish_checkpoint_and_sqlite(evidenced):
    a, _, _, _ = evidenced
    seen = []

    def fail(c, cursor, statement, params, context, many):
        if statement.startswith("INSERT INTO read_response_evidence_events_v2") and len(seen) == 1:
            raise RuntimeError("SYNTHETIC_INSERT_FAILURE")

    def send(*args):
        seen.append(True)
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"})

    event.listen(a[1].engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(LocalQuotaStopped):
            transport(evidenced, send)(*signed())
    finally:
        event.remove(a[1].engine, "before_cursor_execute", fail)
    assert len(seen) == 1
    assert [r["phase"] for r in evidence_rows(evidenced)] == ["RESERVE"]
    assert [r["action"]["kind"] for r in records(a)] == ["RESERVE"]
    assert journal(a)[1][0][1] is None


@pytest.mark.parametrize("phase", ["RESERVE", "FINISH"])
def test_pg_commit_gap_never_returns_response_or_allows_reopen(evidenced, phase):
    a, _, _, _ = evidenced
    seen = []
    r = transport(
        evidenced,
        lambda *args: (
            seen.append(True) or RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"})
        ),
    )
    before = financial(a)

    def fail(connection):
        if connection.execute(select(events.c.sequence).where(events.c.phase == phase)).first():
            raise RuntimeError("SYNTHETIC_PG_COMMIT_FAILURE")

    event.listen(a[1].engine, "commit", fail)
    try:
        with pytest.raises(LocalQuotaStopped):
            r(*signed())
    finally:
        event.remove(a[1].engine, "commit", fail)
    assert len(seen) == (phase == "FINISH")
    assert [row["phase"] for row in evidence_rows(evidenced)] == (
        ["RESERVE"] if phase == "FINISH" else []
    )
    with pytest.raises(LocalQuotaStopped):
        review(evidenced)
    with pytest.raises(LocalQuotaStopped):
        r(*signed())
    assert len(seen) == (phase == "FINISH") and financial(a) == before


@pytest.mark.parametrize("phase", ["RESERVE", "FINISH"])
def test_partial_witness_write_is_not_repaired_or_accepted(evidenced, monkeypatch, phase):
    import packages.persistence.demo_response_evidence as module

    a, _, _, _ = evidenced
    original = module._append_line
    seen = []
    r = transport(
        evidenced,
        lambda *args: (
            seen.append(True) or RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"})
        ),
    )

    def fail(path, previous, line):
        if (phase == "FINISH") == bool(seen):
            with path.open("ab") as f:
                f.write(line[:7])
                f.flush()
            raise OSError("SYNTHETIC_PARTIAL_FILE_FAILURE")
        original(path, previous, line)

    monkeypatch.setattr(module, "_append_line", fail)
    with pytest.raises(LocalQuotaStopped):
        r(*signed())
    assert len(seen) == (phase == "FINISH")
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="WITNESS_MISMATCH"):
        review(evidenced)
    assert files(a[3]) == before
    assert [row["phase"] for row in evidence_rows(evidenced)] == (
        ["RESERVE"] if phase == "FINISH" else []
    )


def test_rehashed_privileged_pg_body_tamper_still_disagrees_with_witness(evidenced):
    from packages.persistence.demo_response_evidence import _body, digest

    transport(evidenced)(*signed())
    a, _, _, _ = evidenced
    row = dict(evidence_rows(evidenced)[-1])
    row["response"] = dict(row["response"], body_sha256=hashlib.sha256(b"forged").hexdigest())
    row["hash"] = digest(_body(row))
    with a[1].engine.begin() as c:
        c.execute(
            text(
                "ALTER TABLE read_response_evidence_events_v2 DISABLE TRIGGER response_evidence_immutable_v2"
            )
        )
        c.execute(
            update(events)
            .where(events.c.sequence == row["sequence"])
            .values(response=row["response"], hash=row["hash"])
        )
        c.execute(
            text(
                "ALTER TABLE read_response_evidence_events_v2 ENABLE TRIGGER response_evidence_immutable_v2"
            )
        )
    with pytest.raises(LocalQuotaStopped, match="WITNESS_MISMATCH"):
        review(evidenced)


def test_account_label_cannot_be_transplanted_or_mutated(evidenced):
    a, _, store, shared = evidenced
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="BINDING_INVALID"):
        store.review(a[3], shared.scope, a[4], "DEMO-other")
    r = transport(evidenced)
    shared.account = "DEMO-other"
    with pytest.raises(LocalQuotaStopped, match="PROVIDER_CHANGED"):
        r(*signed())
    assert files(a[3]) == before and not evidence_rows(evidenced)


def test_mutating_method_is_rejected_before_reservation_or_wire(evidenced):
    seen = []
    r = transport(evidenced, lambda *args: seen.append(True))
    method, path, query, key = signed()
    with pytest.raises(LocalQuotaStopped, match="GET_ONLY"):
        r("POST", path, query, key)
    assert not seen and not evidence_rows(evidenced)


def test_untagged_direct_reservation_is_rolled_back(evidenced):
    a, _, _, shared = evidenced
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="CAPTURE_REQUIRED"):
        shared.reserve_read("/fapi/v3/account", 5)
    assert files(a[3]) == before and not evidence_rows(evidenced)


def test_reservation_leaves_room_for_completion_evidence(evidenced, monkeypatch):
    import packages.persistence.demo_response_evidence as module

    monkeypatch.setattr(module, "MAX_RECORDS", 2)
    seen = []
    r = transport(
        evidenced,
        lambda *args: (
            seen.append(True) or RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"})
        ),
    )
    r(*signed())
    assert len(seen) == 1 and review(evidenced)["pending_requests"] == 0
    before = files(evidenced[0][3])
    with pytest.raises(LocalQuotaStopped, match="CAPACITY_EXHAUSTED"):
        r(*signed())
    assert len(seen) == 1 and files(evidenced[0][3]) == before
    assert review(evidenced)["evidence_events"] == 2


def test_pending_committed_reservation_blocks_new_transport(evidenced):
    a, _, _, _ = evidenced

    def fail(c, cursor, statement, params, context, many):
        if statement.startswith("INSERT INTO read_response_evidence_events_v2") and len(records(a)):
            raise RuntimeError("SYNTHETIC_FINISH_INSERT_FAILURE")

    r = transport(evidenced)
    event.listen(a[1].engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(LocalQuotaStopped):
            r(*signed())
    finally:
        event.remove(a[1].engine, "before_cursor_execute", fail)
    assert review(evidenced)["pending_requests"] == 1
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="UNFINISHED_REQUESTS"):
        transport(evidenced)
    assert files(a[3]) == before


def test_disabled_immutability_trigger_is_rejected_before_wire(evidenced):
    a, _, _, _ = evidenced
    with a[1].engine.begin() as c:
        c.execute(
            text(
                "ALTER TABLE read_response_evidence_events_v2 DISABLE TRIGGER response_evidence_immutable_v2"
            )
        )
    with pytest.raises(LocalQuotaStopped, match="SCHEMA_INVALID"):
        review(evidenced)
    with pytest.raises(LocalQuotaStopped):
        transport(evidenced)
    assert not evidence_rows(evidenced)
