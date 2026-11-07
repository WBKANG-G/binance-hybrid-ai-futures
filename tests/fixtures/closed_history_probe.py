import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.rest import RESTResponse
from packages.domain.contracts import PositionState
from packages.persistence.demo import accounts, events

spec = importlib.util.spec_from_file_location(
    "closed_probe", Path(__file__).parents[2] / "compare_closed_history_reads.py"
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
