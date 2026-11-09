"""Test-only model/clock. No runtime import or admission behavior is added."""

import json
import threading
import time
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs

from integrations.binance_usdm.rest import RESTResponse
from packages.domain.contracts import OrderState, PositionState
from services.execution.models import PaperOrder
from tests.integration.test_paper import signal, snapshot

GET_COSTS = {
    "/fapi/v1/accountConfig": 5,
    "/fapi/v1/time": 1,
    "/fapi/v1/exchangeInfo": 1,
    "/fapi/v3/account": 5,
    "/fapi/v1/symbolConfig": 5,
    "/fapi/v3/positionRisk": 5,
    "/fapi/v1/openOrders": 40,
    "/fapi/v1/openAlgoOrders": 40,
    "/fapi/v1/order": 1,
    "/fapi/v1/algoOrder": 1,
    "/fapi/v1/income": 30,
    "/fapi/v1/ticker/bookTicker": 2,
    "/fapi/v1/premiumIndex": 1,
    "/fapi/v1/klines": 1,
}
WRITE_ROUTES = {
    ("POST", "/fapi/v1/order"),
    ("DELETE", "/fapi/v1/order"),
    ("POST", "/fapi/v1/algoOrder"),
    ("DELETE", "/fapi/v1/algoOrder"),
    ("POST", "/fapi/v1/listenKey"),
    ("PUT", "/fapi/v1/listenKey"),
    ("DELETE", "/fapi/v1/listenKey"),
}
# Candidate test assumption: every mutation costs40; this is not a proven IP cost.
# Separate order-count limits and shared-IP reservations remain unmodeled.
COSTS = {("GET", p): v for p, v in GET_COSTS.items()} | {route: 40 for route in WRITE_ROUTES}


def seed_closed_shape(store, c, fake):
    history = {}
    for index, ambiguous in enumerate([False, False]):
        sig = signal(
            environment="DEMO",
            purpose="DEMO_VALIDATION",
            strategy=None,
            signal_closed_candle_at=store.now() - timedelta(days=2),
            expires_at=store.now() - timedelta(days=2) + timedelta(seconds=10),
        )
        cid = f"DE-audit-old-{index}"
        stop = f"DS-audit-old-{index}"
        tp = f"DT-audit-old-{index}"
        order = PaperOrder(
            client_id=cid,
            signal=sig,
            snapshot=snapshot(),
            group="BTC",
            quantity="0.0007",
            quantity_step="0.0001",
            price_tick="0.1",
            loss_per_unit="500",
            budget="1",
            state=OrderState.UNKNOWN if ambiguous else OrderState.FILLED,
            position_state=PositionState.CLOSED,
            filled="0" if ambiguous else "0.0007",
            remaining="0",
            open_quantity="0",
            average_price="0",
            server_order_id=None if ambiguous else str(200 + index),
            algo_id=stop,
            tp_algo_id=tp,
            exit_id=None,
            exit_state="NONE",
        )
        history[str(sig.signal_id)] = order
        if not ambiguous:
            fake.orders[cid] = dict(
                symbol="BTCUSDT",
                clientOrderId=cid,
                orderId=str(200 + index),
                side="BUY",
                type="LIMIT",
                status="FILLED",
                origQty="0.0007",
                executedQty="0.0007",
                avgPrice="0",
                price=str(sig.suggested_limit_price),
                positionSide="BOTH",
                reduceOnly=False,
            )
            for identifier, kind, trigger in [
                (stop, "STOP_MARKET", sig.initial_stop_price),
                (tp, "TAKE_PROFIT_MARKET", sig.target_price),
            ]:
                fake.algos[identifier] = dict(
                    symbol="BTCUSDT",
                    clientAlgoId=identifier,
                    algoId=identifier + "1",
                    side="SELL",
                    orderType=kind,
                    algoStatus="EXPIRED",
                    triggerPrice=str(trigger),
                    closePosition=True,
                    positionSide="BOTH",
                    workingType="MARK_PRICE",
                    priceProtect=False,
                    algoType="CONDITIONAL",
                    actualOrderId="",
                )

    def seed(ledger):
        ledger.orders.update(history)
        ledger.requests.update({"synthetic-old-unresolved-" + str(n): "UNKNOWN" for n in range(4)})
        ledger.paused = True

    store.change(c.lease, "SYNTHETIC_INSTALLED_HISTORY_SHAPE", seed)
    return history


