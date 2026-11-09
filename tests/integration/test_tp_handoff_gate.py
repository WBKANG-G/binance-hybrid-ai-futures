"""Fenced decision tests. Synthetic local ledger; success proof is never stubbed as flat."""

# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event
from types import SimpleNamespace
from uuid import uuid4, uuid5

import pytest

from packages.domain.contracts import PositionState, RiskState
from services.execution import demo_tp_fenced_handoff_candidate as module
from services.execution.demo_tp_staged_confirmation_candidate import (
    StagedConfirmationTPTriggerCoordinator,
)
from tests.fixtures.tp_fenced_handoff import handoff_coordinator
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.fixture
def protected_context(opposite_case):
    s, b, m, route, old, history = scenario(opposite_case, cap=100000)
    store, prior, fake, _ = s
    c = handoff_coordinator(store, prior, b)
    operation = uuid4()
    own = next(iter(history.values())).model_copy(deep=True)
    own.signal = own.signal.model_copy(update={"signal_id": operation})
    own.client_id, own.algo_id, own.tp_algo_id = (
        "DE-synthetic-owned",
        "DS-synthetic-owned",
        "DT-synthetic-owned",
    )
    own.position_state, own.protected = PositionState.PROTECTED, True
    own.open_quantity = own.filled

    def seed(ledger):
        ledger.orders[str(operation)] = own
        ledger.positions["BTCUSDT"] = own.open_quantity
        ledger.paused = ledger.reconciled = True
        ledger.last_reconciled = store.now()
        ledger.risk_state = RiskState.NORMAL
        ledger.requests[own.algo_id] = ledger.requests[own.tp_algo_id] = "VERIFIED"

    store.change(c.lease, "SYNTHETIC_POST_PAUSE_FULL_PROOF_SHAPE", seed)
    c._own_operation, c._finalizing = operation, True
    b.begin_cleanup()
    assert c._eligible(store.read(c.lease.account_id))
    try:
        yield store, c, b, m, operation
    finally:
        route.close()
        c.client.send = old


@pytest.mark.parametrize(
    "fault",
    [
        "daily_lock",
        "weekly_lock",
        "drawdown_lock",
        "emergency",
        "unpaused",
        "unreconciled",
        "unprotected",
        "partial",
        "remaining",
        "unverified_stop",
        "unverified_target",
        "foreign_position",
        "other_exposure",
        "wrong_scope",
    ],
)
def test_invalid_local_scope_never_uses_the_handoff_even_with_matching_digest(
    protected_context, monkeypatch, fault
):
    store, c, _, m, op = protected_context

    def change(ledger):
        own = ledger.orders[str(op)]
        risks = {
            "daily_lock": RiskState.DAILY_LOCK,
            "weekly_lock": RiskState.WEEKLY_LOCK,
            "drawdown_lock": RiskState.DRAWDOWN_LOCK,
            "emergency": RiskState.EMERGENCY,
        }
        if fault in risks:
            ledger.risk_state = risks[fault]
        elif fault == "unpaused":
            ledger.paused = False
        elif fault == "unreconciled":
            ledger.reconciled = False
        elif fault == "unprotected":
            own.protected = False
        elif fault == "partial":
            own.filled -= Decimal("0.0001")
        elif fault == "remaining":
            own.remaining = Decimal("0.0001")
        elif fault == "unverified_stop":
            ledger.requests[own.algo_id] = "ACKNOWLEDGED"
        elif fault == "unverified_target":
            ledger.requests[own.tp_algo_id] = "UNKNOWN"
        elif fault == "foreign_position":
            ledger.positions["ETHUSDT"] = Decimal("1")
        elif fault == "other_exposure":
            other = next(order for key, order in ledger.orders.items() if key != str(op))
            other.open_quantity = Decimal("0.0001")
        else:
            ledger.symbols = ("ETHUSDT",)

    store.change(c.lease, "SYNTHETIC_INELIGIBLE_HANDOFF", change)
    before = store.read(c.lease.account_id)
    c._handoff = module.PauseHandoff(module.ledger_hash(before), c._identity(), c.handoff_clock())
    called = []
    monkeypatch.setattr(
        StagedConfirmationTPTriggerCoordinator,
        "command",
        lambda self, operation, command: called.append((operation, command)) or "ORIGINAL_REQUIRED",
    )
    monkeypatch.setattr(
        module,
        "full_snapshot",
        lambda *a: pytest.fail("ineligible local state must not enter fresh handoff query"),
    )
    assert c.command(uuid5(op, "finish"), "EMERGENCY_FLATTEN") == "ORIGINAL_REQUIRED"
    assert len(called) == 1 and not m.rows and not c.handoff_used
    assert store.read(c.lease.account_id) == before


def test_concurrent_duplicate_finish_has_one_effect_and_never_reports_flat(
    protected_context, monkeypatch
):
    store, c, _, _, op = protected_context
    ledger = store.read(c.lease.account_id)
    c._handoff = module.PauseHandoff(module.ledger_hash(ledger), c._identity(), c.handoff_clock())
    monkeypatch.setattr(
        module,
        "full_snapshot",
        lambda client, state, now: SimpleNamespace(
            fingerprint=state.fingerprint, transfers=state.income_seen.copy()
        ),
    )
    began, release = Event(), Event()
    effects = []

    def effect():
        effects.append(True)
        began.set()
        assert release.wait(5)

    monkeypatch.setattr(c, "_flatten", effect)
    command = uuid5(op, "finish")
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(c.command, command, "EMERGENCY_FLATTEN")
        try:
            assert began.wait(5)
            two = pool.submit(c.command, command, "EMERGENCY_FLATTEN")
        finally:
            release.set()
        assert one.result(timeout=5) == two.result(timeout=5) == "UNKNOWN"
    assert effects == [True] and c.handoff_used == c.handoff_checks == 1
    assert store.read(c.lease.account_id).risk_state == RiskState.EMERGENCY
