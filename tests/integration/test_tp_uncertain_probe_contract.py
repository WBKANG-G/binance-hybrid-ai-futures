"""Probe-only contracts from an explicitly seeded post-proof uncertainty state."""

# ruff: noqa: F811
from dataclasses import replace
from decimal import Decimal

import pytest

from packages.domain.contracts import PositionState, RiskState
from services.execution.demo_tp_fenced_handoff_candidate import ledger_hash
from services.execution.demo_tp_uncertain_cancel_candidate import PENDING_MARKER, UncertainTicket
from tests.fixtures.tp_uncertain_cancel import uncertain_coordinator
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_handoff_gate import protected_context  # noqa: F401


@pytest.fixture
def uncertain_context(protected_context):
    store, prior, b, m, op = protected_context
    c = uncertain_coordinator(store, prior, b)
    c._own_operation = op
    c._finalizing = True

    def seed(ledger):
        own = ledger.orders[str(op)]
        own.position_state = PositionState.CLOSED
        own.protected = False
        own.open_quantity = own.actual_risk = Decimal("0")
        ledger.positions["BTCUSDT"] = Decimal("0")
        ledger.paused = True
        ledger.reconciled = False
        ledger.last_reconciled = store.now()
        ledger.operations[c._marker()] = PENDING_MARKER

    store.change(c.lease, "SYNTHETIC_POST_PROOF_UNCERTAINTY", seed)
    own = store.read(c.lease.account_id).orders[str(op)]
    fake = m.fake
    fake.positions["BTCUSDT"] = Decimal("0")
    for identifier, kind, price, status in [
        (own.algo_id, "STOP_MARKET", own.signal.initial_stop_price, "NEW"),
        (own.tp_algo_id, "TAKE_PROFIT_MARKET", own.signal.target_price, "CANCELED"),
    ]:
        fake.algos[identifier] = dict(
            symbol="BTCUSDT",
            clientAlgoId=identifier,
            algoId=identifier + "1",
            side="SELL",
            orderType=kind,
            algoStatus=status,
            triggerPrice=str(price),
            closePosition=True,
            positionSide="BOTH",
            workingType="MARK_PRICE",
            priceProtect=False,
            algoType="CONDITIONAL",
            actualOrderId="",
        )
    ledger = store.read(c.lease.account_id)
    ticket = UncertainTicket(ledger_hash(ledger), c._identity(), c.handoff_clock(), (own.algo_id,))
    return store, c, b, m, ticket


@pytest.mark.parametrize(
    "risk",
    [RiskState.DAILY_LOCK, RiskState.WEEKLY_LOCK, RiskState.DRAWDOWN_LOCK, RiskState.EMERGENCY],
)
def test_read_only_pending_probe_preserves_existing_locks_and_financial_state(
    uncertain_context, risk
):
    store, c, b, m, ticket = uncertain_context
    store.change(
        c.lease, "SYNTHETIC_EXISTING_LOCK", lambda ledger: setattr(ledger, "risk_state", risk)
    )
    before = store.read(c.lease.account_id)
    ticket = replace(ticket, state_hash=ledger_hash(before))
    assert c._probe_ticket(ticket, c.client) is True
    assert store.read(c.lease.account_id) == before
    assert all(row["method"] == "GET" for row in m.rows)
    assert not b.entry_sent


@pytest.mark.parametrize("fault", ["expired", "future", "nan", "fence", "state", "receipt"])
def test_invalid_proof_ticket_cannot_start_any_http(uncertain_context, fault):
    store, c, b, m, ticket = uncertain_context
    if fault == "expired":
        ticket = replace(ticket, created=ticket.created - 9)
    elif fault == "future":
        ticket = replace(ticket, created=ticket.created + 9)
    elif fault == "nan":
        ticket = replace(ticket, created=float("nan"))
    elif fault == "fence":
        ticket = replace(
            ticket, identity=(ticket.identity[0], ticket.identity[1], ticket.identity[2] + 1)
        )
    elif fault == "state":
        store.change(
            c.lease,
            "SYNTHETIC_CHANGED_FINGERPRINT",
            lambda ledger: setattr(ledger, "fingerprint", "different"),
        )
    else:
        store.change(
            c.lease,
            "SYNTHETIC_CHANGED_MARKER",
            lambda ledger: ledger.operations.__setitem__(c._marker(), "OTHER"),
        )
    before = store.read(c.lease.account_id)
    assert c._probe_ticket(ticket, c.client) is False
    assert not m.rows and store.read(c.lease.account_id) == before
    assert not b.entry_sent
