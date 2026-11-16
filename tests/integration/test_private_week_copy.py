"""Nov 16 day/week observation-only private-key pipeline on disposable schemas."""

# ruff: noqa: F401,F811
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import demo_key_session as keys
from services.execution import demo_copy_private_week_review as module
from services.execution import demo_copy_volatile_credentials as volatile
from services.execution.demo_copy_evidence_rehearsal import (
    SYNTHETIC_PRIVATE_KEY,
    SYNTHETIC_PRIVATE_SECRET,
)
from services.execution.demo_evidence_snapshot import evidence_snapshot
from tests.integration.test_copy_clock_rehearsal import call as clock_call
from tests.integration.test_copy_clock_rehearsal import rehearsal
from tests.integration.test_read_audit_bootstrap import ready
from tests.integration.test_response_evidence_bootstrap import setup_ready
from tests.integration.test_runtime_read_accounting import financial
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_copy_account_content import account_body
from tests.unit.test_demo_key_session import session_dir


@pytest.fixture
def weekly_case(rehearsal, session_dir, monkeypatch):
    a, writer, current, before, sleep = rehearsal
    a[2].seconds += 6 * 86400  # existing fixture is Nov 10; now Nov 16.
    with writer.begin() as c:
        ledger = c.execute(select(accounts.c.state)).scalar_one()
        ledger["daily_key"] = "2026-11-13"
        ledger["weekly_key"] = "2026-11-09"
        c.execute(update(accounts).values(state=ledger))
    case = (a, writer, current, evidence_snapshot(a[1].engine, a[3]), sleep)
    clock_call(case)
    monkeypatch.setattr(keys.time, "time", lambda: int(a[2].utc().timestamp()))
    answers = iter([SYNTHETIC_PRIVATE_KEY, SYNTHETIC_PRIVATE_SECRET])
    monkeypatch.setattr(keys.getpass, "getpass", lambda *args: next(answers))
    monkeypatch.setattr(volatile, "_readonly_tmpfs", lambda: True)
    keys.register()
    lease = volatile.PrivateReadCredentialLease(
        "DEMO-test", a[0].scope, enable_private_read=True, copy_only=True, clocks=current
    )
    yield case, lease, session_dir
    lease.close()


def call(case, **kw):
    inner, lease, folder = case
    a, writer, current, before, sleep = inner
    return module.rehearse_private_week_copy(
        a[1].engine,
        writer,
        "DEMO-test",
        a[3],
        a[0].scope,
        a[4],
        a[1].installation_id(),
        evidence_snapshot(a[1].engine, a[3])["sha256"],
        a[3] / ("synthetic-response-" + uuid4().hex),
        json.dumps(account_body()).encode(),
        lease,
        copy_only=True,
        synthetic_only=True,
        acknowledge_week_boundary_observation=kw.pop("ack", True),
        clocks=current,
        **kw,
    )


def test_explicit_observation_only_flag_precedes_any_access():
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_SYNTHETIC_OBSERVATION"):
        module.rehearse_private_week_copy(*([None] * 11))


def test_new_day_and_week_remain_unchanged_after_private_pipeline(weekly_case):
    inner, lease, folder = weekly_case
    a = inner[0]
    fin = financial(a)
    key_before = (folder / keys.FILENAME).read_bytes()
    report = call(weekly_case)
    assert report["review_day_kst"] == report["review_week_kst"] == "2026-11-16"
    assert report["stored_day_kst"] == "2026-11-13" and report["stored_week_kst"] == "2026-11-09"
    assert report["day_and_week_evidence_still_required"]
    assert (
        report["pipeline"]["private_tmpfs_credentials_used"]
        and report["pipeline"]["synthetic_sender_calls"] == 1
    )
    assert report["pipeline"]["exchange_http_attempts"] == 0 and financial(a) == fin
    assert (folder / keys.FILENAME).read_bytes() == key_before
    for flag in (
        "baseline_adoption_approved",
        "baseline_source_accepted",
        "operator_attestation_received",
        "daily_baseline_changed",
        "weekly_baseline_changed",
        "execution_approved",
    ):
        assert not report[flag]
    with pytest.raises(LocalQuotaStopped):
        call(weekly_case)


@pytest.mark.parametrize(
    "fault", ["old_profile", "already_adopted", "no_ack", "real_looking_credentials"]
)
def test_wrong_period_or_unacknowledged_observation_denied_before_mutation(
    weekly_case, monkeypatch, fault
):
    inner, lease, folder = weekly_case
    a, writer, current, before, sleep = inner
    kw = {}
    if fault == "old_profile":
        a[2].seconds -= 86400
    if fault == "already_adopted":
        with writer.begin() as c:
            state = c.execute(select(accounts.c.state)).scalar_one()
            state["daily_key"] = "2026-11-16"
            state["weekly_key"] = "2026-11-16"
            c.execute(update(accounts).values(state=state))
    if fault == "no_ack":
        kw["ack"] = False
    if fault == "real_looking_credentials":
        lease.close()
        keys.clear()
        answers = iter(["OTHER_SYNTHETIC_PAIR", "OTHER_SYNTHETIC_SECRET"])
        monkeypatch.setattr(keys.getpass, "getpass", lambda *args: next(answers))
        keys.register()
        lease = volatile.PrivateReadCredentialLease(
            "DEMO-test", a[0].scope, enable_private_read=True, copy_only=True, clocks=current
        )
        weekly_case = (inner, lease, folder)
    before = evidence_snapshot(a[1].engine, a[3])
    try:
        with pytest.raises(LocalQuotaStopped):
            call(weekly_case, **kw)
        assert evidence_snapshot(a[1].engine, a[3]) == before
    finally:
        lease.close()


def test_day_change_after_capture_never_reports_period_adoption(weekly_case, monkeypatch):
    inner, lease, folder = weekly_case
    a = inner[0]
    fin = financial(a)
    original = module.rehearse_synthetic_copy_evidence

    def shifted(*args, **kw):
        result = original(*args, **kw)
        a[2].seconds += 86400
        return result

    monkeypatch.setattr(module, "rehearse_synthetic_copy_evidence", shifted)
    with pytest.raises(LocalQuotaStopped):
        call(weekly_case)
    assert financial(a) == fin
