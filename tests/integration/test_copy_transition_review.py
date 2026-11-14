"""Copy-only rehearsal on fresh schemas; actual Windows sources are never used."""

# ruff: noqa: F401,F811
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update

from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_recovery import recover_completed_read_clock
from packages.persistence.demo_response_evidence import ResponseEvidenceStore, bindings
from services.execution.demo_copy_transition_review import review_copy_transition
from services.execution.demo_evidence_snapshot import evidence_snapshot
from services.execution.demo_runtime_storage import RuntimeClocks
from tests.integration.test_demo_read_audit import reader
from tests.integration.test_read_audit_bootstrap import ready
from tests.integration.test_response_evidence_bootstrap import (
    call as setup_v2,
)
from tests.integration.test_response_evidence_bootstrap import (
    clock,
    setup_ready,
    shared,
)
from tests.integration.test_runtime_read_accounting import signed
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system


def call(a, **kw):
    return review_copy_transition(
        a[1].engine,
        "DEMO-test",
        a[3],
        a[0].scope,
        a[4],
        a[1].installation_id(),
        copy_only_review=True,
        clocks=kw.pop("clocks", clock(a)),
        **kw,
    )


def snapshot(a):
    return evidence_snapshot(a[1].engine, a[3])


def test_missing_copy_attestation_precedes_any_access():
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_COPY_REVIEW"):
        review_copy_transition(None, None, None, None, None, None)


@pytest.mark.parametrize("v2", [False, True])
def test_valid_local_state_is_review_only_not_execution_ready(setup_ready, v2):
    a = setup_ready
    if v2:
        setup_v2(a, apply=True, readers_stopped=True)
    before = snapshot(a)
    report = call(a)
    assert report["blockers"] == [] and report["clock_matches"] and report["period_keys_match"]
    assert report["v2_bound"] == v2 and not report["copy_isolation_independently_verified"]
    assert not report["installation_ready"] and not report["execution_approved"]
    assert snapshot(a) == before


@pytest.mark.parametrize(
    "fault",
    [
        "boot",
        "drift",
        "backward",
        "day",
        "week",
        "pending",
        "owner",
        "pause",
        "exposure",
        "startup",
        "historical",
    ],
)
def test_blockers_are_reported_without_repair_or_state_change(setup_ready, fault):
    a = setup_ready
    opts = {}
    if fault == "boot":
        opts["boot"] = lambda: "new-boot"
    if fault == "drift":
        opts["wall_clock"] = lambda: a[2].utc() + timedelta(seconds=2)
    if fault == "backward":
        opts["monotonic"] = lambda: 0.0
    if fault == "startup":
        a[2].seconds = 69.0
    if fault == "pending":
        shared(a)[1].reserve_read("/fapi/v3/account", 5)
    if fault in {"day", "week", "owner", "pause", "exposure", "historical"}:
        with a[1].engine.begin() as c:
            ledger = c.execute(select(accounts.c.state)).scalar_one()
            if fault == "owner":
                c.execute(update(accounts).values(owner=str(uuid4())))
            else:
                if fault == "day":
                    ledger["daily_key"] = "2026-11-01"
                if fault == "week":
                    ledger["weekly_key"] = "2026-10-26"
                if fault == "pause":
                    ledger["paused"] = False
                if fault == "exposure":
                    ledger["positions"] = {"BTCUSDT": "1"}
                if fault == "historical":
                    ledger["requests"] = {
                        "a": "UNKNOWN",
                        "b": "SUBMITTING",
                        "c": "UNKNOWN",
                        "d": "UNKNOWN",
                    }
                c.execute(update(accounts).values(state=ledger))
    before = snapshot(a)
    report = call(a, clocks=clock(a, **opts))
    assert report["blockers"] and not report["review_found_no_local_transition_blockers"]
    if fault == "historical":
        assert report["historical_unresolved_requests"] == 4
    assert snapshot(a) == before


def test_v2_legacy_rebase_is_blocked_before_intent_or_clock_change(setup_ready):
    a = setup_ready
    setup_v2(a, apply=True, readers_stopped=True)
    changed = clock(a, boot=lambda: "different-boot")
    before = snapshot(a)
    assert "V2_CLOCK_TRANSITION_UNSUPPORTED" in call(a, clocks=changed)["blockers"]
    audit, old = shared(a)
    with pytest.raises(LocalQuotaStopped, match="V2_EVIDENCE_REQUIRES_REVIEW"):
        recover_completed_read_clock(
            a[3], a[0].scope, a[4], anchor=a[1], audit=audit, apply=True, clocks=changed
        )
    assert snapshot(a) == before


@pytest.mark.parametrize("fault", ["partial", "orphan"])
def test_incomplete_v2_blocks_legacy_clock_rebase_without_cleanup(setup_ready, fault):
    a = setup_ready
    audit, _ = shared(a)
    if fault == "partial":
        with a[1].engine.begin() as c:
            bindings.create(c)
    else:
        ResponseEvidenceStore.witness(a[3], a[0].scope).write_bytes(b"SYNTHETIC_ORPHAN")
    before = snapshot(a)
    with pytest.raises(LocalQuotaStopped, match="PARTIAL_V2|V2_EVIDENCE"):
        recover_completed_read_clock(
            a[3],
            a[0].scope,
            a[4],
            anchor=a[1],
            audit=audit,
            apply=True,
            clocks=clock(a, boot=lambda: "new-boot"),
        )
    assert snapshot(a) == before


