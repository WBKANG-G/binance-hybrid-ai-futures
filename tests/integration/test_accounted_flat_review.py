"""Five bounded GETs preserve prior audit history; invalid or nonflat data stops early."""

# ruff: noqa: F401, F811
import json
from urllib.parse import parse_qsl
from uuid import uuid5

import pytest

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import probe_demo_accounted_reads as probe
from scripts import review_demo_read_deployment as deployment
from tests.integration.test_accounted_read_probe import (
    anchored,
    candidate,
    demo_system,
    installation,
    invoke,
    marker,
    ready,
    sender,
)
from tests.integration.test_runtime_read_accounting import financial


@pytest.fixture
def prior(installation):
    result = invoke(installation, sender(installation, []))
    assert result["modeled_weight_charged"] == 6
    return installation


def flat_marker(case):
    return deployment.STORAGE.parent / (
        "accounted-flat-review-" + str(uuid5(case[0][4], probe.FLAT_POLICY))
    )


def flat_sender(case, seen, change=None):
    charge = 6

    def send(method, path, query, key):
        nonlocal charge
        assert method == "GET"
        seen.append(path)
        resources, actions = probe.resource_rows(case[0][1].engine, case[0][0].scope)
        assert actions[max(actions)]["kind"] == "RESERVE" and actions[max(actions)]["path"] == path
        costs = dict(probe.FLAT_ROUTES)
        charge += costs[path]
        if path.endswith("/time"):
            body = {"serverTime": int(case[0][2].utc().timestamp() * 1000)}
            assert key is None and query.get_secret_value() == ""
        else:
            assert key is not None
            assert set(dict(parse_qsl(query.get_secret_value()))) == {
                "timestamp",
                "recvWindow",
                "signature",
            }
            body = (
                {"dualSidePosition": False, "multiAssetsMargin": False}
                if path.endswith("/accountConfig")
                else []
            )
        if change is not None:
            body = change(path, body)
        return RESTResponse(200, json.dumps(body).encode(), {"x-mbx-used-weight-1m": str(charge)})

    return send


