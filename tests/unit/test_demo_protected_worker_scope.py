"""A test child must be incapable of using production endpoints or credentials."""

import pytest
from pydantic import SecretStr

from tests.fixtures.demo_protected_worker import LoopbackTransport, loopback_endpoint
from tests.fixtures.demo_protected_worker import test_context as worker_context


@pytest.mark.parametrize(
    "url",
    [
        "https://fapi.binance.com",
        "http://demo-fapi.binance.com",
        "http://127.0.0.1:8000/path",
        "http://user:pass@127.0.0.1:8000",
        "http://127.0.0.1:8000?redirect=x",
    ],
)
def test_remote_or_ambiguous_endpoints_rejected(url):
    with pytest.raises(ValueError):
        loopback_endpoint(url, "http")


def test_wrong_key_rejected_before_any_loopback_request():
    transport = LoopbackTransport("http://127.0.0.1:1")
    with pytest.raises(ValueError, match="SYNTHETIC_CREDENTIALS_ONLY"):
        transport("GET", "/fapi/v3/account", SecretStr(""), SecretStr("DO_NOT_SEND"))


def test_explicit_test_context_required(monkeypatch):
    monkeypatch.delenv("TEST_ONLY_PROTECTED_WORKER", raising=False)
    with pytest.raises(ValueError, match="SYNTHETIC_TEST_CONTEXT_REQUIRED"):
        worker_context()


def test_any_binance_env_rejected_before_database(monkeypatch):
    monkeypatch.setenv("TEST_ONLY_PROTECTED_WORKER", "1")
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "DO_NOT_READ")
    with pytest.raises(ValueError, match="SYNTHETIC_TEST_CONTEXT_REQUIRED"):
        worker_context()
