"""Strict REST snapshot used after startup, reconnect, fills and uncertain writes."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from integrations.binance_usdm.demo import DemoClient, DemoFailure, DemoNotFound
from integrations.binance_usdm.filters import number
from packages.domain.contracts import OrderState, PositionState
from services.execution.demo_models import DemoLedger, DemoSnapshot

MAX_INCOME_PAGES = 10
INCOME_PAGE_SIZE = 1000


def signed_number(value: object) -> Decimal:
    if isinstance(value, (float, bool)) or not isinstance(value, (str, int, Decimal)):
        raise DemoFailure("Invalid DEMO financial number")
    result = Decimal(value)
    if not result.is_finite():
        raise DemoFailure("Non-finite DEMO financial number")
    return result


def pick(raw: Any, keys: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(raw, dict) or any(key not in raw for key in keys):
        raise DemoFailure("Incomplete DEMO order snapshot")
    return {
        key: str(raw[key]).lower() if isinstance(raw[key], bool) else str(raw[key]) for key in keys
    }


ORDER_KEYS = (
    "symbol",
    "clientOrderId",
    "orderId",
    "side",
    "type",
    "status",
    "origQty",
    "executedQty",
    "avgPrice",
    "price",
    "positionSide",
    "reduceOnly",
)
ALGO_KEYS = (
    "symbol",
    "clientAlgoId",
    "algoId",
    "side",
    "orderType",
    "algoStatus",
    "triggerPrice",
    "closePosition",
    "positionSide",
    "workingType",
    "priceProtect",
    "algoType",
)


def snapshot(
    client: DemoClient, ledger: DemoLedger, now: datetime, *, include_closed_history: bool = True
) -> DemoSnapshot:
    try:
        return _snapshot(client, ledger, now, include_closed_history=include_closed_history)
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        # Keep malformed financial responses inside the coordinator's existing
        # fail-closed/minimal-position emergency reconciliation path.
        raise DemoFailure("Invalid/incomplete DEMO snapshot") from None


def operational_snapshot(client: DemoClient, ledger: DemoLedger, now: datetime) -> DemoSnapshot:
    """Current exchange truth plus unresolved intents; full historical review stays explicit."""
    return snapshot(client, ledger, now, include_closed_history=False)


def _snapshot(
    client: DemoClient, ledger: DemoLedger, now: datetime, *, include_closed_history: bool = True
) -> DemoSnapshot:
    mode = client.request("GET", "/fapi/v1/positionSide/dual")
    assets = client.request("GET", "/fapi/v1/multiAssetsMargin")
    if (
        not isinstance(mode, dict)
        or mode.get("dualSidePosition") is not False
        or not isinstance(assets, dict)
        or assets.get("multiAssetsMargin") is not False
    ):
        raise DemoFailure(
            "DEMO supports verified one-way/single-asset only; settings are never changed automatically"
        )
    account = client.request("GET", "/fapi/v3/account")
    configs = client.request("GET", "/fapi/v1/symbolConfig")
    positions_raw = client.request("GET", "/fapi/v3/positionRisk")
    regular = client.request("GET", "/fapi/v1/openOrders")
    algos_raw = client.request("GET", "/fapi/v1/openAlgoOrders")
    if not isinstance(account, dict) or not all(
        isinstance(x, list) for x in (positions_raw, regular, algos_raw, configs)
    ):
        raise DemoFailure("Invalid DEMO account/open-order response")
    wallet = signed_number(account["totalWalletBalance"])
    unrealized = signed_number(account["totalUnrealizedProfit"])
    available = number(account["availableBalance"], allow_zero=True)
    margin = number(account["totalInitialMargin"], allow_zero=True)
    positions: dict[str, Decimal] = {}
    entry_prices: dict[str, Decimal] = {}
    leverage: dict[str, Decimal] = {}
    margin_types: dict[str, str] = {}
    isolated_wallets: dict[str, Decimal] = {}
    isolated_margins: dict[str, Decimal] = {}
    liquidation_prices: dict[str, Decimal] = {}
    scope = set(client.settings.symbols)
    for item in configs:
        symbol = item["symbol"]
        if symbol in margin_types:
            raise DemoFailure("Duplicate DEMO symbol configuration")
        actual_mode = item.get("marginType")
        if actual_mode not in {"CROSSED", "ISOLATED"}:
            raise DemoFailure("Unknown DEMO margin mode")
        margin_types[symbol] = actual_mode
        if not scope or symbol in scope:
            if actual_mode != client.settings.margin_mode:
                raise DemoFailure("DEMO margin mode does not match explicit configuration")
            auto_add = item.get("isAutoAddMargin")
            if actual_mode == "ISOLATED" and auto_add is not False and auto_add != "false":
                raise DemoFailure("ISOLATED requires verified automatic margin addition disabled")
        leverage[symbol] = number(item["leverage"])
        if (
            client.settings.margin_mode == "ISOLATED"
            and symbol in scope
            and leverage[symbol] > ledger.policy.limit("max_leverage")
        ):
            raise DemoFailure("ISOLATED configured leverage exceeds DEMO policy")
    if scope - margin_types.keys():
        raise DemoFailure("Missing configured DEMO symbol settings")
    for row in positions_raw:
        if row.get("positionSide") != "BOTH":
            raise DemoFailure("Unsupported DEMO position mode")
        symbol = row["symbol"]
        if symbol in positions:
            raise DemoFailure("Ambiguous DEMO positions")
        positions[symbol] = signed_number(row["positionAmt"])
        entry_prices[symbol] = number(row["entryPrice"], allow_zero=True)
        if positions[symbol]:
            if scope and symbol not in scope:
                raise DemoFailure("DEMO position outside configured symbol scope")
            expected = client.settings.margin_mode
            if margin_types.get(symbol) != expected:
                raise DemoFailure("DEMO position margin settings unavailable or mismatched")
            if "marginType" in row and row["marginType"] != (
                "isolated" if expected == "ISOLATED" else "cross"
            ):
                raise DemoFailure("DEMO position/config margin mismatch")
            if expected == "ISOLATED":
                isolated_wallets[symbol] = number(row["isolatedWallet"])
                isolated_margins[symbol] = number(row["isolatedMargin"], allow_zero=True)
                liquidation_prices[symbol] = number(row["liquidationPrice"])
                # A locked isolated wallet cannot fund another entry. Do not add it
                # to availableBalance or totalWalletBalance (already account totals).
                margin = max(margin, sum(isolated_wallets.values(), Decimal(0)))
        # V3 omits leverage: account configuration is queried separately per symbol.

    orders = {str(row["clientOrderId"]): pick(row, ORDER_KEYS) for row in regular}
    algos = {
        str(row["clientAlgoId"]): pick(row, ALGO_KEYS)
        | {"actualOrderId": str(row.get("actualOrderId", ""))}
        for row in algos_raw
    }
    if len(orders) != len(regular) or len(algos) != len(algos_raw):
        raise DemoFailure("Duplicate remote IDs")
    if scope and any(row["symbol"] not in scope for row in [*orders.values(), *algos.values()]):
        raise DemoFailure("DEMO open order outside configured symbol scope")
    for order in ledger.orders.values():
        if (
            not include_closed_history
            and order.state
            in {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED}
            and order.position_state == PositionState.CLOSED
            and not (order.remaining or order.open_quantity or order.actual_risk or order.protected)
            and (
                (order.filled > 0 and order.exit_state == "FILLED")
                or (not order.filled and order.exit_id is None and order.exit_state == "NONE")
            )
            and not any(identifier in orders for identifier in (order.client_id, order.exit_id))
            and not any(identifier in algos for identifier in (order.algo_id, order.tp_algo_id))
        ):
            # Only previously proven closed records absent from BOTH fresh open
            # lists are omitted. Pending/unknown/partial/protected entries and
            # anything reappearing remotely still use the original-ID queries.
            continue
        if order.client_id not in orders:
            try:
                raw = client.request(
                    "GET",
                    "/fapi/v1/order",
                    {"symbol": order.signal.symbol, "origClientOrderId": order.client_id},
                )
                orders[order.client_id] = pick(raw, ORDER_KEYS)
            except DemoNotFound:
                pass  # Preserve SUBMITTING/UNKNOWN and its reservation; absence is not rejection.
        for algo_id in [order.algo_id, order.tp_algo_id]:
            if algo_id and algo_id not in algos:
                try:
                    raw = client.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": algo_id})
                    algos[algo_id] = pick(raw, ALGO_KEYS) | {
                        "actualOrderId": str(raw.get("actualOrderId", ""))
                    }
                except DemoNotFound:
                    pass
        if order.exit_id and order.exit_id not in orders:
            try:
                raw = client.request(
                    "GET",
                    "/fapi/v1/order",
                    {"symbol": order.signal.symbol, "origClientOrderId": order.exit_id},
                )
                orders[order.exit_id] = pick(raw, ORDER_KEYS)
            except DemoNotFound:
                pass
    for algo in algos.values():
        actual = algo.get("actualOrderId")
        if algo["algoStatus"] == "FINISHED" and actual and actual != "0":
            raw = client.request(
                "GET", "/fapi/v1/order", {"symbol": algo["symbol"], "orderId": actual}
            )
            orders["ALGO_EXEC:" + actual] = pick(raw, ORDER_KEYS)
    data = dict(
        positions={s: str(q) for s, q in positions.items()},
        orders=orders,
        algos=algos,
        wallet=str(wallet),
        unrealized=str(unrealized),
        available=str(available),
        margin=str(margin),
        margin_types=margin_types,
        leverage={s: str(q) for s, q in leverage.items()},
        entry_prices={s: str(q) for s, q in entry_prices.items()},
        isolated_wallets={s: str(q) for s, q in isolated_wallets.items()},
        isolated_margins={s: str(q) for s, q in isolated_margins.items()},
        liquidation_prices={s: str(q) for s, q in liquidation_prices.items()},
    )
    transfers: dict[str, str] = {}
    if ledger.income_since_ms is not None:
        for page in range(1, MAX_INCOME_PAGES + 1):
            incomes = client.request(
                "GET",
                "/fapi/v1/income",
                {
                    "startTime": str(ledger.income_since_ms),
                    "endTime": str(int(now.timestamp() * 1000)),
                    "limit": str(INCOME_PAGE_SIZE),
                    "page": str(page),
                },
            )
            if not isinstance(incomes, list):
                raise DemoFailure("Invalid DEMO income response")
            for item in incomes:
                if item["incomeType"] in {"TRANSFER", "WELCOME_BONUS"}:
                    if item.get("asset") != "USDT":
                        raise DemoFailure("Unexpected account transfer asset")
                    transfers[item["incomeType"] + ":" + str(item["tranId"])] = str(
                        signed_number(item["income"])
                    )
            if len(incomes) < INCOME_PAGE_SIZE:
                break
        else:
            raise DemoFailure("DEMO income pagination incomplete; refuse new entries")
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return DemoSnapshot(
        as_of=now,
        equity=wallet + unrealized,
        wallet_balance=wallet,
        available_balance=available,
        margin_used=margin,
        positions=positions,
        entry_prices=entry_prices,
        leverage=leverage,
        orders=orders,
        algos=algos,
        fingerprint=fingerprint,
        transfers=transfers,
        margin_types=margin_types,
        isolated_wallets=isolated_wallets,
        isolated_margins=isolated_margins,
        liquidation_prices=liquidation_prices,
    )
