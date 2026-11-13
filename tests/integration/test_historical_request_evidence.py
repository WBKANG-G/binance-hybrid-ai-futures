"""Real installed audited transport; original-ID synthetic replies only, no financial writes."""

# ruff: noqa: F401,F811
import json
from dataclasses import replace
from urllib.parse import parse_qs

import pytest

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import adopt_demo_accounted_day13 as adoption
from scripts import probe_demo_accounted_reads as common
from scripts import review_demo_historical_requests as history
from scripts import review_demo_post_adoption as gate
from scripts import review_demo_read_code_install as fingerprint
from scripts import review_demo_read_deployment as deployment
from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport
from tests.integration.test_post_adoption_gate import (
    adopted,
    anchored,
    base_installation,
    candidate,
    day12,
    day_sender,
    demo_system,
    installation,
    invoke,
    prepare,
    ready,
    reviewed,
    run,
    sender,
)
from tests.integration.test_runtime_read_accounting import financial, settings
from tests.unit.test_quota_startup_read_only import files

pytestmark = pytest.mark.parametrize("installation", ["historical"], indirect=True)


def execute(case, *, status=False, sender=None, clock=None, loader=None, state=None):
    from pathlib import Path

    a = case[0]
    return history.run(
        a[1].engine,
        deployment.STORAGE,
        a[0].scope,
        a[4],
        a[1].installation_id(),
        "DEMO-test",
        settings(a),
        loader or (lambda: a[7].client.credentials),
        fingerprint,
        adoption,
        gate,
        common,
        json.loads(Path("docs/request_cost_evidence/PINNED_RUNTIME_COST_PROOF.json").read_text()),
        status_only=status,
        clocks=clock or case[3],
        sender=sender,
        source_reference="test-source",
        state=state,
    )


def response(item):
    o = item["order"]
    if item["role"] == "STOP_LOSS":
        return dict(
            symbol="BTCUSDT",
            positionSide="BOTH",
            side="SELL",
            clientAlgoId=item["identifier"],
            algoId="7",
            algoType="CONDITIONAL",
            orderType="STOP_MARKET",
            closePosition=True,
            workingType="MARK_PRICE",
            priceProtect=False,
            triggerPrice=str(o.signal.initial_stop_price),
            algoStatus="CANCELED",
        )
    return dict(
        symbol="BTCUSDT",
        positionSide="BOTH",
        side="BUY" if item["role"] == "ENTRY" else "SELL",
        clientOrderId=item["identifier"],
        orderId="8",
        type="LIMIT" if item["role"] == "ENTRY" else "MARKET",
        reduceOnly=item["role"] == "EXIT",
        origQty=str(o.quantity),
        executedQty=str(o.quantity),
        price=str(o.signal.suggested_limit_price),
        status="FILLED",
    )


def wire(case, seen, mode="ok"):
    plan = history.plan_requests(case[0][6].read("DEMO-test"), common)
    mapping = {p["identifier"]: p for p in plan}
    weight = 268

    def send(method, path, query, key):
        nonlocal weight
        assert method == "GET"
        seen.append(path)
        weight += 5 if path.endswith("/accountConfig") else 1
        status = 200
        if path.endswith("/time"):
            body = {"serverTime": int(case[0][2].utc().timestamp() * 1000)}
        elif path.endswith("/accountConfig"):
            body = {"dualSidePosition": False, "multiAssetsMargin": False}
        else:
            params = parse_qs(query.get_secret_value())
            identifier = (params.get("origClientOrderId") or params["clientAlgoId"])[0]
            resources, events = common.resource_rows(case[0][1].engine, case[0][0].scope)
            assert events[max(events)]["kind"] == "RESERVE"
            body = response(mapping[identifier])
            if mode == "missing":
                status = 400
                body = {"code": -2013, "msg": "PRIVATE_DO_NOT_PRINT"}
            elif mode == "mismatch":
                body["clientOrderId"] = "different"
            elif mode == "network":
                raise OSError("PRIVATE_KEY_DO_NOT_PRINT")
            elif mode == "rate":
                status = 429
                body = {"code": -1003}
        return RESTResponse(
            status, json.dumps(body).encode(), {"x-mbx-used-weight-1m": str(weight)}
        )

    return send


