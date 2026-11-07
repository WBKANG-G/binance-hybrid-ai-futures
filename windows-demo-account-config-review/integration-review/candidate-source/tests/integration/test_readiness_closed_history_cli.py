"""Installed readiness CLI uses a narrow view; SDK, financial and DB truth stay strict."""

import json
from uuid import uuid4

import pytest
import yaml
from pydantic import SecretStr
from sqlalchemy import select

from integrations.binance_usdm.demo import DemoClient, DemoCredentials, DemoSettings
from integrations.binance_usdm.demo_snapshot import operational_snapshot, snapshot
from packages.persistence.demo import accounts, events
from scripts import check_demo_session_ready as ready
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.unit.test_readiness_closed_history import INCIDENT, NOW, RECEIPT, basis  # noqa: F401


def rows(store):
    with store.engine.connect() as conn:
        return conn.execute(select(accounts)).mappings().all(), conn.execute(
            select(events)
        ).mappings().all()


@pytest.fixture
def installed(demo_system, basis, monkeypatch):  # noqa: F811
    store, old, fake = demo_system
    _, ledger = basis
    cfg = DemoSettings.model_validate(
        yaml.safe_load(ready.Path("configs/demo-isolated.example.yaml").read_text())
    )
    ledger.policy = cfg.policy
    fake.at = NOW
    o = ledger.orders[INCIDENT]
    other = o.model_copy(deep=True)
    other.signal = other.signal.model_copy(update={"signal_id": uuid4()})
    other.client_id = "DE-unadopted"
    other.algo_id = "DS-unadopted"
    other.tp_algo_id = "DT-unadopted"
    ledger.orders[str(other.signal.signal_id)] = other
    store.create("DEMO-ready", ledger)
    fake.orders[o.client_id] = {
        "symbol": "BTCUSDT",
        "clientOrderId": o.client_id,
        "orderId": "28668115919",
        "side": "BUY",
        "type": "LIMIT",
        "status": "FILLED",
        "origQty": "0.0007",
        "executedQty": "0.0007",
        "avgPrice": "0",
        "price": "100",
        "positionSide": "BOTH",
        "reduceOnly": False,
    }
    for identity, kind, trigger in [
        (o.algo_id, "STOP_MARKET", "90"),
        (o.tp_algo_id, "TAKE_PROFIT_MARKET", "120"),
    ]:
        fake.algos[identity] = {
            "symbol": "BTCUSDT",
            "clientAlgoId": identity,
            "algoId": identity + "1",
            "side": "SELL",
            "orderType": kind,
            "algoStatus": "EXPIRED",
            "triggerPrice": trigger,
            "closePosition": True,
            "positionSide": "BOTH",
            "workingType": "MARK_PRICE",
            "priceProtect": False,
            "algoType": "CONDITIONAL",
            "actualOrderId": "",
        }

    def sender(method, path, query, key):
        response = fake(method, path, query, key)
        data = json.loads(response.body)
        if path == "/fapi/v1/symbolConfig":
            for row in data:
                if row["symbol"] == "BTCUSDT":
                    row.update(marginType="ISOLATED", isAutoAddMargin=False)
        if path == "/fapi/v3/positionRisk":
            for row in data:
                row.update(
                    marginType="isolated",
                    isolatedWallet="1",
                    isolatedMargin="1",
                    liquidationPrice="1",
                )
        return fake.response(data, response.status)

    client = DemoClient(
        cfg,
        DemoCredentials(SecretStr("synthetic"), SecretStr("synthetic")),
        send=sender,
        clock_ms=lambda: int(fake.at.timestamp() * 1000),
    )
    client.sync_clock()
    monkeypatch.setattr(ready, "ACCOUNT", "DEMO-ready")
    monkeypatch.setattr(ready, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(ready, "DemoStore", lambda *a, **k: store)
    monkeypatch.setattr(ready, "DemoClient", lambda *a, **k: client)
    monkeypatch.setattr(ready, "credentials", lambda **k: client.credentials)
    return store, client, fake


def test_readiness_cli_stays22_and_approved_baseline_stays28_without_writes(
    installed, monkeypatch, capsys
):
    store, client, fake = installed
    before = rows(store)
    from tests.fixtures import approved_snapshot_v1

    baseline_start = len(fake.calls)
    for _ in range(2):
        approved_snapshot_v1.operational_snapshot(client, store.read("DEMO-ready"), store.now())
    assert len(fake.calls) - baseline_start == 28
    start = len(fake.calls)
    for method in ("create", "acquire", "change", "renew", "release"):
        monkeypatch.setattr(store, method, lambda *a, **k: pytest.fail("no mutation"))
    ready.main()
    r = json.loads(capsys.readouterr().out)
    assert r["session_ready"] == "READ_ONLY_READY_ENTRIES_STILL_PAUSED"
    assert r["closed_history_omitted_count"] == 1 and r["read_diagnostic"]["signed_get_count"] == 22
    assert rows(store) == before
    assert all(method == "GET" for method, _, _ in fake.calls[start:])
    assert sum(p.get("origClientOrderId") == "DE-unadopted" for _, _, p in fake.calls[start:]) == 2
    assert not any(p.get("origClientOrderId") == "DE-test" for _, _, p in fake.calls[start:])


@pytest.mark.parametrize("parser", [operational_snapshot, snapshot])
def test_full_history_keeps_ids_and_operational_skips_only_reviewed_closed_record(
    installed, parser
):
    store, client, fake = installed
    start = len(fake.calls)
    parser(client, store.read("DEMO-ready"), store.now())
    calls = fake.calls[start:]
    expected = 1 if parser is snapshot else 0
    assert sum(p.get("origClientOrderId") == "DE-test" for _, _, p in calls) == expected
    assert sum(p.get("clientAlgoId") == "DS-test" for _, _, p in calls) == expected
    assert sum(p.get("clientAlgoId") == "DT-test" for _, _, p in calls) == expected
    assert sum(p.get("origClientOrderId") == "DE-unadopted" for _, _, p in calls) == 1


@pytest.mark.parametrize("fault", ["position", "open_order", "open_algo", "rate_limit"])
def test_fresh_global_exposure_and_rate_limit_cannot_pass_readiness(installed, capsys, fault):
    from decimal import Decimal

    store, client, fake = installed
    if fault == "position":
        fake.positions["BTCUSDT"] = Decimal("0.0007")
    elif fault == "open_order":
        fake.orders["DE-test"]["status"] = "NEW"
    elif fault == "open_algo":
        fake.algos["DS-test"]["algoStatus"] = "NEW"
    else:
        fake.rate_limit = True
    before = rows(store)
    with pytest.raises(SystemExit):
        ready.main()
    r = json.loads(capsys.readouterr().out)
    assert r["session_ready"] == "BLOCKED" and rows(store) == before


def test_receipt_corruption_uses_original_28_reads_instead_of_omission(installed, capsys):
    store, client, fake = installed
    lease = store.acquire("DEMO-ready", uuid4())
    store.change(
        lease, "TEST_CORRUPT_RECEIPT", lambda s: s.operations.update({RECEIPT: "bad-json"})
    )
    store.release(lease)
    before = rows(store)
    ready.main()
    r = json.loads(capsys.readouterr().out)
    assert r["closed_history_omitted_count"] == 0 and r["read_diagnostic"]["signed_get_count"] == 28
    assert rows(store) == before
