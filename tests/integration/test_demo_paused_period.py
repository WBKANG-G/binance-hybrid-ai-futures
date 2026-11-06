"""Real PG and synthetic signed exchange: NORMAL paused Monday preview, no writes."""
# ruff: noqa: F811

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from packages.domain.contracts import RiskState
from packages.persistence.demo import accounts, events, outbox
from services.execution import demo_paused_period as module
from services.execution.demo_period_review import PeriodReviewBlocked, review_periods
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.integration.test_demo_period_review import period_case  # noqa: F401
from tests.unit.test_demo_trade_history_window import row


@pytest.fixture
def case(period_case):
    store, c, fake, *_ = period_case
    store.release(c.lease)
    fake.at = datetime(2026, 11, 1, 7, tzinfo=UTC)
    from uuid import uuid4

    c.lease = store.acquire("DEMO-test", uuid4())

    def prepare(ledger):
        ledger.risk_state = RiskState.NORMAL
        ledger.last_reconciled = fake.at
        ledger.value = ledger.value.model_copy(update={"as_of": fake.at})
        ledger.daily_key, ledger.weekly_key = "2026-11-01", "2026-10-26"

    store.change(c.lease, "TEST_NORMAL_PRE_MONDAY_ANCHOR", prepare)
    store.release(c.lease)
    fake.at = datetime(2026, 11, 2, 7, 30, tzinfo=UTC)
    c.client.sync_clock()
    return period_case


def preview(case, attest=True, ledger=None):
    store, c, *_ = case
    return module.review_paused_periods(
        store,
        c.client,
        ledger or store.read("DEMO-test"),
        account_id="DEMO-test",
        attest_no_activity=attest,
    )


def financial_rows(store):
    with store.engine.connect() as conn:
        return {
            t.name: list(conn.execute(select(t)).mappings()) for t in [accounts, events, outbox]
        }


def test_monday_day_week_candidate_uses_signed_truth_and_leaves_every_financial_row_unchanged(case):
    store, _, fake, *_ = case
    before = financial_rows(store)
    statements = []
    start = len(fake.calls)

    def collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(store.engine, "before_cursor_execute", collect)
    try:
        report = preview(case)
    finally:
        event.remove(store.engine, "before_cursor_execute", collect)
    assert report["review"] == "PAUSED_NORMAL_PERIOD_PREVIEW_NO_DATABASE_CHANGE"
    assert report["old_day_kst"] == "2026-11-01" and report["old_week_kst"] == "2026-10-26"
    assert report["proposed_day_kst"] == report["proposed_week_kst"] == "2026-11-02"
    assert report["day_boundary_utc"] == report["week_boundary_utc"] == "2026-11-01T15:00:00+00:00"
    assert (
        report["proposed_daily_base_usdt"]
        == report["proposed_weekly_base_usdt"]
        == str(fake.wallet)
    )
    assert report["risk_state"] == "NORMAL" and report["paused"] and report["preview_only"]
    assert (
        report["operator_attestation_used"] and not report["account_wide_exchange_export_verified"]
    )
    assert not report["database_changed"] and not report["unresolved_requests_acknowledged"]
    assert report["high_water_preserved"] and report["old_valuation_timestamp_preserved"]
    assert 0 < report["read_diagnostic"]["signed_get_count"] <= 600
    assert 0 < report["read_diagnostic"]["clock_probe_count"] <= 6
    assert financial_rows(store) == before
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert all(m == "GET" and "asyn" not in p for m, p, _ in fake.calls[start:])
    assert preview(case)["review_token"] == report["review_token"]


@pytest.mark.parametrize("state", [s for s in RiskState if s != RiskState.NORMAL])
def test_every_non_normal_state_is_rejected_before_network_without_risk_substitution(case, state):
    store, _, fake, *_ = case
    ledger = store.read("DEMO-test")
    ledger.risk_state = state
    before, count = ledger.model_dump_json(), len(fake.calls)
    with pytest.raises(PeriodReviewBlocked):
        preview(case, ledger=ledger)
    assert ledger.model_dump_json() == before and len(fake.calls) == count


