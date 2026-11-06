"""Bounded account-only batching never hides urgent, invalid or cancelled hints."""

import asyncio

import pytest

from integrations.binance_usdm.demo import DemoFailure
from tests.unit.test_demo_event_inbox import close, event, ready


def test_account_burst_invalidates_before_delay_and_uses_one_latest_hint():
    async def run():
        inbox, reader, failures = await ready([event(1000), event(2000)])
        invalidated = asyncio.Event()

        async def invalidate():
            invalidated.set()
            return 0.25

        take = asyncio.create_task(inbox.take(before_coalesce=invalidate))
        await invalidated.wait()
        assert not take.done() and inbox.pending == 2
        hint = await asyncio.wait_for(take, 1)
        assert hint.stamp == 2000 and inbox.coalesced == 1 and not failures
        assert inbox.pending == 0 and not inbox.urgent.is_set()
        await close(reader)

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind",
    ["ORDER_TRADE_UPDATE", "ALGO_UPDATE", "MARGIN_CALL", "ACCOUNT_CONFIG_UPDATE", "TRADE_LITE"],
)
def test_every_nonaccount_kind_is_urgent_even_if_later_account_hint_is_latest(kind):
    async def run():
        inbox, reader, _ = await ready([event(1000, kind), event(2000)])

        async def forbidden():
            pytest.fail("urgent hint must not wait or call batching hook")

        await asyncio.wait_for(inbox.take(before_coalesce=forbidden), 1)
        assert not inbox.urgent.is_set() and inbox.coalesced == 1
        await close(reader)

    asyncio.run(run())


def test_urgent_frame_interrupts_an_existing_account_batch():
    async def run():
        from services.execution.demo_event_inbox import EventInbox
        from tests.unit.test_demo_event_inbox import Source

        source = Source([event()])
        inbox = EventInbox(lambda: 2000, lambda: None)
        reader = asyncio.create_task(inbox.run(source))
        started = asyncio.Event()

        async def invalidate():
            started.set()
            return 0.25

        take = asyncio.create_task(inbox.take(before_coalesce=invalidate))
        await started.wait()
        source.queue.put_nowait(event(kind="ALGO_UPDATE"))
        hint = await asyncio.wait_for(take, 1)
        assert hint.kind == "ALGO_UPDATE" and inbox.coalesced == 1
        await close(reader)

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad", [event(kind="UNKNOWN"), event(kind="listenKeyExpired"), event(2001), "NaN"]
)
def test_invalid_frame_during_batch_stops_admission_and_cannot_be_masked(bad):
    async def run():
        from services.execution.demo_event_inbox import EventInbox
        from tests.unit.test_demo_event_inbox import Source

        source = Source([event()])
        failures = []
        inbox = EventInbox(lambda: 2000, lambda: failures.append(True))
        reader = asyncio.create_task(inbox.run(source))
        started = asyncio.Event()

        async def invalidate():
            started.set()
            return 0.25

        take = asyncio.create_task(inbox.take(before_coalesce=invalidate))
        await started.wait()
        source.queue.put_nowait(bad)
        source.queue.put_nowait(event())
        with pytest.raises(DemoFailure):
            await asyncio.wait_for(take, 1)
        await reader
        assert failures == [True] and inbox.failure is not None
        assert inbox.pending == 1  # No batch was consumed after invalidation failure.

    asyncio.run(run())


@pytest.mark.parametrize("delay", [-1, 0.251, float("nan"), float("inf")])
def test_bad_hook_delay_fails_before_consuming_hint(delay):
    async def run():
        inbox, reader, _ = await ready([event()])

        async def hook():
            return delay

        with pytest.raises(DemoFailure, match="batch delay"):
            await inbox.take(before_coalesce=hook)
        assert inbox.pending == 1
        await close(reader)

    asyncio.run(run())


