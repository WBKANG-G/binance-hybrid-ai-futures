"""Copy-only stale-day evidence setup; real PG/SQLite, synthetic GETs only."""

# ruff: noqa: F401,F811
import pytest
from sqlalchemy import event, inspect, select, text, update

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_response_evidence import (
    EvidenceReadJournal,
    ResponseEvidenceStore,
    bindings,
    events,
)
from services.execution.demo_copy_baseline_evidence import (
    prepare_copy_baseline_evidence,
    review_copy_baseline_requirements,
)
from services.execution.demo_evidence_snapshot import evidence_snapshot
from services.execution.demo_response_evidence_transport import EvidenceReadTransport
from tests.integration.test_demo_read_audit import reader, records
from tests.integration.test_read_audit_bootstrap import ready
from tests.integration.test_response_evidence_bootstrap import (
    call as operational_setup,
)
from tests.integration.test_response_evidence_bootstrap import (
    clock,
    setup_ready,
    shared,
)
from tests.integration.test_runtime_read_accounting import financial, settings, signed
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system


@pytest.fixture
def stale_copy(setup_ready):
    a = setup_ready
    a[2].seconds += 6 * 86400  # Nov 15, same KST week as Nov 13.
    with a[1].engine.begin() as c:
        ledger = c.execute(select(accounts.c.state)).scalar_one()
        ledger["daily_key"] = "2026-11-13"
        ledger["weekly_key"] = "2026-11-09"
        ledger["requests"] = {"a": "UNKNOWN", "b": "UNKNOWN", "c": "UNKNOWN", "d": "SUBMITTING"}
        c.execute(update(accounts).values(state=ledger))
    audit, old = shared(a)
    reader((a, audit, old))(*signed())
    return a


def snap(a):
    return evidence_snapshot(a[1].engine, a[3])


def call(a, *, plan=False, **kw):
    fn = review_copy_baseline_requirements if plan else prepare_copy_baseline_evidence
    return fn(
        a[1].engine,
        "DEMO-test",
        a[3],
        a[0].scope,
        a[4],
        a[1].installation_id(),
        kw.pop("pin", snap(a)["sha256"]),
        copy_only=True,
        clocks=kw.pop("clocks", clock(a)),
        **kw,
    )


def test_missing_copy_context_and_reader_stop_precede_any_access():
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_COPY_CONTEXT"):
        prepare_copy_baseline_evidence(None, None, None, None, None, None, None)
    with pytest.raises(LocalQuotaStopped, match="READERS_STOPPED_REQUIRED"):
        prepare_copy_baseline_evidence(
            None, "DEMO-test", None, None, None, None, "0" * 64, copy_only=True, apply=True
        )


def test_same_week_is_day_only_and_plan_never_accepts_an_amount_or_attestation(stale_copy):
    a = stale_copy
    before = snap(a)
    result = call(a, plan=True)
    assert result["transition_kind"] == "DAY_ONLY" and not result["weekly_change_proposed"]
    assert result["stored_day_kst"] == "2026-11-13" and result["current_day_kst"] == "2026-11-15"
    assert not result["baseline_amount_proposed"] and not result["old_amount_reused"]
    assert not result["baseline_adoption_approved"] and not result["operator_attestation_received"]
    assert result["historical_unresolved_requests"] == 4 and len(result["required_evidence"]) == 8
    assert snap(a) == before
    assert call(a)["evidence_schema_required"] and snap(a) == before


def test_week_rollover_requires_separate_weekly_review(stale_copy):
    a = stale_copy
    a[2].seconds += 86400
    before = snap(a)
    result = call(a, plan=True)
    assert (
        result["transition_kind"] == "DAY_AND_WEEK_SEPARATE_REVIEW"
        and result["weekly_change_proposed"]
    )
    assert snap(a) == before


