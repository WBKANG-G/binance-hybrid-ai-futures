"""Actual clock transactions on disposable schemas; simulated elapsed wait."""

# ruff: noqa: F401,F811
import pytest
from sqlalchemy import create_engine, select, text, update

from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_copy_clock_rehearsal import rehearse_copy_clock
from services.execution.demo_evidence_snapshot import evidence_snapshot
from tests.integration.test_copy_transition_review import snapshot
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


@pytest.fixture
def rehearsal(setup_ready):
    a = setup_ready
    audit, old = shared(a)
    synthetic = reader((a, audit, old))
    for _ in range(27):
        synthetic(*signed())
    from packages.persistence.demo_read_recovery import recover_completed_read_clock

    recover_completed_read_clock(
        a[3],
        a[0].scope,
        a[4],
        anchor=a[1],
        audit=audit,
        apply=True,
        clocks=clock(a, boot=lambda: "intermediate-boot"),
    )
    with a[1].engine.begin() as c:
        ledger = c.execute(select(accounts.c.state)).scalar_one()
        ledger["requests"] = {"a": "UNKNOWN", "b": "UNKNOWN", "c": "UNKNOWN", "d": "SUBMITTING"}
        c.execute(update(accounts).values(state=ledger))
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    writer = create_engine(
        a[1].engine.url, connect_args={"options": "-csearch_path=" + schema}, pool_size=5
    )
    a[2].seconds += 86400
    current = clock(a, boot=lambda: "new-boot")
    before = snapshot(a)

    def sleep(seconds):
        a[2].seconds += seconds

    yield a, writer, current, before, sleep
    writer.dispose()


def call(case, **kw):
    a, writer, current, before, sleep = case
    return rehearse_copy_clock(
        a[1].engine,
        writer,
        "DEMO-test",
        a[3],
        a[0].scope,
        a[4],
        a[1].installation_id(),
        kw.pop("expected_snapshot_sha256", before["sha256"]),
        apply=True,
        copy_only=True,
        clocks=current,
        sleeper=kw.pop("sleeper", sleep),
        **kw,
    )


def test_explicit_copy_apply_precedes_any_access():
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_COPY_APPLY"):
        rehearse_copy_clock(None, None, None, None, None, None, None, None)


def test_exact_clock_append_hold_and_preserved_financial_state(rehearsal):
    a, writer, current, before, _ = rehearsal
    progress = []
    result = call(rehearsal, progress=progress.append)
    assert result["startup_hold_verified"] and sum(progress) > 0
    assert result["audit_events_after"] == result["prior_audit_events"] + 1
    assert result["prior_audit_events"] == 55 and result["audit_events_after"] == 56
    assert result["revision_after"] == result["prior_revision"] + 1
    assert result["cumulative_weight_before"] == result["cumulative_weight_after"] == 135
    diagnostic = result["diagnostic_after"]
    assert diagnostic["clock_matches"] and not diagnostic["period_keys_match"]
    assert diagnostic["historical_unresolved_requests"] == 4
    assert "NEW_PERIOD_BASELINE_EVIDENCE_REQUIRED_NO_AUTO_ADOPTION" in diagnostic["blockers"]
    assert not result["installation_ready"] and not result["financial_ledger_changed"]
    after = snapshot(a)
    assert all(
        after["tables"][name] == values
        for name, values in before["tables"].items()
        if name not in {"demo_read_audit_events", "demo_shared_weight_checkpoints"}
    )
    assert after["catalog"] == before["catalog"]
    with pytest.raises(LocalQuotaStopped, match="TRANSITION_NOT_REQUIRED"):
        call(rehearsal, expected_snapshot_sha256=after["sha256"])
    assert snapshot(a) == after


def test_wrong_prior_snapshot_blocks_before_mutation(rehearsal):
    a, _, _, before, _ = rehearsal
    with pytest.raises(LocalQuotaStopped, match="PRIOR_DIAGNOSTIC_MISMATCH"):
        call(rehearsal, expected_snapshot_sha256="0" * 64)
    assert snapshot(a) == before