def test_six_gets_keep_all_history_and_original_states_with_readonly_status(adopted):
    before = financial(adopted[0])
    disk = files(deployment.STORAGE.parent)
    seen = []
    result = execute(adopted, sender=wire(adopted, seen))
    assert seen == [
        "/fapi/v1/time",
        "/fapi/v1/accountConfig",
        "/fapi/v1/order",
        "/fapi/v1/order",
        "/fapi/v1/algoOrder",
        "/fapi/v1/order",
    ]
    assert (
        result["http_attempts"] == 6
        and result["requests_observed"] == 4
        and result["requests_unqueried"] == 0
    )
    assert (
        result["modeled_weight_charged"] == 10
        and result["audit_events_added"] == result["resource_checkpoints_added"] == 12
    )
    assert result["shared_total_weight"] == 278 and result["request_states_unchanged"]
    assert all(not v["remote_resolution_verified"] for v in result["observations"])
    assert not result["reconciliation_performed"] and not result["full_history_proof_replaced"]
    assert not result["capacity_shortfall_resolved"] and not result["execution_approved"]
    assert financial(adopted[0]) == before
    now_disk = files(deployment.STORAGE.parent)
    active = next(deployment.STORAGE.glob("*.weight.sqlite3"))
    assert all(
        now_disk[k] == v
        for k, v in disk.items()
        if k != str(active.relative_to(deployment.STORAGE.parent))
    )

    def forbidden():
        pytest.fail("status read credentials")

    status = execute(
        adopted,
        status=True,
        loader=forbidden,
        clock=replace(adopted[3], boot=lambda: "changed-boot"),
    )
    assert (
        status["http_attempts"] == 0
        and status["original_http_attempts"] == 6
        and not status["credential_session_accessed"]
    )
    assert (
        status["modeled_weight_charged"]
        == status["audit_events_added"]
        == status["resource_checkpoints_added"]
        == 0
    )
    assert status["original_attempt_summary"]["modeled_weight_charged"] == 10
    assert files(deployment.STORAGE.parent) == now_disk
    with pytest.raises(common.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        execute(adopted, sender=wire(adopted, seen))
    assert len(seen) == 6


def test_http400_not_found_stops_before_remaining_ids_and_remains_unresolved(adopted):
    before = financial(adopted[0])
    seen = []
    result = execute(adopted, sender=wire(adopted, seen, "missing"))
    assert len(seen) == 3 and result["http_attempts"] == 3 and result["completed_reads"] == 2
    assert result["stopped_after_not_found"] and result["requests_unqueried"] == 3
    assert result["observations"][0]["observed_status"] == "NOT_FOUND_UNRESOLVED"
    assert not result["observations"][0]["remote_resolution_verified"]
    assert result["modeled_weight_charged"] == 7 and result["shared_total_weight"] == 275
    assert "PRIVATE_DO_NOT_PRINT" not in json.dumps(result)
    assert financial(adopted[0]) == before
    assert execute(adopted, status=True)["original_http_attempts"] == 3


@pytest.mark.parametrize("mode", ["network", "mismatch", "rate"])
def test_unknown_failure_never_retries_or_invents_a_complete_receipt(adopted, mode):
    before = financial(adopted[0])
    seen = []
    state = {}
    with pytest.raises((LocalQuotaStopped, common.ProbeBlocked)):
        execute(adopted, sender=wire(adopted, seen, mode), state=state)
    assert len(seen) == 3 and financial(adopted[0]) == before
    _, folder = history.paths(deployment.STORAGE, adopted[0][4])
    assert (folder / "intent.json").exists() and not (folder / "complete.json").exists()
    assert state["transport"]["http_attempts"] == 3
    with pytest.raises(common.ProbeBlocked, match="INCOMPLETE_ATTEMPT"):
        execute(adopted, status=True)
    with pytest.raises(common.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        execute(adopted, sender=wire(adopted, seen))
    assert len(seen) == 3


def test_clock_refusal_precedes_credentials_intent_and_network(adopted):
    disk = files(deployment.STORAGE.parent)

    def forbidden():
        pytest.fail("credentials before clock validation")

    with pytest.raises(common.ProbeBlocked, match="CLOCK_REVIEW_REQUIRED"):
        execute(adopted, clock=replace(adopted[3], boot=lambda: "changed"), loader=forbidden)
    assert files(deployment.STORAGE.parent) == disk


def test_expired_credentials_leave_no_intent_or_charge(adopted):
    before = (
        common.resource_rows(adopted[0][1].engine, adopted[0][0].scope),
        files(deployment.STORAGE.parent),
    )

    def expired():
        raise ValueError("private")

    with pytest.raises(common.ProbeBlocked, match="CREDENTIAL_SESSION_UNAVAILABLE"):
        execute(adopted, loader=expired)
    assert before == (
        common.resource_rows(adopted[0][1].engine, adopted[0][0].scope),
        files(deployment.STORAGE.parent),
    )


def test_tampered_status_safety_flags_are_not_accepted(adopted):
    execute(adopted, sender=wire(adopted, []))
    _, folder = history.paths(deployment.STORAGE, adopted[0][4])
    p = folder / "complete.json"
    data = json.loads(p.read_text())
    data["capacity_shortfall_resolved"] = True
    p.write_text(json.dumps(data))
    with pytest.raises(common.ProbeBlocked, match="SAFETY_FLAGS_CHANGED"):
        execute(adopted, status=True)


def test_saved_not_found_cannot_be_presented_as_a_matching_fill(adopted):
    execute(adopted, sender=wire(adopted, [], "missing"))
    _, folder = history.paths(deployment.STORAGE, adopted[0][4])
    p = folder / "complete.json"
    data = json.loads(p.read_text())
    data["observations"][0]["observed_status"] = "FILLED"
    data["observations"][0]["response_identity_fields_match"] = True
    p.write_text(json.dumps(data))
    with pytest.raises(common.ProbeBlocked, match="SAVED_OBSERVATION_CHANGED"):
        execute(adopted, status=True)
