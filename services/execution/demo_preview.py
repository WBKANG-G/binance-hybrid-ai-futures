"""Read-only, non-executable DEMO order illustrations; never a trading signal."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Any, Literal

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import operational_snapshot as snapshot
from integrations.binance_usdm.filters import SymbolRules, number
from services.execution.demo_models import DemoLedger
from services.risk.paper import capped_quantity, minimum_entry_quantity, unit_loss


class PreviewBlocked(DemoFailure):
    """Fixed, credential-free preview rejection reason safe for display."""


def ceil_step(value: Decimal, step: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 80
        return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def preview(
    client: DemoClient,
    ledger: DemoLedger,
    *,
    entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "PASSIVE",
) -> dict[str, Any]:
    if entry_style not in {"PASSIVE", "OPPOSITE_QUOTE"}:
        raise PreviewBlocked("Invalid preview entry style")
    if client.settings.demo_trading_enabled:
        raise PreviewBlocked("Preview requires writes disabled")
    if client.settings.margin_mode != "ISOLATED" or client.settings.symbols != ("BTCUSDT",):
        raise PreviewBlocked("Preview requires explicit ISOLATED BTCUSDT scope")
    if (
        ledger.margin_mode != client.settings.margin_mode
        or ledger.symbols != client.settings.symbols
        or ledger.policy != client.settings.policy
        or ledger.risk_state.value != "NORMAL"
        or not ledger.reconciled
        or any(o.remaining or o.open_quantity for o in ledger.orders.values())
    ):
        raise PreviewBlocked("Preview requires a reconciled empty NORMAL isolated ledger")
    now = datetime.fromtimestamp((client.clock_ms() + client.offset) / 1000, UTC)
    account = snapshot(client, ledger, now)
    if (
        any(account.positions.values())
        or any(row["status"] in {"NEW", "PARTIALLY_FILLED"} for row in account.orders.values())
        or any(row["algoStatus"] == "NEW" for row in account.algos.values())
    ):
        raise PreviewBlocked("Preview requires an empty DEMO account")
    if account.fingerprint != ledger.fingerprint:
        raise PreviewBlocked("Account changed after reconciliation; preview withheld")
    symbol = "BTCUSDT"
    info = client.public("/fapi/v1/exchangeInfo")
    rules = SymbolRules.from_exchange_info(info, symbol)
    book = client.public("/fapi/v1/ticker/bookTicker", {"symbol": symbol})
    mark_raw = client.public("/fapi/v1/premiumIndex", {"symbol": symbol})
    if not isinstance(book, dict) or not isinstance(mark_raw, dict):
        raise PreviewBlocked("Invalid preview market response")
    now_ms = client.clock_ms() + client.offset
    for row in (book, mark_raw):
        if row.get("symbol") != symbol:
            raise PreviewBlocked("Preview market symbol mismatch")
        observed = row.get("time")
        if type(observed) is not int or not 0 <= now_ms - observed <= 5000:
            raise PreviewBlocked("Preview market timestamp missing/stale/future")
    bid, ask, mark = (
        number(book["bidPrice"]),
        number(book["askPrice"]),
        number(mark_raw["markPrice"]),
    )
    if bid > ask or (ask - bid) / mark * 100 > ledger.policy.limit("max_spread_pct"):
        raise PreviewBlocked("Preview spread gate blocked")
    leverage = account.leverage.get(symbol, Decimal(0))
    if not 0 < leverage <= ledger.policy.limit("max_leverage") or account.equity <= 0:
        raise PreviewBlocked("Preview equity/leverage invalid")
    illustrations = []
    with localcontext() as ctx:
        ctx.prec = 80
        quotes = (("LONG", bid), ("SHORT", ask))
        if entry_style == "OPPOSITE_QUOTE":
            # No tick padding or price chase: the opposite quote must itself be valid.
            rules.validate_price(bid)
            rules.validate_price(ask)
            quotes = (("LONG", ask), ("SHORT", bid))
        for direction, raw_price in quotes:
            price = (
                rules.floor_price(raw_price)
                if (direction == "LONG") == (entry_style == "PASSIVE")
                else ceil_step(raw_price, rules.tick_size)
            )
            if price <= 0:
                raise PreviewBlocked("Preview price rounds to zero")
            qty = minimum_entry_quantity(rules, price)
            rules.validate_entry(qty, price, mark)
            # Explicit mechanical test assumptions, never strategy output or LIVE defaults.
            stop = (
                rules.floor_price(min(price, mark) * Decimal("0.995"))
                if direction == "LONG"
                else ceil_step(max(price, mark) * Decimal("1.005"), rules.tick_size)
            )
            target = (
                ceil_step(max(price, mark) * Decimal("1.01"), rules.tick_size)
                if direction == "LONG"
                else rules.floor_price(min(price, mark) * Decimal("0.99"))
            )
            rules.validate_price(stop)
            rules.validate_price(target)
            if (
                direction == "LONG" and not stop < min(price, mark) <= max(price, mark) < target
            ) or (
                direction == "SHORT" and not target < min(price, mark) <= max(price, mark) < stop
            ):
                raise PreviewBlocked("Preview stop/target tick rounding invalid")
            deviation = abs((ask if direction == "LONG" else bid) - price) / price * 100
            notional, margin = qty * price, qty * price / leverage
            estimated_risk = qty * unit_loss(ledger.policy, price, stop)
            risk_sized = rules.floor_quantity(
                account.equity
                * ledger.policy.limit("risk_per_trade_pct")
                / 100
                / unit_loss(ledger.policy, price, stop)
            )
            bounded = capped_quantity(risk_sized, qty, rules)
            rules.validate_entry(bounded, price, mark)
            if bounded != qty:
                raise PreviewBlocked("Minimum quantity does not fit current risk sizing")
            opening_fee = notional * ledger.policy.fee_rate
            if (
                deviation > ledger.policy.limit("max_slippage_pct")
                or estimated_risk > account.equity * ledger.policy.limit("risk_per_trade_pct") / 100
                or estimated_risk
                > account.equity * ledger.policy.limit("max_correlated_risk_pct") / 100
                or notional > account.equity * ledger.policy.limit("max_single_notional_pct") / 100
                or notional > account.equity * ledger.policy.limit("max_total_notional_pct") / 100
                or account.margin_used + margin
                > account.equity * ledger.policy.limit("max_margin_usage_pct") / 100
                or margin + opening_fee > account.available_balance
            ):
                raise PreviewBlocked("Preview minimum-size order exceeds risk/margin budget")
            illustrations.append(
                dict(
                    direction=direction,
                    entry_type="LIMIT",
                    limit_price=str(price),
                    quantity=str(qty),
                    execution_quantity_cap=str(qty),
                    capped_risk_quantity=str(bounded),
                    sizing_consistency="PASS",
                    notional_usdt=str(notional),
                    estimated_initial_margin_usdt=str(margin),
                    assumed_stop_price=str(stop),
                    assumed_target_price=str(target),
                    estimated_stop_loss_including_costs_usdt=str(estimated_risk),
                )
            )
    # Do not show a completed plan if account state moved during the public queries.
    end = snapshot(
        client, ledger, datetime.fromtimestamp((client.clock_ms() + client.offset) / 1000, UTC)
    )
    if end.fingerprint != account.fingerprint:
        raise PreviewBlocked("Account changed during preview; result withheld")
    observed_at = datetime.fromtimestamp(now_ms / 1000, UTC)
    final_ms = client.clock_ms() + client.offset
    if any(not 0 <= final_ms - row["time"] <= 5000 for row in (book, mark_raw)):
        raise PreviewBlocked("Preview quotes expired during final account verification")
    return dict(
        environment="DEMO",
        preview="READ_ONLY_NOT_EXECUTABLE",
        symbol=symbol,
        margin_mode="ISOLATED",
        entry_style=entry_style,
        leverage=str(leverage),
        orders_enabled=False,
        live_trading_enabled=False,
        entry_signal_supplied=False,
        as_of=observed_at.isoformat(),
        expires_at=(
            datetime.fromtimestamp(min(book["time"], mark_raw["time"]) / 1000, UTC)
            + timedelta(seconds=5)
        ).isoformat(),
        test_assumptions=dict(
            stop_distance_pct="0.5",
            target_distance_pct="1",
            quantity_basis="MINIMUM_EXCHANGE_VALID_SIZE",
            strategy_signal=False,
        ),
        algo_order_write_support="UNVERIFIED",
        liquidation_buffer="UNVERIFIED_BEFORE_FILL",
        tick_size=str(rules.tick_size),
        step_size=str(rules.step_size),
        min_quantity=str(rules.min_qty),
        min_notional_usdt=str(rules.min_notional),
        bid=str(bid),
        ask=str(ask),
        mark=str(mark),
        illustrations=illustrations,
    )
