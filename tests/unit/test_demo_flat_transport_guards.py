"""No entry/exit replay; original transport for eligible flat protection cleanup."""

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.rest import RESTResponse
from services.execution import demo_flat_transport as module
from tests.unit.test_demo_transport import client


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/fapi/v1/order"),
        ("POST", "/fapi/v1/algoOrder"),
        ("DELETE", "/fapi/v1/order"),
        ("GET", "/fapi/v1/trade/asyn"),
        ("PUT", "/fapi/v1/listenKey"),
    ],
)
def test_disallowed_mutations_and_exports_never_reach_sdk(method, path):
    source, fake = client()
    count = len(fake.calls)
    with module.confirmation_context(source) as bounded:
        with pytest.raises(DemoFailure):
            bounded.request(method, path, {"symbol": "BTCUSDT"})
    assert len(fake.calls) == count


def test_custom_send_preserved_and_context_restored_on_error():
    source, _ = client()
    original = source.send
    with pytest.raises(ValueError):
        with module.confirmation_context(source) as bounded:
            assert source.send is original and bounded.pooled is None
            raise ValueError("test")
    assert source.send is original


def test_pooled_get_but_cleanup_delete_uses_original_sender(monkeypatch):
    source, _ = client()
    calls = []

    def sender(method, path, query, key):
        calls.append((method, path))
        return RESTResponse(200, b"{}", {})

    class Pool:
        closed = False

        def __call__(self, *args):
            assert args[0] == "GET"
            calls.append(("POOLED", args[1]))
            return RESTResponse(200, b"{}", {})

        def close(self):
            self.closed = True

    pool = Pool()
    monkeypatch.setattr(module, "transport", sender)
    monkeypatch.setattr(module, "RecoveryTransport", lambda: pool)
    source.send = sender
    with module.confirmation_context(source):
        source.send("GET", "/fapi/v1/order", SecretStr(""), None)
        source.send("DELETE", "/fapi/v1/algoOrder", SecretStr(""), None)
        with pytest.raises(DemoFailure):
            source.send("POST", "/fapi/v1/order", SecretStr(""), None)
    assert calls == [("POOLED", "/fapi/v1/order"), ("DELETE", "/fapi/v1/algoOrder")]
    assert source.send is sender and pool.closed


def test_readonly_source_cannot_delete_protection():
    source, fake = client(enabled=False)
    count = len(fake.calls)
    with module.confirmation_context(source) as bounded:
        with pytest.raises(DemoFailure):
            bounded.request("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": "DS-test"})
    assert len(fake.calls) == count
