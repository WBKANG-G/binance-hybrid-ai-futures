"""Request evidence and checkpoint are atomic; review never adopts unexplained use."""

# ruff: noqa: F811

import copy
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import DBAPIError

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import (
    AuditedDemoReadJournal,
    ReadAuditStore,
    read_bindings,
    read_events,
)
from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_runtime_read_accounting import financial, journal, settings, signed
from tests.integration.test_shared_weight_checkpoint import anchored  # noqa: F401
from tests.unit.test_quota_startup_read_only import files


@pytest.fixture
def audited(anchored):
    a = anchored
    store = ReadAuditStore(a[1].engine)
    store.provision(apply=True)
    store.bind_empty(a[0], apply=True)
    shared = AuditedDemoReadJournal(a[3], a[0].scope, a[4], anchors=a[1], audit=store, **a[5])
    return a, store, shared


def reader(a, sender=None):
    base, audit, shared = a
    return AccountedDemoReadTransport(
        settings(base),
        shared,
        enable_reads=True,
        sender=sender or (lambda *a: RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"})),
        monotonic=base[2].mono,
    )


def records(a):
    with a[1].engine.connect() as c:
        return c.execute(select(read_events).order_by(read_events.c.sequence)).mappings().all()


def test_binding_is_explicit_and_does_not_change_shared_or_financial_state(anchored):
    a = anchored
    store = ReadAuditStore(a[1].engine)
    before = files(a[3])
    fin = financial(a)
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_PROVISION"):
        store.provision()
    store.provision(apply=True)
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_BIND"):
        store.bind_empty(a[0])
    store.bind_empty(a[0], apply=True)
    store.bind_empty(a[0], apply=True)
    assert files(a[3]) == before and financial(a) == fin
    shared = AuditedDemoReadJournal(a[3], a[0].scope, a[4], anchors=a[1], audit=store, **a[5])
    assert store.review(shared)["local_read_activity_complete"]
    assert files(a[3]) == before


def test_used_unaudited_journal_cannot_be_adopted(anchored):
    a = anchored
    t = a[0].reserve(5, "ORDINARY")
    a[0].finish(t, used_weight=5, status=200)
    store = ReadAuditStore(a[1].engine)
    store.provision(apply=True)
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="USED_STORE_NOT_ADOPTED"):
        store.bind_empty(a[0], apply=True)
    assert files(a[3]) == before


def test_successful_wire_is_preceded_by_reservation_evidence_and_full_review_is_read_only(audited):
    a, audit, shared = audited
    fin = financial(a)

    def send(*args):
        rows = records(a)
        assert len(rows) == 1 and rows[0]["action"]["kind"] == "RESERVE"
        assert journal(a)[1][0][1] is None
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"})

    r = reader(audited, send)
    assert r.diagnostics()["read_audit_attached"]
    r(*signed())
    assert [x["action"]["kind"] for x in records(a)] == ["RESERVE", "FINISH"]
    before = files(a[3])
    rows = records(a)
    report = audit.review(shared)
    assert report["read_audit_chain_verified"] and report["local_read_activity_complete"]
    assert report["audit_events"] == 2 and report["shared_total_weight"] == 5
    assert report["pending_requests"] == 0 and not report["execution_approved"]
    assert files(a[3]) == before and records(a) == rows and financial(a) == fin
    # Reopened processes accept the recorded GET state, not an empty-only shortcut.
    reopened = AuditedDemoReadJournal(a[3], shared.scope, a[4], anchors=a[1], audit=audit, **a[5])
    assert audit.review(reopened) == report


def test_view_and_reopen_do_not_add_unrecorded_clock_ticks(audited):
    a, audit, shared = audited
    reader(audited)(*signed())
    a[2].seconds += 1
    before = files(a[3])
    rows = records(a)
    assert shared.view()["pressure"] == 100
    AuditedDemoReadJournal(a[3], shared.scope, a[4], anchors=a[1], audit=audit, **a[5])
    assert files(a[3]) == before and records(a) == rows


