"""Readiness against real migrated PG; mocks only engine routing to a private schema."""

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from apps.api.main import create_app
from packages.persistence import health
from tests.integration.test_demo import system as demo_system  # noqa: F401


@pytest.fixture
def ready_db(demo_system, monkeypatch):  # noqa: F811
    store, _, _ = demo_system
    monkeypatch.setattr(health, "create_engine", lambda *a, **kw: store.engine)
    return store


def test_current_migrated_database_is_http_200_with_trading_disabled(ready_db):
    with TestClient(create_app()) as client:
        result = client.get("/api/v1/system/ready")
    assert result.status_code == 200
    assert result.json()["database"] == "ready"
    assert result.json()["orders_enabled"] is False
    assert result.json()["live_trading_enabled"] is False


def test_populated_0003_upgrade_turns_503_into_200_without_ledger_changes(ready_db):
    cfg = Config("alembic.ini")
    before = ready_db.read("DEMO-test")
    with ready_db.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0003_demo")
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/system/ready").status_code == 503
        with ready_db.engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        assert client.get("/api/v1/system/ready").status_code == 200
    assert ready_db.read("DEMO-test") == before


@pytest.mark.parametrize("kind", ["unknown", "missing", "multiple"])
def test_wrong_missing_or_ambiguous_revision_remains_503(ready_db, kind):
    with ready_db.engine.begin() as conn:
        conn.execute(text("DELETE FROM alembic_version"))
        if kind != "missing":
            conn.execute(text("INSERT INTO alembic_version(version_num) VALUES ('unknown_schema')"))
        if kind == "multiple":
            conn.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:head)"),
                {"head": health.EXPECTED_SCHEMA_REVISION},
            )
    with TestClient(create_app()) as client:
        result = client.get("/api/v1/system/ready")
    assert result.status_code == 503 and result.json()["database"] == "unavailable"
    assert not result.json()["orders_enabled"]
