"""Fenced two-period adoption, rollback and duplicate no-op with actual PG."""
# ruff: noqa: F811

import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from packages.domain.contracts import RiskState
from services.execution import demo_paused_period as module
from services.execution.demo_period_review import PeriodReviewBlocked
from services.risk.paper import RiskRejected
from tests.integration.test_demo_paused_period import (
    case,  # noqa: F401
    correction,  # noqa: F401
    day_case,  # noqa: F401
    demo_system,  # noqa: F401
    financial_rows,
    period_case,  # noqa: F401
    preview,
)


def adopt(case, token, operation=None, attest=True, accept=True, lease=None):
    store, c, *_ = case
    return module.apply_paused_periods(
        store,
        lease or c.lease,
        c.client,
        operation or uuid4(),
        token,
        attest_no_activity=attest,
        accept_operator_evidence=accept,
    )


def acquire(case):
    store, c, *_ = case
    c.lease = store.acquire("DEMO-test", uuid4())


def test_apply_preserves_all_financial_history_owner_fence_valuation_and_risk_with_one_receipt(
    case,
):
    store, _, fake, *_ = case
    acquire(case)
    token = preview(case)["review_token"]
    operation = uuid4()
    ledger = store.read("DEMO-test")
    before = financial_rows(store)
    start = len(fake.calls)
    assert adopt(case, token, operation) == module.ADOPTED
    after = store.read("DEMO-test")
    rows = financial_rows(store)
    changed = {k for k in ledger.model_dump() if ledger.model_dump()[k] != after.model_dump()[k]}
    assert changed <= {
        "daily_key",
        "weekly_key",
        "daily_base",
        "weekly_base",
        "attested_period_baselines",
        "operations",
        "reconciled",
        "stream_ready",
    }
    assert after.daily_key == after.weekly_key == "2026-11-02"
    assert after.daily_base == after.weekly_base == fake.wallet
    assert after.risk_state == RiskState.NORMAL and after.paused
    assert after.requests == ledger.requests and after.orders == ledger.orders
    assert after.high_water == ledger.high_water and after.value == ledger.value
    assert after.last_reconciled == ledger.last_reconciled
    original_account, new_account = before["demo_accounts"][0], rows["demo_accounts"][0]
    assert {k: v for k, v in original_account.items() if k != "state"} == {
        k: v for k, v in new_account.items() if k != "state"
    }
    for name in ["demo_events", "demo_outbox"]:
        assert len(rows[name]) == len(before[name]) + 1
        assert all(old in rows[name] for old in before[name])
    receipt = after.attested_period_baselines[str(operation)]
    assert receipt.review_token == token and not receipt.account_wide_exchange_export_verified
    assert receipt.old_daily_key == "2026-11-01" and receipt.old_weekly_key == "2026-10-26"
    assert (
        receipt.old_daily_base == ledger.daily_base
        and receipt.old_weekly_base == ledger.weekly_base
    )
    assert after.operations[str(operation)] == module.OPERATION
    assert all(m == "GET" and "asyn" not in p for m, p, _ in fake.calls[start:])
    before_replay, count = financial_rows(store), len(fake.calls)
    assert adopt(case, token, operation) == "ALREADY_RECORDED_NO_ACTION"
    assert financial_rows(store) == before_replay and len(fake.calls) == count


@pytest.mark.parametrize("fault", ["no_attest", "no_accept", "bad_token", "non_v4", "write_client"])
def test_invalid_apply_intent_is_rejected_without_transport_or_financial_changes(case, fault):
    store, c, fake, *_ = case
    acquire(case)
    token = preview(case)["review_token"]
    if fault == "bad_token":
        token = "not-a-token"
    if fault == "write_client":
        c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    before, count = financial_rows(store), len(fake.calls)
    with pytest.raises(PeriodReviewBlocked):
        adopt(
            case,
            token,
            UUID(int=0) if fault == "non_v4" else uuid4(),
            attest=fault != "no_attest",
            accept=fault != "no_accept",
        )
    assert financial_rows(store) == before and len(fake.calls) == count


