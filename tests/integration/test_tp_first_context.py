"""Budget/source identity and serialization, without substituting a flat proof."""

# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from services.execution.demo_flat_transport import confirmation_context
from services.execution.demo_tp_fenced_handoff_candidate import FencedHandoffTPTriggerCoordinator
from services.execution.demo_tp_staged_confirmation_candidate import StagedConfirmationTPClient
from tests.fixtures.tp_first_confirmation import first_coordinator
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_handoff_gate import protected_context  # noqa: F401


def test_confirmation_waits_for_same_instance_command_lock(protected_context, monkeypatch):
    store, prior, b, _, _ = protected_context
    c = first_coordinator(store, prior, b)
    started, entered = Event(), Event()

    def full_proof_required(self):
        entered.set()
        return "UNKNOWN"  # Never synthesize a successful flat result.

    monkeypatch.setattr(FencedHandoffTPTriggerCoordinator, "confirm_flat", full_proof_required)

    def confirm():
        started.set()
        return c.confirm_flat()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with c._handoff_lock:
            future = pool.submit(confirm)
            assert started.wait(5)
            assert not entered.wait(0.1)
        assert future.result(timeout=5) == "UNKNOWN"
    assert entered.is_set() and c._first_confirmation_source is None


def test_pending_probe_rejects_an_observer_for_a_different_source(protected_context):
    store, prior, b, meter, _ = protected_context
    c = first_coordinator(store, prior, b)
    source = c.client
    different = StagedConfirmationTPClient(
        source.settings, source.credentials, send=b, clock_ms=source.clock_ms
    )
    before = store.read(c.lease.account_id)
    with confirmation_context(source) as observer:
        with pytest.raises(ValueError, match="observer/source mismatch"):
            c._pending_protection(different, observer=observer)
    assert not meter.rows and store.read(c.lease.account_id) == before
