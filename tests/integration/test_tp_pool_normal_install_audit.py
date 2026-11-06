"""Real PostgreSQL NORMAL guard path; venue data is synthetic, GET-only."""

import hashlib
import importlib.util
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID

import pytest
from sqlalchemy import select

from integrations.binance_usdm.demo import DemoFailure
from packages.domain.contracts import RiskState
from packages.persistence.demo import accounts, events
from services.execution.demo_recovery import RecoveryBlocked
from services.execution.demo_recovery import review as recovery_review
from services.execution.demo_recovery_budget import AuditBudgetExceeded
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "normal_install_guard", Path("guard_tp_read_pool_install.py")
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
MISSING = ("DS-install-old-a", "DS-install-old-b", "DT-install-old-c", "DX-install-old-d")


@pytest.fixture
def normal(opposite_case, monkeypatch):  # noqa: F811
    store, c, fake, options = opposite_case
    options.update(fill=False, trigger=False)
    report = exercise_opposite(opposite_case, monkeypatch)
    assert report["position_state"] == "CLOSED" and report["filled_quantity"] in {"0", "0.0000"}
    store.change(
        c.lease,
        "TEST_MISSING_HISTORICAL_REQUESTS",
        lambda s: s.requests.update(dict.fromkeys(MISSING, "UNKNOWN")),
    )
    store.release(c.lease)
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    monkeypatch.setattr(guard, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(guard, "OPERATION", UUID(report["operation_id"]))
    monkeypatch.setattr(guard, "DemoStore", lambda e: store)
    monkeypatch.setattr(
        guard, "UNRESOLVED_REFS", {hashlib.sha256(x.encode()).hexdigest()[:16] for x in MISSING}
    )
    return store, c.client, fake


def rows(store):
    with store.engine.connect() as conn:
        return conn.execute(select(accounts)).mappings().all(), conn.execute(
            select(events)
        ).mappings().all()


def test_real_normal_install_pre_and_post_audit_preserve_every_row(normal):
    store, client, fake = normal
    before, start = rows(store), len(fake.calls)
    # Regression reproduction: unchanged recovery is EMERGENCY-only; no HTTP.
    with pytest.raises(RecoveryBlocked) as blocked:
        recovery_review(store, client, store.read("DEMO-test"))
    assert blocked.value.code == "RECOVERY_LOCK_STATE_MISMATCH"
    assert len(fake.calls) == start
    for phase, digest in [("pre", guard.OLD_SHA), ("post", guard.NEW_SHA)]:
        result = guard.run(store.engine, phase, digest=digest, client=client)
        assert result["remote_empty_verified"] and result["unresolved_count"] == 4
        assert result["audited_request_count"] == len(store.read("DEMO-test").requests)
        assert result["signed_query_count"] > result["audited_request_count"]
        assert result["installation_review"] == "NORMAL_PAUSED_FULL_READ_ONLY_AUDIT"
        assert not result["database_changed"] and not result["recovery_applied"]
        assert "review_token" not in result
    assert rows(store) == before
    assert all(method == "GET" for method, _, _ in fake.calls[start:])
    for identifier in store.read("DEMO-test").requests:
        assert any(
            p.get("clientAlgoId", p.get("origClientOrderId")) == identifier
            for _, _, p in fake.calls[start:]
        )


@pytest.mark.parametrize(
    "fault",
    [
        "emergency",
        "unpaused",
        "period",
        "loss",
        "position",
        "leverage",
        "transfer",
        "cohort",
        "identity",
        "active",
        "query",
        "snapshot",
        "late_snapshot",
        "budget",
        "history_limit",
        "write_enabled",
    ],
)
def test_actual_normal_audit_rejects_unsafe_conditions_without_mutation(normal, monkeypatch, fault):
    store, client, fake = normal
    ledger = store.read("DEMO-test")
    if fault == "emergency":
        ledger.risk_state = RiskState.EMERGENCY
    elif fault == "unpaused":
        ledger.paused = False
    elif fault == "period":
        fake.at += timedelta(days=1)
    elif fault == "loss":
        fake.wallet = Decimal("1")
    elif fault == "position":
        fake.positions["BTCUSDT"] = Decimal("0.0007")
    elif fault == "leverage":
        fake.leverage = "1"
    elif fault == "transfer":
        ledger.income_seen["unexpected-transfer"] = "1"
    elif fault == "cohort":
        monkeypatch.setattr(guard, "UNRESOLVED_REFS", set())
    elif fault == "history_limit":
        ledger.requests.update({f"DS-overflow-{n}": "UNKNOWN" for n in range(129)})
    elif fault == "write_enabled":
        client.settings = client.settings.model_copy(update={"demo_trading_enabled": True})
    elif fault == "budget":
        from services.execution import demo_recovery_budget as budget

        monkeypatch.setattr(budget, "MAX_SIGNED_QUERIES", 1)
    elif fault in {"snapshot", "late_snapshot"}:
        original, calls = guard.snapshot, []

        def changed(*args):
            snap = original(*args)
            calls.append(1)
            if len(calls) == (3 if fault == "late_snapshot" else 2):
                snap.fingerprint = "changed"
            return snap

        monkeypatch.setattr(guard, "snapshot", changed)
    else:
        original = client.send

        def altered(method, path, query, key):
            params = parse_qs(query.get_secret_value())
            if path == "/fapi/v1/algoOrder" and params.get("clientAlgoId") == [MISSING[0]]:
                if fault == "query":
                    raise DemoFailure("synthetic unavailable")
                return fake.response(
                    {
                        "symbol": "BTCUSDT",
                        "clientAlgoId": "wrong" if fault == "identity" else MISSING[0],
                        "algoStatus": "NEW" if fault == "active" else "CANCELED",
                    }
                )
            return original(method, path, query, key)

        client.send = altered
    before, state_before, start = rows(store), ledger.model_dump_json(), len(fake.calls)
    with pytest.raises((ValueError, DemoFailure, AuditBudgetExceeded)):
        guard.normal_audit(store, client, ledger)
    assert rows(store) == before and ledger.model_dump_json() == state_before
    assert all(method == "GET" for method, _, _ in fake.calls[start:])
