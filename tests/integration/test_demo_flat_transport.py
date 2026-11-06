# ruff: noqa: F811
"""Exact parent confirmation control, real PG/TLS and deterministic setup cost."""

from decimal import Decimal

import pytest

from integrations.binance_usdm.demo import DemoFailure, transport
from packages.domain.contracts import PositionState
from services.execution import demo_flat_transport as flat
from services.execution.demo import coherent_snapshot, historical_snapshot
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery_budget import AuditBudgetExceeded, RecoveryBudget
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.integration.test_demo_period_review import period_case  # noqa: F401
from tests.integration.test_demo_recovery_batches import history_case  # noqa: F401
from tests.integration.test_demo_recovery_https import wire
from tests.unit.test_recovery_https_transport import tls_server  # noqa: F401


def parent_confirm(self) -> str:
    """Query existing intents; never submit/cancel an entry or submit an exit.

    Leftover protection is removed only after both exchange and local history
    establish flat. An ACK, a missing order, or a zero position alone is insufficient.
    """

    cleanup_keys: list[str] = []

    def observe(ledger: DemoLedger) -> bool:
        if not ledger.paused:
            raise DemoFailure("Flat confirmation requires paused entries")
        first = historical_snapshot(self.client, ledger, self.store.now())
        second = historical_snapshot(self.client, ledger, self.store.now())
        remote = coherent_snapshot(first, second, ledger)
        self._merge(ledger, remote)
        cleanup_keys[:] = [
            key
            for key, order in ledger.orders.items()
            if order.position_state == PositionState.CLOSED
            and any(
                remote.algos.get(identifier or "", {}).get("algoStatus") == "NEW"
                for identifier in (order.algo_id, order.tp_algo_id)
            )
        ]
        return bool(
            ledger.reconciled
            and not any(remote.positions.values())
            and not any(
                row["status"] in {"NEW", "PARTIALLY_FILLED"} for row in remote.orders.values()
            )
            and all(not o.remaining and not o.open_quantity for o in ledger.orders.values())
        )

    if not self.store.change(self.lease, "DEMO_FLAT_CONFIRMATION_QUERY", observe):
        return "UNKNOWN"
    if self.client.settings.demo_trading_enabled:
        for key in tuple(cleanup_keys):
            self._cleanup(key)

    def verify(ledger: DemoLedger) -> bool:
        if not observe(ledger):
            return False
        remote = historical_snapshot(self.client, ledger, self.store.now())
        return bool(
            remote.fingerprint == ledger.fingerprint
            and all(
                row["algoStatus"] in {"CANCELED", "EXPIRED", "FINISHED", "REJECTED"}
                for row in remote.algos.values()
            )
        )

    return (
        "VERIFIED_FLAT"
        if self.store.change(self.lease, "DEMO_FLAT_CONFIRMATION_CHECK", verify)
        else "UNKNOWN"
    )


def setup(history_case, tls_server, monkeypatch, setup_cost="0.8", request_cost="0.001"):
    result = wire(
        history_case,
        tls_server,
        monkeypatch,
        setup_cost=Decimal(setup_cost),
        request_cost=Decimal(request_cost),
    )
    elapsed = result[5]
    monkeypatch.setattr(flat, "RecoveryBudget", lambda: RecoveryBudget(lambda: float(elapsed[0])))
    return result


def track_rollback(store, monkeypatch):
    original = store.change
    failed = []

    def change(lease, kind, apply):
        before = store.read(lease.account_id)
        try:
            return original(lease, kind, apply)
        except Exception:
            failed.append((kind, before, store.read(lease.account_id)))
            raise

    monkeypatch.setattr(store, "change", change)
    return failed


def preserved(before, after):
    for name in (
        "policy",
        "risk_state",
        "paused",
        "requests",
        "orders",
        "daily_base",
        "weekly_base",
        "high_water",
        "net_transfers",
        "recovery_receipts",
    ):
        assert getattr(before, name) == getattr(after, name), name


def test_exact_parent_clock_expires_but_same_full_proof_pooled_completes(
    history_case, tls_server, monkeypatch
):
    store, c, fake, before, saved, elapsed, base = setup(history_case, tls_server, monkeypatch)
    call_start = len(fake.calls)
    failed = track_rollback(store, monkeypatch)
    source = c.client
    source.send = transport
    try:
        with pytest.raises(DemoFailure, match="Clock stale or cooldown active"):
            parent_confirm(c)
        assert elapsed[0] >= 60
        assert failed and all(a == b for _, a, b in failed)
        preserved(before, store.read("DEMO-test"))
        elapsed[0] = Decimal(0)
        fake.at = base
        result = c.confirm_flat()
        assert result == "VERIFIED_FLAT"
        metrics = c.flat_confirmation_diagnostic
        assert metrics["audit_connection_attempts"] == 1
        assert (
            metrics["audit_reused_requests"] > 100 and metrics["confirmation_cleanup_attempts"] == 0
        )
        assert c.client is source and source.send is transport
        final = store.read("DEMO-test")
        assert final.requests == before.requests and final.daily_base == before.daily_base
        queried = {
            fields.get("origClientOrderId") or fields.get("clientAlgoId")
            for m, p, fields in fake.calls[call_start:]
            if m == "GET"
        }
        bound = {
            identifier
            for order in before.orders.values()
            for identifier in (order.client_id, order.algo_id, order.tp_algo_id, order.exit_id)
            if identifier
        }
        assert bound <= queried
        assert final.requests == before.requests  # Unbound legacy ambiguous requests are preserved.
        assert all(m == "GET" for m, p, fields in fake.calls[call_start:])
    finally:
        source.send = saved


def test_slow_queries_still_block_before_lease_and_restore_client(
    history_case, tls_server, monkeypatch
):
    store, c, _, before, saved, elapsed, _ = setup(
        history_case, tls_server, monkeypatch, setup_cost="0.01", request_cost="0.4"
    )
    failed = track_rollback(store, monkeypatch)
    source = c.client
    source.send = transport
    try:
        with pytest.raises(AuditBudgetExceeded) as error:
            c.confirm_flat()
        assert error.value.code == "REQUEST_AUDIT_TIME_BUDGET"
        assert 40 <= elapsed[0] < 45
        assert failed and all(a == b for _, a, b in failed)
        preserved(before, store.read("DEMO-test"))
        assert c.client is source
        assert source.send is transport
        assert c.flat_confirmation_stage in {"QUERY", "VERIFY"}
        assert c.flat_confirmation_diagnostic["audit_connection_attempts"] == 1
    finally:
        source.send = saved


def test_clock_refresh_within_unchanged40_budget_and_all_history(
    history_case, tls_server, monkeypatch
):
    _, c, _, _, saved, elapsed, _ = setup(
        history_case, tls_server, monkeypatch, setup_cost="0.01", request_cost="0.14"
    )
    source = c.client
    source.send = transport
    try:
        assert c.confirm_flat() == "VERIFIED_FLAT"
        assert 20 <= elapsed[0] < 40
        assert c.flat_confirmation_diagnostic["confirmation_clock_probes"] >= 1
        assert c.flat_confirmation_stage == "VERIFY"
    finally:
        source.send = saved
