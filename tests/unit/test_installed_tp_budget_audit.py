"""Audit integrity and explicit limitations; never an execution admission test."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.rest import RESTResponse
from tests.fixtures.installed_tp_budget_audit import COSTS, Meter
from tests.fixtures.installed_tp_phase_audit import PhaseAudit


def meter(capacity=6000):
    called = []
    fake = SimpleNamespace(at=datetime(2026, 10, 19, 7, tzinfo=UTC), positions={}, algos={})

    def send(*args):
        called.append(args)
        return RESTResponse(200, b"{}", {})

    return Meter(send, fake, capacity=capacity), called


def test_synthetic_capacity_is_get_only_and_stops_at_first_exceedance():
    m, called = meter(capacity=6)
    assert m("GET", "/fapi/v1/accountConfig", SecretStr(""), None).status == 200
    assert m("GET", "/fapi/v1/accountConfig", SecretStr(""), None).status == 429
    assert len(called) == 1 and m.rows[-1]["used_weight_1m_model"] == 10
    assert all(COSTS[route] == 0 for route in COSTS if route[0] != "GET")


def test_model_counter_resets_only_on_a_new_minute():
    m, _ = meter()
    m("GET", "/fapi/v1/accountConfig", SecretStr(""), None)
    m.base += timedelta(minutes=1)
    m("GET", "/fapi/v1/accountConfig", SecretStr(""), None)
    assert list(m.peaks.values()) == [5, 5]


@pytest.mark.parametrize(
    "path,params",
    [
        ("/fapi/v1/unknown", ""),
        ("/fapi/v1/openOrders", "symbol=BTCUSDT"),
        ("/fapi/v1/openAlgoOrders", "symbol=BTCUSDT"),
        ("/fapi/v1/klines", "symbol=BTCUSDT&interval=1m&limit=1000"),
        ("/fapi/v1/ticker/bookTicker", ""),
    ],
)
def test_unmodeled_route_or_query_scope_has_no_invented_cost(path, params):
    m, called = meter()
    with pytest.raises(AssertionError):
        m("GET", path, SecretStr(params), None)
    assert not called


def test_observer_preserves_response_identity_and_redacts_private_inputs():
    response = RESTResponse(200, b"private-body-probe", {})
    audit = PhaseAudit(lambda *a: response, {("GET", "/fapi/v1/accountConfig"): 5})
    with audit.span("OBSERVATION"):
        result = audit(
            "GET",
            "/fapi/v1/accountConfig",
            SecretStr("signature=private-signature-probe"),
            SecretStr("private-key-probe"),
        )
    assert result is response
    report = audit.report()
    assert not any(
        x in json.dumps(report)
        for x in ("private-key-probe", "private-signature-probe", "private-body-probe")
    )
    assert report["phase_assumed_weights"] == {"OBSERVATION": 5}


def test_full_history_proof_phase_has_priority_during_finalization():
    audit = PhaseAudit(
        lambda *a: RESTResponse(200, b"{}", {}), {("GET", "/fapi/v1/accountConfig"): 5}
    )
    audit.begin_finalization()
    with audit.span("FLAT_CONFIRMATION"):
        audit("GET", "/fapi/v1/accountConfig", SecretStr(""), None)
    assert audit.report()["phase_attempts"] == {"FLAT_CONFIRMATION": 1}


def test_truncated_or_unknown_trace_is_explicit_not_a_complete_proof():
    audit = PhaseAudit(
        lambda *a: RESTResponse(200, b"{}", {}), {("GET", "/fapi/v1/accountConfig"): 5}, trace_cap=1
    )
    audit("GET", "/fapi/v1/accountConfig", SecretStr(""), None)
    audit("GET", "/fapi/v1/unknown", SecretStr(""), None)
    r = audit.report()
    assert r["trace_truncated"] and not r["complete_cost_model"] and r["unmodeled_attempts"] == 1


@pytest.mark.parametrize("cost", [-1, True])
def test_invalid_cost_assumptions_are_rejected(cost):
    with pytest.raises(ValueError):
        PhaseAudit(lambda *a: None, {("GET", "/path"): cost})
