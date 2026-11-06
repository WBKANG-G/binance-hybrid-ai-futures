"""Synthetic Binance-shaped responses. Never represent actual DEMO evidence."""

import json
from decimal import Decimal as D
from urllib.parse import parse_qs

from integrations.binance_usdm.demo import DemoUnknown
from integrations.binance_usdm.rest import RESTResponse


class FakeDemo:
    def __init__(self, at):
        self.at = at
        self.orders = {}
        self.algos = {}
        self.positions = {}
        self.prices = {}
        self.calls = []
        self.lost_submit = False
        self.reject_submit = False
        self.reject_stop = False
        self.lost_cancel = False
        self.defer_exit = False
        self.hidden_order = False
        self.wallet = D("10000")
        self.dual = False
        self.multi = False
        self.incomes = []
        self.leverage = "2"
        self.rate_limit = False

    @staticmethod
    def response(data, status=200):
        return RESTResponse(status, json.dumps(data).encode(), {})

    def __call__(self, method, path, query, api_key):
        raw = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        params = {k: v for k, v in raw.items() if k not in {"signature", "timestamp", "recvWindow"}}
        self.calls.append((method, path, params))
        if path == "/fapi/v1/time":
            return self.response({"serverTime": int(self.at.timestamp() * 1000)})
        if self.rate_limit:
            return self.response({"code": -1003}, 429)
        if path == "/fapi/v1/exchangeInfo":
            from tests.fixtures.binance import exchange_info

            return self.response(exchange_info())
        if path == "/fapi/v1/ticker/bookTicker":
            return self.response({"bidPrice": "100", "askPrice": "100.1"})
        if path == "/fapi/v1/premiumIndex":
            return self.response({"markPrice": "100"})
        if path == "/fapi/v1/listenKey":
            return self.response({"listenKey": "syntheticDemonstrationKey1234567890"})
        if method == "GET":
            if path == "/fapi/v1/positionSide/dual":
                return self.response({"dualSidePosition": self.dual})
            if path == "/fapi/v1/multiAssetsMargin":
                return self.response({"multiAssetsMargin": self.multi})
            if path == "/fapi/v3/account":
                return self.response(
                    {
                        "totalWalletBalance": str(self.wallet),
                        "totalUnrealizedProfit": "0",
                        "availableBalance": str(max(self.wallet, D(0))),
                        "totalInitialMargin": "0",
                    }
                )
            if path == "/fapi/v1/symbolConfig":
                return self.response(
                    [
                        {"symbol": s, "marginType": "CROSSED", "leverage": self.leverage}
                        for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
                    ]
                )
            if path == "/fapi/v1/income":
                return self.response(self.incomes)
            if path == "/fapi/v3/positionRisk":
                return self.response(
                    [
                        {
                            "symbol": s,
                            "positionAmt": str(q),
                            "entryPrice": str(self.prices.get(s, 0)),
                            "positionSide": "BOTH",
                        }
                        for s, q in self.positions.items()
                    ]
                )
            if path == "/fapi/v1/openOrders":
                return self.response(
                    [
                        v
                        for v in self.orders.values()
                        if v["status"] in {"NEW", "PARTIALLY_FILLED"} and not self.hidden_order
                    ]
                )
            if path == "/fapi/v1/openAlgoOrders":
                return self.response([v for v in self.algos.values() if v["algoStatus"] == "NEW"])
            if path == "/fapi/v1/order":
                result = self.orders.get(params.get("origClientOrderId"))
                if "orderId" in params:
                    result = next(
                        (v for v in self.orders.values() if str(v["orderId"]) == params["orderId"]),
                        None,
                    )
                if result is None or self.hidden_order:
                    return self.response({"code": -2013}, 400)
                return self.response(result)
            if path == "/fapi/v1/algoOrder":
                result = self.algos.get(params["clientAlgoId"])
                return self.response(result) if result else self.response({"code": -2013}, 400)
        if method == "POST" and path == "/fapi/v1/order":
            if self.reject_submit:
                return self.response({"code": -2019}, 400)
            key = params["newClientOrderId"]
            assert key not in self.orders, "A repeated POST must fail the test"
            self.orders[key] = dict(
                symbol=params["symbol"],
                clientOrderId=key,
                orderId=str(len(self.orders) + 1),
                side=params["side"],
                type=params["type"],
                status="NEW",
                origQty=params["quantity"],
                executedQty="0",
                avgPrice="0",
                price=params.get("price", "0"),
                positionSide="BOTH",
                reduceOnly=params.get("reduceOnly") == "true",
            )
            if params["type"] == "MARKET" and not self.defer_exit:
                self.close(key)
            if self.lost_submit:
                raise DemoUnknown("Injected response loss")
            return self.response(self.orders[key])
        if method == "POST" and path == "/fapi/v1/algoOrder":
            if self.reject_stop and params["type"] == "STOP_MARKET":
                return self.response({"code": -2021}, 400)
            key = params["clientAlgoId"]
            assert key not in self.algos
            self.algos[key] = dict(
                symbol=params["symbol"],
                clientAlgoId=key,
                algoId=str(len(self.algos) + 101),
                side=params["side"],
                orderType=params["type"],
                algoStatus="NEW",
                triggerPrice=params["triggerPrice"],
                closePosition=True,
                positionSide="BOTH",
                workingType=params["workingType"],
                actualOrderId="",
                priceProtect=False,
                algoType="CONDITIONAL",
            )
            return self.response(self.algos[key])
        if method == "DELETE" and path == "/fapi/v1/order":
            if self.lost_cancel:
                raise DemoUnknown("Injected cancel timeout")
            order = self.orders.get(params["origClientOrderId"])
            if order and order["status"] in {"NEW", "PARTIALLY_FILLED"}:
                order["status"] = "CANCELED"
            return self.response(order or {"code": -2011})
        if method == "DELETE" and path == "/fapi/v1/algoOrder":
            self.algos[params["clientAlgoId"]]["algoStatus"] = "CANCELED"
            return self.response(self.algos[params["clientAlgoId"]])
        raise AssertionError((method, path))

    def fill(self, key, quantity, price=D("100")):
        order = self.orders[key]
        quantity = D(quantity)
        old = D(order["executedQty"])
        new = old + quantity
        assert new <= D(order["origQty"])
        order["avgPrice"] = str((D(order["avgPrice"]) * old + price * quantity) / new)
        order["executedQty"] = str(new)
        order["status"] = "FILLED" if new == D(order["origQty"]) else "PARTIALLY_FILLED"
        self.positions[order["symbol"]] = new * (1 if order["side"] == "BUY" else -1)
        self.prices[order["symbol"]] = D(order["avgPrice"])

    def close(self, key):
        order = self.orders[key]
        order["status"] = "FILLED"
        order["executedQty"] = order["origQty"]
        order["avgPrice"] = "100"
        self.positions[order["symbol"]] = D(0)
        self.prices[order["symbol"]] = D(0)

    def trigger(self, algo_id):
        algo = self.algos[algo_id]
        quantity = abs(self.positions[algo["symbol"]])
        key = "synthetic-algo-execution-" + algo_id
        self.orders[key] = dict(
            symbol=algo["symbol"],
            clientOrderId=key,
            orderId=str(len(self.orders) + 1),
            side=algo["side"],
            type="MARKET",
            status="FILLED",
            origQty=str(quantity),
            executedQty=str(quantity),
            avgPrice=algo["triggerPrice"],
            price="0",
            positionSide="BOTH",
            reduceOnly=True,
        )
        algo["algoStatus"] = "FINISHED"
        algo["actualOrderId"] = self.orders[key]["orderId"]
        self.positions[algo["symbol"]] = D(0)
        self.prices[algo["symbol"]] = D(0)
