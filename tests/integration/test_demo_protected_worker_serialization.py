"""Real two-second PostgreSQL lock timeout: stream callback and event cannot overlap."""

import asyncio
import threading
import time
from uuid import uuid4

from services.execution import demo_protected_worker_check as module
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


def test_slow_pause_completes_before_event_handler_and_does_not_self_lock(case, monkeypatch):  # noqa: F811
    store, old, fake, options = case
    options["trigger"] = False
    operation = uuid4()
    client = module.ScopedClient(
        old.client.settings,
        old.client.credentials,
        send=old.client.send,
        clock_ms=old.client.clock_ms,
        account="DEMO-test",
        operation=operation,
        entry_enabled=True,
    )
    client.sync_clock()
    c = module.CappedCoordinator(store, old.lease, client)
    c.refresh_metadata()
    pause_entered = threading.Event()
    stamps = {}
    sqlstates = []
    command = c.command
    event = c.event

    def slow_command(op, name):
        if str(op) == module.marker(operation, "pause-new"):

            def hold(state):
                pause_entered.set()
                time.sleep(2.4)  # Longer than the original PostgreSQL lock_timeout='2s'.

            store.change(c.lease, "TEST_HOLD_PROTECTED_PAUSE", hold)
            stamps["pause_finished"] = time.monotonic()
        return command(op, name)

    def record_event(raw):
        stamps["event_started"] = time.monotonic()
        return event(raw)

    monkeypatch.setattr(c, "command", slow_command)
    monkeypatch.setattr(c, "event", record_event)

    async def exercise():
        processed = asyncio.Event()

        async def sequenced_session(coordinator, *, connector=None, on_ready=None):
            await module.settled_call(coordinator.connected)
            await module.settled_call(on_ready)
            assert await asyncio.to_thread(pause_entered.wait, 5)
            try:
                await module.settled_call(
                    coordinator.event, {"e": "ALGO_UPDATE", "E": client.clock_ms()}
                )
            except Exception as error:
                sqlstates.append(
                    getattr(getattr(error, "orig", None), "sqlstate", type(error).__name__)
                )
                raise
            finally:
                processed.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(module, "session", sequenced_session)
        await module.child_run(c, operation, stop=processed.is_set, ready_timeout=8, lifetime=15)

    asyncio.run(exercise())
    assert not sqlstates, sqlstates
    assert stamps["pause_finished"] <= stamps["event_started"]
    current = store.read("DEMO-test")
    assert current.operations[module.marker(operation, "ready")] == "FULL_PROTECTED_READY"
    assert current.paused and current.orders[str(operation)].position_state.value == "CLOSED"
    assert (
        not any(fake.positions.values())
        and client.entry_attempts == 1
        and client.exit_attempts == 1
    )