def test_five_global_gets_link_prior_receipt_and_preserve_all_existing_rows(prior):
    a = prior[0]
    fin = financial(a)
    before, _ = probe.resource_rows(a[1].engine, a[0].scope)
    old_receipt = {p.name: p.read_bytes() for p in marker(prior).iterdir()}
    seen = []
    result = invoke(prior, flat_sender(prior, seen), flat_review=True)
    assert seen == [p for p, _ in probe.FLAT_ROUTES]
    assert result["http_attempts"] == result["completed_reads"] == 5
    assert result["modeled_weight_charged"] == 91
    assert result["audit_events_added"] == result["resource_checkpoints_added"] == 10
    assert result["accounted_read_probe"] == "FIVE_GETS_FLAT_OBSERVED_KEEP_PAUSED"
    assert result["prior_two_get_receipt_verified"] and result["positions_or_orders_checked"]
    assert (
        result["positions_empty_at_read"]
        and result["regular_orders_empty_at_read"]
        and result["algo_orders_empty_at_read"]
    )
    assert result["position_rows_observed"] == 0 and not result["snapshot_atomic"]
    assert len(result["observation_times_utc"]) == 3
    assert (
        not result["exchange_account_identity_verified"]
        and not result["account_order_usage_verified"]
    )
    assert (
        not result["execution_approved"]
        and not result["orders_enabled"]
        and not result["live_trading_enabled"]
    )
    after, actions = probe.resource_rows(a[1].engine, a[0].scope)
    assert all(after[k] == v for k, v in before.items()) and len(after) == len(before) + 20
    assert sum(r["cost"] for r in actions.values() if r["kind"] == "RESERVE") == 97
    assert financial(a) == fin
    assert {p.name: p.read_bytes() for p in marker(prior).iterdir()} == old_receipt
    assert flat_marker(prior).stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in flat_marker(prior).iterdir())
    with pytest.raises(probe.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        invoke(prior, lambda *a: pytest.fail("repeat wire"), flat_review=True)


def test_zero_rows_across_multiple_symbols_are_valid_without_symbol_filter(prior):
    seen = []

    def rows(path, body):
        return (
            [
                dict(symbol="BTCUSDT", positionSide="BOTH", positionAmt="0.000"),
                dict(symbol="ETHUSDT", positionSide="BOTH", positionAmt="-0"),
            ]
            if path.endswith("/positionRisk")
            else body
        )

    result = invoke(prior, flat_sender(prior, seen, rows), flat_review=True)
    assert result["position_rows_observed"] == 2 and len(seen) == 5


@pytest.mark.parametrize(
    "path,payload,code,attempts,weight",
    [
        (
            "/fapi/v3/positionRisk",
            [dict(symbol="BTCUSDT", positionSide="BOTH", positionAmt="0.1")],
            "NONZERO_POSITION",
            3,
            11,
        ),
        (
            "/fapi/v3/positionRisk",
            [dict(symbol="ETHUSDT", positionSide="BOTH", positionAmt="-1")],
            "NONZERO_POSITION",
            3,
            11,
        ),
        ("/fapi/v3/positionRisk", {}, "LIST_REQUIRED", 3, 11),
        (
            "/fapi/v3/positionRisk",
            [dict(symbol="BTCUSDT", positionSide="LONG", positionAmt="0")],
            "SIDE_INVALID",
            3,
            11,
        ),
        (
            "/fapi/v3/positionRisk",
            [dict(symbol="BTCUSDT", positionSide="BOTH", positionAmt="NaN")],
            "AMOUNT_INVALID",
            3,
            11,
        ),
        ("/fapi/v1/openOrders", [dict(orderId=1)], "OPEN_ORDERS_OBSERVED", 4, 51),
        ("/fapi/v1/openAlgoOrders", [dict(algoId=1)], "OPEN_ORDERS_OBSERVED", 5, 91),
        ("/fapi/v1/openAlgoOrders", {"orders": []}, "LIST_REQUIRED", 5, 91),
    ],
)
def test_nonflat_or_invalid_response_stops_without_cancel_or_retry(
    prior, path, payload, code, attempts, weight
):
    seen = []
    state = {}
    fin = financial(prior[0])

    def change(route, body):
        return payload if route == path else body

    with pytest.raises(probe.ProbeBlocked, match=code):
        invoke(prior, flat_sender(prior, seen, change), flat_review=True, state=state)
    assert len(seen) == attempts and state["transport"]["http_attempts"] == attempts
    assert state["transport"]["modeled_weight_charged"] == weight
    assert financial(prior[0]) == fin
    assert flat_marker(prior).joinpath("intent.json").exists()
    assert not flat_marker(prior).joinpath("complete.json").exists()
    with pytest.raises(probe.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        invoke(prior, lambda *a: pytest.fail("retry"), flat_review=True)


def test_prior_receipt_is_required_before_credentials_or_requests(installation):
    with pytest.raises((probe.ProbeBlocked, OSError)):
        invoke(
            installation,
            lambda *a: pytest.fail("wire"),
            flat_review=True,
            credential_loader=lambda: pytest.fail("keys"),
        )
    assert not flat_marker(installation).exists()


def test_changed_prior_receipt_refuses_before_credential_read(prior):
    path = marker(prior) / "complete.json"
    value = json.loads(path.read_text())
    value["completed_reads"] = 0
    path.write_text(json.dumps(value))
    with pytest.raises(probe.ProbeBlocked, match="PRIOR_PROBE_RECEIPT_INVALID"):
        invoke(
            prior,
            lambda *a: pytest.fail("wire"),
            flat_review=True,
            credential_loader=lambda: pytest.fail("keys"),
        )
    assert not flat_marker(prior).exists()


def test_unknown_third_response_keeps_reserved_charge_and_no_completed_receipt(prior):
    seen = []
    state = {}
    good = flat_sender(prior, seen)

    def unknown(*args):
        if args[1].endswith("/positionRisk"):
            seen.append(args[1])
            raise OSError("PRIVATE_SYNTHETIC_EXCEPTION")
        return good(*args)

    with pytest.raises(LocalQuotaStopped):
        invoke(prior, unknown, flat_review=True, state=state)
    assert len(seen) == 3 and state["transport"]["modeled_weight_charged"] == 11
    assert state["transport"]["completed_reads"] == 2
    assert not flat_marker(prior).joinpath("complete.json").exists()


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        "",
        True,
        0,
        {"data": []},
        [{}],
        [{"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": 0}],
        [dict(symbol="BTCUSDT", positionSide="BOTH", positionAmt="0")] * 2,
    ],
)
def test_position_unknown_missing_numeric_or_duplicate_is_never_empty(value):
    with pytest.raises(probe.ProbeBlocked):
        probe.verify_flat_payload("/fapi/v3/positionRisk", value)