def test_copy_rehearsal_rebases_only_v1_clock_and_keeps_stale_account_day_blocked(setup_ready):
    a = setup_ready
    audit, old = shared(a)
    reader((a, audit, old))(*signed())
    # Fresh synthetic schema is the disposable copy for this rehearsal.
    with a[1].engine.begin() as c:
        state = c.execute(select(accounts.c.state)).scalar_one()
        state["requests"] = {"a": "UNKNOWN", "b": "UNKNOWN", "c": "UNKNOWN", "d": "SUBMITTING"}
        c.execute(update(accounts).values(state=state))
    before_account = state
    a[2].seconds += 86400
    changed = clock(a, boot=lambda: "new-boot")
    report = call(a, clocks=changed)
    assert not report["clock_matches"] and not report["period_keys_match"]
    result = recover_completed_read_clock(
        a[3], a[0].scope, a[4], anchor=a[1], audit=audit, apply=True, clocks=changed
    )
    assert result["shared_journal_changed"] and result["read_clock_recovery"] == "WAIT_STARTUP_HOLD"
    assert not result["financial_ledger_changed"] and not result["period_baselines_changed"]
    a[2].seconds += 71
    report = call(a, clocks=changed)
    assert report["clock_matches"] and not report["period_keys_match"]
    assert report["historical_unresolved_requests"] == 4
    assert "NEW_PERIOD_BASELINE_EVIDENCE_REQUIRED_NO_AUTO_ADOPTION" in report["blockers"]
    before = snapshot(a)
    with pytest.raises(LocalQuotaStopped, match="PERIOD_MISMATCH"):
        setup_v2(a, apply=True, readers_stopped=True, clocks=changed)
    assert snapshot(a) == before
    with a[1].engine.connect() as c:
        assert c.execute(select(accounts.c.state)).scalar_one() == before_account


def test_changed_clock_during_review_is_not_reported_as_success(setup_ready):
    a = setup_ready
    boots = iter(["first-boot", "second-boot"])
    before = snapshot(a)
    with pytest.raises(LocalQuotaStopped, match="CLOCK_CHANGED_DURING_APPLY"):
        call(a, clocks=clock(a, boot=lambda: next(boots)))
    assert snapshot(a) == before


@pytest.mark.parametrize("fault", ["binding", "installation", "partial", "orphan"])
def test_wrong_bindings_and_incomplete_evidence_refuse_review_without_repair(setup_ready, fault):
    from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY

    a = setup_ready
    if fault == "binding":
        with a[1].engine.begin() as c:
            state = c.execute(select(accounts.c.state)).scalar_one()
            state["operations"][RUNTIME_STORAGE_KEY] = "{}"
            c.execute(update(accounts).values(state=state))
    if fault == "partial":
        with a[1].engine.begin() as c:
            bindings.create(c)
    if fault == "orphan":
        path = ResponseEvidenceStore.witness(a[3], a[0].scope)
        path.write_bytes(b"SYNTHETIC_ORPHAN")
        path.chmod(0o600)
    before = snapshot(a)
    with pytest.raises(LocalQuotaStopped):
        if fault == "installation":
            review_copy_transition(
                a[1].engine,
                "DEMO-test",
                a[3],
                a[0].scope,
                a[4],
                uuid4(),
                copy_only_review=True,
                clocks=clock(a),
            )
        else:
            call(a)
    assert snapshot(a) == before


def test_external_state_change_during_review_is_blocked_and_not_undone(setup_ready, monkeypatch):
    import services.execution.demo_copy_transition_review as module

    a = setup_ready
    original = module.evidence_snapshot
    seen = []

    def changed(*args):
        result = original(*args)
        if not seen:
            seen.append(True)
            with a[1].engine.begin() as c:
                state = c.execute(select(accounts.c.state)).scalar_one()
                state["paused"] = False
                c.execute(update(accounts).values(state=state))
        return result

    monkeypatch.setattr(module, "evidence_snapshot", changed)
    with pytest.raises(LocalQuotaStopped, match="STATE_CHANGED_DURING_REVIEW"):
        call(a)
    with a[1].engine.connect() as c:
        assert not c.execute(select(accounts.c.state)).scalar_one()["paused"]


def test_kst_day_crossing_at_final_snapshot_cannot_return_stale_period_report(
    setup_ready, monkeypatch
):
    from datetime import UTC, datetime

    import services.execution.demo_copy_transition_review as module

    a = setup_ready
    original = module.evidence_snapshot
    seen = []

    def changed(*args):
        result = original(*args)
        seen.append(True)
        if len(seen) == 2:
            a[2].seconds += 2
        return result

    def wall():
        return datetime(2026, 11, 13, 14, 59, 59, tzinfo=UTC) + timedelta(
            seconds=a[2].seconds - 70.001
        )

    before = snapshot(a)
    monkeypatch.setattr(module, "evidence_snapshot", changed)
    with pytest.raises(LocalQuotaStopped, match="PERIOD_CHANGED_DURING_REVIEW"):
        call(a, clocks=clock(a, wall_clock=wall))
    assert snapshot(a) == before
