import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.domain.config import AppConfig


def test_health_and_no_trading_routes() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/system/health")
        assert response.status_code == 200
        body = response.json()
        assert body["live_trading_enabled"] is False
        assert body["orders_enabled"] is False
        assert body["phase"] == 4
        assert body["environment"] == "PAPER"
        assert body["database"] in ["not_checked", "ready", "unavailable"]
        for path in ["/api/v1/orders", "/api/v1/control/resume", "/api/v1/risk/config/activate"]:
            assert client.post(path, json={}).status_code == 404
        assert "DATABASE_URL" not in response.text
        assert "password" not in response.text


def test_lifespan_rejects_live() -> None:
    with pytest.raises(ValueError, match="LIVE"):
        with TestClient(create_app(AppConfig(mode="LIVE"))):
            pass


def test_environment_toggle_prevents_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError):
        with TestClient(create_app()):
            pass


def test_database_failure_is_a_503_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://dev:do-not-disclose@127.0.0.1:1/hybrid"
    )
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/system/ready")
        assert response.status_code == 503
        assert response.json()["database"] == "unavailable"
        assert "do-not-disclose" not in response.text


def test_invalid_database_url_does_not_disclose_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "invalid-db-url-do-not-disclose")
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/system/ready")
        assert response.status_code == 503
        assert "do-not-disclose" not in response.text


def test_invalid_config_startup_error_is_sanitized(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("binance_secret: do-not-disclose\n")
    monkeypatch.setenv("APP_CONFIG_FILE", str(path))
    with pytest.raises(ValueError) as error:
        with TestClient(create_app()):
            pass
    assert "do-not-disclose" not in str(error.value)


def test_market_data_status_is_explicitly_untrusted_without_worker(monkeypatch):
    import apps.api.main as module

    def status(symbol, interval, now):
        return dict(
            symbol=symbol,
            interval=interval,
            as_of=None,
            observed_at=now,
            is_stale=True,
            reconciliation_required=True,
            reason="NOT_STARTED",
            orders_enabled=False,
        )

    monkeypatch.setattr(module, "read_market_status", status)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/market-data/status?symbol=BTCUSDT&interval=1m")
        assert response.status_code == 200 and response.json()["is_stale"] is True
        assert response.json()["as_of"] is None and response.json()["orders_enabled"] is False
        assert client.get("/api/v1/market-data/status?symbol=invalid!").status_code == 422


def test_paper_worker_is_disabled_without_explicit_cli():
    with TestClient(create_app()) as client:
        body = client.get("/api/v1/paper/status").json()
        assert body["worker_enabled"] is False and body["is_stale"] is True
        assert body["requires_explicit_cli"] is True and body["orders_enabled"] is False
        assert client.post("/api/v1/paper/submit", json={}).status_code == 404
        assert client.get("/api/v1/paper/status?account_id=LIVE-test").status_code == 422


def test_demo_api_has_no_trading_routes_and_defaults_disabled():
    with TestClient(create_app()) as client:
        body = client.get("/api/v1/demo/status").json()
        assert body["demo_trading_enabled"] is False and body["is_stale"] is True
        assert body["requires_explicit_cli"] is True and body["api_order_routes_enabled"] is False
        assert client.get("/api/v1/demo/status?account_id=PAPER-test").status_code == 422
        assert client.post("/api/v1/demo/orders", json={}).status_code == 404
        assert client.get("/api/v1/system/health").json()["demo_trading_enabled"] is False
