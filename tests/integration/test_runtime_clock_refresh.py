"""A rejected SDK clock response cannot authorize proof through its weight header."""
# ruff: noqa: F811

import asyncio
import sqlite3

import pytest

from integrations.binance_usdm.demo import DemoClockSample, DemoFailure
from integrations.binance_usdm.demo_tp_admission import TPAdmissionStopped
from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred
from integrations.binance_usdm.rest import RESTResponse
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_runtime_header_provenance import prepared


@pytest.mark.parametrize(
    "fault", ["missing_time", "wrong_shape", "malformed_json", "boolean_time", "offset", "slow_rtt"]
)
def test_rejected_clock_body_or_sample_cannot_leave_proof_admissible(assembly, fault):
    calls = []
    bodies = {
        "missing_time": b"{}",
        "wrong_shape": b"[]",
        "malformed_json": b"{",
        "boolean_time": b'{"serverTime":true}',
        "offset": b'{"serverTime":1005000}',
        "slow_rtt": b'{"serverTime":1000000}',
    }

    def send(method, path, query, key):
        calls.append(path)
        return RESTResponse(200, bodies[fault], {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    client = session.coordinator.client
    client.synced_at = 999_000
    client.clock_sample = DemoClockSample(999_000, 999_000, 999_000)
    ticks = iter([1_000_000, 1_000_000, 1_001_001] if fault == "slow_rtt" else [1_000_000] * 3)
    client.clock_ms = lambda: next(ticks, 1_001_001 if fault == "slow_rtt" else 1_000_000)
    try:
        assembly[3].seconds += 6
        with pytest.raises(ProofDeferred):
            b.require_proof(session.coordinator.proof_batch)
        with pytest.raises(DemoFailure):
            client.sync_clock()
        assert b.untrusted and b.stopped
        assert not b._fresh(b.clock())
        assert client.synced_at is None and client.clock_sample is None
        with pytest.raises(TPAdmissionStopped):
            b.require_proof(session.coordinator.proof_batch)
        assert calls == ["/fapi/v1/time"] * 2 and b.total_charge == 2
        # A received response is known, not an invented possible-send failure.
        assert b.shared.read_only_snapshot()["pending_attempts"] == 0
    finally:
        session.close()


def test_successful_sdk_refresh_keeps_original_client_and_cost_controls(assembly):
    calls = []

    def send(method, path, query, key):
        calls.append(path)
        return RESTResponse(200, b'{"serverTime":1000000}', {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    client = session.coordinator.client
    client.clock_ms = lambda: 1_000_000
    source = assembly[5]
    before = (source.offset, source.synced_at, source.clock_sample)
    try:
        assembly[3].seconds += 6
        with pytest.raises(ProofDeferred):
            b.require_proof(session.coordinator.proof_batch)
        client.sync_clock()
        b.require_proof(session.coordinator.proof_batch)
        assert client.synced_at == 1_000_000 and client.offset == 0
        assert client.clock_sample is not None
        assert (source.offset, source.synced_at, source.clock_sample) == before
        assert calls == ["/fapi/v1/time"] * 2 and b.total_charge == 2
        assert not b.stopped and b.clock_refresh_failures == 0
    finally:
        session.close()


@pytest.mark.parametrize("initial_age", [0, 6])
def test_existing_confirmation_loop_refreshes_after_midproof_deferral(
    assembly, monkeypatch, initial_age
):
    from integrations.binance_usdm.demo_tp_window_budget import scope

    calls = []

    def send(method, path, query, key):
        calls.append((path, scope.get()))
        body = (
            b'[{"symbol":"BTCUSDT","positionSide":"BOTH","positionAmt":"0","entryPrice":"0"}]'
            if path.endswith("positionRisk")
            else b'{"serverTime":1000000}'
        )
        return RESTResponse(200, body, {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    c = session.coordinator
    c.client.clock_ms = lambda: 1_000_000
    reached = []

    class ProofReached(Exception):
        pass

    def proof_checkpoint():
        reached.append(b.header_sequence)
        if len(reached) == 1:
            assembly[3].seconds += 6
            b.require_proof(c.proof_batch)
            raise AssertionError("stale proof admitted")
        assert b._fresh(b.clock())
        # This isolates scheduling. Do not invent a full-flat result.
        raise ProofReached

    monkeypatch.setattr(c, "confirm_flat", proof_checkpoint)
    try:
        assembly[3].seconds += initial_age
        with pytest.raises(ProofReached):
            asyncio.run(c.confirm_exit_budgeted())
        assert len(reached) == 2 and reached[1] > reached[0]
        assert b.proof_metadata_deferrals == 1 and b.clock_refresh_failures == 0
        assert c.confirmation_attempts_completed == 0 and c.budgeted_flat_confirmation == "UNKNOWN"
        assert calls[-1] == ("/fapi/v1/time", "WATCH")
        assert all(lane == "WATCH" for _, lane in calls[1:])
        assert not b.stopped and b.shared.read_only_snapshot()["pending_attempts"] == 0
    finally:
        session.close()


@pytest.mark.parametrize("fault", ["malformed_json", "throttle", "timeout"])
def test_confirmation_refresh_failure_stops_without_reaching_or_retrying_proof(
    assembly, monkeypatch, fault
):
    from packages.persistence.demo_shared_weight import SharedWeightStopped

    calls = []

    def send(method, path, query, key):
        calls.append(path)
        if len(calls) == 1:
            return RESTResponse(200, b'{"serverTime":1000000}', {"x-mbx-used-weight-1m": "600"})
        if fault == "timeout":
            raise TimeoutError("synthetic response loss")
        return RESTResponse(
            429 if fault == "throttle" else 200, b"{", {"x-mbx-used-weight-1m": "601"}
        )

    session, b = prepared(assembly, send)
    c = session.coordinator
    c.client.clock_ms = lambda: 1_000_000

    def forbidden():
        raise AssertionError("proof must not start")

    monkeypatch.setattr(c, "confirm_flat", forbidden)
    try:
        assembly[3].seconds += 6
        with pytest.raises((DemoFailure, TimeoutError)):
            asyncio.run(c.confirm_exit_budgeted())
        assert calls == ["/fapi/v1/time"] * 2 and b.clock_refresh_failures == 1
        assert b.untrusted and b.stopped and c.client.synced_at is None
        assert c.confirmation_attempts_completed == 0 and c.budgeted_flat_confirmation == "UNKNOWN"
        with pytest.raises(TPAdmissionStopped):
            b.require_proof(c.proof_batch)
        with sqlite3.connect(b.shared.path.as_uri() + "?mode=ro", uri=True) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE done IS NULL"
            ).fetchone()[0] == (0 if fault == "malformed_json" else 1)
        if fault == "malformed_json":
            assert b.shared.read_only_snapshot()["pending_attempts"] == 0
        else:
            with pytest.raises(SharedWeightStopped):
                b.shared.read_only_snapshot()
    finally:
        session.close()
