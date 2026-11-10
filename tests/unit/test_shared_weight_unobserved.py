"""Known response completion is distinct from a trustworthy usage observation."""

# ruff: noqa: F811
import pytest

from packages.persistence.demo_shared_weight import WINDOW, SharedWeightStopped
from tests.unit.test_demo_shared_weight import ready, shared  # noqa: F401


def test_unobserved_response_keeps_full_cost_from_completion(shared):
    b, clock = ready(shared)
    token = b.reserve(2, "ORDINARY")
    clock.seconds += 200
    b.finish_unobserved(token, status=200)
    assert b.view()["pending_attempts"] == 0
    assert b.view()["pressure"] == 2
    clock.seconds += WINDOW - 0.001
    assert b.view()["pressure"] == 2
    clock.seconds += 0.002
    assert b.view()["pressure"] == 0


@pytest.mark.parametrize("status", [418, 429, 0, 600, None, True])
def test_unobserved_response_invalid_status_or_throttle_keeps_pending(shared, status):
    b, _ = ready(shared)
    token = b.reserve(2, "ORDINARY")
    b.finish_unobserved(token, status=status)
    with pytest.raises(SharedWeightStopped):
        b.view()