class Meter:
    def __init__(
        self,
        sender,
        fake,
        *,
        capacity=6000,
        partial=False,
        spike=False,
        pages=1,
        full=False,
        when="finalization",
        latency=0.0,
    ):
        self.sender, self.fake = sender, fake
        self.capacity, self.partial, self.spike = capacity, partial, spike
        self.pages, self.full, self.when = pages, full, when
        self.latency = latency
        self.finalizing = False
        self.started = time.monotonic()
        self.base = fake.at
        self.lock = threading.Lock()
        self.rows = []
        self.peaks = {}
        self.used = {}
        self.spiked = False

    def now(self):
        self.fake.at = self.base + timedelta(seconds=time.monotonic() - self.started)
        return self.fake.at

    def clock_ms(self):
        return int(self.now().timestamp() * 1000)

    def __call__(self, method, path, query, key):
        self.now()
        params = {
            k: v[0]
            for k, v in parse_qs(query.get_secret_value()).items()
            if k not in {"signature", "timestamp", "recvWindow"}
        }
        cost = COSTS.get((method, path))
        if cost is None:
            raise AssertionError("Unmodeled synthetic route")
        if path in {"/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders"}:
            assert not params
        if path in {"/fapi/v1/ticker/bookTicker", "/fapi/v1/premiumIndex"}:
            assert params == {"symbol": "BTCUSDT"}
        if path == "/fapi/v1/klines":
            assert params == {"symbol": "BTCUSDT", "interval": "1m", "limit": "2"}
        if self.latency:
            time.sleep(self.latency / 2)
            self.now()
        protected = (
            any(self.fake.positions.values())
            and sum(a["algoStatus"] == "NEW" for a in tuple(self.fake.algos.values())) >= 2
        )
        bucket = int(self.now().timestamp()) // 60
        with self.lock:
            if (
                self.spike
                and not self.spiked
                and protected
                and method == "GET"
                and path == "/fapi/v1/time"
            ):
                self.used[bucket] = self.capacity
                self.spiked = True
            used = self.used.get(bucket, 0) + cost
            self.used[bucket] = used
            self.peaks[bucket] = max(self.peaks.get(bucket, 0), used)
            denied = used > self.capacity
            row = {
                "seq": len(self.rows) + 1,
                "method": method,
                "path": path,
                "modeled_request_weight": cost,
                "used_weight_1m_model": used,
                "minute": bucket,
                "elapsed_ms": int((time.monotonic() - self.started) * 1000),
                "status": 429 if denied else None,
            }
            self.rows.append(row)
        headers = {"x-mbx-used-weight-1m": str(used)}
        if denied:
            if self.latency:
                time.sleep(self.latency / 2)
            return RESTResponse(429, b"synthetic-not-json", headers | {"retry-after": "120"})
        response = self.sender(method, path, query, key)
        if (
            self.partial
            and method == "POST"
            and path == "/fapi/v1/order"
            and params.get("type") == "LIMIT"
        ):
            self.fake.fill(params["newClientOrderId"], Decimal("0.0003"), Decimal("80000.1"))
        if method == "GET" and path == "/fapi/v1/income":
            pages = self.pages if self.finalizing or self.when == "preparation" else 1
            page = int(params["page"])
            full = page < pages or self.full and page == pages
            response = self.fake.response([{"incomeType": "COMMISSION"}] * 1000 if full else [])
        if path == "/fapi/v1/exchangeInfo":
            data = json.loads(response.body)
            data["rateLimits"] = [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": self.capacity,
                }
            ]
            response = self.fake.response(data)
        row["status"] = response.status
        if self.latency:
            time.sleep(self.latency / 2)
        return RESTResponse(response.status, response.body, headers)