def test_pending_read_is_not_cleared_or_rebased(setup_ready):
    a = setup_ready
    shared(a)[1].reserve_read("/fapi/v3/account", 5)
    with a[1].engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    writer = create_engine(a[1].engine.url, connect_args={"options": "-csearch_path=" + schema})
    before = snapshot(a)
    try:
        with pytest.raises(LocalQuotaStopped, match="UNSAFE_LOCAL_STATE"):
            rehearse_copy_clock(
                a[1].engine,
                writer,
                "DEMO-test",
                a[3],
                a[0].scope,
                a[4],
                a[1].installation_id(),
                before["sha256"],
                apply=True,
                copy_only=True,
                clocks=clock(a, boot=lambda: "new-boot"),
            )
        assert snapshot(a) == before
    finally:
        writer.dispose()


def test_v2_bound_cannot_use_v1_transition(setup_ready):
    a = setup_ready
    setup_v2(a, apply=True, readers_stopped=True)
    with a[1].engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    writer = create_engine(a[1].engine.url, connect_args={"options": "-csearch_path=" + schema})
    before = snapshot(a)
    try:
        with pytest.raises(LocalQuotaStopped, match="V2_BOUND_FORBIDDEN"):
            rehearse_copy_clock(
                a[1].engine,
                writer,
                "DEMO-test",
                a[3],
                a[0].scope,
                a[4],
                a[1].installation_id(),
                before["sha256"],
                apply=True,
                copy_only=True,
                clocks=clock(a, boot=lambda: "new-boot"),
            )
        assert snapshot(a) == before
    finally:
        writer.dispose()


def test_interrupted_hold_leaves_copy_transition_for_review_and_never_retries(rehearsal):
    a, _, _, before, _ = rehearsal

    def interrupted(seconds):
        raise OSError("SYNTHETIC_WAIT_INTERRUPTED")

    with pytest.raises(OSError, match="SYNTHETIC_WAIT"):
        call(rehearsal, sleeper=interrupted)
    after = snapshot(a)
    assert after["sha256"] != before["sha256"]
    with pytest.raises(LocalQuotaStopped, match="PRIOR_DIAGNOSTIC_MISMATCH"):
        call(rehearsal)
    assert snapshot(a) == after


def test_unrelated_auxiliary_append_cannot_hide_in_the_two_allowed_tables(rehearsal, monkeypatch):
    from uuid import uuid4

    import services.execution.demo_copy_clock_rehearsal as module
    from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore

    a, writer, current, _, _ = rehearsal
    original = module.recover_completed_read_clock

    def changed(*args, **kw):
        result = original(*args, **kw)
        other = a[3] / "other"
        other.mkdir(mode=0o700)
        SharedWeightAnchorStore(writer).bootstrap(
            other,
            "DEMO-OTHER",
            uuid4(),
            monotonic=current.monotonic,
            wall_clock=current.wall_clock,
            boot=current.boot,
        )
        return result

    monkeypatch.setattr(module, "recover_completed_read_clock", changed)
    with pytest.raises(LocalQuotaStopped, match="AUXILIARY_ROWS_CHANGED"):
        call(rehearsal)


def test_external_financial_change_during_hold_is_not_undone_or_accepted(rehearsal):
    a, writer, _, _, _ = rehearsal
    changed = []

    def progress(remaining):
        if not changed:
            changed.append(True)
            with writer.begin() as c:
                ledger = c.execute(select(accounts.c.state)).scalar_one()
                ledger["paused"] = False
                c.execute(update(accounts).values(state=ledger))

    with pytest.raises(LocalQuotaStopped, match="STATE_CHANGED_DURING_HOLD"):
        call(rehearsal, progress=progress)
    with writer.connect() as c:
        assert c.execute(select(accounts.c.state)).scalar_one()["paused"] is False


@pytest.mark.parametrize(
    "jump,code", [(-100, "CLOCK_CHANGED_DURING_APPLY"), (86400, "PERIOD_CHANGED_DURING_REHEARSAL")]
)
def test_clock_or_period_change_during_hold_cannot_report_success(rehearsal, jump, code):
    a, _, _, before, _ = rehearsal
    changed = []

    def progress(remaining):
        if not changed:
            changed.append(True)
            a[2].seconds += jump

    with pytest.raises(LocalQuotaStopped, match=code):
        call(rehearsal, progress=progress)
    assert snapshot(a)["sha256"] != before["sha256"]