@pytest.mark.parametrize("fault", ["no_attestation", "unpaused", "write_client"])
def test_missing_opt_in_or_bad_context_is_rejected_without_network(case, fault):
    store, c, fake, *_ = case
    ledger = store.read("DEMO-test")
    if fault == "unpaused":
        ledger.paused = False
    elif fault == "write_client":
        c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    count = len(fake.calls)
    with pytest.raises(PeriodReviewBlocked):
        preview(case, attest=fault != "no_attestation", ledger=ledger)
    assert len(fake.calls) == count


def test_original_emergency_only_public_entry_still_rejects_normal(case):
    store, c, fake, *_ = case
    count = len(fake.calls)
    with pytest.raises(PeriodReviewBlocked, match="READ_ONLY_PAUSED"):
        review_periods(
            store,
            c.client,
            store.read("DEMO-test"),
            account_id="DEMO-test",
            attest_no_activity=True,
        )
    assert len(fake.calls) == count


@pytest.mark.parametrize(
    "fault", ["trade", "income", "wallet", "old_loss", "missing_anchor", "valuation", "day_only"]
)
def test_new_preview_keeps_signed_contradiction_and_anchor_loss_guards(case, fault):
    store, _, fake, trades, incomes, _ = case
    before = financial_rows(store)
    ledger = store.read("DEMO-test")
    stamp = int(ledger.last_reconciled.timestamp() * 1000) + 1000
    if fault == "trade":
        trades.append(row(id=999, time=stamp, orderId=999))
    elif fault == "income":
        incomes.append(
            {"incomeType": "TRANSFER", "tranId": 999, "time": stamp, "income": "0", "asset": "USDT"}
        )
    elif fault == "wallet":
        fake.wallet -= Decimal(1)
    elif fault == "old_loss":
        ledger.daily_base = Decimal(10000)
    elif fault == "missing_anchor":
        ledger.last_reconciled = None
    elif fault == "valuation":
        ledger.value = ledger.value.model_copy(update={"as_of": fake.at})
    elif fault == "day_only":
        fake.at = datetime(2026, 11, 1, 8, tzinfo=UTC)
    with pytest.raises(PeriodReviewBlocked):
        preview(case, ledger=ledger)
    assert financial_rows(store) == before


def test_budget_failure_is_fixed_safe_code_and_does_not_change_finance(case, monkeypatch):
    from services.execution.demo_recovery_budget import RecoveryBudget, RecoveryReadClient

    store, _, _, *_ = case
    before = financial_rows(store)
    monkeypatch.setattr(
        module,
        "RecoveryReadClient",
        lambda client: RecoveryReadClient(client, RecoveryBudget(clock=iter([0, 41]).__next__)),
    )
    with pytest.raises(module.PeriodPreviewBudgetBlocked) as caught:
        preview(case)
    assert caught.value.code == "REQUEST_AUDIT_TIME_BUDGET"
    assert financial_rows(store) == before


def test_cli_real_preview_does_not_acquire_lease_or_change_finance(case, monkeypatch, capsys):
    from scripts import review_demo_paused_periods as cli

    store, c, *_ = case
    before = financial_rows(store)
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: store.engine)
    monkeypatch.setattr(cli, "DemoClient", lambda *a, **kw: c.client)
    monkeypatch.setattr(cli, "DemoStore", lambda *a, **kw: store)
    monkeypatch.setattr(cli, "session_credentials", lambda **kw: c.client.credentials)
    monkeypatch.setattr(
        store, "acquire", lambda *a, **kw: pytest.fail("preview cannot acquire lease")
    )
    monkeypatch.setattr("sys.argv", ["preview", "--attest-no-activity-since-anchor"])
    cli.main()
    assert (
        json.loads(capsys.readouterr().out)["review"]
        == "PAUSED_NORMAL_PERIOD_PREVIEW_NO_DATABASE_CHANGE"
    )
    assert financial_rows(store) == before