def test_pruning_completed_requests_is_explained_in_next_audit_transition(audited):
    a, audit, shared = audited
    r = reader(audited)
    r(*signed())
    a[2].seconds += 60.002
    reader(audited)(*signed())
    assert len(journal(a)[1]) == 1
    result = audit.review(shared)
    assert result["audit_events"] == 4 and result["shared_total_weight"] == 10


def test_pending_request_remains_auditable_and_never_means_ready(audited):
    a, audit, shared = audited
    shared.reserve_read("/fapi/v3/account", 5)
    a[2].seconds += 100
    result = audit.review(shared)
    assert result["pending_requests"] == 1 and not result["local_read_activity_complete"]
    assert shared.view()["pending_weight"] == 5


@pytest.mark.parametrize("kind", ["unknown", "missing", "rate429", "quote"])
def test_uncertainty_and_unobserved_quotes_are_recorded_without_fabricated_credit(audited, kind):
    a, audit, shared = audited

    def sender(*args):
        if kind == "unknown":
            raise RuntimeError("PRIVATE_NETWORK_BODY")
        return RESTResponse(
            429 if kind == "rate429" else 200,
            b"{}",
            {} if kind in ("missing", "quote") else {"x-mbx-used-weight-1m": "100"},
        )

    r = reader(audited, sender)
    if kind == "quote":
        from pydantic import SecretStr

        r("GET", "/fapi/v1/ticker/bookTicker", SecretStr("symbol=BTCUSDT"), None)
        assert audit.review(shared)["local_read_activity_complete"]
        assert shared.view()["pressure"] == 2
    else:
        with pytest.raises(LocalQuotaStopped):
            r(*signed())
        rows = records(a)
        assert len(rows) == 2 and rows[1]["state"][1][0][8] == 1
        assert rows[1]["action"]["kind"] == ("STOP" if kind == "unknown" else "FINISH")
        with pytest.raises(LocalQuotaStopped):
            audit.review(shared)
        assert journal(a)[0] == (5, 1) and journal(a)[1][0][1] is None
    assert "PRIVATE" not in str(records(a))


def test_direct_untagged_reservation_and_false_unobserved_completion_are_refused(audited):
    a, audit, shared = audited
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="ROUTE_CONTEXT"):
        shared.reserve(5, "ORDINARY")
    assert files(a[3]) == before
    token = shared.reserve_read("/fapi/v3/account", 5)
    before = files(a[3])
    rows = records(a)
    with pytest.raises(LocalQuotaStopped, match="ACTION_INVALID"):
        shared.finish_unobserved(token, status=200)
    assert files(a[3]) == before and records(a) == rows


def test_base_provider_write_after_registration_is_detected_not_silently_accepted(audited):
    a, audit, shared = audited
    t = a[0].reserve(5, "ORDINARY")
    a[0].finish(t, used_weight=5, status=200)
    before = files(a[3])
    rows = records(a)
    with pytest.raises(LocalQuotaStopped, match="UNRECORDED_CHANGE"):
        audit.review(shared)
    with pytest.raises(LocalQuotaStopped):
        reader(audited)(*signed())
    assert files(a[3]) == before and records(a) == rows


@pytest.mark.parametrize("target", ["bindings", "events"])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_audit_tables_are_append_only(audited, target, operation):
    a, audit, shared = audited
    reader(audited)(*signed())
    table = read_bindings if target == "bindings" else read_events
    with pytest.raises(DBAPIError):
        with a[1].engine.begin() as c:
            c.execute(
                delete(table)
                if operation == "delete"
                else update(table).values(scope="DEMO-replaced")
            )
    assert audit.review(shared)["read_audit_chain_verified"]


