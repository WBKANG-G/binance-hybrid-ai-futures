"""Require current authoritative metadata before starting a full proof batch."""

# ruff: noqa: F811
import sqlite3

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred
from integrations.binance_usdm.rest import RESTResponse
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_quota_startup_review import snapshot
from tests.integration.test_runtime_header_provenance import prepared
from tests.unit.test_quota_startup_read_only import files


@pytest.mark.parametrize("age", [5.001, 6.0, 60.002])
def test_stale_observation_defers_full_proof_before_reservation_or_send(assembly, age):
    calls = []

    def send(method, path, query, key):
        calls.append((method, path))
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    try:
        assembly[3].seconds += age
        before = b.shared.read_only_snapshot()
        domain, disk = snapshot(assembly[0]), files(assembly[8].order_directory)
        shared_disk = files(b.shared.directory)
        previous = (b.total_charge, b.forwarded, b.header_at, b.header_sequence, len(calls))
        with pytest.raises(ProofDeferred):
            b.require_proof(session.coordinator.proof_batch)
        assert b.shared.read_only_snapshot() == before
        assert snapshot(assembly[0]) == domain
        assert (
            files(assembly[8].order_directory) == disk and files(b.shared.directory) == shared_disk
        )
        assert (b.total_charge, b.forwarded, b.header_at, b.header_sequence, len(calls)) == previous
        assert not b.stopped and not b.untrusted
    finally:
        session.close()


@pytest.mark.parametrize("age", [0, 4.999, 5.0])
def test_current_observation_allows_original_proof_budget_check(assembly, age):
    def send(method, path, query, key):
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    try:
        assembly[3].seconds += age
        b.require_proof(session.coordinator.proof_batch)
        assert b.proof_metadata_deferrals == 0 and not b.stopped
    finally:
        session.close()


def test_only_explicit_authoritative_read_recovers_stale_proof_admission(assembly):
    calls = []

    def send(method, path, query, key):
        calls.append(path)
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    try:
        assembly[3].seconds += 6
        for _ in range(2):
            with pytest.raises(ProofDeferred):
                b.require_proof(session.coordinator.proof_batch)
        assert calls == ["/fapi/v1/time"] and b.total_charge == 1
        b("GET", "/fapi/v1/time", SecretStr(""), None)
        b.require_proof(session.coordinator.proof_batch)
        assert calls == ["/fapi/v1/time"] * 2 and b.total_charge == 2
        assert b.proof_metadata_deferrals == 2 and not b.stopped
    finally:
        session.close()


@pytest.mark.parametrize("fault", ["missing", "throttle", "timeout"])
def test_failed_explicit_refresh_never_reopens_proof_or_retries(assembly, fault):
    from integrations.binance_usdm.demo_tp_admission import TPAdmissionStopped
    from packages.persistence.demo_shared_weight import SharedWeightStopped

    calls = []

    def send(method, path, query, key):
        calls.append(path)
        if len(calls) == 1:
            return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})
        if fault == "timeout":
            raise TimeoutError("synthetic clock response lost")
        return RESTResponse(429 if fault == "throttle" else 200, b"{}", {})

    session, b = prepared(assembly, send)
    try:
        assembly[3].seconds += 6
        with pytest.raises(ProofDeferred):
            b.require_proof(session.coordinator.proof_batch)
        if fault == "timeout":
            with pytest.raises(TimeoutError):
                b("GET", "/fapi/v1/time", SecretStr(""), None)
        else:
            b("GET", "/fapi/v1/time", SecretStr(""), None)
        with pytest.raises(TPAdmissionStopped):
            b.require_proof(session.coordinator.proof_batch)
        assert b.stopped and b.untrusted and len(calls) == 2
        assert b.total_charge == 2
        with sqlite3.connect(b.shared.path.as_uri() + "?mode=ro", uri=True) as connection:
            assert connection.execute(
                "SELECT COUNT(*), SUM(weight) FROM attempts WHERE done IS NULL"
            ).fetchone() == (1, 1)
        with pytest.raises(SharedWeightStopped):
            b.shared.read_only_snapshot()
    finally:
        session.close()


def test_missing_observation_defers_without_creating_one(assembly):
    def send(method, path, query, key):
        raise AssertionError("implicit request forbidden")

    args = list(assembly)
    args[6] = send
    from tests.integration.test_managed_runtime_assembly import open_session

    session = open_session(args, allow_synthetic_model_execution=True)
    b = session.budget
    try:
        with pytest.raises(ProofDeferred):
            b.require_proof(session.coordinator.proof_batch)
        assert b.header_at is None and b.forwarded == 0 and b.total_charge == 0
        assert b.shared.read_only_snapshot()["pending_attempts"] == 0
        assert not b.stopped
    finally:
        session.close()


def test_clock_regression_is_a_hard_stop_instead_of_a_resource_deferral(assembly):
    from integrations.binance_usdm.demo_tp_admission import TPAdmissionStopped

    def send(method, path, query, key):
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    try:
        assembly[3].seconds -= 1
        with pytest.raises(TPAdmissionStopped):
            b.require_proof(session.coordinator.proof_batch)
        assert b.untrusted and b.stopped and b.reason == "MONOTONIC_CLOCK_UNTRUSTED"
        assert b.proof_metadata_deferrals == 0 and b.total_charge == 1
    finally:
        session.close()
