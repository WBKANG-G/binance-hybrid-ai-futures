"""DEMO observation status never claims a replay/local test is exchange verification."""

from datetime import datetime

from services.execution.demo_models import DemoLedger


def demo_status(ledger: DemoLedger | None, now: datetime) -> dict[str, object]:
    result: dict[str, object] = dict(
        environment="DEMO",
        api_order_routes_enabled=False,
        requires_explicit_cli=True,
        live_trading_enabled=False,
        demo_trading_enabled=False,
        as_of=None,
        observed_at=now,
        is_stale=True,
        reconciliation_required=True,
        private_stream_fresh=False,
        reason="NOT_ENABLED",
        positions=[],
    )
    if ledger is None:
        return result
    as_of = ledger.last_reconciled
    age = (now - as_of).total_seconds() if as_of else None
    stream_age = (now - ledger.stream_at).total_seconds() if ledger.stream_at else None
    stream_fresh = (
        ledger.stream_ready
        and stream_age is not None
        and 0 <= stream_age <= ledger.policy.max_snapshot_age_seconds
    )
    stale = (
        age is None or not 0 <= age <= ledger.policy.max_snapshot_age_seconds or not stream_fresh
    )
    result.update(
        as_of=as_of,
        is_stale=stale,
        private_stream_fresh=stream_fresh,
        reconciliation_required=stale or not ledger.reconciled,
        reason="DEMO_LEDGER_SNAPSHOT",
        risk_state=ledger.risk_state.value,
        policy_version=ledger.policy.risk.version,
        positions=[
            dict(
                symbol=o.signal.symbol,
                quantity=str(o.open_quantity),
                state=o.position_state.value,
                protected=o.protected,
            )
            for o in ledger.orders.values()
            if o.open_quantity
        ],
    )
    return result
