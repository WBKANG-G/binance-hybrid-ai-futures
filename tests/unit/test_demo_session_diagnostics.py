"""Only allowlisted classifications and bounded timing may leave the session."""

import pytest
from sqlalchemy.exc import OperationalError
from websockets.exceptions import ConnectionClosed

from integrations.binance_usdm.demo import DemoFailure, DemoRateLimited
from services.execution.demo_models import PrivateSessionDiagnostic
from services.execution.demo_session_diagnostics import event_info, failure_diagnostic
from services.risk.paper import RiskRejected


@pytest.mark.parametrize(
    "kind,stamp,want_kind,want_age",
    [
        ("ACCOUNT_UPDATE", 1000, "ACCOUNT_UPDATE", 500),
        ("PRIVATE_SECRET_DO_NOT_OUTPUT", 1000, "UNKNOWN", 500),
        ([], 1000, "UNKNOWN", 500),
        ("TRADE_LITE", True, "TRADE_LITE", None),
        ("TRADE_LITE", "1000", "TRADE_LITE", None),
        ("ALGO_UPDATE", -(10**30), "ALGO_UPDATE", 3600000),
        ("MARGIN_CALL", 10**30, "MARGIN_CALL", -60000),
    ],
)
def test_event_metadata_is_bounded_and_never_copies_unknown_payload(
    kind, stamp, want_kind, want_age
):
    assert event_info({"e": kind, "E": stamp, "private": "SECRET"}, 1500) == (want_kind, want_age)


@pytest.mark.parametrize(
    "error,stage,code",
    [
        (DemoFailure("Unrecognized DEMO private event"), "EVENT", "EVENT_UNKNOWN"),
        (DemoFailure("Stale/future DEMO private event"), "EVENT", "EVENT_TIME_INVALID"),
        (DemoFailure("DEMO private event oversized"), "RECEIVE", "EVENT_OVERSIZED"),
        (DemoFailure("Invalid DEMO event object"), "PARSE", "EVENT_OBJECT_INVALID"),
        (ValueError("PRIVATE_SECRET"), "PARSE", "EVENT_JSON_INVALID"),
        (ConnectionClosed(None, None), "RECEIVE", "SOCKET_CLOSED"),
        (TimeoutError("PRIVATE_SECRET"), "PING", "TIMEOUT"),
        (DemoRateLimited("PRIVATE_SECRET"), "HEARTBEAT", "RATE_LIMITED"),
        (RiskRejected("PRIVATE_SECRET"), "HEARTBEAT", "LEASE_OR_RISK"),
        (
            DemoFailure("DEMO REST snapshots changed mid-reconciliation"),
            "EVENT",
            "SNAPSHOT_CHANGED",
        ),
        (DemoFailure("Invalid/incomplete DEMO snapshot"), "RECONCILE", "SNAPSHOT_INVALID"),
        (DemoFailure("Clock stale or cooldown active"), "HEARTBEAT", "CLOCK_OR_COOLDOWN"),
        (DemoFailure("PRIVATE_SECRET"), "READY_CALLBACK", "DEMO_QUERY_OR_SESSION"),
        (RuntimeError("PRIVATE_SECRET"), "CONNECT", "OTHER_SESSION_FAILURE"),
    ],
)
def test_classification_does_not_expose_error_text(error, stage, code):
    result = failure_diagnostic(error, PrivateSessionDiagnostic(stage=stage))
    assert result.code == code and result.stage == stage
    assert "PRIVATE_SECRET" not in result.model_dump_json()


@pytest.mark.parametrize(
    "sqlstate,code",
    [
        ("55P03", "DB_LOCK_TIMEOUT"),
        ("40P01", "DB_DEADLOCK"),
        ("08006", "DB_CONNECTION"),
        ("SECRET", "OTHER_DATABASE"),
    ],
)
def test_database_classification_only_exposes_allowlisted_state(sqlstate, code):
    error = Exception("PRIVATE_SECRET")
    error.sqlstate = sqlstate
    result = failure_diagnostic(
        OperationalError("PRIVATE_SQL", {}, error), PrivateSessionDiagnostic(stage="EVENT")
    )
    assert result.code == code and "PRIVATE" not in result.model_dump_json()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/fapi/v1/order", "/fapi/v1/algoOrder", "/fapi/v1/leverage"])
def test_observation_client_denies_all_order_and_account_mutations_before_sdk(
    method, path, monkeypatch
):
    from integrations.binance_usdm.demo import DemoClient
    from scripts.observe_demo_private import ObservationClient

    def forbidden(*a, **k):
        pytest.fail("Mutation must not reach transport")

    monkeypatch.setattr(DemoClient, "request", forbidden)
    client = object.__new__(ObservationClient)
    with pytest.raises(DemoFailure, match="forbids order/account mutations"):
        client.request(method, path, {})


def test_observation_get_still_delegates_to_original_sdk(monkeypatch):
    from integrations.binance_usdm.demo import DemoClient
    from scripts.observe_demo_private import ObservationClient

    monkeypatch.setattr(DemoClient, "request", lambda self, method, path, params: {"path": path})
    assert object.__new__(ObservationClient).request("GET", "/fapi/v3/account") == {
        "path": "/fapi/v3/account"
    }
