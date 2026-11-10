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


def test_close_prevents_a_first_claim_and_is_idempotent():
    claim = ConfirmationClaim()
    assert claim.close() and claim.close()
    assert not claim.claim()
    state = claim.diagnostics()
    assert state["confirmation_closed"] and not state["confirmation_started"]
    assert state["closed_calls_refused"] == 1


def test_active_claim_prevents_close_until_owner_finishes():
    claim = ConfirmationClaim()
    assert claim.claim() and not claim.close()
    assert not claim.diagnostics()["confirmation_closed"]
    claim.finish()
    assert claim.close() and not claim.claim()


def test_atomic_close_and_claim_cannot_both_win_while_owner_is_active():
    claim = ConfirmationClaim()
    ready = Barrier(16)

    def compete(index):
        ready.wait(timeout=5)
        return ("claim", claim.claim()) if index % 2 else ("close", claim.close())

    with ThreadPoolExecutor(max_workers=16) as workers:
        results = list(workers.map(compete, range(16)))
    starts = sum(ok for operation, ok in results if operation == "claim")
    closes = sum(ok for operation, ok in results if operation == "close")
    assert (starts, closes) in {(1, 0), (0, 8)}
