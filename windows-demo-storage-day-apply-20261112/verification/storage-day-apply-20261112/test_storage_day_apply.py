"""Fenced day12 transition preserves all prior financial/storage receipt hashes."""

# ruff: noqa: F811
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from services.risk.paper import RiskRejected

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "preview_fixture", BASE / "storage-day-preview-20261112/test_storage_day_preview.py"
)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
original_setup = previous.setup
closed = previous.closed
demo_system = previous.demo_system
applied = previous.applied
configured = previous.configured
spec = importlib.util.spec_from_file_location(
    "storage_day_adoption", Path(__file__).with_name("adopt_storage_day_20261112.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def setup(original_setup):
    # The older day fixture deliberately uses a minimal closed order. Supply the
    # archived pre-close shape required by the production readiness parser before
    # building the day11/storage/day12 chain; rebuild only this synthetic day9 hash.
    store = original_setup[0]
    old = previous.previous.prior.m
    lease = store.acquire("DEMO-test", uuid4())
    try:

        def enrich(ledger):
            order = ledger.orders[m.INCIDENT]
            order.algo_id, order.tp_algo_id = "synthetic-sl", "synthetic-tp"
            archived = order.model_dump(mode="json")
            archived.update(open_quantity="0.0007", position_state="OPEN_UNPROTECTED")
            raw = json.loads(ledger.operations[m.RECEIPT])
            raw["original_order"] = archived
            ledger.operations[m.RECEIPT] = json.dumps(raw)
            record = json.loads(ledger.operations[old.APPLY_KEY])
            record["preserved_ledger_sha256"] = old.preserved(ledger)
            ledger.operations[old.APPLY_KEY] = json.dumps(record)
            assert old.recorded(ledger)

        store.change(lease, "SYNTHETIC_READINESS_ARCHIVE", enrich)
    finally:
        store.release(lease)
    return original_setup


@pytest.fixture
def adoption(configured, monkeypatch):
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(m.r, "ACCOUNT", "DEMO-test")
    return configured


def run(a, **kw):
    state, client, monitor, *_ = a
    return m.adopt_storage_day(
        state[0],
        state[1],
        client,
        monitor,
        attest=True,
        attested_day="2026-11-12",
        accept_operator_evidence=True,
        parent=state[2],
        clocks=state[3],
        **kw,
    )


def post(a):
    state = a[0]
    return m.postcheck_day12(state[0], state[1], parent=state[2], clocks=state[3])


def test_apply_extends_receipt_chain_without_rewriting_old_receipts_or_storage(adoption):
    a, _, monitor, _, calls = adoption
    before = a[0].read("DEMO-test")
    files = previous.previous.disk(a[2])
    anchors = m.checkpoint_token(a[1].engine)
    with a[0].engine.connect() as c:
        old_events = list(c.execute(select(m.r.events)).mappings())
    result = run(adoption)
    after = a[0].read("DEMO-test")
    assert result["day_kst"] == "2026-11-12" and result["period_keys_match"]
    assert result["daily_base_usdt"] == "4998.74183608" and result["database_changed"]
    assert not result["execution_approved"] and result["leader_released"]
    assert result["http_attempts"] == len(calls) == len(monitor.records) == 30
    assert all(call[0] == "GET" for call in calls)
    prior, adopted = m.projected_prior(after)
    assert adopted and prior == before
    assert m.day12_preserved(after) == m.day12_preserved(before)
    assert set(after.operations) - set(before.operations) == {m.DAY12_KEY}
    assert set(after.attested_day_baselines) - set(before.attested_day_baselines) == {m.DAY12_KEY}
    assert all(after.operations[k] == v for k, v in before.operations.items())
    assert previous.previous.disk(a[2]) == files and m.checkpoint_token(a[1].engine) == anchors
    with a[0].engine.connect() as c:
        now_events = {
            row["event_id"]: dict(row) for row in c.execute(select(m.r.events)).mappings()
        }
    assert all(now_events[row["event_id"]] == dict(row) for row in old_events)
    rows = previous.previous.rows(a[0])
    calls_before = len(calls)
    assert not run(adoption)["database_changed"]
    assert previous.previous.rows(a[0]) == rows and len(calls) == calls_before
    assert post(adoption)["period_keys_match"]
    from services.execution.readiness_closed_history import readiness_view

    view, omitted = readiness_view(adoption[1].settings, after, a[0].now())
    assert omitted == 1 and m.INCIDENT not in view.orders
    assert a[0].read("DEMO-test") == after


@pytest.mark.parametrize("kind", ["attest", "accept", "date"])
def test_required_application_evidence_flags_are_not_inferred(adoption, kind):
    state, client, monitor, _, calls = adoption
    flags = {"attest": True, "attested_day": "2026-11-12", "accept_operator_evidence": True}
    flags[
        {"attest": "attest", "accept": "accept_operator_evidence", "date": "attested_day"}[kind]
    ] = False if kind != "date" else "2026-11-13"
    before = previous.previous.rows(state[0])
    files = previous.previous.disk(state[2])
    with pytest.raises(m.Blocked):
        m.adopt_storage_day(
            state[0], state[1], client, monitor, parent=state[2], clocks=state[3], **flags
        )
    assert (
        not calls
        and previous.previous.rows(state[0]) == before
        and previous.previous.disk(state[2]) == files
    )


@pytest.mark.parametrize(
    "fault", ["open_regular", "open_algo", "hedge_mode", "late_second_pass", "non_usdt_fee"]
)
def test_failed_signed_precheck_does_not_acquire_lease_or_apply(adoption, fault):
    state, _, _, options, calls = adoption
    options["response_fault"] = fault
    before = previous.previous.rows(state[0])
    files = previous.previous.disk(state[2])
    with pytest.raises(m.Blocked):
        run(adoption)
    assert previous.previous.rows(state[0]) == before and previous.previous.disk(state[2]) == files
    assert state[0].read("DEMO-test").daily_key == "2026-11-11"


@pytest.mark.parametrize("fault", ["stale", "midnight", "exposure"])
def test_final_fenced_check_failure_releases_lease_and_preserves_baseline(
    adoption, monkeypatch, fault
):
    state = adoption[0]
    before = state[0].read("DEMO-test")
    files = previous.previous.disk(state[2])
    original = m.read_state
    count = [0]

    def changed(client, ledger):
        count[0] += 1
        result = original(client, ledger)
        if count[0] == 3:
            if fault == "stale":
                state[4](31)
            elif fault == "midnight":
                state[4](86400)
            else:
                raise m.Blocked("CURRENT_EXPOSURE_OR_OPEN_ORDER")
        return result

    monkeypatch.setattr(m, "read_state", changed)
    with pytest.raises((m.Blocked, RiskRejected)):
        run(adoption)
    assert state[0].read("DEMO-test") == before and previous.previous.disk(state[2]) == files
    with state[0].engine.connect() as c:
        assert c.execute(select(m.r.accounts.c.owner)).scalar_one() is None


@pytest.mark.parametrize("part", ["record", "typed_receipt", "old_receipt", "storage_binding"])
def test_postcheck_refuses_tampered_chain_without_rewriting_it(adoption, part):
    run(adoption)
    ledger = adoption[0][0].read("DEMO-test")
    if part == "record":
        record = json.loads(ledger.operations[m.DAY12_KEY])
        record["prior_full_ledger_sha256"] = "0" * 64
        ledger.operations[m.DAY12_KEY] = json.dumps(record)
    elif part == "typed_receipt":
        ledger.attested_day_baselines[m.DAY12_KEY].signed_evidence_hash = "x"
    elif part == "old_receipt":
        ledger.operations[m.r.DAY_KEY] = "{}"
    else:
        ledger.operations[m.r.SHARED_KEY] = "{}"
    with pytest.raises((m.Blocked, m.r.Blocked)):
        m.projected_prior(ledger)


def test_existing_leader_is_not_replaced(adoption):
    state = adoption[0]
    lease = state[0].acquire("DEMO-test", uuid4())
    before = previous.previous.rows(state[0])
    files = previous.previous.disk(state[2])
    try:
        with pytest.raises(m.Blocked, match="ACTIVE_OWNER_BLOCKED"):
            run(adoption)
        assert (
            previous.previous.rows(state[0]) == before and previous.previous.disk(state[2]) == files
        )
    finally:
        state[0].release(lease)


def test_recovered_clock_audit_and_checkpoint_survive_day12_adoption(adoption, monkeypatch):
    from dataclasses import replace

    spec = importlib.util.spec_from_file_location(
        "clock_recovery", BASE / "runtime-clock-recovery-20261112/recover_unused_shared_clock.py"
    )
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    monkeypatch.setattr(recovery.r, "ACCOUNT", "DEMO-test")
    a, old_client, old_monitor, _, calls = adoption
    clocks = replace(a[3], boot=lambda: "00000000-0000-0000-0000-000000000002")
    recovery.recover(a[0], a[1], parent=a[2], clocks=clocks)
    a[4](70.002)
    files = previous.previous.disk(a[2])
    anchors = m.checkpoint_token(a[1].engine)
    route = a[5][7]
    monitor = m.Monitor(route, clock=old_monitor.clock)
    client = m.DayPreviewClient(
        old_client.settings, old_client.credentials, send=monitor, clock_ms=old_client.clock_ms
    )
    monitor.server_ms = lambda: client.clock_ms() + client.offset
    result = m.adopt_storage_day(
        a[0],
        a[1],
        client,
        monitor,
        attest=True,
        attested_day="2026-11-12",
        accept_operator_evidence=True,
        parent=a[2],
        clocks=clocks,
    )
    assert result["clock_recovery_files_preserved"] and result["period_keys_match"]
    assert previous.previous.disk(a[2]) == files and m.checkpoint_token(a[1].engine) == anchors
    assert any("clock-recovery-" in path and path.endswith("original.sqlite3") for path in files)


def test_postcheck_after_next_midnight_reports_mismatch_without_reapplying(adoption):
    run(adoption)
    a = adoption[0]
    before = previous.previous.rows(a[0])
    files = previous.previous.disk(a[2])
    calls = len(adoption[4])
    a[4](86400)
    report = post(adoption)
    assert report["day_kst"] == "2026-11-12" and not report["period_keys_match"]
    assert not run(adoption)["database_changed"]
    assert (
        len(adoption[4]) == calls
        and previous.previous.rows(a[0]) == before
        and previous.previous.disk(a[2]) == files
    )


def test_commit_ack_failure_is_resolved_by_receipt_without_a_second_transition(
    adoption, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    a = adoption[0]
    original = a[0].change
    progress = {}
    calls = adoption[4]

    def lost(lease, kind, callback):
        result = original(lease, kind, callback)
        if kind == "DEMO_ATTESTED_DAY12_WITH_PRESERVED_STORAGE_CHAIN":
            raise OperationalError("PRIVATE_DB_RESPONSE", {}, ValueError("PRIVATE_DETAIL"))
        return result

    monkeypatch.setattr(a[0], "change", lost)
    with pytest.raises(OperationalError):
        run(adoption, progress=progress)
    assert progress["application_may_have_completed"]
    assert post(adoption)["period_keys_match"]
    before = previous.previous.rows(a[0])
    count = len(calls)
    assert not run(adoption)["database_changed"]
    assert len(calls) == count and previous.previous.rows(a[0]) == before


def test_apply_holds_shared_locks_on_both_storage_lock_files(adoption, monkeypatch):
    import fcntl
    import os

    a = adoption[0]
    original = a[0].change
    checked = []

    def locked(lease, kind, callback):
        if kind == "DEMO_ATTESTED_DAY12_WITH_PRESERVED_STORAGE_CHAIN":
            root = a[2] / m.r.STORAGE_DIRECTORY
            for path in root.glob("*/*.lock"):
                fd = os.open(path, os.O_RDONLY)
                try:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    checked.append(path)
                finally:
                    os.close(fd)
        return original(lease, kind, callback)

    monkeypatch.setattr(a[0], "change", locked)
    run(adoption)
    assert len(checked) == 2
