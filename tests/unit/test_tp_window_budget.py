"""Wire gate invariants with an independent clock and transport recorder."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo_tp_admission import TPAdmissionStopped, TPModelProfile
from integrations.binance_usdm.demo_tp_window_budget import (
    ProofDeferred,
    WindowTPBudget,
    weight_scope,
)
from integrations.binance_usdm.rest import RESTResponse
from tests.fixtures.tp_admission_synthetic_exchange import COSTS


def setup(synthetic=True):
    now, calls, metadata = [0.0], [], {"weight": "1", "status": 200, "missing": False}

    def send(method, path, query, key):
        calls.append((now[0], method, path))
        headers = {} if metadata["missing"] else {"x-mbx-used-weight-1m": metadata["weight"]}
        return RESTResponse(metadata["status"], b"{}", headers)

    b = WindowTPBudget(
        send,
        TPModelProfile(2),
        COSTS,
        allow_synthetic_model_execution=synthetic,
        monotonic=lambda: now[0],
    )
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    b.configure(
        {
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": 6000,
                }
            ]
        }
    )
    return b, now, calls, metadata


def test_default_real_execution_disabled():
    b, _, calls, _ = setup(False)
    with pytest.raises(TPAdmissionStopped):
        b.preflight()
    assert len(calls) == 1 and not b.admitted


def test_synthetic_6000_admits_with_entry_and_rescue_reserved():
    b, _, _, _ = setup()
    b.preflight()
    b.before_preparation()
    b.admit_entry()
    assert b.admitted and b.available() >= b.profile.entry + b.RESCUE


def test_lower_header_does_not_refund_own_attempts():
    b, _, _, metadata = setup()
    b.preflight()
    for _ in range(100):
        b("GET", "/fapi/v1/accountConfig", SecretStr(""), None)
    assert b.available() == 5700 - 501
    metadata["weight"] = "0"
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    assert b.available() == 5700 - 502


def test_concurrent_callers_cannot_cross_wire_ceiling():
    b, _, calls, _ = setup()
    b.preflight()
    b.begin_cleanup()
    b.flat_observed = True

    def call(_):
        try:
            with weight_scope("PROOF"):
                b("GET", "/fapi/v1/openOrders", SecretStr(""), None)
            return True
        except ProofDeferred:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        accepted = sum(pool.map(call, range(300)))
    assert accepted == 139
    assert b.peak == 5561 and len(calls) == 140


def test_own_weight_expires_only_after_guarded_60_seconds():
    b, now, _, _ = setup()
    b.preflight()
    b("GET", "/fapi/v1/accountConfig", SecretStr(""), None)
    now[0] = 60
    assert b.available() == 5694
    now[0] = 60.001
    assert b.available() == 5700


def test_proof_deferral_does_not_mark_exchange_failure_or_forward():
    b, _, calls, _ = setup()
    b.preflight()
    with pytest.raises(ProofDeferred):
        b.require_proof(5700)
    assert len(calls) == 1 and not b.stopped and not b.untrusted


@pytest.mark.parametrize("fault", ["missing", "rate", "backward", "nan", "foreign"])
def test_bad_metadata_or_scope_never_unblocks_writes(fault):
    b, now, calls, metadata = setup()
    b.preflight()
    if fault == "missing":
        metadata["missing"] = True
    elif fault == "rate":
        metadata["status"] = 429
    elif fault == "backward":
        now[0] = -1
    elif fault == "nan":
        now[0] = float("nan")
    else:
        with pytest.raises(TPAdmissionStopped):
            b("GET", "/fapi/v3/positionRisk", SecretStr("symbol=BTCUSDT"), None)
        assert len(calls) == 1
        return
    try:
        b("GET", "/fapi/v1/time", SecretStr(""), None)
    except TPAdmissionStopped:
        pass
    count = len(calls)
    with pytest.raises(TPAdmissionStopped):
        b("POST", "/fapi/v1/order", SecretStr("type=LIMIT"), None)
    assert len(calls) == count and b.stopped


def test_lost_entry_transport_is_charged_once():
    b, _, _, _ = setup()
    b.preflight()
    b.before_preparation()
    b.admit_entry()
    b.owned_entry_id, b.owned_quantity, b.owned_side = "DE-owned", Decimal(".0007"), "BUY"

    def lost(*args):
        raise OSError("lost response")

    b.sender = lost
    query = SecretStr("type=LIMIT&newClientOrderId=DE-owned&symbol=BTCUSDT&side=BUY&quantity=.0007")
    with pytest.raises(OSError):
        b("POST", "/fapi/v1/order", query, None)
    charged = b.total_charge
    with pytest.raises(TPAdmissionStopped):
        b("POST", "/fapi/v1/order", query, None)
    assert b.entry_sent and b.total_charge == charged


def test_rescue_reserve_survives_ordinary_stop():
    b, _, _, _ = setup()
    b.preflight()
    b.begin_cleanup()
    with pytest.raises(TPAdmissionStopped):
        for _ in range(200):
            b("GET", "/fapi/v1/openOrders", SecretStr(""), None)
    assert b.available() >= 512
    with weight_scope("RESCUE"):
        b("GET", "/fapi/v3/positionRisk", SecretStr(""), None)
    assert b.peak <= 5700