def test_cancellation_during_batch_leaves_hint_pending_for_session_shutdown():
    async def run():
        inbox, reader, failures = await ready([event()])
        started = asyncio.Event()

        async def invalidate():
            started.set()
            return 0.25

        take = asyncio.create_task(inbox.take(before_coalesce=invalidate))
        await started.wait()
        take.cancel()
        with pytest.raises(asyncio.CancelledError):
            await take
        assert inbox.pending == 1 and not failures and not reader.done()
        await close(reader)

    asyncio.run(run())


def test_continuous_account_hints_do_not_extend_fixed_deadline():
    async def run():
        from services.execution.demo_event_inbox import EventInbox
        from tests.unit.test_demo_event_inbox import Source

        source = Source([event(), event()])
        inbox = EventInbox(lambda: 2000, lambda: None)
        reader = asyncio.create_task(inbox.run(source))
        calls = []

        async def hook():
            calls.append(True)
            return 0.25

        async def flood():
            while True:
                await asyncio.sleep(0.01)
                source.queue.put_nowait(event())

        producer = asyncio.create_task(flood())
        await asyncio.wait_for(inbox.take(before_coalesce=hook), 1)
        assert len(calls) == 1 and inbox.coalesced >= 1
        await close(producer)
        await close(reader)

    asyncio.run(run())


def protected_candidate():
    from datetime import UTC, datetime
    from decimal import Decimal
    from types import SimpleNamespace as NS

    from packages.domain.contracts import OrderState, PositionState, RiskState
    from services.execution.demo_tp_budget_candidate import CoalescedBudgetedTPTriggerCoordinator

    now = datetime(2026, 11, 6, tzinfo=UTC)
    order = NS(
        state=OrderState.FILLED,
        quantity=Decimal("0.0007"),
        filled=Decimal("0.0007"),
        open_quantity=Decimal("0.0007"),
        remaining=0,
        protected=True,
        position_state=PositionState.PROTECTED,
        algo_id="DS-own",
        tp_algo_id="DT-own",
        exit_id=None,
        exit_state="NONE",
        signal=NS(
            signal_id="own",
            purpose="DEMO_VALIDATION",
            symbol="BTCUSDT",
            direction="LONG",
            suggested_limit_price=Decimal("80000"),
        ),
    )
    ledger = NS(
        orders={"own": order},
        positions={"BTCUSDT": Decimal("0.0007")},
        risk_state=RiskState.NORMAL,
        reconciled=True,
        stream_ready=True,
        last_reconciled=now,
        stream_at=now,
        policy=NS(max_snapshot_age_seconds=30),
        validation_evidence={"own": {"stop_verified": True, "target_verified": True}},
    )
    c = CoalescedBudgetedTPTriggerCoordinator.__new__(CoalescedBudgetedTPTriggerCoordinator)
    c.session_failed = False
    c.client = NS(
        settings=NS(demo_trading_enabled=True, margin_mode="ISOLATED", symbols=("BTCUSDT",))
    )
    c.request_budget = NS(stopped=False)
    c.lease = NS(account_id="synthetic")
    c.store = NS(read=lambda _: ledger, now=lambda: now)
    calls = []

    def disconnect():
        calls.append(True)
        ledger.reconciled = ledger.stream_ready = False

    c.disconnect = disconnect
    return c, ledger, order, calls


def test_eligible_protected_position_invalidates_admission_before_returning_delay():
    c, ledger, _, calls = protected_candidate()
    assert c.prepare_event_batch() == 0.25
    assert calls == [True] and not ledger.reconciled and not ledger.stream_ready
    assert c.prepare_event_batch() == 0  # No batching using already-invalidated evidence.


