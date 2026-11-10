"""A receiver failure during resource waiting must stop the next ordinary audit."""

# ruff: noqa: F811
import asyncio
import json

import pytest

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred
from services.execution.demo_event_inbox import EventInbox
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_runtime_observation_budget import observing  # noqa: F401


@pytest.mark.parametrize("fault", ["disconnect", "expired", "stale", "malformed", "overflow"])
@pytest.mark.parametrize("when", ["during_wait", "credit_return"])
def test_receiver_failure_blocks_resume_after_resource_check(observing, monkeypatch, when, fault):
    a, s, c, b, calls, resumed, watches = observing
    inbox = EventInbox(lambda: 1000000, lambda: setattr(c, "session_failed", True))

    class ClosedSocket:
        async def recv(self):
            if fault == "disconnect":
                raise OSError("synthetic socket closed")
            if fault == "malformed":
                return "{"
            return json.dumps(
                {
                    "e": "listenKeyExpired" if fault == "expired" else "ACCOUNT_UPDATE",
                    "E": 940000 if fault == "stale" else 1000000,
                }
            )

    def fail_receiver():
        asyncio.run(inbox.run(ClosedSocket()))
        assert inbox.failure is not None and c.session_failed

    count = [0]

    def credit(cost):
        count[0] += 1
        if when == "credit_return":
            fail_receiver()
        elif count[0] == 1:
            raise ProofDeferred

    monkeypatch.setattr(b, "require_proof", credit)
    if when == "during_wait":
        c._observation_sleep = lambda delay: fail_receiver()
    with pytest.raises(DemoFailure, match="session"):
        c.connected()
    assert not resumed and c.budgeted_flat_confirmation == "UNKNOWN"


def test_failed_session_without_flat_hint_still_blocks_original_observation(observing):
    a, s, c, b, calls, resumed, watches = observing
    c.session_failed = True
    b.flat_observed = False
    calls.clear()
    before = a[0].read(c.lease.account_id)
    with pytest.raises(DemoFailure, match="session"):
        c.connected()
    assert not resumed and not calls and not watches
    assert a[0].read(c.lease.account_id) == before
    assert not b.untrusted  # Original fenced safety cleanup has not been disabled.
