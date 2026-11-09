"""NORMAL/paused rollover uses the same signed evidence, without resetting hard locks."""

from decimal import Decimal
from uuid import uuid4

import pytest

from packages.domain.contracts import RiskState
from services.execution import demo_paused_day as module
from services.execution.demo_day_reconstruction import (
    DayReconstructionBlocked,
    review_day_reconstruction,
)
from services.execution.demo_history_review import HistoryBlocked, review_flat_history
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.unit.test_recovery_https_transport import tls_server  # noqa: F401


@pytest.fixture
def case(day_case):  # noqa: F811
    store, c, *rest = day_case
    store.change(
        c.lease,
        "TEST_NORMAL_PAUSED",
        lambda ledger: setattr(ledger, "risk_state", RiskState.NORMAL),
    )
    return store, c, *rest


def preview(case, attest=True):
    store, c, *_ = case
    return module.review_paused_day(
        store,
        c.client,
        store.read("DEMO-test"),
        account_id="DEMO-test",
        attest_no_other_trades=attest,
    )


def apply(case, token, operation=None, attest=True, accept=True):
    store, c, *_ = case
    return module.apply_paused_day(
        store,
        c.lease,
        c.client,
        operation or uuid4(),
        token,
        attest_no_other_trades=attest,
        accept_operator_evidence=accept,
    )


def test_normal_preview_is_immutable_and_source_is_explicit(case):
    store, _, fake, *_ = case
    before = store.read("DEMO-test")
    count = len(fake.calls)
    report = preview(case)
    assert report["review"] == "PAUSED_NORMAL_DAY_PREVIEW_NO_DATABASE_CHANGE"
    assert report["risk_state"] == "NORMAL" and report["paused"]
    assert (
        report["operator_attestation_used"] and not report["account_wide_exchange_export_verified"]
    )
    assert report["read_diagnostic"]["signed_get_count"] > 0
    assert store.read("DEMO-test") == before
    assert all(m == "GET" for m, p, fields in fake.calls[count:])


def test_apply_changes_only_day_receipt_and_flags_preserving_normal_pause(case):
    store, _, fake, *_ = case
    before = store.read("DEMO-test")
    report = preview(case)
    operation = uuid4()
    result = apply(case, report["review_token"], operation)
    assert result == "OPERATOR_ATTESTED_DAY_BASELINE_ADOPTED_ENTRIES_STILL_PAUSED"
    after = store.read("DEMO-test")
    assert after.risk_state == RiskState.NORMAL and after.paused
    assert after.daily_key == report["proposed_day_kst"] and after.daily_base == Decimal(
        report["proposed_daily_base_usdt"]
    )
    a = before.model_dump(mode="json")
    b = after.model_dump(mode="json")
    changed = {k for k in a if a[k] != b[k]}
    assert changed <= {
        "daily_key",
        "daily_base",
        "operations",
        "attested_day_baselines",
        "reconciled",
        "stream_ready",
    }
    assert (
        after.value.as_of == before.value.as_of
        and after.weekly_base == before.weekly_base
        and after.high_water == before.high_water
    )
    assert after.requests == before.requests
    assert apply(case, report["review_token"], operation) == "ALREADY_RECORDED_NO_ACTION"


@pytest.mark.parametrize(
    "state",
    [
        RiskState.EMERGENCY,
        RiskState.UNHEALTHY,
        RiskState.DAILY_LOCK,
        RiskState.WEEKLY_LOCK,
        RiskState.DRAWDOWN_LOCK,
    ],
)
def test_new_entrypoint_cannot_reset_any_non_normal_lock(case, state):
    store, c, *_ = case
    store.change(c.lease, "TEST_HARD_LOCK", lambda ledger: setattr(ledger, "risk_state", state))
    before = store.read("DEMO-test")
    with pytest.raises(DayReconstructionBlocked, match="NORMAL_PAUSED_READ_ONLY_REQUIRED"):
        preview(case)
    assert store.read("DEMO-test") == before


def test_old_public_entrypoints_still_require_emergency(case):
    store, c, *_ = case
    with pytest.raises(DayReconstructionBlocked, match="ATTESTED_DAY_CONTEXT_MISMATCH"):
        review_day_reconstruction(
            store,
            c.client,
            store.read("DEMO-test"),
            account_id="DEMO-test",
            attest_no_other_trades=True,
        )
    with pytest.raises(HistoryBlocked, match="READ_ONLY_CONTEXT_REQUIRED"):
        review_flat_history(store, c.client, store.read("DEMO-test"))