def test_post_sqlite_pg_failure_rolls_back_checkpoint_and_evidence_together(audited):
    a, audit, shared = audited
    calls = []
    r = reader(audited, lambda *args: calls.append(True))

    def fail(connection):
        raise RuntimeError("PRIVATE_PG_FAILURE")

    event.listen(a[1].engine, "commit", fail)
    try:
        with pytest.raises(LocalQuotaStopped):
            r(*signed())
    finally:
        event.remove(a[1].engine, "commit", fail)
    assert not calls and not records(a) and journal(a)[1][0][1] is None
    with pytest.raises(LocalQuotaStopped):
        audit.review(shared)


def test_audit_insert_failure_rolls_back_sqlite_without_sending(audited, monkeypatch):
    a, audit, shared = audited
    before = files(a[3])
    calls = []

    def fail(*args):
        raise RuntimeError("PRIVATE_AUDIT_FAILURE")

    monkeypatch.setattr(audit, "append", fail)
    with pytest.raises(LocalQuotaStopped):
        reader(audited, lambda *args: calls.append(True))(*signed())
    assert not calls and files(a[3]) == before and not records(a)


@pytest.mark.parametrize(
    "fault",
    [
        "undercharge",
        "pending_removed",
        "clock_backwards",
        "wrong_method",
        "identity",
        "extra_field",
        "unobserved_account",
        "bool_cost",
    ],
)
def test_full_transition_verifier_rejects_fabricated_or_lowered_evidence(audited, fault):
    from packages.persistence.demo_read_audit import validate_transition

    a, audit, shared = audited
    if fault == "bool_cost":
        from pydantic import SecretStr

        reader(audited)("GET", "/fapi/v1/time", SecretStr(""), None)
    else:
        reader(audited)(*signed())
    with a[1].engine.connect() as c:
        root = c.execute(select(read_bindings)).mappings().one()["base_state"]
    event_rows = records(a)
    before = copy.deepcopy(root)
    after = copy.deepcopy(event_rows[0]["state"])
    action = copy.deepcopy(event_rows[0]["action"])
    if fault == "undercharge":
        after[1][0][7] -= 1
        after[2][0][1] -= 1
    elif fault == "pending_removed":
        before = copy.deepcopy(event_rows[0]["state"])
        after = copy.deepcopy(event_rows[1]["state"])
        action = copy.deepcopy(event_rows[1]["action"])
        after[2] = []
    elif fault == "clock_backwards":
        after[1][0][6] = before[1][0][6] - 1
    elif fault == "wrong_method":
        action["kind"] = "POST"
    elif fault == "identity":
        after[1][0][0] = str(uuid4())
    elif fault == "extra_field":
        action["credential"] = "SYNTHETIC_PRIVATE_MARKER"
    elif fault == "bool_cost":
        before = copy.deepcopy(event_rows[0]["state"])
        after = copy.deepcopy(event_rows[1]["state"])
        action = copy.deepcopy(event_rows[1]["action"])
        action["cost"] = True
    else:
        before = copy.deepcopy(event_rows[0]["state"])
        after = copy.deepcopy(event_rows[1]["state"])
        action = copy.deepcopy(event_rows[1]["action"])
        action["observe"] = False
    with pytest.raises(LocalQuotaStopped):
        validate_transition(before, after, action)


def test_audit_capacity_reserves_space_to_finish_existing_requests(audited, monkeypatch):
    from packages.persistence import demo_read_audit as module

    a, audit, shared = audited
    monkeypatch.setattr(module, "MAX_RECORDS", 2)
    token = shared.reserve_read("/fapi/v3/account", 5)
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="COMPLETION_CREDIT_REQUIRED"):
        shared.reserve_read("/fapi/v3/account", 5)
    assert files(a[3]) == before and len(records(a)) == 1
    shared.finish(token, used_weight=5, status=200)
    assert audit.review(shared)["local_read_activity_complete"] and len(records(a)) == 2
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="RECORD_LIMIT"):
        shared.reserve_read("/fapi/v3/account", 5)
    assert files(a[3]) == before


