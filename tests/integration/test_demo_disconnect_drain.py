"""Fault observation deadline must not interrupt already-started session cleanup."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from services.execution import demo_open_disconnect_check as module
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_open_disconnect import disconnect_case as case  # noqa: F401
from tests.integration.test_demo_open_disconnect import exercise
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401


def test_observed_close_drains_cleanup_beyond_fault_deadline_then_reconnects(case, monkeypatch):  # noqa: F811
    original = module.session
    calls = 0
    completed = False

    async def slow_cleanup(*args, **kwargs):
        nonlocal calls, completed
        calls += 1
        index = calls
        try:
            return await original(*args, **kwargs)
        finally:
            if index == 1:
                await asyncio.sleep(2.1)
                completed = True

    monkeypatch.setattr(module, "session", slow_cleanup)
    report = asyncio.run(exercise(case, uuid4(), timeout=2))
    assert report["disconnect_check"] == "OPEN_ORDER_SOCKET_LOSS_CANCEL_RECONNECT_VERIFIED", report
    assert completed and calls == 2
    assert report["new_entry_post_count"] == 1
    assert report["flat_confirmation"] == "VERIFIED_FLAT"


def test_fault_not_observed_before_deadline_is_rejected_without_entry(case):  # noqa: F811
    _, c, fake, _ = case

    @asynccontextmanager
    async def stalled(url):
        await asyncio.sleep(10)
        yield None

    report = asyncio.run(module.run(c, uuid4(), connector=stalled, phase_timeout=0.02))
    assert report["disconnect_check"] == "NOT_ACCEPTED"
    assert report["failure"] == "PHASE_TIMEOUT"
    assert report["injected_close_count"] == 0 and report["reconnect_count"] == 0
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and len(posts(fake)) == 0
