"""Explicit client and coordinator selection for synthetic experiments only."""

from services.execution.demo_tp_fenced_handoff_candidate import FencedHandoffTPTriggerCoordinator
from services.execution.demo_tp_staged_confirmation_candidate import StagedConfirmationTPClient


def handoff_coordinator(store, prior, budget):
    source = prior.client
    client = StagedConfirmationTPClient(
        source.settings, source.credentials, send=budget, clock_ms=source.clock_ms
    )
    client.offset, client.synced_at = source.offset, source.synced_at
    return FencedHandoffTPTriggerCoordinator(store, prior.lease, client, budget)