def test_bounded_snapshot_refuses_new_rows_without_losing_existing_pending_credit(
    audited, monkeypatch
):
    from packages.persistence import demo_read_audit as module

    a, audit, shared = audited
    monkeypatch.setattr(module, "MAX_ROWS", 1)
    token = shared.reserve_read("/fapi/v3/account", 5)
    before = files(a[3])
    rows = records(a)
    with pytest.raises(LocalQuotaStopped, match="STATE_LIMIT"):
        shared.reserve_read("/fapi/v3/account", 5)
    assert files(a[3]) == before and records(a) == rows
    shared.finish(token, used_weight=5, status=200)
    assert audit.review(shared)["local_read_activity_complete"]


def test_completion_ack_failure_preserves_only_the_committed_reservation_evidence(audited):
    a, audit, shared = audited
    fired = [False]

    def fail(connection):
        # Target the write transaction containing FINISH, not the preceding
        # read-only reservation lookup transaction.
        found = connection.execute(
            select(read_events.c.event_id).where(read_events.c.action["kind"].astext == "FINISH")
        ).first()
        if found is not None:
            fired[0] = True
            raise RuntimeError("SYNTHETIC_PRIVATE_PG_ERROR")

    event.listen(a[1].engine, "commit", fail)
    try:
        with pytest.raises(LocalQuotaStopped):
            reader(audited)(*signed())
    finally:
        event.remove(a[1].engine, "commit", fail)
    assert fired[0]
    assert len(records(a)) == 1 and records(a)[0]["action"]["kind"] == "RESERVE"
    assert journal(a)[1][0][1] is not None
    with pytest.raises(LocalQuotaStopped):
        audit.review(shared)


def test_concurrent_reader_instances_keep_request_contexts_separate(audited):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    a, audit, shared = audited
    barrier = Barrier(2)

    def send(*args):
        barrier.wait(timeout=5)
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"})

    readers = [reader(audited, send) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda r: r(*signed()), readers))
    assert len(results) == 2 and len(records(a)) == 4
    assert audit.review(shared)["local_read_activity_complete"] and journal(a)[0] == (10, 0)


def test_registered_used_scope_binding_is_idempotent_without_new_clock_writes(audited):
    a, audit, shared = audited
    reader(audited)(*signed())
    a[2].seconds += 1
    before = files(a[3])
    rows = records(a)
    audit.bind_empty(a[0], apply=True)
    assert (
        files(a[3]) == before
        and records(a) == rows
        and audit.review(shared)["local_read_activity_complete"]
    )


def test_reopen_after_boot_change_preserves_every_file_and_refuses_use(audited):
    a, audit, shared = audited
    reader(audited)(*signed())
    before = files(a[3])
    rows = records(a)
    options = a[5] | {"boot": lambda: "other-boot"}
    with pytest.raises(LocalQuotaStopped, match="SHARED_CLOCK_OR_BOOT_CHANGED"):
        AuditedDemoReadJournal(a[3], shared.scope, a[4], anchors=a[1], audit=audit, **options)
    assert files(a[3]) == before and records(a) == rows


def test_self_consistent_insert_cannot_replace_the_independent_checkpoint(audited):
    from uuid import uuid5

    from sqlalchemy import insert

    from packages.persistence.demo_read_audit import digest

    a, audit, shared = audited
    with a[1].engine.begin() as c:
        root = c.execute(select(read_bindings)).mappings().one()
        token = str(uuid4())
        action = dict(
            kind="RESERVE",
            path="/fapi/v3/account",
            cost=5,
            token=token,
            status=None,
            weight=None,
            sent=False,
            observe=True,
        )
        row = dict(
            scope=shared.scope,
            sequence=0,
            event_id=str(uuid5(shared.identity, "demo-read-audit:0")),
            revision=root["base_revision"] + 1,
            checkpoint_digest=root["base_digest"],
            token=token,
            action=action,
            state=root["base_state"],
            previous_hash=root["root_hash"],
        )
        row["hash"] = digest(row)
        c.execute(insert(read_events).values(**row))
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="UNRECORDED_CHANGE"):
        audit.review(shared)
    assert files(a[3]) == before


