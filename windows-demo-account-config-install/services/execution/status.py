"""Read-only PAPER status; an old successful replay is never a running worker."""

from datetime import datetime

from services.execution.models import Ledger


def paper_status(ledger: Ledger | None, now: datetime) -> dict[str, object]:
    result: dict[str, object] = dict(
        environment="PAPER",
        worker_enabled=False,
        requires_explicit_cli=True,
        live_trading_enabled=False,
        orders_enabled=False,
        as_of=None,
        observed_at=now,
        is_stale=True,
        reconciliation_required=True,
        reason="NOT_STARTED",
        positions=[],
    )
    if ledger is None:
        return result
    age = (now - ledger.last_reconciled).total_seconds() if ledger.last_reconciled else None
    stale = age is None or not 0 <= age <= ledger.policy.max_snapshot_age_seconds
    result.update(
        as_of=ledger.last_reconciled,
        is_stale=stale,
        reconciliation_required=stale or not ledger.reconciled,
        reason="REPLAY_SNAPSHOT",
        risk_state=ledger.risk_state.value,
        policy_version=ledger.policy.risk.version,
        positions=[
            dict(
                symbol=o.signal.symbol,
                state=o.position_state.value,
                quantity=str(o.open_quantity),
                protected=o.protected,
            )
            for o in ledger.orders.values()
            if o.open_quantity
        ],
    )
    return result