@pytest.mark.parametrize(
    "case",
    [
        "partial",
        "remaining",
        "missing_stop",
        "missing_tp",
        "unprotected",
        "exit_intent",
        "exit_unknown",
        "order_unknown",
        "missing_stop_proof",
        "missing_tp_proof",
        "stream_untrusted",
        "not_reconciled",
        "session_failed",
        "budget_stopped",
        "emergency",
        "stale",
        "future",
        "other_position",
        "position_mismatch",
        "second_active",
        "wrong_scope",
        "wrong_direction",
        "over_cap",
        "over_notional",
        "disabled",
        "crossed",
    ],
)
def test_ineligible_or_uncertain_positions_never_delay_event_reconciliation(case):
    from copy import deepcopy
    from datetime import timedelta
    from decimal import Decimal

    from packages.domain.contracts import OrderState, RiskState

    c, ledger, order, calls = protected_candidate()
    if case == "partial":
        order.filled = order.open_quantity = Decimal("0.0003")
    elif case == "remaining":
        order.remaining = Decimal("0.0001")
    elif case == "missing_stop":
        order.algo_id = None
    elif case == "missing_tp":
        order.tp_algo_id = None
    elif case == "unprotected":
        order.protected = False
    elif case == "exit_intent":
        order.exit_id = "DX-own"
    elif case == "exit_unknown":
        order.exit_state = "UNKNOWN"
    elif case == "order_unknown":
        order.state = OrderState.UNKNOWN
    elif case == "missing_stop_proof":
        ledger.validation_evidence["own"]["stop_verified"] = False
    elif case == "missing_tp_proof":
        ledger.validation_evidence["own"]["target_verified"] = False
    elif case == "stream_untrusted":
        ledger.stream_ready = False
    elif case == "not_reconciled":
        ledger.reconciled = False
    elif case == "session_failed":
        c.session_failed = True
    elif case == "budget_stopped":
        c.request_budget.stopped = True
    elif case == "emergency":
        ledger.risk_state = RiskState.EMERGENCY
    elif case == "stale":
        ledger.last_reconciled -= timedelta(seconds=15)
    elif case == "future":
        ledger.stream_at += timedelta(seconds=1)
    elif case == "other_position":
        ledger.positions["ETHUSDT"] = 1
    elif case == "position_mismatch":
        ledger.positions["BTCUSDT"] = Decimal("0.0003")
    elif case == "second_active":
        ledger.orders["other"] = deepcopy(order)
    elif case == "wrong_scope":
        order.signal.purpose = "OTHER"
    elif case == "wrong_direction":
        order.signal.direction = "SHORT"
    elif case == "over_cap":
        order.quantity = Decimal("0.0008")
    elif case == "over_notional":
        order.signal.suggested_limit_price = Decimal("1000000")
    elif case == "disabled":
        c.client.settings.demo_trading_enabled = False
    elif case == "crossed":
        c.client.settings.margin_mode = "CROSSED"
    before = deepcopy(ledger)
    assert c.prepare_event_batch() == 0 and not calls and ledger == before


def test_idle_without_hint_never_calls_invalidation_hook():
    async def run():
        inbox, reader, _ = await ready([])

        async def forbidden():
            pytest.fail("idle wait must not invalidate or introduce extra delay")

        with pytest.raises(TimeoutError):
            await inbox.take(timeout=0.001, before_coalesce=forbidden)
        await close(reader)

    asyncio.run(run())


def test_pending_overflow_during_batch_still_fails_closed():
    async def run():
        from services.execution.demo_event_inbox import MAX_PENDING_EVENTS, EventInbox
        from tests.unit.test_demo_event_inbox import Source

        source = Source([event()])
        failures = []
        inbox = EventInbox(lambda: 2000, lambda: failures.append(True))
        reader = asyncio.create_task(inbox.run(source))
        started = asyncio.Event()

        async def invalidate():
            started.set()
            return 0.25

        take = asyncio.create_task(inbox.take(before_coalesce=invalidate))
        await started.wait()
        for _ in range(MAX_PENDING_EVENTS):
            source.queue.put_nowait(event())
        with pytest.raises(DemoFailure):
            await asyncio.wait_for(take, 1)
        await reader
        assert failures == [True] and inbox.failure.code == "EVENT_BUFFER_OVERFLOW"

    asyncio.run(run())