def _process_read(url, schema, directory, scope, identity, seconds, config, barrier, queue):
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy import create_engine

    from integrations.binance_usdm.demo import DemoSettings
    from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore
    from tests.unit.test_durable_order_quota import QuotaClock

    engine = create_engine(url, connect_args={"options": "-csearch_path=" + schema})
    try:
        anchor = SharedWeightAnchorStore(engine)
        audit = ReadAuditStore(engine)
        clock = QuotaClock()
        clock.seconds = seconds
        shared = AuditedDemoReadJournal(
            Path(directory),
            scope,
            UUID(identity),
            anchors=anchor,
            audit=audit,
            monotonic=clock.mono,
            wall_clock=clock.utc,
            boot=lambda: "test-boot",
        )

        def sender(*args):
            barrier.wait(timeout=10)
            return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"})

        r = AccountedDemoReadTransport(
            DemoSettings.model_validate(config),
            shared,
            enable_reads=True,
            sender=sender,
            monotonic=clock.mono,
        )
        r(*signed())
        queue.put("ok")
    except Exception as error:
        queue.put(type(error).__name__)
    finally:
        engine.dispose()


def test_independent_processes_append_to_the_same_request_chain(audited):
    import multiprocessing

    a, audit, shared = audited
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    with a[1].engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    assert schema.startswith("phase4_") and schema.replace("_", "").isalnum()
    args = (
        a[1].engine.url.render_as_string(hide_password=False),
        schema,
        str(a[3]),
        shared.scope,
        str(shared.identity),
        a[2].seconds,
        settings(a).model_dump(mode="json"),
        barrier,
        queue,
    )
    workers = [ctx.Process(target=_process_read, args=args) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(15)
        assert all(worker.exitcode == 0 for worker in workers)
        assert [queue.get(timeout=5) for _ in workers] == ["ok", "ok"]
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
    report = audit.review(shared)
    assert report["audit_events"] == 4 and report["local_read_activity_complete"]
    assert journal(a)[0] == (10, 0)


def test_pre_send_deadline_refusal_is_audited_without_a_fabricated_http_response(
    audited, monkeypatch
):
    a, audit, shared = audited
    reserve = shared.reserve_read

    def delayed(path, cost):
        token = reserve(path, cost)
        a[2].seconds += 1
        return token

    monkeypatch.setattr(shared, "reserve_read", delayed)
    r = AccountedDemoReadTransport(
        settings(a),
        shared,
        enable_reads=True,
        max_seconds=1,
        sender=lambda *args: pytest.fail("expired deadline forbids HTTP"),
        monotonic=a[2].mono,
    )
    with pytest.raises(LocalQuotaStopped, match="DEADLINE"):
        r(*signed())
    report = audit.review(shared)
    assert report["successful_reads"] == 0 and report["known_unsent_requests"] == 1
    assert report["pending_requests"] == 0 and report["local_read_activity_complete"]
    assert report["shared_total_weight"] == 5 and r.diagnostics()["http_attempts"] == 0


def test_completed_http_failure_is_distinct_from_success_and_unsent(audited):
    a, audit, shared = audited
    r = reader(
        audited,
        lambda *args: RESTResponse(503, b"SYNTHETIC_PRIVATE_BODY", {"x-mbx-used-weight-1m": "100"}),
    )
    with pytest.raises(LocalQuotaStopped, match="HTTP_ERROR_NO_RETRY"):
        r(*signed())
    report = audit.review(shared)
    assert report["failed_response_reads"] == 1 and report["successful_reads"] == 0
    assert report["known_unsent_requests"] == 0 and report["pending_requests"] == 0
    assert not report["financial_proof_verified"] and not report["execution_approved"]
    assert "SYNTHETIC_PRIVATE" not in str(records(a))
