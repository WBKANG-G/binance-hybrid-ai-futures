"""Instrumentation cannot change calls, hide errors, or leak request material."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.rest import RESTResponse
from tests.fixtures.tp_phase_audit import PhaseAudit


def test_same_request_and_response_once_no_private_material_recorded():
    calls = []
    response = RESTResponse(200, b"private-body", {"Set-Cookie": "private-cookie"})
    query, key = SecretStr("private-query"), SecretStr("private-key")

    def sender(*args):
        calls.append(args)
        return response

    audit = PhaseAudit(sender, {("GET", "/known"): 7})
    with audit.span("PREPARATION"):
        assert audit("GET", "/known", query, key) is response
    assert len(calls) == 1 and calls[0] == ("GET", "/known", query, key)
    encoded = json.dumps(audit.report())
    assert "private" not in encoded
    assert audit.report()["phase_assumed_weights"] == {"PREPARATION": 7}


def test_original_exception_propagates_without_retry_or_error_text():
    calls = []
    original = OSError("private-transport-error")

    def fail(*args):
        calls.append(True)
        raise original

    audit = PhaseAudit(fail, {("POST", "/known"): 40})
    with pytest.raises(OSError) as error:
        audit("POST", "/known", SecretStr("private"), None)
    assert error.value is original and len(calls) == 1
    assert audit.report()["trace"][0]["status"] is None
    assert "private" not in json.dumps(audit.report())


def test_nested_spans_are_exclusive_and_finalization_crosses_thread_boundaries():
    audit = PhaseAudit(lambda *a: RESTResponse(200, b"{}", {}), {("GET", "/known"): 1})
    with audit.span("OBSERVATION"):
        with audit.span("SESSION_READY"):
            audit("GET", "/known", SecretStr(""), None)
    audit.begin_finalization()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(audit, "GET", "/known", SecretStr(""), None).result()
    with audit.span("FLAT_CONFIRMATION"):
        audit("GET", "/known", SecretStr(""), None)
    r = audit.report()
    assert r["phase_attempts"] == {"OBSERVATION": 1, "FINALIZATION": 1, "FLAT_CONFIRMATION": 1}
    assert r["attempts"] == r["known_assumed_total_weight"] == 3


def test_context_propagation_and_reset_after_exception():
    async def run():
        audit = PhaseAudit(lambda *a: RESTResponse(200, b"{}", {}), {("GET", "/known"): 1})
        with pytest.raises(ValueError):
            with audit.span("PREPARATION"):
                await asyncio.to_thread(audit, "GET", "/known", SecretStr(""), None)
                raise ValueError
        audit("GET", "/known", SecretStr(""), None)
        assert audit.report()["phase_attempts"] == {"PREPARATION": 1, "SESSION_OTHER": 1}

    asyncio.run(run())


def test_trace_cap_never_discards_aggregate_cost_or_blocks_forwarding():
    calls = []

    def send(*args):
        calls.append(True)
        return RESTResponse(200, b"{}", {})

    audit = PhaseAudit(send, {("GET", "/known"): 2}, trace_cap=2)
    for _ in range(5):
        audit("GET", "/known", SecretStr(""), None)
    r = audit.report()
    assert len(calls) == r["attempts"] == 5 and r["known_assumed_total_weight"] == 10
    assert len(r["trace"]) == 2 and r["trace_truncated"]


def test_unmodeled_path_is_redacted_and_cannot_claim_complete_cost_model():
    audit = PhaseAudit(lambda *a: RESTResponse(200, b"{}", {}), {})
    audit("GET", "/private-user-id", SecretStr("private"), None)
    r = audit.report()
    assert not r["complete_cost_model"] and r["unmodeled_attempts"] == 1
    assert r["trace"][0]["assumed_weight"] is None
    assert "private" not in json.dumps(r)


@pytest.mark.parametrize("cap", [0, -1, 10001, True, 1.5])
def test_bad_trace_caps_rejected(cap):
    with pytest.raises(ValueError):
        PhaseAudit(lambda *a: None, {}, trace_cap=cap)


@pytest.mark.parametrize("weight", [0, -1, True, 1.5])
def test_bad_costs_rejected(weight):
    with pytest.raises(ValueError):
        PhaseAudit(lambda *a: None, {("GET", "/known"): weight})
