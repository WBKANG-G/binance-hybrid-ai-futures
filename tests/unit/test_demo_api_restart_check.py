"""Read-only API identity and hard one-entry transport guard."""

import io
import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from integrations.binance_usdm.demo import DemoClient
from services.execution import demo_api_restart_check as check


def test_api_instance_stable_within_lifespan_and_new_after_restart():
    app = create_app()
    with TestClient(app) as client:
        before = client.get("/api/v1/system/instance").json()
        assert str(UUID(before["instance_id"])) == before["instance_id"]
        assert before == client.get("/api/v1/system/instance").json()
        assert before["environment"] == "PAPER" and before["orders_enabled"] is False
        assert client.post("/api/v1/orders").status_code == 404
    with TestClient(app) as client:
        assert client.get("/api/v1/system/instance").json()["instance_id"] != before["instance_id"]


def install_response(monkeypatch, body):
    class Reply(io.BytesIO):
        pass

    class Opener:
        def open(self, request, timeout):
            assert (
                request.full_url == check.API_URL and request.get_method() == "GET" and timeout == 2
            )
            return Reply(json.dumps(body).encode())

    monkeypatch.setattr(check, "build_opener", lambda *a: Opener())


def test_instance_reader_checks_read_only_context(monkeypatch):
    identity = str(uuid4())
    install_response(
        monkeypatch,
        dict(
            instance_id=identity,
            environment="PAPER",
            orders_enabled=False,
            live_trading_enabled=False,
        ),
    )
    assert check.api_instance() == identity


@pytest.mark.parametrize(
    "updates",
    [
        {"instance_id": "bad"},
        {"environment": "LIVE"},
        {"orders_enabled": True},
        {"live_trading_enabled": True},
        {"instance_id": True},
    ],
)
def test_untrusted_api_identity_cannot_prove_restart(monkeypatch, updates):
    install_response(
        monkeypatch,
        dict(
            instance_id=str(uuid4()),
            environment="PAPER",
            orders_enabled=False,
            live_trading_enabled=False,
        )
        | updates,
    )
    with pytest.raises(check.RestartBlocked, match="API_CONTEXT_INVALID"):
        check.api_instance()


def client(monkeypatch):
    c = object.__new__(check.RestartClient)
    c.allowed_entry = "DE-test"
    c.allowed_quantity = Decimal("0.0007")
    c.entry_post_attempts = c.entry_post_sent = 0
    calls = []
    monkeypatch.setattr(DemoClient, "request", lambda self, *args: calls.append(args) or {})
    return c, calls


def order(**updates):
    return (
        dict(
            newClientOrderId="DE-test",
            symbol="BTCUSDT",
            side="BUY",
            type="LIMIT",
            quantity="0.0007",
        )
        | updates
    )


def test_second_entry_attempt_is_rejected_before_transport(monkeypatch):
    c, calls = client(monkeypatch)
    c.request("POST", "/fapi/v1/order", order())
    with pytest.raises(check.RestartBlocked, match="DUPLICATE_ENTRY_ATTEMPT"):
        c.request("POST", "/fapi/v1/order", order())
    assert len(calls) == 1 and c.entry_post_attempts == 2 and c.entry_post_sent == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"newClientOrderId": "foreign"},
        {"symbol": "ETHUSDT"},
        {"side": "SELL"},
        {"type": "MARKET"},
        {"quantity": "0.0008"},
    ],
)
def test_wrong_entry_scope_is_not_transmitted(monkeypatch, changes):
    c, calls = client(monkeypatch)
    with pytest.raises(check.RestartBlocked, match="ENTRY_SCOPE_INVALID"):
        c.request("POST", "/fapi/v1/order", order(**changes))
    assert not calls


def test_exit_transport_does_not_consume_entry_allowance(monkeypatch):
    c, calls = client(monkeypatch)
    c.request("POST", "/fapi/v1/order", {"reduceOnly": "true"})
    assert c.entry_post_sent == 0 and len(calls) == 1
