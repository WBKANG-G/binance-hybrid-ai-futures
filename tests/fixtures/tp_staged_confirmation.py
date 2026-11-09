"""Create an explicit candidate client before any synthetic entry is submitted."""

from services.execution.demo_tp_staged_confirmation_candidate import (
    StagedConfirmationTPClient,
    StagedConfirmationTPTriggerCoordinator,
)


def staged_coordinator(store, prior, budget):
    source = prior.client
    client = StagedConfirmationTPClient(
        source.settings, source.credentials, send=budget, clock_ms=source.clock_ms
    )
    client.offset, client.synced_at = source.offset, source.synced_at
    return StagedConfirmationTPTriggerCoordinator(store, prior.lease, client, budget)
