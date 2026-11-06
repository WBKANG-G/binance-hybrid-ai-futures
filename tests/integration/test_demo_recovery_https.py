# ruff: noqa: F811
"""TLS setup cost regression with real PG/TLS, full unchanged request audit and budgets."""

import json
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import transport
from services.execution import demo_recovery as recovery_module
from services.execution import demo_recovery_budget as limits
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.integration.test_demo_period_review import period_case  # noqa: F401
from tests.integration.test_demo_recovery_batches import history_case  # noqa: F401
from tests.unit.test_recovery_https_transport import tls_server  # noqa: F401


def wire(
    history_case,
    tls_server,
    monkeypatch,
    *,
    setup_cost=Decimal("0.4"),
    request_cost=Decimal("0.001"),
):
    store, c, fake = history_case
    before = store.read("DEMO-test")
    saved = c.client.send
    base = fake.at
    elapsed = [Decimal(0)]

    def advance(value):
        elapsed[0] += value
        fake.at = base + timedelta(seconds=float(elapsed[0]))

    tls_server.on_connect = lambda: advance(setup_cost)
    tls_server.on_request = lambda: advance(request_cost)

    def answer(path, headers):
        parsed = urlsplit(path)
        value = headers.get("X-MBX-APIKEY")
        response = saved(
            "GET", parsed.path, SecretStr(parsed.query), SecretStr(value) if value else None
        )
        return response.status, response.body, {}

    tls_server.responder = answer
    original_budget = limits.RecoveryBudget
    monkeypatch.setattr(
        limits, "RecoveryBudget", lambda: original_budget(lambda: float(elapsed[0]))
    )
    return store, c, fake, before, saved, elapsed, base


def test_tls_setup_cost_control_exceeds40_but_reuse_completes_all_ids(
    history_case, tls_server, monkeypatch
):
    store, c, fake, before, saved, elapsed, base = wire(history_case, tls_server, monkeypatch)
    # This injected wrapper calls the original one-connection-per-request SDK
    # transport. All recovery code and its 40s limit are otherwise identical.
    c.client.send = lambda *args: transport(*args)
    try:
        with pytest.raises(recovery_module.RecoveryBlocked) as blocked:
            recovery_module.review(store, c.client, before)
        assert blocked.value.code == "REQUEST_AUDIT_TIME_BUDGET"
        assert store.read("DEMO-test") == before
        control_connections = tls_server.connections
        assert control_connections > 50
        elapsed[0] = Decimal(0)
        fake.at = base
        c.client.send = transport
        report = recovery_module.review(store, c.client, before)
        assert report["audited_request_count"] == len(before.requests) == 31
        assert report["unresolved_count"] == 2 and report["risk_gates"] == "PASS"
        assert report["audit_transport_mode"] == "POOLED_HTTPS"
        assert report["audit_connection_attempts"] == 1
        assert report["audit_reused_requests"] >= report["signed_query_count"] - 1
        assert store.read("DEMO-test") == before and c.client.send is transport
        assert limits.MAX_AUDIT_SECONDS == 40 and limits.MAX_SIGNED_QUERIES == 600
        assert limits.MAX_CLOCK_PROBES == 6 and limits.MAX_HISTORY_REQUESTS == 128
        queried = set()
        from urllib.parse import parse_qs

        for path, _ in tls_server.records:
            fields = parse_qs(urlsplit(path).query)
            queried.update(fields.get("origClientOrderId", []) + fields.get("clientAlgoId", []))
        assert set(before.requests) <= queried
    finally:
        c.client.send = saved


def test_pooled_report_token_matches_original_sdk_without_setup_pressure(
    history_case, tls_server, monkeypatch
):
    store, c, fake, before, saved, elapsed, base = wire(
        history_case, tls_server, monkeypatch, setup_cost=Decimal(0)
    )
    try:
        c.client.send = lambda *args: transport(*args)
        original = recovery_module.review(store, c.client, before)
        elapsed[0] = Decimal(0)
        fake.at = base
        c.client.send = transport
        pooled = recovery_module.review(store, c.client, before)
        assert pooled["review_token"] == original["review_token"]
        assert pooled["audited_request_count"] == original["audited_request_count"] == 31
        assert pooled["signed_query_count"] == original["signed_query_count"]
        assert pooled["unresolved_count"] == original["unresolved_count"] == 2
        assert store.read("DEMO-test") == before
    finally:
        c.client.send = saved


def test_slow_bodies_still_exhaust40_without_unlock_and_report_safe_metrics(
    history_case, tls_server, monkeypatch
):
    store, c, _, before, saved, _, _ = wire(
        history_case,
        tls_server,
        monkeypatch,
        setup_cost=Decimal("0.01"),
        request_cost=Decimal("0.4"),
    )
    c.client.send = transport
    try:
        with pytest.raises(recovery_module.RecoveryBlocked) as error:
            recovery_module.review(store, c.client, before)
        assert error.value.code == "REQUEST_AUDIT_TIME_BUDGET"
        metrics = error.value.transport_diagnostics
        assert metrics["audit_transport_mode"] == "POOLED_HTTPS"
        assert metrics["audit_connection_attempts"] == 1 and metrics["audit_reused_requests"] > 0
        rendered = json.dumps(metrics)
        assert "synthetic-key" not in rendered and "signature=" not in rendered
        assert store.read("DEMO-test") == before and c.client.send is transport
    finally:
        c.client.send = saved
