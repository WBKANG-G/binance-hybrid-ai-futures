"""Synthetic weight/history support; copied from reviewed 61-case shadow validation.

Observed deltas are assumptions; unknown route cost40 is not a proven maximum.
"""

import threading
import time
from datetime import timedelta
from decimal import Decimal as D
from urllib.parse import parse_qs

from integrations.binance_usdm.rest import RESTResponse
from packages.domain.contracts import OrderState, PositionState
from services.execution.models import PaperOrder
from tests.integration.test_paper import signal, snapshot

GET_COSTS = {
    "/fapi/v1/algoOrder": 1,
    "/fapi/v1/exchangeInfo": 1,
    "/fapi/v1/income": 30,
    "/fapi/v1/multiAssetsMargin": 30,
    "/fapi/v1/openAlgoOrders": 40,
    "/fapi/v1/openOrders": 40,
    "/fapi/v1/order": 1,
    "/fapi/v1/positionSide/dual": 35,
    "/fapi/v1/symbolConfig": 5,
    "/fapi/v1/time": 1,
    "/fapi/v3/account": 5,
    "/fapi/v3/positionRisk": 5,
}


def seed_history(store, c, fake):
    history = {}
    for n in range(19):
        manual = n == 16
        ambiguous = n == 17
        unfilled = n == 18
        cid = f"DE-shadow-history-{n}"
        exit_id = None if manual or unfilled else f"DX-shadow-history-{n}"
        stop = None if unfilled else f"DS-shadow-history-{n}"
        target = None if ambiguous or unfilled else f"DT-shadow-history-{n}"
        qty = D("0.0007")
        filled = D(0) if ambiguous or unfilled else qty
        sig = signal(
            environment="DEMO",
            purpose="DEMO_VALIDATION",
            strategy=None,
            signal_closed_candle_at=store.now() - timedelta(days=2),
            expires_at=store.now() - timedelta(days=2) + timedelta(seconds=10),
            suggested_limit_price="80000.1",
            initial_stop_price="79600",
            target_price="80800.2",
        )
        key = str(sig.signal_id)
        order = PaperOrder(
            client_id=cid,
            signal=sig,
            snapshot=snapshot(),
            group="BTC",
            quantity=qty,
            quantity_step="0.0001",
            price_tick="0.1",
            loss_per_unit="500",
            budget="1",
            state=OrderState.CANCELED if not filled else OrderState.FILLED,
            position_state=PositionState.CLOSED,
            filled=filled,
            remaining="0",
            open_quantity="0",
            average_price="80000.1" if filled else "0",
            server_order_id=str(1000 + n),
            algo_id=stop,
            tp_algo_id=target,
            exit_id=exit_id,
            exit_state="NONE" if manual or unfilled else "UNKNOWN" if ambiguous else "FILLED",
        )
        history[key] = order
        raw = dict(
            symbol="BTCUSDT",
            clientOrderId=cid,
            orderId=str(1000 + n),
            side="BUY",
            type="LIMIT",
            status=order.state.value,
            origQty=str(qty),
            executedQty=str(filled),
            avgPrice=str(order.average_price),
            price="80000.1",
            positionSide="BOTH",
            reduceOnly=False,
        )
        fake.orders[cid] = raw
        if exit_id and not ambiguous:
            fake.orders[exit_id] = dict(
                raw,
                clientOrderId=exit_id,
                orderId=str(2000 + n),
                side="SELL",
                type="MARKET",
                status="FILLED",
                reduceOnly=True,
                price="0",
            )
        for identifier, kind, price in (
            (stop, "STOP_MARKET", "79600"),
            (target, "TAKE_PROFIT_MARKET", "80800.2"),
        ):
            if identifier:
                fake.algos[identifier] = dict(
                    symbol="BTCUSDT",
                    clientAlgoId=identifier,
                    algoId=str(3000 + 2 * n + (kind == "TAKE_PROFIT_MARKET")),
                    side="SELL",
                    orderType=kind,
                    algoStatus="EXPIRED",
                    triggerPrice=price,
                    closePosition=True,
                    positionSide="BOTH",
                    workingType="MARK_PRICE",
                    priceProtect=False,
                    algoType="CONDITIONAL",
                    actualOrderId="",
                )
        if ambiguous:
            fake.algos.pop(stop)
    # Two additional historical no-send uncertainties: source status remains preserved.
    fake.algos.pop("DS-shadow-history-0")
    fake.algos.pop("DT-shadow-history-1")

    def bind(ledger):
        ledger.orders.update(history)
        ledger.paused = True
        ledger.stream_ready = ledger.reconciled = False
        for order in history.values():
            ledger.requests[order.client_id] = order.state.value
            if order.exit_id:
                ledger.requests[order.exit_id] = (
                    "UNKNOWN" if order.exit_state == "UNKNOWN" else "FILLED"
                )
            for identifier in (order.algo_id, order.tp_algo_id):
                if identifier:
                    ledger.requests[identifier] = (
                        "UNKNOWN" if identifier not in fake.algos else "VERIFIED"
                    )

    store.change(c.lease, "SYNTHETIC_WEIGHT_SHADOW_HISTORY", bind)
    return history


