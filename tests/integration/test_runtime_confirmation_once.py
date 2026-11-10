"""One scheduler ownership claim per runtime, including failure/cancellation."""

# ruff: noqa: F811
import asyncio
from threading import Event

import pytest

from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_runtime_confirmation_deadline import scheduler


@pytest.mark.parametrize("ending", ["attempts", "deadline", "deferrals", "exception", "cancel"])
def test_terminal_confirmation_call_cannot_restart_limits(assembly, monkeypatch, ending):
    session, c, state = scheduler(
        assembly,
        monkeypatch,
        "renew" if ending == "deadline" else "defer" if ending == "deferrals" else "normal",
    )

    class OriginalFailure(Exception):
        pass

    failed = []
    if ending == "deferrals":
        c._confirmation_clock = lambda: 0.0
    if ending == "exception":

        def renew(lease):
            state["calls"].append("renew")
            if not failed:
                failed.append(True)
                raise OriginalFailure

        monkeypatch.setattr(c.store, "renew", renew)
    if ending == "cancel":
        original = c._confirmation_sleep

        async def pause(delay):
            if not failed:
                failed.append(True)
                raise asyncio.CancelledError
            await original(delay)

        c._confirmation_sleep = pause
    try:
        if ending == "exception":
            with pytest.raises(OriginalFailure):
                asyncio.run(c.confirm_exit_budgeted())
        elif ending == "cancel":
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(c.confirm_exit_budgeted())
        else:
            assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        before = (
            list(state["calls"]),
            list(state["sleeps"]),
            c.confirmation_stop_reason,
            c.confirmation_deferrals,
            c.confirmation_attempts_completed,
        )
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert (
            state["calls"],
            state["sleeps"],
            c.confirmation_stop_reason,
            c.confirmation_deferrals,
            c.confirmation_attempts_completed,
        ) == before
        assert c.budgeted_flat_confirmation == "UNKNOWN"
    finally:
        session.close()


def test_concurrent_confirmation_call_cannot_start_a_second_loop(assembly, monkeypatch):
    session, c, state = scheduler(assembly, monkeypatch, "normal")
    entered, release = Event(), Event()

    def renew(lease):
        state["calls"].append("renew")
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(c.store, "renew", renew)

    async def run():
        first = asyncio.create_task(c.confirm_exit_budgeted())
        second = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            second = asyncio.create_task(c.confirm_exit_budgeted())
            assert await asyncio.wait_for(asyncio.shield(second), 0.2) == "UNKNOWN"
            assert state["calls"] == ["renew"]
            diagnostic = c.confirmation_diagnostics()
            assert diagnostic["confirmation_call_active"]
            assert diagnostic["repeated_calls_refused"] == 1
            assert c.confirmation_stop_reason == "RUNNING"
        finally:
            release.set()
            await asyncio.gather(
                first, *([second] if second is not None else []), return_exceptions=True
            )

    try:
        asyncio.run(run())
        assert state["calls"].count("proof") == 4
        assert c.budgeted_flat_confirmation == "UNKNOWN"
    finally:
        release.set()
        session.close()


def test_rejected_repeat_does_not_read_a_new_deadline_clock(assembly, monkeypatch):
    session, c, state = scheduler(assembly, monkeypatch, "normal")
    touched = []

    def forbidden():
        touched.append(True)
        raise AssertionError("repeat read clock")

    try:
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        c._confirmation_clock = forbidden
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert not touched
    finally:
        session.close()