def test_copy_setup_preserves_financial_state_and_never_weakens_operational_period_guard(
    stale_copy,
):
    a = stale_copy
    before = snap(a)
    fin = financial(a)
    history = records(a)
    with pytest.raises(LocalQuotaStopped, match="PERIOD_MISMATCH"):
        operational_setup(a, apply=True, readers_stopped=True)
    result = call(a, apply=True, readers_stopped=True)
    assert result["evidence_schema_created"] and result["evidence_binding_created"]
    assert not result["evidence_reads_enabled"] and not result["baseline_adoption_approved"]
    assert financial(a) == fin and records(a) == history
    after = snap(a)
    assert all(after["tables"][n] == v for n, v in before["tables"].items())
    assert all(after["files"][n] == v for n, v in before["files"].items())
    with pytest.raises(LocalQuotaStopped, match="PERIOD_MISMATCH"):
        operational_setup(a, apply=True, readers_stopped=True)
    assert snap(a) == after
    again = call(a, apply=True, readers_stopped=True)
    assert not again["evidence_schema_created"] and not again["evidence_binding_created"]
    assert snap(a) == after


def test_synthetic_body_link_does_not_authorize_a_baseline(stale_copy):
    a = stale_copy
    call(a, apply=True, readers_stopped=True)
    audit, _ = shared(a)
    store = ResponseEvidenceStore(a[1].engine)
    journal = EvidenceReadJournal(
        a[3],
        a[0].scope,
        a[4],
        anchors=a[1],
        audit=audit,
        evidence=store,
        account="DEMO-test",
        enable_evidence_binding=True,
        read_only=False,
        **a[5],
    )
    body = b'{"totalWalletBalance":"1"}'
    transport = EvidenceReadTransport(
        settings(a),
        journal,
        enable_evidence_reads=True,
        sender=lambda *args: RESTResponse(200, body, {"x-mbx-used-weight-1m": "100"}),
        monotonic=a[2].mono,
    )
    fin = financial(a)
    transport(*signed())
    with a[1].engine.connect() as c:
        sequence = c.execute(
            select(events.c.sequence).where(events.c.phase == "FINISH")
        ).scalar_one()
    assert store.review(a[3], a[0].scope, a[4], "DEMO-test", sequence=sequence, body=body)[
        "response_body_digest_matches"
    ]
    plan = call(a, plan=True)
    assert plan["transition_kind"] == "DAY_ONLY" and not plan["evidence_binding_required"]
    assert not plan["baseline_adoption_approved"] and not plan["baseline_amount_proposed"]
    assert financial(a) == fin


@pytest.mark.parametrize(
    "fault", ["owner", "unpaused", "exposure", "pending", "boot", "bad_week", "backwards", "pin"]
)
def test_unsafe_context_denied_before_auxiliary_changes(stale_copy, fault):
    a = stale_copy
    opts = {}
    extra = {}
    if fault in {"owner", "unpaused", "exposure", "bad_week", "backwards"}:
        with a[1].engine.begin() as c:
            state = c.execute(select(accounts.c.state)).scalar_one()
            if fault == "owner":
                c.execute(update(accounts).values(owner="00000000-0000-4000-8000-000000000001"))
            else:
                if fault == "unpaused":
                    state["paused"] = False
                if fault == "exposure":
                    state["positions"] = {"BTCUSDT": "1"}
                if fault == "bad_week":
                    state["weekly_key"] = "2026-11-10"
                if fault == "backwards":
                    state["daily_key"] = "2026-11-16"
                    state["weekly_key"] = "2026-11-16"
                c.execute(update(accounts).values(state=state))
    if fault == "pending":
        shared(a)[1].reserve_read("/fapi/v3/account", 5)
    if fault == "boot":
        opts["boot"] = lambda: "new-boot"
    if fault == "pin":
        extra["pin"] = "0" * 64
    before = snap(a)
    with pytest.raises(LocalQuotaStopped):
        call(a, apply=True, readers_stopped=True, clocks=clock(a, **opts), **extra)
    assert snap(a) == before and bindings.name not in inspect(a[1].engine).get_table_names()