class Meter:
    def __init__(
        self,
        sender,
        fake,
        *,
        seed,
        unknown_cost,
        cap,
        spike,
        partial,
        options,
        dual_cost=35,
        windowed=False,
    ):
        self.sender, self.fake = sender, fake
        self.used = seed
        self.seed = seed
        self.unknown_cost = unknown_cost
        self.get_costs = dict(GET_COSTS)
        self.get_costs["/fapi/v1/positionSide/dual"] = dual_cost
        self.cap = cap
        self.spike = spike
        self.spiked = False
        self.partial = partial
        self.options = options
        self.windowed = windowed
        self.window = int(fake.at.timestamp()) // 60
        self.window_origin = self.window
        self.window_peaks = {self.window: seed}
        self.rows = []
        self.lock = threading.Lock()
        self.started = time.monotonic()

    def __call__(self, method, path, query, key):
        params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        protected = (
            any(self.fake.positions.values())
            and any(
                a["algoStatus"] == "NEW" and a["orderType"] == "STOP_MARKET"
                for a in tuple(self.fake.algos.values())
            )
            and any(
                a["algoStatus"] == "NEW" and a["orderType"] == "TAKE_PROFIT_MARKET"
                for a in tuple(self.fake.algos.values())
            )
        )
        if method == "POST" and path == "/fapi/v1/order":
            stage = "reduce_exit" if params.get("reduceOnly") == "true" else "entry"
        elif method == "POST" and path == "/fapi/v1/algoOrder":
            stage = "protection"
        elif method == "DELETE":
            stage = "cancel_or_cleanup"
        elif protected:
            stage = "protected_reads"
        elif any(self.fake.positions.values()):
            stage = "unprotected_reads"
        elif any(
            o["status"] == "FILLED"
            and o["clientOrderId"].startswith("DE-")
            and "shadow-history" not in o["clientOrderId"]
            for o in tuple(self.fake.orders.values())
        ):
            stage = "post_fill_flat_reads"
        else:
            stage = "pre_entry_reads"
        cost = (
            self.get_costs[path]
            if method == "GET" and path in self.get_costs
            else self.unknown_cost
        )
        with self.lock:
            window = int(self.fake.at.timestamp()) // 60
            if self.windowed and window != self.window:
                self.window = window
                self.used = 0
            if (
                self.spike
                and not self.spiked
                and protected
                and method == "GET"
                and path == "/fapi/v1/time"
            ):
                self.used = max(self.used, self.cap)
                self.spiked = True
            self.used += cost
            self.window_peaks[self.window] = max(self.window_peaks.get(self.window, 0), self.used)
            blocked = self.used > self.cap
            row = {
                "seq": len(self.rows) + 1,
                "method": method,
                "path": path,
                "stage": stage,
                "assumed_weight": cost,
                "simulated_used_weight": self.used,
                "forwarded": not blocked,
                "status": 429 if blocked else None,
                "elapsed_ms": int((time.monotonic() - self.started) * 1000),
            }
            self.rows.append(row)
        if blocked:
            return RESTResponse(
                429, b"not-json", {"X-MBX-USED-WEIGHT-1M": str(self.used), "Retry-After": "120"}
            )
        response = self.sender(method, path, query, key)
        if (
            self.partial
            and method == "POST"
            and path == "/fapi/v1/order"
            and params.get("type") == "LIMIT"
        ):
            self.fake.fill(params["newClientOrderId"], D("0.0003"), D("80000.1"))
        row["status"] = response.status
        return RESTResponse(
            response.status,
            response.body,
            {"X-MBX-USED-WEIGHT-1M": str(row["simulated_used_weight"])},
        )
