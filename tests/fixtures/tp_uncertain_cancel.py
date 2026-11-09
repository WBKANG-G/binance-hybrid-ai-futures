"""Explicit construction for synthetic experiments; no activation CLI."""

from services.execution.demo_tp_uncertain_cancel_candidate import (
    UncertainCancellationClient,
    UncertainCancellationTPTriggerCoordinator,
)


def uncertain_coordinator(store, prior, budget):
    source = prior.client
    client = UncertainCancellationClient(
        source.settings, source.credentials, send=budget, clock_ms=source.clock_ms
    )
    client.offset, client.synced_at = source.offset, source.synced_at
    return UncertainCancellationTPTriggerCoordinator(store, prior.lease, client, budget)
