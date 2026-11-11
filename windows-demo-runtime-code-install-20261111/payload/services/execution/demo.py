"""Durable DEMO coordinator. No simulator is accepted as authenticated evidence."""

from decimal import Decimal
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID

from integrations.binance_usdm.demo import DemoClient, DemoFailure, DemoRejected
from integrations.binance_usdm.demo_snapshot import operational_snapshot as snapshot
from integrations.binance_usdm.demo_snapshot import signed_number
from integrations.binance_usdm.demo_snapshot import snapshot as historical_snapshot
from integrations.binance_usdm.filters import SymbolRules, number
from integrations.binance_usdm.orders import build_entry, build_protection
from packages.domain.contracts import (
    AlgoOrder,
    EntryOrder,
    OrderState,
    PositionState,
    RiskState,
    Signal,
    TradingMode,
)
from packages.persistence.demo import DemoStore
from services.execution.demo_event_clock import validate_event_time
from services.execution.demo_flat_transport import confirmation_context
from services.execution.demo_initial_fill import InitialFillBlocked, settle_initial_fill
from services.execution.demo_models import (
    DemoLedger,
    DemoSnapshot,
    InitialFillSettleReason,
    PrivateSessionDiagnostic,
)
from services.execution.demo_reconciliation import coherent_snapshot
from services.execution.models import Lease, PaperOrder
from services.execution.paper import account_risk
from services.risk.paper import (
    AccountValue,
    GateSnapshot,
    RiskRejected,
    minimum_entry_quantity,
    period_keys,
    size,
    unit_loss,
)

ZERO = Decimal(0)
TERMINAL = {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED}
STATES = {
    "NEW": OrderState.ACKNOWLEDGED,
    "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
    "FILLED": OrderState.FILLED,
    "CANCELED": OrderState.CANCELED,
    "EXPIRED": OrderState.EXPIRED,
    "EXPIRED_IN_MATCH": OrderState.EXPIRED,
    "REJECTED": OrderState.REJECTED,
}