@pytest.mark.parametrize(
    "fault", ["wrong_token", "wallet", "risk", "valuation", "day_changed", "expired_during_review"]
)
def test_apply_revalidates_and_rolls_back_instead_of_adopting_stale_candidate(
    case, fault, monkeypatch
):
    store, _, fake, *_ = case
    acquire(case)
    token = preview(case)["review_token"]
    if fault == "wrong_token":
        token = "0" * 64
    if fault == "wallet":
        fake.wallet -= 1
    if fault in {"risk", "valuation"}:

        def mutate(ledger):
            if fault == "risk":
                ledger.risk_state = RiskState.DAILY_LOCK
            else:
                ledger.value = ledger.value.model_copy(update={"as_of": fake.at})

        store.change(case[1].lease, "TEST_DRIFT", mutate)
    if fault == "day_changed":
        fake.at += timedelta(days=1)
    if fault == "expired_during_review":
        original = module.review_paused_periods

        def expire(*a, **kw):
            report = original(*a, **kw)
            fake.at += timedelta(seconds=61)
            return report

        monkeypatch.setattr(module, "review_paused_periods", expire)
    before = financial_rows(store)
    with pytest.raises((PeriodReviewBlocked, RiskRejected)):
        adopt(case, token)
    assert financial_rows(store) == before


def test_stale_fence_cannot_send_or_apply(case):
    store, c, fake, *_ = case
    acquire(case)
    token, stale = preview(case)["review_token"], c.lease
    store.release(stale)
    c.lease = store.acquire("DEMO-test", uuid4())
    before, count = financial_rows(store), len(fake.calls)
    with pytest.raises(RiskRejected):
        adopt(case, token, lease=stale)
    assert financial_rows(store) == before and len(fake.calls) == count


def test_same_operation_with_other_token_conflicts_without_network(case):
    store, _, fake, *_ = case
    acquire(case)
    token, operation = preview(case)["review_token"], uuid4()
    adopt(case, token, operation)
    before, count = financial_rows(store), len(fake.calls)
    with pytest.raises(PeriodReviewBlocked, match="PERIOD_OPERATION_CONFLICT"):
        adopt(case, "0" * 64, operation)
    assert financial_rows(store) == before and len(fake.calls) == count


def test_cross_operation_namespace_conflict_blocks_before_network(case):
    store, c, fake, *_ = case
    acquire(case)
    operation = uuid4()
    store.change(
        c.lease,
        "TEST_OTHER_OPERATION",
        lambda ledger: ledger.operations.update({str(operation): "OTHER"}),
    )
    token = preview(case)["review_token"]
    before, count = financial_rows(store), len(fake.calls)
    with pytest.raises(PeriodReviewBlocked, match="PERIOD_OPERATION_CONFLICT"):
        adopt(case, token, operation)
    assert financial_rows(store) == before and len(fake.calls) == count


def test_cli_apply_releases_lease_then_duplicate_is_read_only_without_keys(
    case, monkeypatch, capsys
):
    from scripts import review_demo_paused_periods as cli

    store, c, *_ = case
    token, operation = preview(case)["review_token"], uuid4()
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda *a, **kw: store)
    monkeypatch.setattr(cli, "DemoClient", lambda *a, **kw: c.client)
    monkeypatch.setattr(cli, "session_credentials", lambda **kw: c.client.credentials)
    monkeypatch.setattr(
        "sys.argv",
        [
            "apply",
            "--attest-no-activity-since-anchor",
            "--accept-operator-evidence",
            "--apply-review",
            token,
            "--operation-id",
            str(operation),
        ],
    )
    cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report["period_baselines"] == module.ADOPTED and report["leader_released"]
    assert report["paused"] and report["risk_state"] == "NORMAL"
    assert report["adopted_day_kst"] == report["adopted_week_kst"] == "2026-11-02"
    before = financial_rows(store)
    monkeypatch.setattr(
        store, "acquire", lambda *a, **kw: pytest.fail("duplicate must not acquire")
    )
    monkeypatch.setattr(
        cli, "session_credentials", lambda **kw: pytest.fail("duplicate needs no keys")
    )
    cli.main()
    replay = json.loads(capsys.readouterr().out)
    assert (
        replay["period_baselines"] == "ALREADY_RECORDED_NO_ACTION"
        and replay["already_recorded_read_only"]
    )
    assert not replay["period_baselines_changed"]
    assert financial_rows(store) == before


def test_audit_insert_failure_rolls_back_both_periods_and_receipt(case):
    from sqlalchemy import event

    store, _, _, *_ = case
    acquire(case)
    token = preview(case)["review_token"]
    before = financial_rows(store)

    def reject_audit(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO DEMO_EVENTS"):
            raise RuntimeError("SYNTHETIC_AUDIT_WRITE_FAILURE")

    event.listen(store.engine, "before_cursor_execute", reject_audit)
    try:
        with pytest.raises(RuntimeError, match="SYNTHETIC_AUDIT_WRITE_FAILURE"):
            adopt(case, token)
    finally:
        event.remove(store.engine, "before_cursor_execute", reject_audit)
    assert financial_rows(store) == before
