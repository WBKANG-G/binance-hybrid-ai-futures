"""Model-only candidate: never an execution approval or shared-IP reservation."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoFailure, DemoRateLimited
from integrations.binance_usdm.demo_tp_admission import (
    TPAdmissionBudget,
    TPAdmissionStopped,
    TPModelProfile,
    published_limit,
)
from integrations.binance_usdm.rest import RESTResponse
from tests.unit.test_demo_transport import client

COSTS = {
    ("GET", "/fapi/v1/time"): 1,
    ("GET", "/fapi/v1/exchangeInfo"): 1,
    ("GET", "/fapi/v1/accountConfig"): 5,
    ("POST", "/fapi/v1/order"): 40,
    ("POST", "/fapi/v1/algoOrder"): 40,
    ("DELETE", "/fapi/v1/listenKey"): 40,
}


def setup(limit=100000, synthetic=True):
    calls = []
    clock = [1.0]
    state = {"header": True, "status": 200, "weight": 1}

    def send(m, p, q, k):
        calls.append((m, p))
        headers = {"x-mbx-used-weight-1m": str(state["weight"])} if state["header"] else {}
        return RESTResponse(state["status"], b"{}", headers | {"retry-after": "120"})

    b = TPAdmissionBudget(
        send,
        TPModelProfile(2),
        COSTS,
        allow_synthetic_model_execution=synthetic,
        monotonic=lambda: clock[0],
    )
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    b.configure(
        {
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": limit,
                }
            ]
        }
    )
    return b, calls, clock, state


def admitted():
    b, calls, clock, state = setup()
    b.preflight()
    b.before_preparation()
    b.admit_entry()
    return b, calls, clock, state


def test_published6000_refuses_before_preparation_reservation_or_post():
    b, calls, _, _ = setup(limit=6000)
    with pytest.raises(TPAdmissionStopped):
        b.preflight()
    r = b.diagnostics()
    assert r["reason"] == "MODEL_LIFECYCLE_DOES_NOT_FIT" and not r["model_entry_admitted"]
    assert r["profile"]["cleanup_assumption"] == 28352 and len(calls) == 1


def test_default_never_enables_real_execution_even_at_large_limit():
    b, calls, _, _ = setup(synthetic=False)
    with pytest.raises(TPAdmissionStopped):
        b.preflight()
    assert b.reason == "UNPROVEN_MODEL_REAL_EXECUTION_DISABLED" and len(calls) == 1
    assert not b.diagnostics()["actual_tp_execution_approved"]


@pytest.mark.parametrize("params", ["type=MARKET", "type=LIMIT", "type=LIMIT&reduceOnly=false"])
def test_no_new_entry_without_admission(params):
    b, calls, _, _ = setup()
    b.preflight()
    count = len(calls)
    with pytest.raises(TPAdmissionStopped):
        b("POST", "/fapi/v1/order", SecretStr(params), None)
    assert len(calls) == count


def test_possible_send_entry_is_one_shot_and_no_refund_after_exception():
    b, calls, _, _ = admitted()

    def lost(*a):
        raise OSError("private-transport-failure")

    b.sender = lost
    with pytest.raises(OSError):
        b("POST", "/fapi/v1/order", SecretStr("type=LIMIT"), None)
    assert b.entry_sent and b.used == 41 and b.untrusted
    with pytest.raises(TPAdmissionStopped):
        b("POST", "/fapi/v1/order", SecretStr("type=LIMIT"), None)
    assert b.used == 41


def test_cleanup_reserve_is_not_consumed_by_ordinary_requests():
    b, calls, _, _ = admitted()
    b.finish_entry()
    b.used = 100000 - b.profile.cleanup - b.profile.headroom - 3
    count = len(calls)
    with pytest.raises(TPAdmissionStopped):
        b("GET", "/fapi/v1/accountConfig", SecretStr(""), None)
    assert b.used == 100000 - b.profile.cleanup - b.profile.headroom - 3 and len(calls) == count
    b.begin_cleanup()
    assert b("GET", "/fapi/v1/accountConfig", SecretStr(""), None).status == 200


def test_entry_protection_finishes_even_if_model_allowance_is_exceeded():
    b, calls, _, _ = admitted()
    b("POST", "/fapi/v1/order", SecretStr("type=LIMIT"), None)
    b.weights["ENTRY_PROTECTION"] = b.profile.entry
    assert b("POST", "/fapi/v1/algoOrder", SecretStr("type=STOP_MARKET"), None).status == 200
    assert b.entry_overrun and b.stopped
    assert b("POST", "/fapi/v1/algoOrder", SecretStr("type=TAKE_PROFIT_MARKET"), None).status == 200
    with pytest.raises(TPAdmissionStopped):
        b.finish_entry()
    b.begin_cleanup()
    assert b("POST", "/fapi/v1/order", SecretStr("type=MARKET&reduceOnly=true"), None).status == 200


def test_missing_header_does_not_rewrite_post_response_or_block_existing_protection():
    b, _, _, state = admitted()
    state["header"] = False
    response = b("POST", "/fapi/v1/order", SecretStr("type=LIMIT"), None)
    assert response.status == 200 and b.stopped
    assert b("POST", "/fapi/v1/algoOrder", SecretStr("type=STOP_MARKET"), None).status == 200
    with pytest.raises(TPAdmissionStopped):
        b.finish_entry()


def test_private_key_close_survives_local_stop_before_finalization_switch():
    b, calls, _, _ = admitted()
    b.finish_entry()
    b._mark("TEST_LOCAL_STOP")
    assert b.phase == "OBSERVATION"
    assert b("DELETE", "/fapi/v1/listenKey", SecretStr(""), None).status == 200
    assert b.weights["CLEANUP"] == 40 and calls[-1] == ("DELETE", "/fapi/v1/listenKey")


def test_new_entry_forbidden_during_cleanup_even_after_previous_admission():
    b, calls, _, _ = admitted()
    b.begin_cleanup()
    count = len(calls)
    with pytest.raises(TPAdmissionStopped):
        b("POST", "/fapi/v1/order", SecretStr("type=LIMIT"), None)
    assert len(calls) == count


@pytest.mark.parametrize("phase", ["BOOTSTRAP", "PREPARATION", "OBSERVATION", "CLEANUP"])
def test_unknown_routes_have_no_fallback_cost(phase):
    b, calls, _, _ = setup()
    b.phase = phase
    count = len(calls)
    with pytest.raises(TPAdmissionStopped):
        b("GET", "/unknown", SecretStr(""), None)
    assert len(calls) == count and b.reason == "UNMODELED_ROUTE"


def test_lower_weight_header_and_new_minute_never_refund_credit():
    b, _, clock, state = admitted()
    b.finish_entry()
    before = b.used
    state["weight"] = 0
    clock[0] += 120
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    assert b.used == before + 1 and b.diagnostics()["minute_reset_credit"] == 0


@pytest.mark.parametrize("delta", [6, -1, float("nan")])
def test_stale_or_invalid_clock_denies_new_admission(delta):
    b, calls, clock, _ = setup()
    clock[0] += delta
    with pytest.raises(TPAdmissionStopped):
        b.preflight()
    assert len(calls) == 1


@pytest.mark.parametrize("status", [418, 429])
def test_sdk_cooldown_is_preserved_and_cleanup_does_not_bypass_it(status):
    c, _ = client(False)
    b, _, _, state = setup()
    b.preflight()
    state["status"] = status
    c.send = b
    c.synced_at = c.clock_ms()
    with pytest.raises(DemoRateLimited):
        c.request("GET", "/fapi/v1/accountConfig")
    count = b.forwarded
    b.begin_cleanup()
    with pytest.raises(DemoFailure):
        c.request("GET", "/fapi/v1/accountConfig")
    assert b.forwarded == count and c.cooldown_until > c.clock_ms()


def test_independent_thread_cannot_consume_reserved_entry_allowance():
    b, calls, _, _ = admitted()
    b.used = 100000 - b.profile.cleanup - b.profile.entry - b.profile.headroom
    count = len(calls)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(b, "GET", "/fapi/v1/accountConfig", SecretStr(""), None)
        with pytest.raises(TPAdmissionStopped):
            future.result()
    assert len(calls) == count
    assert b("POST", "/fapi/v1/algoOrder", SecretStr("type=STOP_MARKET"), None).status == 200


@pytest.mark.parametrize(
    "raw",
    [
        {},
        [],
        {"rateLimits": []},
        {
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": True,
                    "limit": 6000,
                }
            ]
        },
    ],
)
def test_invalid_limit_metadata_is_rejected(raw):
    with pytest.raises(TPAdmissionStopped):
        published_limit(raw)


@pytest.mark.parametrize("history", [-1, 129, True])
def test_history_profile_outside_candidate_scope_is_rejected(history):
    with pytest.raises(ValueError):
        TPModelProfile(history)


def test_cleanup_model_overrun_is_reported_without_blocking_known_safety_requests():
    b, calls, _, state = admitted()
    b.finish_entry()
    b.begin_cleanup()
    b.weights["CLEANUP"] = b.profile.cleanup
    state["header"] = False
    assert b("POST", "/fapi/v1/order", SecretStr("type=MARKET&reduceOnly=true"), None).status == 200
    assert b.cleanup_overrun and b.untrusted and b.stopped
    assert b("GET", "/fapi/v1/accountConfig", SecretStr(""), None).status == 200
    assert calls[-1] == ("GET", "/fapi/v1/accountConfig")