class DemoCoordinator:
    def __init__(self, store: DemoStore, lease: Lease, client: DemoClient):
        self.store, self.lease, self.client = store, lease, client
        self.session_failed = False
        self.session_diagnostic = PrivateSessionDiagnostic()
        self.rules: dict[str, SymbolRules] = {}
        self.excluded_symbols: dict[str, str] = {}
        if store.read(lease.account_id).policy != client.settings.policy:
            raise RiskRejected("DEMO immutable policy mismatch")
        stored = store.read(lease.account_id)
        if (
            stored.margin_mode != client.settings.margin_mode
            or stored.symbols != client.settings.symbols
        ):
            raise RiskRejected("DEMO immutable margin/symbol scope mismatch; use a separate ledger")

    def _fresh(self, ledger: DemoLedger) -> None:
        now = self.store.now()
        account_risk(ledger, now)
        if (
            self.session_failed
            or ledger.paused
            or ledger.risk_state != RiskState.NORMAL
            or not ledger.reconciled
            or not ledger.stream_ready
        ):
            raise RiskRejected("DEMO entry gate locked/not reconciled/private stream unavailable")
        if (
            ledger.last_reconciled is None
            or ledger.stream_at is None
            or any(
                not 0 <= (now - t).total_seconds() <= ledger.policy.max_snapshot_age_seconds
                for t in (ledger.last_reconciled, ledger.stream_at)
            )
        ):
            raise RiskRejected("DEMO account/private stream stale")

    def refresh_metadata(self) -> None:
        info = self.client.public("/fapi/v1/exchangeInfo")
        # Clear cached rules before refresh; failed refresh must not retain tradable rules.
        self.rules = {}
        self.excluded_symbols = {}
        rules: dict[str, SymbolRules] = {}
        for item in info["symbols"]:
            if (
                item.get("status") != "TRADING"
                or item.get("contractType") != "PERPETUAL"
                or item.get("quoteAsset") != "USDT"
                or item.get("marginAsset") != "USDT"
            ):
                continue
            symbol = item["symbol"]
            try:
                rules[symbol] = SymbolRules.from_exchange_info(info, symbol)
            except ValueError:
                self.excluded_symbols[symbol] = "INVALID_SYMBOL_FILTERS"
        self.rules = rules

    def _check_metadata_exposure(self, ledger: DemoLedger, remote: DemoSnapshot) -> None:
        excluded = set(self.excluded_symbols)
        exposed = {symbol for symbol, qty in remote.positions.items() if qty != ZERO}
        exposed.update(
            row["symbol"]
            for row in remote.orders.values()
            if row["status"] in {"NEW", "PARTIALLY_FILLED"}
        )
        exposed.update(row["symbol"] for row in remote.algos.values() if row["algoStatus"] == "NEW")
        exposed.update(
            order.signal.symbol
            for order in ledger.orders.values()
            if order.remaining or order.open_quantity
        )
        if excluded & exposed:
            raise RiskRejected("Excluded DEMO symbol has exposure; manual review required")

    def reserve(
        self,
        signal: Signal,
        gate: GateSnapshot,
        *,
        quantity_cap: Decimal | None = None,
        entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "PASSIVE",
    ) -> PaperOrder:
        signal = Signal.model_validate(signal.model_dump())
        if entry_style not in {"PASSIVE", "OPPOSITE_QUOTE"} or (
            signal.purpose != "DEMO_VALIDATION" and entry_style != "PASSIVE"
        ):
            raise RiskRejected("Explicit opposite-quote style is restricted to DEMO validation")
        if quantity_cap is not None:
            quantity_cap = number(quantity_cap)
        if signal.purpose == "DEMO_VALIDATION" and (
            quantity_cap is None
            or self.client.settings.margin_mode != "ISOLATED"
            or self.client.settings.symbols != ("BTCUSDT",)
        ):
            raise RiskRejected("DEMO validation requires capped ISOLATED BTCUSDT execution")
        if not self.client.settings.demo_trading_enabled:
            raise RiskRejected("DEMO entry reservations disabled in read-only preflight")
        if signal.environment != TradingMode.DEMO:
            raise RiskRejected("Only DEMO signals can reach the DEMO coordinator")
        if self.client.settings.symbols and signal.symbol not in self.client.settings.symbols:
            raise RiskRejected("DEMO signal outside configured symbol scope")
        rules = self.rules.get(signal.symbol)
        if rules is None:
            raise RiskRejected("DEMO symbol metadata unavailable")

        def apply(ledger: DemoLedger) -> PaperOrder:
            key = str(signal.signal_id)
            if key in ledger.orders:
                if ledger.orders[key].signal != signal:
                    raise RiskRejected("Conflicting DEMO signal ID")
                if ledger.entry_quantity_caps.get(key) != quantity_cap:
                    raise RiskRejected("Conflicting DEMO quantity cap for existing signal")
                if ledger.validation_entry_styles.get(key, "PASSIVE") != entry_style:
                    raise RiskRejected("Conflicting DEMO validation entry style")
                return ledger.orders[key].model_copy(deep=True)
            self._fresh(ledger)
            # Re-fetch under the same account lock before granting a new reservation.
            remote = snapshot(self.client, ledger, self.store.now())
            if remote.fingerprint != ledger.fingerprint:
                raise RiskRejected("DEMO remote state changed; reconcile first")
            quantity, loss = size(
                ledger.policy,
                signal,
                gate,
                ledger.value.equity,
                rules,
                self.store.now(),
                quantity_cap=quantity_cap,
            )
            if signal.purpose == "DEMO_VALIDATION" and (
                quantity != minimum_entry_quantity(rules, signal.suggested_limit_price)
                or quantity_cap != quantity
                or quantity * signal.suggested_limit_price > Decimal("100")
            ):
                raise RiskRejected("DEMO validation requires exact minimum size below 100 USDT")
            active = [o for o in ledger.orders.values() if o.remaining or o.open_quantity]
            equity = ledger.value.equity
            notional = quantity * signal.suggested_limit_price
            total = sum((o.notional for o in active), ZERO) + notional
            risk = sum((o.charged_risk for o in active), ZERO) + quantity * loss
            leverage = remote.leverage.get(signal.symbol, ZERO)
            if leverage <= 0 or any(
                remote.leverage.get(o.signal.symbol, ZERO) <= 0 for o in active
            ):
                raise RiskRejected("Missing verified DEMO leverage")
            existing_margin = sum(
                (o.notional / remote.leverage[o.signal.symbol] for o in active), ZERO
            )
            reserved_margin = sum(
                (
                    o.remaining * o.signal.suggested_limit_price / remote.leverage[o.signal.symbol]
                    for o in active
                    if o.state
                    in {OrderState.RISK_RESERVED, OrderState.SUBMITTING, OrderState.UNKNOWN}
                ),
                ZERO,
            )
            if (
                leverage <= 0
                or leverage > ledger.policy.limit("max_leverage")
                or len(active) >= ledger.policy.limit("max_concurrent_positions")
                or any(o.signal.symbol == signal.symbol for o in active)
                or notional > equity * ledger.policy.limit("max_single_notional_pct") / 100
                or total > equity * ledger.policy.limit("max_total_notional_pct") / 100
                or max(existing_margin, remote.margin_used) + notional / leverage
                > equity * ledger.policy.limit("max_margin_usage_pct") / 100
                or notional / leverage + reserved_margin > remote.available_balance
                or risk > equity * ledger.policy.limit("max_correlated_risk_pct") / 100
            ):
                raise RiskRejected("DEMO reservation/filter/leverage/exposure cap")
            client_id = "DE-" + sha256((self.lease.account_id + key).encode()).hexdigest()[:30]
            order = PaperOrder(
                client_id=client_id,
                signal=signal,
                snapshot=gate,
                group=gate.correlation_group,
                quantity=quantity,
                quantity_step=rules.step_size,
                price_tick=rules.tick_size,
                remaining=quantity,
                loss_per_unit=loss,
                budget=quantity * loss,
            )
            ledger.orders[key] = order
            if signal.purpose == "DEMO_VALIDATION":
                ledger.validation_entry_styles[key] = entry_style
            if quantity_cap is not None:
                ledger.entry_quantity_caps[key] = quantity_cap
            return order.model_copy(deep=True)

        return self.store.change(self.lease, "DEMO_ENTRY_RESERVED", apply)

    def submit(self, key: str) -> None:
        def stage(ledger: DemoLedger) -> bool:
            order = ledger.orders[key]
            if order.state != OrderState.RISK_RESERVED:
                return False
            self._fresh(ledger)
            if self.store.now() >= order.signal.expires_at:
                raise RiskRejected("Expired DEMO signal")
            order.state = OrderState.SUBMITTING
            ledger.requests[order.client_id] = "SUBMITTING"
            return True

        if not self.store.change(self.lease, "DEMO_ENTRY_INTENT", stage):
            return

        def effect(ledger: DemoLedger) -> None:
            order = ledger.orders[key]
            try:
                self._fresh(ledger)
                # Updated market data and metadata, not the original candidate price alone.
                self.refresh_metadata()
                if order.signal.symbol not in self.rules:
                    raise DemoFailure("DEMO symbol metadata unavailable before submission")
                remote = snapshot(self.client, ledger, self.store.now())
                if remote.fingerprint != ledger.fingerprint:
                    raise DemoFailure("DEMO account/margin settings changed before submission")
                book = self.client.public(
                    "/fapi/v1/ticker/bookTicker", {"symbol": order.signal.symbol}
                )
                mark = self.client.public("/fapi/v1/premiumIndex", {"symbol": order.signal.symbol})
                if order.signal.purpose == "DEMO_VALIDATION":
                    current_ms = self.client.clock_ms() + self.client.offset
                    for row in (book, mark):
                        observed = row.get("time")
                        if (
                            row.get("symbol") != order.signal.symbol
                            or type(observed) is not int
                            or not 0 <= current_ms - observed <= 5000
                        ):
                            raise DemoFailure(
                                "DEMO validation quote timestamp missing/stale/future"
                            )
                    current_mark = number(mark["markPrice"])
                    stop, target = order.signal.initial_stop_price, order.signal.target_price
                    if not min(stop, target) < current_mark < max(stop, target):
                        raise DemoFailure("DEMO validation mark moved beyond stop/target bracket")
                    if order.quantity != minimum_entry_quantity(
                        self.rules[order.signal.symbol], order.signal.suggested_limit_price
                    ):
                        raise DemoFailure(
                            "DEMO validation minimum-size rules changed before submission"
                        )
                latest = GateSnapshot.model_validate(
                    order.snapshot.model_dump()
                    | {
                        "market_at": self.store.now(),
                        "bid": book["bidPrice"],
                        "ask": book["askPrice"],
                        "mark": mark["markPrice"],
                    }
                )
                allowed_quantity, _ = size(
                    ledger.policy,
                    order.signal,
                    latest,
                    ledger.value.equity,
                    self.rules[order.signal.symbol],
                    self.store.now(),
                    quantity_cap=order.quantity,
                )
                if allowed_quantity < order.quantity:
                    raise DemoFailure("Reserved DEMO quantity no longer fits current risk budget")
                intent = build_entry(
                    EntryOrder(
                        client_order_id=order.client_id,
                        signal_id=order.signal.signal_id,
                        environment=TradingMode.DEMO,
                        account_id=self.lease.account_id,
                        symbol=order.signal.symbol,
                        side="BUY" if order.signal.direction == "LONG" else "SELL",
                        quantity=order.quantity,
                        limit_price=order.signal.suggested_limit_price,
                    ),
                    self.rules[order.signal.symbol],
                    mark_price=latest.mark,
                )
                if self.session_failed:
                    ledger.request_no_send_evidence[order.client_id] = "LOCAL_VALIDATION_FAILED"
                    raise DemoFailure("DEMO private receiver failed before entry submission")
                self.client.submit(intent)
                ledger.requests[order.client_id] = "ACKNOWLEDGED"
            except DemoRejected:
                order.state, order.remaining = OrderState.REJECTED, ZERO
                ledger.requests[order.client_id] = "REJECTED"
            except (DemoFailure, ValueError):
                order.state = OrderState.UNKNOWN
                ledger.requests[order.client_id] = "UNKNOWN"
            ledger.reconciled = False

        self.store.change(self.lease, "DEMO_ENTRY_EFFECT", effect)
        self.reconcile()

    def disconnect(self) -> None:
        def apply(ledger: DemoLedger) -> None:
            ledger.stream_ready, ledger.reconciled = False, False

        self.store.change(self.lease, "DEMO_STREAM_UNTRUSTED", apply)

    def connected(self) -> None:
        # Call only after verified WSS handshake or pong; REST reconciliation follows.
        def apply(ledger: DemoLedger) -> None:
            ledger.stream_ready, ledger.stream_at = True, self.store.now()
            ledger.reconciled = False

        self.store.change(self.lease, "DEMO_STREAM_CONNECTED", apply)
        self.reconcile()

    def event(self, raw: dict[str, Any]) -> None:
        # Events are invalidation hints; REST truth prevents duplicate/out-of-order fill arithmetic.
        if raw.get("e") not in {
            "ORDER_TRADE_UPDATE",
            "ALGO_UPDATE",
            "ACCOUNT_UPDATE",
            "ACCOUNT_CONFIG_UPDATE",
            "MARGIN_CALL",
            "listenKeyExpired",
            "TRADE_LITE",
        }:
            self.disconnect()
            raise DemoFailure("Unrecognized DEMO private event")
        if raw["e"] == "listenKeyExpired":
            self.disconnect()
            return
        stamp = raw.get("E")
        try:
            validate_event_time(self.client, stamp, self.session_diagnostic)
        except DemoFailure:
            self.disconnect()
            raise
        self.connected()

    @staticmethod
    def _stop_ok(order: PaperOrder, raw: dict[str, str] | None) -> bool:
        return bool(
            raw
            and raw["clientAlgoId"] == order.algo_id
            and raw["algoType"] == "CONDITIONAL"
            and raw["priceProtect"] == "false"
            and raw["symbol"] == order.signal.symbol
            and raw["side"] == ("SELL" if order.signal.direction == "LONG" else "BUY")
            and raw["orderType"] == "STOP_MARKET"
            and raw["algoStatus"] == "NEW"
            and raw["closePosition"] == "true"
            and raw["positionSide"] == "BOTH"
            and raw["workingType"] == "MARK_PRICE"
            and signed_number(raw["triggerPrice"]) == order.signal.initial_stop_price
        )

    def reconcile(self) -> None:
        mismatch: list[str] = []
        settle_reason: list[InitialFillSettleReason] = []
        try:
            self._reconcile_checked(mismatch, settle_reason)
        except DemoFailure as exc:
            # Persist only allowlisted local categories, never raw transport errors.
            failure_code: Literal["SNAPSHOT_CHANGED", "SNAPSHOT_INVALID", "OTHER_DEMO_FAILURE"] = (
                "SNAPSHOT_CHANGED"
                if str(exc) == "DEMO REST snapshots changed mid-reconciliation"
                else "SNAPSHOT_INVALID"
                if str(exc) == "Invalid/incomplete DEMO snapshot"
                else "OTHER_DEMO_FAILURE"
            )

            def failed(ledger: DemoLedger) -> None:
                ledger.reconciliation_failure_code = failure_code
                if mismatch:
                    ledger.last_reconciliation_mismatch_fields = tuple(mismatch)
                if settle_reason:
                    ledger.reconciliation_settle_reason = settle_reason[0]
                ledger.reconciled = False
                ledger.paused = True
                for existing in ledger.orders.values():
                    if existing.open_quantity:
                        existing.protected = False
                        existing.position_state = PositionState.OPEN_UNPROTECTED
                # Protection metadata failure must not prevent a minimal position query
                # and a risk-reducing exit for an unprotected managed position.
                try:
                    rows = self.client.request("GET", "/fapi/v3/positionRisk")
                    if not isinstance(rows, list):
                        raise DemoFailure("Invalid emergency position response")
                    if any(signed_number(row["positionAmt"]) for row in rows):
                        # Unattributed exchange exposure still keeps the emergency lock.
                        ledger.risk_state = RiskState.EMERGENCY
                    for order in ledger.orders.values():
                        if order.position_state == PositionState.CLOSED or (
                            order.state in TERMINAL and order.filled == ZERO
                        ):
                            # A terminal unfilled entry cannot own a later trade's position.
                            # Preserve existing ambiguous records for an explicit audited review.
                            continue
                        for row in rows:
                            if (
                                row.get("symbol") != order.signal.symbol
                                or row.get("positionSide") != "BOTH"
                            ):
                                continue
                            amount = signed_number(row["positionAmt"])
                            if (
                                amount
                                and amount * (1 if order.signal.direction == "LONG" else -1) > 0
                                and abs(amount) <= order.quantity
                                and not order.protected
                            ):
                                order.open_quantity = abs(amount)
                                order.average_price = number(row["entryPrice"])
                                order.actual_risk = abs(amount) * order.average_price
                                order.position_state = PositionState.OPEN_UNPROTECTED
                                ledger.risk_state = RiskState.EMERGENCY
                except (DemoFailure, ValueError, KeyError):
                    pass

            self.store.change(self.lease, "DEMO_RECONCILIATION_FAILED", failed)
            if self.store.read(self.lease.account_id).risk_state == RiskState.EMERGENCY:
                self._flatten()
            raise

    def _reconcile_checked(
        self,
        mismatch: list[str] | None = None,
        settle_reason: list[InitialFillSettleReason] | None = None,
    ) -> None:
        self.store.change(
            self.lease, "DEMO_RECONCILING", lambda ledger: setattr(ledger, "reconciled", False)
        )

        def query_merge(ledger: DemoLedger) -> None:
            self.client.sync_clock()
            first = snapshot(self.client, ledger, self.store.now())
            second = snapshot(self.client, ledger, self.store.now())
            try:
                coherent = coherent_snapshot(first, second, ledger)
            except DemoFailure as original:
                fields = [
                    k
                    for k in type(first).model_fields
                    if k not in {"as_of", "fingerprint"} and getattr(first, k) != getattr(second, k)
                ]
                if mismatch is not None:
                    mismatch[:] = sorted(fields)
                try:
                    coherent, key, evidence = settle_initial_fill(
                        first,
                        second,
                        ledger,
                        self.client,
                        lambda: snapshot(self.client, ledger, self.store.now()),
                    )
                except InitialFillBlocked as blocked:
                    if settle_reason is not None:
                        settle_reason[:] = [blocked.code]
                    raise original from None
                except (DemoFailure, ValueError, KeyError, TypeError):
                    if settle_reason is not None:
                        settle_reason[:] = ["FILL_PROOF_MISMATCH"]
                    raise original from None
                ledger.initial_fill_settlements[key] = evidence
                ledger.last_reconciliation_mismatch_fields = tuple(mismatch or ())
                ledger.reconciliation_failure_code = "NONE"
                ledger.reconciliation_settle_reason = "VERIFIED_INITIAL_FILL"
            self._merge(ledger, coherent)

        self.store.change(self.lease, "DEMO_REST_SNAPSHOT", query_merge)
        if not self.client.settings.demo_trading_enabled:

            def read_only(ledger: DemoLedger) -> None:
                if any(o.open_quantity and not o.protected for o in ledger.orders.values()):
                    ledger.paused = True
                    ledger.reconciled = False
                    if ledger.risk_state == RiskState.NORMAL:
                        ledger.risk_state = RiskState.UNHEALTHY

            self.store.change(self.lease, "DEMO_READ_ONLY_OBSERVATION", read_only)
            return
        ledger = self.store.read(self.lease.account_id)
        for key, order in ledger.orders.items():
            if (
                order.position_state == PositionState.EXIT_PENDING
                and not order.open_quantity
                and order.remaining
            ):
                self._cancel_only(key)
        ledger = self.store.read(self.lease.account_id)
        for key, order in ledger.orders.items():
            if order.open_quantity and not order.protected:
                self._protect(key, "SL")
            current = self.store.read(self.lease.account_id).orders[key]
            if current.open_quantity and current.protected and current.tp_algo_id is None:
                self._protect(key, "TP")

        def finish(ledger: DemoLedger) -> list[str]:
            actual = snapshot(self.client, ledger, self.store.now())
            self._merge(ledger, actual)
            if any(o.open_quantity and not o.protected for o in ledger.orders.values()):
                ledger.risk_state = RiskState.EMERGENCY
                ledger.reconciled = False
            return [
                key
                for key, order in ledger.orders.items()
                if order.position_state == PositionState.CLOSED
                and any(
                    actual.algos.get(identifier or "", {}).get("algoStatus") == "NEW"
                    for identifier in (order.algo_id, order.tp_algo_id)
                )
            ]

        cleanup_keys = self.store.change(self.lease, "DEMO_PROTECTION_RECHECK", finish)
        ledger = self.store.read(self.lease.account_id)
        if ledger.risk_state == RiskState.EMERGENCY:
            self._flatten()
        # Flat cleanup is a separate queried/fenced side effect.
        for key in cleanup_keys:
            self._cleanup(key)

    def _merge(self, ledger: DemoLedger, remote: DemoSnapshot) -> None:
        self._check_metadata_exposure(ledger, remote)
        now = self.store.now()
        if ledger.income_since_ms is None:
            if remote.equity <= 0:
                raise DemoFailure("DEMO account has no positive equity")
            ledger.value = AccountValue(as_of=now, initial=remote.equity)
            ledger.daily_base = ledger.weekly_base = ledger.high_water = remote.equity
            ledger.daily_key, ledger.weekly_key = period_keys(now, ledger.policy.week_start_kst)
            ledger.income_since_ms = int(now.timestamp() * 1000)
        for key, value in remote.transfers.items():
            if key in ledger.income_seen and ledger.income_seen[key] != value:
                raise DemoFailure("DEMO transfer identity conflict")
            if key not in ledger.income_seen:
                ledger.net_transfers += signed_number(value)
                ledger.income_seen[key] = value
        # Exchange equity already includes realized/unrealized/fees/funding; do not double-count income.
        ledger.value = AccountValue(
            as_of=now,
            initial=ledger.value.initial,
            realized=remote.equity - ledger.value.initial - ledger.net_transfers,
            net_deposits=ledger.net_transfers,
        )
        ledger.available_balance, ledger.margin_used = remote.available_balance, remote.margin_used
        ledger.positions = remote.positions
        ledger.margin_types = remote.margin_types
        ledger.verified_leverage = remote.leverage
        ledger.isolated_wallets = remote.isolated_wallets
        ledger.isolated_margins = remote.isolated_margins
        known_orders = {o.client_id for o in ledger.orders.values()} | {
            o.exit_id for o in ledger.orders.values() if o.exit_id
        }
        known_algos = {a for o in ledger.orders.values() for a in (o.algo_id, o.tp_algo_id) if a}
        mismatch = any(
            k not in known_orders
            and not k.startswith("ALGO_EXEC:")
            and row["status"] in {"NEW", "PARTIALLY_FILLED"}
            for k, row in remote.orders.items()
        )
        mismatch = mismatch or any(
            k not in known_algos and row["algoStatus"] == "NEW" for k, row in remote.algos.items()
        )
        managed_symbols: set[str] = set()
        unresolved = False
        for order in ledger.orders.values():
            row = remote.orders.get(order.client_id)
            if row is None:
                if order.state in {
                    OrderState.SUBMITTING,
                    OrderState.UNKNOWN,
                    OrderState.CANCEL_REQUESTED,
                }:
                    order.state = OrderState.UNKNOWN
                    unresolved = True
                continue
            if (
                row["clientOrderId"] != order.client_id
                or (order.server_order_id is not None and row["orderId"] != order.server_order_id)
                or row["symbol"] != order.signal.symbol
                or row["side"] != ("BUY" if order.signal.direction == "LONG" else "SELL")
                or row["type"] != "LIMIT"
                or row["reduceOnly"] != "false"
                or row["positionSide"] != "BOTH"
                or signed_number(row["origQty"]) != order.quantity
                or signed_number(row["price"]) != order.signal.suggested_limit_price
            ):
                mismatch = True
                continue
            filled = number(row["executedQty"], allow_zero=True)
            if filled < order.filled or filled > order.quantity:
                mismatch = True
                continue
            order.server_order_id = row["orderId"]
            order.filled = filled
            order.state = STATES.get(row["status"], OrderState.UNKNOWN)
            order.remaining = ZERO if order.state in TERMINAL else order.quantity - filled
            if order.state == OrderState.UNKNOWN:
                unresolved = True
            pos = remote.positions.get(order.signal.symbol, ZERO)
            direction = 1 if order.signal.direction == "LONG" else -1
            # A queried terminal zero-fill entry has no position to close. Do
            # not erase legacy ambiguous exposure, exit intent or protection.
            if (
                order.state in {OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED}
                and filled == ZERO
                and pos == ZERO
                and order.remaining == ZERO
                and order.open_quantity == ZERO
                and order.actual_risk == ZERO
                and not order.protected
                and not order.exit_id
                and not order.algo_id
                and not order.tp_algo_id
            ):
                order.position_state = PositionState.CLOSED
            if order.position_state == PositionState.CLOSED and order.remaining == 0:
                continue  # Historical position; it cannot acquire a later signal's exposure.
            if filled:
                managed_symbols.add(order.signal.symbol)
                expected_closed = False
                if order.exit_id:
                    exit_row = remote.orders.get(order.exit_id)
                    if (
                        exit_row
                        and exit_row["status"] == "FILLED"
                        and exit_row["reduceOnly"] == "true"
                        and exit_row["symbol"] == order.signal.symbol
                        and exit_row["clientOrderId"] == order.exit_id
                        and exit_row["side"] == ("SELL" if direction == 1 else "BUY")
                        and exit_row["positionSide"] == "BOTH"
                        and signed_number(exit_row["executedQty"]) > 0
                    ):
                        expected_closed = True
                        order.exit_state = "FILLED"
                        ledger.requests[order.exit_id] = "FILLED"
                for client_id in [order.algo_id, order.tp_algo_id]:
                    algo = remote.algos.get(client_id or "")
                    if algo and algo["algoStatus"] == "FINISHED":
                        execution = remote.orders.get("ALGO_EXEC:" + algo.get("actualOrderId", ""))
                        if (
                            execution
                            and execution["status"] == "FILLED"
                            and execution["symbol"] == order.signal.symbol
                            and execution["side"] == ("SELL" if direction == 1 else "BUY")
                        ):
                            expected_closed = True
                if pos == 0 and expected_closed and order.remaining:
                    order.open_quantity, order.actual_risk, order.protected = ZERO, ZERO, False
                    order.position_state = PositionState.EXIT_PENDING
                    unresolved = True
                    continue
                if expected_closed and pos:
                    ledger.risk_state = RiskState.EMERGENCY
                if pos == 0 and expected_closed and order.remaining == 0:
                    order.open_quantity, order.actual_risk, order.protected = ZERO, ZERO, False
                    order.position_state = PositionState.CLOSED
                    continue
                if pos * direction < 0 or abs(pos) > filled or (pos == 0 and not expected_closed):
                    mismatch = True
                    unresolved = True
                    order.open_quantity = max(order.open_quantity, filled)
                else:
                    if abs(pos) < filled and not expected_closed:
                        mismatch = True  # Manual partial close is not silently adopted.
                    order.open_quantity = abs(pos)
                order.average_price = number(row["avgPrice"])
                if pos and remote.entry_prices[order.signal.symbol] != order.average_price:
                    mismatch = True
                order.actual_risk = order.open_quantity * unit_loss(
                    ledger.policy, order.average_price, order.signal.initial_stop_price
                )
                order.protected = bool(pos) and self._stop_ok(
                    order, remote.algos.get(order.algo_id or "")
                )
                if order.protected and order.signal.purpose == "DEMO_VALIDATION":
                    ledger.validation_evidence.setdefault(str(order.signal.signal_id), {})[
                        "stop_verified"
                    ] = True
                order.position_state = (
                    PositionState.PROTECTED if order.protected else PositionState.OPEN_UNPROTECTED
                )
                if order.actual_risk + order.pending_risk > order.budget:
                    ledger.risk_state = RiskState.EMERGENCY
            for client_id in [order.algo_id, order.tp_algo_id]:
                if client_id in remote.algos:
                    ledger.algo_server_ids[client_id] = remote.algos[client_id]["algoId"]
            if order.open_quantity and order.protected and order.algo_id:
                ledger.requests[order.algo_id] = "VERIFIED"
            if order.open_quantity and order.tp_algo_id:
                tp = remote.algos.get(order.tp_algo_id)
                valid_tp = bool(
                    tp
                    and tp["clientAlgoId"] == order.tp_algo_id
                    and tp["symbol"] == order.signal.symbol
                    and tp["side"] == ("SELL" if order.signal.direction == "LONG" else "BUY")
                    and tp["algoType"] == "CONDITIONAL"
                    and tp["orderType"] == "TAKE_PROFIT_MARKET"
                    and tp["algoStatus"] == "NEW"
                    and tp["closePosition"] == "true"
                    and tp["positionSide"] == "BOTH"
                    and tp["workingType"] == "MARK_PRICE"
                    and tp["priceProtect"] == "false"
                    and signed_number(tp["triggerPrice"]) == order.signal.target_price
                )
                if valid_tp:
                    if order.signal.purpose == "DEMO_VALIDATION":
                        ledger.validation_evidence.setdefault(str(order.signal.signal_id), {})[
                            "target_verified"
                        ] = True
                    ledger.requests[order.tp_algo_id] = "VERIFIED"
                elif not tp or tp["algoStatus"] != "FINISHED":
                    ledger.paused = True
                    unresolved = True

        mismatch = mismatch or any(
            q and symbol not in managed_symbols for symbol, q in remote.positions.items()
        )
        if mismatch and ledger.risk_state not in {
            RiskState.EMERGENCY,
            RiskState.DAILY_LOCK,
            RiskState.WEEKLY_LOCK,
            RiskState.DRAWDOWN_LOCK,
        }:
            ledger.risk_state = RiskState.UNHEALTHY
        if not mismatch and not ledger.orders and ledger.risk_state == RiskState.UNHEALTHY:
            ledger.risk_state = RiskState.NORMAL
        account_risk(ledger, now)
        active = [o for o in ledger.orders.values() if o.remaining or o.open_quantity]
        if active and (
            any(
                remote.leverage.get(o.signal.symbol, ZERO) <= 0
                or remote.leverage[o.signal.symbol] > ledger.policy.limit("max_leverage")
                for o in active
            )
            or remote.equity <= 0
            or sum((o.notional for o in active), ZERO)
            > remote.equity * ledger.policy.limit("max_total_notional_pct") / 100
            or any(
                o.notional > remote.equity * ledger.policy.limit("max_single_notional_pct") / 100
                for o in active
            )
            or remote.margin_used
            > remote.equity * ledger.policy.limit("max_margin_usage_pct") / 100
            or sum((o.actual_risk + o.pending_risk for o in active), ZERO)
            > remote.equity * ledger.policy.limit("max_correlated_risk_pct") / 100
        ):
            ledger.risk_state = RiskState.EMERGENCY
        if self.client.settings.margin_mode == "ISOLATED":
            for order in active:
                if not order.open_quantity:
                    continue
                symbol = order.signal.symbol
                liquidation = remote.liquidation_prices.get(symbol, ZERO)
                stop = order.signal.initial_stop_price
                unsafe_stop = (
                    stop <= liquidation if order.signal.direction == "LONG" else stop >= liquidation
                )
                if (
                    remote.isolated_margins.get(symbol, ZERO) <= ZERO
                    or remote.isolated_wallets.get(symbol, ZERO) <= ZERO
                    or liquidation <= ZERO
                    or unsafe_stop
                ):
                    ledger.risk_state = RiskState.EMERGENCY
        ledger.fingerprint = remote.fingerprint
        ledger.last_reconciled = now
        ledger.reconciled = (
            not mismatch
            and not unresolved
            and all(not o.open_quantity or o.protected for o in ledger.orders.values())
        )

    def _protect(self, key: str, kind: str) -> None:
        def stage(ledger: DemoLedger) -> str | None:
            order = ledger.orders[key]
            if (
                order.position_state in {PositionState.CLOSED, PositionState.EXIT_PENDING}
                or order.open_quantity <= ZERO
            ):
                return None
            identifier = ("DS-" if kind == "SL" else "DT-") + order.client_id[3:]
            if identifier in ledger.requests:
                return None  # Query the same ID; never repeat an uncertain Algo POST.
            if kind == "SL":
                order.algo_id = identifier
            else:
                order.tp_algo_id = identifier
            ledger.requests[identifier] = "SUBMITTING"
            return identifier

        identifier = self.store.change(self.lease, "DEMO_" + kind + "_INTENT", stage)
        if identifier is None:
            return

        def effect(ledger: DemoLedger) -> None:
            order = ledger.orders[key]
            submission_attempted = False
            try:
                protection = AlgoOrder(
                    client_algo_id=identifier,
                    position_id=order.signal.signal_id,
                    environment=TradingMode.DEMO,
                    account_id=self.lease.account_id,
                    symbol=order.signal.symbol,
                    side="SELL" if order.signal.direction == "LONG" else "BUY",
                    order_type="STOP_MARKET" if kind == "SL" else "TAKE_PROFIT_MARKET",
                    trigger_price=order.signal.initial_stop_price
                    if kind == "SL"
                    else order.signal.target_price,
                )
                intent = build_protection(
                    protection, self.rules[order.signal.symbol], working_type="MARK_PRICE"
                )
                # Explicitly avoid priceProtect delaying the stop trigger.
                submission_attempted = True
                self.client.request(
                    "POST", intent.path, dict(intent.params) | {"priceProtect": "FALSE"}
                )
                ledger.requests[identifier] = "ACKNOWLEDGED"
            except (DemoFailure, ValueError):
                if submission_attempted:
                    ledger.requests[identifier] = "UNKNOWN"
                else:
                    ledger.requests[identifier] = "NOT_SENT"
                    ledger.request_no_send_evidence[identifier] = "LOCAL_VALIDATION_FAILED"
                ledger.paused = True
                if kind == "SL":
                    ledger.risk_state = RiskState.EMERGENCY

        self.store.change(self.lease, "DEMO_" + kind + "_EFFECT", effect)
        if kind == "SL":

            def verify(ledger: DemoLedger) -> None:
                self._merge(ledger, snapshot(self.client, ledger, self.store.now()))

            self.store.change(self.lease, "DEMO_SL_VERIFIED_QUERY", verify)

    def cancel(self, key: str) -> None:
        self._cancel_only(key)
        self.reconcile()

    def _cancel_only(self, key: str) -> None:
        def stage(ledger: DemoLedger) -> str | None:
            order = ledger.orders[key]
            if order.state == OrderState.RISK_RESERVED:
                order.state, order.remaining = OrderState.EXPIRED, ZERO
                return None
            if order.state not in TERMINAL:
                order.state = OrderState.CANCEL_REQUESTED
            ledger.reconciled = False
            return order.client_id

        identifier = self.store.change(self.lease, "DEMO_CANCEL_INTENT", stage)
        if identifier is None:
            return

        def effect(ledger: DemoLedger) -> None:
            order = ledger.orders[key]
            try:
                self.client.request(
                    "DELETE",
                    "/fapi/v1/order",
                    {"symbol": order.signal.symbol, "origClientOrderId": identifier},
                )
            except DemoFailure:
                order.state = OrderState.UNKNOWN

        self.store.change(self.lease, "DEMO_CANCEL_EFFECT", effect)

    def _flatten(self) -> None:
        for key, order in self.store.read(self.lease.account_id).orders.items():
            if order.remaining:

                def cancel(ledger: DemoLedger, key: str = key) -> None:
                    current = ledger.orders[key]
                    try:
                        self.client.request(
                            "DELETE",
                            "/fapi/v1/order",
                            {
                                "symbol": current.signal.symbol,
                                "origClientOrderId": current.client_id,
                            },
                        )
                    except DemoFailure:
                        pass

                self.store.change(self.lease, "DEMO_EMERGENCY_CANCEL", cancel)
            if not order.open_quantity or (
                order.exit_id and (order.exit_state != "FILLED" or order.exit_generation >= 3)
            ):
                continue
            generation = order.exit_generation + 1
            identifier = (
                "DX-" + order.client_id[3:] + (("-" + str(generation)) if generation > 1 else "")
            )

            def stage(ledger: DemoLedger, key: str = key, identifier: str = identifier) -> None:
                current = ledger.orders[key]
                current.exit_id, current.exit_state = identifier, "SUBMITTING"
                current.exit_generation += 1
                current.position_state = PositionState.EXIT_PENDING
                ledger.requests[identifier] = "SUBMITTING"

            self.store.change(self.lease, "DEMO_REDUCE_ONLY_INTENT", stage)

            def effect(ledger: DemoLedger, key: str = key, identifier: str = identifier) -> None:
                current = ledger.orders[key]
                try:
                    rows = self.client.request("GET", "/fapi/v3/positionRisk")
                    if not isinstance(rows, list):
                        raise DemoFailure("Invalid emergency position response")
                    matches = [
                        row
                        for row in rows
                        if row.get("symbol") == current.signal.symbol
                        and row.get("positionSide") == "BOTH"
                    ]
                    if len(matches) > 1:
                        raise DemoFailure("Ambiguous emergency position")
                    amount = signed_number(matches[0]["positionAmt"]) if matches else ZERO
                    if (
                        amount == 0
                        or amount * (1 if current.signal.direction == "LONG" else -1) <= 0
                    ):
                        # No POST occurred in this fenced effect. Preserve that
                        # distinction without clearing risk or reopening entries.
                        current.exit_state = "UNKNOWN"
                        ledger.requests[identifier] = "NOT_SENT"
                        ledger.request_no_send_evidence[identifier] = (
                            "POSITION_ALREADY_FLAT"
                            if amount == 0
                            else "POSITION_DIRECTION_MISMATCH"
                        )
                        return
                    self.client.request(
                        "POST",
                        "/fapi/v1/order",
                        {
                            "symbol": current.signal.symbol,
                            "side": "SELL" if amount > 0 else "BUY",
                            "type": "MARKET",
                            "reduceOnly": "true",
                            "quantity": format(abs(amount), "f"),
                            "newClientOrderId": identifier,
                        },
                    )
                    ledger.requests[identifier] = "ACKNOWLEDGED"
                    current.exit_state = "ACKNOWLEDGED"
                except DemoFailure:
                    current.exit_state = "UNKNOWN"
                    ledger.requests[identifier] = "UNKNOWN"

            self.store.change(self.lease, "DEMO_REDUCE_ONLY_EFFECT", effect)

    def _cleanup(self, key: str) -> None:
        def effect(ledger: DemoLedger) -> None:
            order = ledger.orders[key]
            remote = snapshot(self.client, ledger, self.store.now())
            if remote.positions.get(order.signal.symbol, ZERO) != 0:
                return
            if any(
                row["symbol"] == order.signal.symbol
                and row["status"] in {"NEW", "PARTIALLY_FILLED"}
                for row in remote.orders.values()
            ):
                return
            for identifier in [order.algo_id, order.tp_algo_id]:
                raw = remote.algos.get(identifier or "")
                if raw and raw["algoStatus"] == "NEW":
                    try:
                        self.client.request(
                            "DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": identifier or ""}
                        )
                    except DemoFailure:
                        ledger.reconciled = False

        self.store.change(self.lease, "DEMO_VERIFIED_FLAT_CLEANUP", effect)

    def confirm_flat(self) -> str:
        source = self.client
        self.flat_confirmation_stage = "TRANSPORT_SETUP"
        self.flat_confirmation_diagnostic: dict[str, str | int] = {}
        with confirmation_context(source) as bounded:
            self.client = bounded
            try:
                return self._confirm_flat_checked()
            finally:
                self.flat_confirmation_diagnostic = bounded.diagnostics()
                self.client = source

    def _confirm_flat_checked(self) -> str:
        """Query existing intents; never submit/cancel an entry or submit an exit.

        Leftover protection is removed only after both exchange and local history
        establish flat. An ACK, a missing order, or a zero position alone is insufficient.
        """

        cleanup_keys: list[str] = []

        def observe(ledger: DemoLedger) -> bool:
            if not ledger.paused:
                raise DemoFailure("Flat confirmation requires paused entries")
            first = historical_snapshot(self.client, ledger, self.store.now())
            second = historical_snapshot(self.client, ledger, self.store.now())
            remote = coherent_snapshot(first, second, ledger)
            self._merge(ledger, remote)
            cleanup_keys[:] = [
                key
                for key, order in ledger.orders.items()
                if order.position_state == PositionState.CLOSED
                and any(
                    remote.algos.get(identifier or "", {}).get("algoStatus") == "NEW"
                    for identifier in (order.algo_id, order.tp_algo_id)
                )
            ]
            return bool(
                ledger.reconciled
                and not any(remote.positions.values())
                and not any(
                    row["status"] in {"NEW", "PARTIALLY_FILLED"} for row in remote.orders.values()
                )
                and all(not o.remaining and not o.open_quantity for o in ledger.orders.values())
            )

        self.flat_confirmation_stage = "QUERY"
        if not self.store.change(self.lease, "DEMO_FLAT_CONFIRMATION_QUERY", observe):
            return "UNKNOWN"
        self.flat_confirmation_stage = "CLEANUP"
        if self.client.settings.demo_trading_enabled:
            for key in tuple(cleanup_keys):
                self._cleanup(key)

        def verify(ledger: DemoLedger) -> bool:
            if not observe(ledger):
                return False
            remote = historical_snapshot(self.client, ledger, self.store.now())
            return bool(
                remote.fingerprint == ledger.fingerprint
                and all(
                    row["algoStatus"] in {"CANCELED", "EXPIRED", "FINISHED", "REJECTED"}
                    for row in remote.algos.values()
                )
            )

        self.flat_confirmation_stage = "VERIFY"
        return (
            "VERIFIED_FLAT"
            if self.store.change(self.lease, "DEMO_FLAT_CONFIRMATION_CHECK", verify)
            else "UNKNOWN"
        )

    def command(self, operation_id: UUID, command: str) -> str:
        if command not in {"PAUSE_ENTRIES", "SAFE_STOP", "EMERGENCY_FLATTEN"}:
            raise ValueError("Invalid DEMO command")

        def stage(ledger: DemoLedger) -> None:
            key = str(operation_id)
            if key in ledger.operations and ledger.operations[key] != command:
                raise ValueError("Conflicting operation ID")
            ledger.operations[key] = command
            ledger.paused = True
            if command == "EMERGENCY_FLATTEN":
                ledger.risk_state = RiskState.EMERGENCY

        self.store.change(self.lease, "DEMO_OPERATION_INTENT", stage)
        if command in {"PAUSE_ENTRIES", "EMERGENCY_FLATTEN"}:
            for key, order in self.store.read(self.lease.account_id).orders.items():
                if order.remaining:
                    self.cancel(key)
        self.reconcile()
        ledger = self.store.read(self.lease.account_id)
        if command == "EMERGENCY_FLATTEN":
            return (
                "VERIFIED_FLAT"
                if ledger.reconciled
                and not any(ledger.positions.values())
                and all(not o.remaining and not o.open_quantity for o in ledger.orders.values())
                else "UNKNOWN"
            )
        return "ENTRIES_BLOCKED"

    def expire(self) -> None:
        for key, order in self.store.read(self.lease.account_id).orders.items():
            if order.remaining and self.store.now() >= order.signal.expires_at:
                self.cancel(key)

    def resume_entries(self, operation_id: UUID) -> str:
        """Explicit CLI operator action; never clears loss/emergency/mismatch locks."""

        def apply(ledger: DemoLedger) -> str:
            key = str(operation_id)
            if key in ledger.operations and ledger.operations[key] != "RESUME_ENTRIES":
                raise ValueError("Conflicting DEMO operation ID")
            if not self.client.settings.demo_trading_enabled:
                raise RiskRejected("DEMO entry mutations disabled")
            account_risk(ledger, self.store.now())
            if (
                self.session_failed
                or ledger.risk_state != RiskState.NORMAL
                or not ledger.reconciled
                or not ledger.stream_ready
            ):
                raise RiskRejected("Resume requires NORMAL risk and reconciled private stream")
            if (
                ledger.last_reconciled is None
                or ledger.stream_at is None
                or any(
                    not 0
                    <= (self.store.now() - at).total_seconds()
                    <= ledger.policy.max_snapshot_age_seconds
                    for at in (ledger.last_reconciled, ledger.stream_at)
                )
            ):
                raise RiskRejected("Resume observation is stale")
            if snapshot(self.client, ledger, self.store.now()).fingerprint != ledger.fingerprint:
                raise RiskRejected("Resume requires unchanged reconciled account")
            ledger.paused = False
            ledger.operations[key] = "RESUME_ENTRIES"
            return "ENTRIES_READY_FOR_NEW_SIGNAL_GATES"

        return self.store.change(self.lease, "DEMO_OPERATOR_RESUME", apply)
