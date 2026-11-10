"""Atomic single ownership independent of asyncio loop or worker thread."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from services.execution.demo_runtime_confirmation import ConfirmationClaim


def test_completion_does_not_return_start_permission():
    claim = ConfirmationClaim()
    assert claim.claim()
    claim.finish()
    assert not claim.claim()
    state = claim.diagnostics()
    assert state["confirmation_started"] and not state["confirmation_call_active"]
    assert state["repeated_calls_refused"] == 1


def test_only_one_thread_can_claim_the_same_confirmation():
    claim = ConfirmationClaim()
    ready = Barrier(16)

    def compete(_):
        ready.wait(timeout=5)
        return claim.claim()

    with ThreadPoolExecutor(max_workers=16) as workers:
        results = list(workers.map(compete, range(16)))
    assert sum(results) == 1
    assert claim.diagnostics()["repeated_calls_refused"] == 15
    claim.finish()
    assert not claim.claim()


def test_ownership_is_local_to_each_coordinator_claim():
    first, second = ConfirmationClaim(), ConfirmationClaim()
    assert first.claim() and second.claim()
    first.finish()
    assert not first.claim()
    assert second.diagnostics()["confirmation_call_active"]