@pytest.mark.parametrize("fault", ["before_write", "after_write", "commit"])
def test_failed_binding_never_repairs_or_retries_orphans(stale_copy, monkeypatch, fault):
    import packages.persistence.demo_response_evidence as module

    a = stale_copy
    before = snap(a)
    original = module._write_new

    def fail(*args):
        if fault == "after_write":
            original(*args)
        raise OSError("SYNTHETIC_WRITE_FAILURE")

    def commit(c):
        if (
            c.execute(text("SELECT to_regclass('read_response_evidence_bindings_v2')")).scalar()
            is not None
        ):
            raise OSError("SYNTHETIC_COMMIT_FAILURE")

    if fault == "commit":
        event.listen(a[1].engine, "commit", commit)
    else:
        monkeypatch.setattr(module, "_write_new", fail)
    try:
        with pytest.raises(OSError, match="SYNTHETIC"):
            call(a, apply=True, readers_stopped=True)
    finally:
        if fault == "commit":
            event.remove(a[1].engine, "commit", commit)
    after = snap(a)
    assert after["tables"] == before["tables"] and after["catalog"] == before["catalog"]
    if fault == "before_write":
        assert after == before
    else:
        with pytest.raises(LocalQuotaStopped, match="ORPHAN"):
            call(a, apply=True, readers_stopped=True)
        assert snap(a) == after


def test_existing_global_schema_and_other_scope_binding_are_preserved(stale_copy):
    from uuid import uuid4

    from packages.persistence.demo_read_audit import AuditedDemoReadJournal, ReadAuditStore

    a = stale_copy
    other = a[3] / "other"
    other.mkdir(mode=0o700)
    identity = uuid4()
    scope = "DEMO-other"
    original = a[1].bootstrap(other, scope, identity, **a[5])
    audit = ReadAuditStore(a[1].engine)
    audit.bind_empty(original, apply=True)
    a[2].seconds += 71
    other_read = AuditedDemoReadJournal(other, scope, identity, anchors=a[1], audit=audit, **a[5])
    store = ResponseEvidenceStore(a[1].engine)
    store.provision(apply=True)
    store.bind(other_read, "DEMO-other", apply=True)
    before = snap(a)
    with a[1].engine.connect() as c:
        prior = [dict(r) for r in c.execute(select(bindings)).mappings()]
    result = call(a, apply=True, readers_stopped=True)
    assert not result["evidence_schema_created"] and result["evidence_binding_created"]
    with a[1].engine.connect() as c:
        preserved = [
            dict(r) for r in c.execute(select(bindings).where(bindings.c.scope == scope)).mappings()
        ]
    assert preserved == prior
    after = snap(a)
    assert after["catalog"] == before["catalog"]
    assert all(after["files"][p] == h for p, h in before["files"].items())


@pytest.mark.parametrize("fault", ["partial", "orphan"])
def test_incomplete_sidecar_is_never_repaired(stale_copy, fault):
    a = stale_copy
    if fault == "partial":
        with a[1].engine.begin() as c:
            bindings.create(c)
    else:
        path = ResponseEvidenceStore.witness(a[3], a[0].scope)
        path.write_bytes(b"SYNTHETIC_ORPHAN")
        path.chmod(0o600)
    before = snap(a)
    with pytest.raises(LocalQuotaStopped, match="PARTIAL|ORPHAN"):
        call(a, apply=True, readers_stopped=True)
    assert snap(a) == before


def test_clock_change_during_binding_never_reports_ready(stale_copy, monkeypatch):
    import packages.persistence.demo_response_evidence as module

    a = stale_copy
    original = module._write_new
    fin = financial(a)

    def changed(*args):
        original(*args)
        a[2].wall_delta += 2

    monkeypatch.setattr(module, "_write_new", changed)
    with pytest.raises(LocalQuotaStopped, match="CONTEXT_CHANGED_DURING_SETUP"):
        call(a, apply=True, readers_stopped=True)
    assert financial(a) == fin
    retained = snap(a)
    with pytest.raises(LocalQuotaStopped, match="UNSAFE_LOCAL_STATE"):
        call(a, apply=True, readers_stopped=True)
    assert snap(a) == retained
