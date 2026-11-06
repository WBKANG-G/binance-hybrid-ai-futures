"""Read-only DB and synthetic signed response contracts; no real venue calls."""

import importlib.util
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from integrations.binance_usdm.demo import DemoRateLimited
from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.rest import RESTResponse
from packages.domain.contracts import PositionState
from packages.persistence.demo import accounts, events
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_tp_rate_install_guard import closed  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "closed_probe", Path(__file__).with_name("compare_closed_history_reads.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def rows(store):
    with store.engine.connect() as c:
        return c.execute(select(accounts)).mappings().all(), c.execute(
            select(events)
        ).mappings().all()


@pytest.fixture
def setup(closed, monkeypatch):  # noqa: F811
    store, old, fake = closed
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    lease = store.acquire("DEMO-test", uuid4())

    def fill(s):
        o = s.orders[m.INCIDENT]
        o.average_price = Decimal("100")
        o.algo_id = "DS-test"
        o.tp_algo_id = "DT-test"
        original = o.model_copy(deep=True)
        original.position_state = PositionState.OPEN_UNPROTECTED
        original.open_quantity = original.filled
        r = json.loads(s.operations[m.RECEIPT])
        r.update(
            {
                "original_order": original.model_dump(mode="json"),
                "manual_close_client_id": "web_synthetic_close",
                "exchange_fingerprint": "a" * 64,
                "prior_ledger_sha256": "b" * 64,
                "acknowledged_at_utc": store.now().isoformat(),
            }
        )
        s.operations[m.RECEIPT] = json.dumps(r)
        # A different unadopted closed record must retain all its original-ID reads.
        other = o.model_copy(deep=True)
        identity = uuid4()
        other.signal = other.signal.model_copy(update={"signal_id": identity})
        other.client_id = "DE-unadopted"
        other.algo_id = "DS-unadopted"
        other.tp_algo_id = "DT-unadopted"
        s.orders[str(identity)] = other

    store.change(lease, "TEST_COMPLETE_MANUAL_RECEIPT", fill)
    store.release(lease)
    o = store.read("DEMO-test").orders[m.INCIDENT]
    fake.orders[o.client_id] = {
        "symbol": "BTCUSDT",
        "clientOrderId": o.client_id,
        "orderId": "28668115919",
        "side": "BUY",
        "type": "LIMIT",
        "status": "FILLED",
        "origQty": "0.0007",
        "executedQty": "0.0007",
        "avgPrice": "100",
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
    opt = {
        "weight": 100,
        "header": True,
        "rate_at": None,
        "change_wallet": False,
        "account_reads": 0,
        "late_open_algo": False,
        "change_local": False,
    }
    clock = [0.0]
    sender = old.send

    def send(method, path, query, key):
        clock[0] += 0.01
        response = sender(method, path, query, key)
        data = json.loads(response.body)
        opt["weight"] += 10
        if path == "/fapi/v1/exchangeInfo":
            data["rateLimits"] = [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": 6000,
                }
            ]
        if path == "/fapi/v3/account":
            opt["account_reads"] += 1
            if opt["account_reads"] == 3:
                if opt["late_open_algo"]:
                    fake.algos["DS-test"]["algoStatus"] = "NEW"
                if opt["change_local"]:
                    with store.engine.begin() as conn:
                        conn.execute(
                            accounts.update()
                            .where(accounts.c.account_id == "DEMO-test")
                            .values(fence=accounts.c.fence + 1)
                        )
            if opt["change_wallet"] and opt["account_reads"] >= 3:
                data["totalWalletBalance"] = str(fake.wallet + 1)
        headers = {"X-MBX-USED-WEIGHT-1M": str(opt["weight"])} if opt["header"] else {}
        if opt["rate_at"] == path:
            return RESTResponse(429, b"not-json", headers | {"Retry-After": "120"})
        return RESTResponse(response.status, json.dumps(data).encode(), headers)

    class Pool:
        def __call__(self, *args):
            return send(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    route = TPReadRouting(send, pool=Pool())
    monitor = m.Monitor(route, clock=lambda: clock[0])
    c = m.ReadClient(old.settings, old.credentials, send=monitor, clock_ms=old.clock_ms)
    monitor.server_ms = lambda: c.clock_ms() + c.offset
    start = len(fake.calls)
    return store, c, monitor, opt, fake, start


def test_read_only_pairs_remove_only_six_old_id_reads_keep_all_current_truth(setup, monkeypatch):  # noqa: F811
    store, c, monitor, opt, fake, start = setup
    before = rows(store)
    for method in ("create", "acquire", "change", "renew", "release"):
        monkeypatch.setattr(store, method, lambda *a, **k: pytest.fail("no DB mutation"))
    r = m.compare(store, c, monitor)
    assert r["original_pair_signed_reads"] == 28 and r["candidate_pair_signed_reads"] == 22
    assert r["signed_reads_removed"] == 6 and not r["runtime_installed"]
    assert not r["active_tp_cost_reduction_proven"]
    assert rows(store) == before
    calls = fake.calls[start:]
    assert all(method == "GET" for method, _, _ in calls)
    for path in [
        "/fapi/v1/positionSide/dual",
        "/fapi/v1/multiAssetsMargin",
        "/fapi/v3/account",
        "/fapi/v1/symbolConfig",
        "/fapi/v3/positionRisk",
        "/fapi/v1/openOrders",
        "/fapi/v1/openAlgoOrders",
        "/fapi/v1/income",
    ]:
        assert sum(p == path for _, p, _ in calls) == 4
    assert sum(p.get("origClientOrderId") == "DE-test" for _, _, p in calls) == 2
    assert sum(p.get("origClientOrderId") == "DE-unadopted" for _, _, p in calls) == 4


@pytest.mark.parametrize(
    "fault",
    [
        "original_order",
        "hash",
        "time",
        "receipt_source",
        "fee",
        "marker",
        "position",
        "unpaused",
        "liveclient",
    ],
)
def test_bad_receipt_context_is_not_optimized(setup, fault):  # noqa: F811
    store, c, monitor, opt, fake, start = setup
    if fault == "liveclient":
        c.settings = c.settings.model_copy(update={"demo_trading_enabled": True})
    else:
        lease = store.acquire("DEMO-test", uuid4())

        def alter(s):
            r = json.loads(s.operations[m.RECEIPT])
            if fault == "original_order":
                r["original_order"]["client_id"] = "DE-other"
            elif fault == "hash":
                r["prior_ledger_sha256"] = "bad"
            elif fault == "time":
                r["acknowledged_at_utc"] = (store.now() + timedelta(days=1)).isoformat()
            elif fault == "receipt_source":
                r["source"] = "OTHER"
            elif fault == "fee":
                r["net_usdt"] = "-0.07025287"
            elif fault == "marker":
                s.operations[
                    next(k for k, v in s.operations.items() if v == "TP_TRIGGER:NOT_ACCEPTED")
                ] = "TP_TRIGGER:ACCEPTED"
            elif fault == "position":
                s.orders[m.INCIDENT].open_quantity = Decimal("0.0007")
            elif fault == "unpaused":
                s.paused = False
            s.operations[m.RECEIPT] = json.dumps(r)

        store.change(lease, "TEST_INVALID_RECEIPT", alter)
        store.release(lease)
    before = rows(store)
    with pytest.raises((m.Blocked, m.DemoFailure)):
        m.compare(store, c, monitor)
    assert rows(store) == before and len(fake.calls) == start


@pytest.mark.parametrize("fault", ["protection", "wallet", "regular", "position"])
def test_current_change_or_unverified_terminal_protection_blocks(setup, fault):  # noqa: F811
    store, c, monitor, opt, fake, start = setup
    if fault == "protection":
        fake.algos["DS-test"]["algoStatus"] = "FINISHED"
    elif fault == "wallet":
        opt["change_wallet"] = True
    elif fault == "regular":
        fake.orders["DE-test"]["status"] = "NEW"
    else:
        fake.positions["BTCUSDT"] = Decimal("0.0007")
    before = rows(store)
    with pytest.raises((m.Blocked, m.DemoFailure)):
        m.compare(store, c, monitor)
    assert rows(store) == before and all(method == "GET" for method, _, _ in fake.calls[start:])


@pytest.mark.parametrize("path", ["/fapi/v1/order", "/fapi/v1/algoOrder", "/fapi/v1/income"])
def test_nonjson_rate_limit_no_retry(setup, path):  # noqa: F811
    store, c, monitor, opt, fake, start = setup
    opt["rate_at"] = path
    with pytest.raises(DemoRateLimited):
        m.compare(store, c, monitor)
    assert monitor.records[-1]["path"] == path and c.cooldown_until > c.clock_ms()


@pytest.mark.parametrize("budget", ["weight", "header", "http", "clock", "time"])
def test_probe_budgets_are_preserved(setup, monkeypatch, budget):  # noqa: F811
    store, c, monitor, opt, fake, start = setup
    if budget == "weight":
        opt["weight"] = 3550
    elif budget == "header":
        opt["header"] = False
    elif budget == "http":
        monkeypatch.setattr(m, "MAX_HTTP_ATTEMPTS", 2)
    elif budget == "clock":
        monkeypatch.setattr(m, "MAX_CLOCK_ATTEMPTS", 0)
    else:
        monitor.started = -100
    with pytest.raises(m.ProbeStop):
        m.compare(store, c, monitor)


def test_request_scope_rejects_all_writes_and_other_ids_before_sender(setup):  # noqa: F811
    store, c, monitor, opt, fake, start = setup
    c.bind(store.read("DEMO-test"))
    for method, path, params in [
        ("POST", "/fapi/v1/order", {}),
        ("DELETE", "/fapi/v1/algoOrder", {}),
        ("GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "origClientOrderId": "DE-unknown"}),
        ("GET", "/fapi/v1/algoOrder", {"clientAlgoId": "DS-unknown"}),
    ]:
        with pytest.raises(m.Blocked):
            c.request(method, path, params)
    assert len(fake.calls) == start


def test_source_pins_verified_against_deployed_reference():
    m.verify_sources()
    assert len(m.NEW_PINS) == 29


def test_full_historical_snapshot_still_queries_every_original_id(setup):  # noqa: F811
    from integrations.binance_usdm.demo_snapshot import snapshot

    store, c, monitor, opt, fake, start = setup
    m.compare(store, c, monitor)
    mark = len(fake.calls)
    snapshot(c, store.read("DEMO-test"), store.now())
    calls = fake.calls[mark:]
    assert sum(p.get("origClientOrderId") == "DE-test" for _, _, p in calls) == 1
    assert sum(p.get("clientAlgoId") == "DS-test" for _, _, p in calls) == 1
    assert sum(p.get("clientAlgoId") == "DT-test" for _, _, p in calls) == 1


@pytest.mark.parametrize("fault", ["late_open_algo", "change_local"])
def test_state_change_during_candidate_stage_cannot_be_reported_as_success(setup, fault):  # noqa: F811
    store, c, monitor, opt, fake, start = setup
    opt[fault] = True
    with pytest.raises(m.Blocked):
        m.compare(store, c, monitor)


def test_missing_original_signed_entry_is_not_receipt_only_success(setup):  # noqa: F811
    store, c, monitor, opt, fake, start = setup
    fake.hidden_order = True
    with pytest.raises((m.Blocked, m.DemoFailure)):
        m.compare(store, c, monitor)


def test_source_failure_precedes_keys_or_database(monkeypatch, capsys):
    monkeypatch.setattr(m, "NEW_PINS", {"missing": "0" * 64})
    monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys"))
    monkeypatch.setattr(m, "create_engine", lambda *a, **k: pytest.fail("no DB"))
    with pytest.raises(SystemExit) as error:
        m.main()
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "INSTALLED_SOURCE_HASH_MISMATCH"


@pytest.mark.parametrize("cleanup_fail", [False, True])
def test_unavailable_credentials_are_redacted_and_both_resources_close(
    monkeypatch, capsys, cleanup_fail
):
    log = []

    class Engine:
        def dispose(self):
            log.append("dispose")

    class Route:
        def close(self):
            log.append("close")
            if cleanup_fail:
                raise ValueError("secret-canary-close")

    monkeypatch.setattr(m, "verify_sources", lambda: None)
    monkeypatch.setattr(m, "create_engine", lambda *a, **k: Engine())
    monkeypatch.setattr(m, "DemoStore", lambda e: object())
    monkeypatch.setattr(m, "local", lambda s: None)
    monkeypatch.setattr(m, "TPReadRouting", lambda *a: Route())
    monkeypatch.setattr(m, "Monitor", lambda *a: object())

    def fail(**kwargs):
        raise ValueError("secret-canary-key")

    monkeypatch.setattr(m, "credentials", fail)
    with pytest.raises(SystemExit) as error:
        m.main()
    raw = capsys.readouterr().out
    assert error.value.code == 2 and log == ["close", "dispose"] and "secret-canary" not in raw
    assert json.loads(raw)["stop_reason"] == (
        "RESOURCE_CLEANUP_UNVERIFIED" if cleanup_fail else "DEMO_CREDENTIAL_SESSION_UNAVAILABLE"
    )