def test_missing_attestation_or_acceptance_cannot_apply(case):
    with pytest.raises(DayReconstructionBlocked, match="EXPLICIT_OPERATOR_ATTESTATION_REQUIRED"):
        preview(case, False)
    report = preview(case)
    with pytest.raises(
        DayReconstructionBlocked, match="EXPLICIT_MANUAL_SOURCE_ACCEPTANCE_REQUIRED"
    ):
        apply(case, report["review_token"], accept=False)


def test_token_change_rolls_back_everything(case):
    store, *_ = case
    preview(case)
    before = store.read("DEMO-test")
    with pytest.raises(DayReconstructionBlocked, match="DAY_PREVIEW_CHANGED"):
        apply(case, "a" * 64)
    assert store.read("DEMO-test") == before


def test_post_anchor_income_cannot_be_erased(case):
    store, c, fake, rows, incomes, raw = case
    anchor = store.read("DEMO-test").last_reconciled
    incomes.append(
        {
            "incomeType": "COMMISSION",
            "income": "-0.01",
            "asset": "USDT",
            "symbol": "BTCUSDT",
            "time": int(anchor.timestamp() * 1000) + 1,
            "tranId": 777,
        }
    )
    before = store.read("DEMO-test")
    with pytest.raises((DayReconstructionBlocked, HistoryBlocked)):
        preview(case)
    assert store.read("DEMO-test") == before


def test_nonzero_transfer_basis_cannot_reset_day(case):
    store, c, *_ = case
    store.change(
        c.lease, "TEST_TRANSFER", lambda ledger: setattr(ledger, "net_transfers", Decimal("1"))
    )
    with pytest.raises(DayReconstructionBlocked, match="NONZERO_OR_UNKNOWN_TRANSFER_BASIS"):
        preview(case)


@pytest.mark.parametrize("field", ["daily_base", "weekly_base", "high_water"])
def test_existing_loss_limit_cannot_be_reset_by_rollover(case, field):
    store, c, *_ = case
    state = store.read("DEMO-test")
    base = state.value.equity * 2
    cap = {
        "daily_base": "daily_loss_limit_pct",
        "weekly_base": "weekly_loss_limit_pct",
        "high_water": "max_drawdown_pct",
    }[field]
    assert (base - state.value.equity) / base * 100 >= state.policy.limit(cap)
    store.change(c.lease, "TEST_LOSS", lambda ledger: setattr(ledger, field, base))
    before = store.read("DEMO-test")
    with pytest.raises(DayReconstructionBlocked, match="EXISTING_LOSS_LIMIT_REACHED"):
        preview(case)
    assert store.read("DEMO-test") == before


def test_budget_exhaustion_is_sanitized_and_preserves_ledger(case, monkeypatch):
    from services.execution import demo_recovery_budget as limits

    store, *_ = case
    before = store.read("DEMO-test")
    monkeypatch.setattr(limits, "MAX_SIGNED_QUERIES", 1)
    with pytest.raises(DayReconstructionBlocked, match="REQUEST_AUDIT_QUERY_BUDGET"):
        preview(case)
    assert store.read("DEMO-test") == before


def test_actual_local_tls_get_pool_is_scoped_to_preview(case, tls_server, monkeypatch):  # noqa: F811
    from integrations.binance_usdm.demo import transport
    from tests.integration.test_demo_recovery_https import wire

    store, c, fake, *_ = case
    before = store.read("DEMO-test")
    saved = c.client.send
    wire((store, c, fake), tls_server, monkeypatch)
    c.client.send = transport
    try:
        report = preview(case)
        assert report["read_diagnostic"]["audit_transport_mode"] == "POOLED_HTTPS"
        assert report["read_diagnostic"]["audit_connection_attempts"] == 1
        assert report["read_diagnostic"]["audit_reused_requests"] > 10
        assert store.read("DEMO-test") == before and c.client.send is transport
    finally:
        c.client.send = saved


def test_extended_history_uses_one_global_budget_without_repeated_clock_probes(case, monkeypatch):
    from datetime import timedelta

    from services.execution import demo_recovery_budget as limits

    store, c, fake, *_ = case

    def old_signal(ledger):
        order = next(iter(ledger.orders.values()))
        order.signal = order.signal.model_copy(
            update={"signal_closed_candle_at": fake.at - timedelta(days=10)}
        )

    store.change(c.lease, "TEST_EXTENDED_HISTORY", old_signal)
    before = store.read("DEMO-test")
    monkeypatch.setattr(limits, "MAX_CLOCK_PROBES", 1)
    report = preview(case)
    assert report["read_diagnostic"]["clock_probe_count"] == 0
    assert report["read_diagnostic"]["signed_get_count"] > 40
    assert store.read("DEMO-test") == before
