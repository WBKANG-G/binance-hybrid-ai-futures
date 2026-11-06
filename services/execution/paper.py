"""Fenced PAPER coordinator: intent -> submit once -> query -> protect -> verify.

All ambiguous side effects are queried, never blindly resubmitted. The database
and durable simulator are distinct truths; a new leader must reconcile both.
"""

from datetime import UTC, datetime, time
from decimal import Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

from integrations.binance_usdm.filters import SymbolRules
from packages.domain.contracts import OrderState, PositionState, RiskState, Signal
from packages.persistence.paper import PaperStore
from services.execution.models import Lease, Ledger, PaperOrder
from services.execution.simulator import PaperExchange
from services.protection.paper import verified_stop
from services.risk.paper import GateSnapshot, RiskRejected, period_keys, size, unit_loss

ZERO = Decimal(0)
TERMINAL = {OrderState.CANCELED, OrderState.FILLED, OrderState.REJECTED, OrderState.EXPIRED}


def account_risk(ledger: Ledger, now: datetime) -> None:
    value = ledger.value
    if not 0 <= (now - value.as_of).total_seconds() <= ledger.policy.max_snapshot_age_seconds:
        if ledger.risk_state in {RiskState.NORMAL, RiskState.COOLDOWN}:
            ledger.risk_state = RiskState.UNHEALTHY
        ledger.reconciled = False
        return
    adjusted = value.adjusted_equity
    day, week = period_keys(now, ledger.policy.week_start_kst)
    # Missing an exact KST opening valuation must not invent a daily/weekly baseline.
    if day != ledger.daily_key or week != ledger.weekly_key:
        boundary = datetime.combine(
            now.astimezone(ZoneInfo("Asia/Seoul")).date(), time(), ZoneInfo("Asia/Seoul")
        ).astimezone(UTC)
        if value.as_of != boundary:
            if ledger.risk_state == RiskState.NORMAL:
                ledger.risk_state = RiskState.UNHEALTHY
            return
    # A boundary resets the accounting baseline, never automatically unlocks a hard lock.
    if day != ledger.daily_key:
        ledger.daily_key, ledger.daily_base = day, max(adjusted, Decimal("0.00000001"))
    if week != ledger.weekly_key:
        ledger.weekly_key, ledger.weekly_base = week, max(adjusted, Decimal("0.00000001"))
    ledger.high_water = max(ledger.high_water, adjusted)
    if ledger.risk_state in {
        RiskState.EMERGENCY,
        RiskState.DAILY_LOCK,
        RiskState.WEEKLY_LOCK,
        RiskState.DRAWDOWN_LOCK,
    }:
        return
    for base, cap, state in (
        (ledger.high_water, "max_drawdown_pct", RiskState.DRAWDOWN_LOCK),
        (ledger.weekly_base, "weekly_loss_limit_pct", RiskState.WEEKLY_LOCK),
        (ledger.daily_base, "daily_loss_limit_pct", RiskState.DAILY_LOCK),
    ):
        if adjusted <= 0 or (base - adjusted) / base * 100 >= ledger.policy.limit(cap):
            ledger.risk_state = state
            return


class PaperCoordinator:
    def __init__(self, store: PaperStore, lease: Lease, exchange: PaperExchange):
        if exchange.store is not store or exchange.lease != lease:
            raise ValueError("PAPER venue/fence mismatch")
        self.store, self.lease, self.exchange = store, lease, exchange

    def reserve(
        self, signal: Signal, snapshot: GateSnapshot, rules: SymbolRules, now: datetime
    ) -> PaperOrder:
        signal = Signal.model_validate(signal.model_dump())

        def apply(ledger: Ledger) -> PaperOrder | None:
            key = str(signal.signal_id)
            if key in ledger.orders:
                if ledger.orders[key].signal != signal:
                    raise RiskRejected("Signal ID payload conflict")
                return ledger.orders[key].model_copy(deep=True)
            account_risk(ledger, now)
            if ledger.paused or not ledger.reconciled or ledger.risk_state != RiskState.NORMAL:
                return None
            if (
                ledger.last_reconciled is None
                or not 0
                <= (now - ledger.last_reconciled).total_seconds()
                <= ledger.policy.max_snapshot_age_seconds
            ):
                ledger.reconciled = False
                return None
            quantity, loss = size(ledger.policy, signal, snapshot, ledger.value.equity, rules, now)
            active = [o for o in ledger.orders.values() if o.remaining or o.open_quantity]
            if any(o.signal.symbol == signal.symbol for o in active):
                raise RiskRejected("No averaging down or multiple symbol entries")
            equity = ledger.value.equity
            notional = quantity * signal.suggested_limit_price
            total = sum((o.notional for o in active), ZERO) + notional
            risk = quantity * loss
            correlated = sum((o.charged_risk for o in active), ZERO) + risk
            if (
                len(active) >= ledger.policy.limit("max_concurrent_positions")
                or notional > equity * ledger.policy.limit("max_single_notional_pct") / 100
                or total > equity * ledger.policy.limit("max_total_notional_pct") / 100
                or total / ledger.policy.limit("max_leverage")
                > equity * ledger.policy.limit("max_margin_usage_pct") / 100
                or correlated > equity * ledger.policy.limit("max_correlated_risk_pct") / 100
            ):
                raise RiskRejected("Portfolio reservation cap")
            client_id = "PE-" + sha256((self.lease.account_id + key).encode()).hexdigest()[:30]
            order = PaperOrder(
                client_id=client_id,
                signal=signal,
                group=snapshot.correlation_group,
                quantity=quantity,
                quantity_step=rules.step_size,
                price_tick=rules.tick_size,
                snapshot=snapshot,
                remaining=quantity,
                loss_per_unit=loss,
                budget=risk,
            )
            ledger.orders[key] = order
            return order.model_copy(deep=True)

        result = self.store.change(
            self.lease,
            now,
            "ENTRY_RESERVATION",
            apply,
            require_current_venue=True,
            replay_signal_id=str(signal.signal_id),
        )
        if result is None:
            raise RiskRejected("Account is locked, stale, paused or not reconciled")
        return result

    def submit(self, signal_id: str, now: datetime) -> None:
        def stage(ledger: Ledger) -> PaperOrder | None:
            order = ledger.orders[signal_id]
            account_risk(ledger, now)
            if order.state != OrderState.RISK_RESERVED:
                return None
            if (
                ledger.paused
                or not ledger.reconciled
                or ledger.risk_state != RiskState.NORMAL
                or now >= order.signal.expires_at
                or any(
                    not 0 <= (now - t).total_seconds() <= ledger.policy.max_snapshot_age_seconds
                    for t in (
                        order.snapshot.market_at,
                        order.snapshot.account_at,
                        order.snapshot.correlation_at,
                    )
                )
            ):
                # Never release until absence at venue is verified in reconciliation.
                ledger.reconciled = False
                return None
            order.state = OrderState.SUBMITTING
            return order.model_copy(deep=True)

        order = self.store.change(self.lease, now, "ENTRY_SUBMITTING", stage)
        if order is None:
            return
        try:
            self.exchange.submit(order, now)
        except TimeoutError:

            def unknown(ledger: Ledger) -> None:
                ledger.orders[signal_id].state = OrderState.UNKNOWN
                ledger.reconciled = False

            self.store.change(self.lease, now, "ENTRY_UNKNOWN", unknown)
            return
        self.reconcile(now)

    def cancel(self, signal_id: str, now: datetime) -> None:
        def stage(ledger: Ledger) -> str:
            order = ledger.orders[signal_id]
            if order.state not in TERMINAL:
                order.state = OrderState.CANCEL_REQUESTED
            return order.client_id

        client_id = self.store.change(self.lease, now, "CANCEL_REQUESTED", stage)
        try:
            self.exchange.cancel(client_id, now)
        except TimeoutError:
            self.store.change(
                self.lease,
                now,
                "CANCEL_UNKNOWN",
                lambda ledger: setattr(ledger, "reconciled", False),
            )
        self.reconcile(now)

    def reconcile(self, now: datetime) -> None:
        # Mark unsafe before querying; crash at any later point requires another reconciliation.
        self.store.change(
            self.lease, now, "RECONCILING", lambda ledger: setattr(ledger, "reconciled", False)
        )
        venue = self.exchange.query(now)

        def merge(ledger: Ledger) -> None:
            ledger.value = venue.value
            mismatch = bool(any(venue.manual_positions.values()))
            known = {o.client_id for o in ledger.orders.values()}
            mismatch = mismatch or bool(set(venue.orders) - known)
            for order in ledger.orders.values():
                remote = venue.orders.get(order.client_id)
                if remote is None:
                    if order.state in {
                        OrderState.SUBMITTING,
                        OrderState.UNKNOWN,
                        OrderState.RECONCILING,
                        OrderState.CANCEL_REQUESTED,
                    }:
                        # Durable simulator absence is authoritative; never auto-resubmit.
                        order.state, order.remaining = OrderState.REJECTED, ZERO
                    continue
                if remote.signal != order.signal or remote.quantity != order.quantity:
                    mismatch = True
                    continue
                filled = sum((q for q, _ in remote.fills.values()), ZERO)
                quote = sum((q * p for q, p in remote.fills.values()), ZERO)
                if filled < order.filled:
                    mismatch = True
                    continue
                for fill_id, pair in remote.fills.items():
                    fingerprint = str(pair)
                    if fill_id in order.seen_fills and order.seen_fills[fill_id] != fingerprint:
                        mismatch = True
                    order.seen_fills[fill_id] = fingerprint
                order.filled = filled
                order.average_price = quote / filled if filled else ZERO
                order.open_quantity = remote.open_quantity
                order.remaining = ZERO if remote.state in TERMINAL else order.quantity - filled
                order.state = remote.state
                order.actual_risk = (
                    remote.open_quantity
                    * unit_loss(ledger.policy, order.average_price, order.signal.initial_stop_price)
                    if remote.open_quantity
                    else ZERO
                )
                order.algo_id = verified_stop(order, venue)
                order.protected = order.algo_id is not None
                if remote.open_quantity:
                    order.position_state = (
                        PositionState.PROTECTED
                        if order.protected
                        else PositionState.OPEN_UNPROTECTED
                    )
                    if order.actual_risk + order.pending_risk > order.budget or (
                        order.open_quantity
                        and (
                            (
                                order.signal.direction == "LONG"
                                and order.average_price <= order.signal.initial_stop_price
                            )
                            or (
                                order.signal.direction == "SHORT"
                                and order.average_price >= order.signal.initial_stop_price
                            )
                        )
                    ):
                        ledger.risk_state = RiskState.EMERGENCY
                else:
                    order.position_state = (
                        PositionState.CLOSED if filled else PositionState.ENTRY_PENDING
                    )
                if order.exit_id:
                    order.exit_state = (
                        "FILLED"
                        if remote.open_quantity == 0 and venue.exits.get(order.exit_id) == "FILLED"
                        else "UNKNOWN"
                    )
            if mismatch and ledger.risk_state not in {
                RiskState.EMERGENCY,
                RiskState.DAILY_LOCK,
                RiskState.WEEKLY_LOCK,
                RiskState.DRAWDOWN_LOCK,
            }:
                ledger.risk_state = RiskState.UNHEALTHY
            if not mismatch and ledger.risk_state == RiskState.UNHEALTHY and not ledger.orders:
                ledger.risk_state = RiskState.NORMAL
            account_risk(ledger, now)

        self.store.change(self.lease, now, "RECONCILED_SNAPSHOT", merge)
        ledger = self.store.read(self.lease.account_id)
        for key, order in ledger.orders.items():
            if order.open_quantity and not order.protected:
                try:
                    self.exchange.protect(order, now)
                    verified = self.exchange.query(now)
                    algo_id = verified_stop(order, verified)
                    if algo_id is None:
                        raise TimeoutError("Stop did not verify")

                    def protected(ledger: Ledger, key: str = key, algo_id: str = algo_id) -> None:
                        o = ledger.orders[key]
                        o.protected, o.algo_id, o.position_state = (
                            True,
                            algo_id,
                            PositionState.PROTECTED,
                        )

                    self.store.change(self.lease, now, "STOP_VERIFIED", protected)
                except TimeoutError:
                    self.store.change(
                        self.lease,
                        now,
                        "STOP_FAILURE_EMERGENCY",
                        lambda ledger: setattr(ledger, "risk_state", RiskState.EMERGENCY),
                    )
            if not order.open_quantity:
                for stop in venue.stops.values():
                    if stop.symbol == order.signal.symbol and stop.active:
                        self.exchange.cancel_stop_if_flat(stop.algo_id, now)

        def recheck_actual(ledger: Ledger) -> None:
            active = [o for o in ledger.orders.values() if o.remaining or o.open_quantity]
            equity = ledger.value.equity
            total = sum((o.notional for o in active), ZERO)
            charged = sum((o.charged_risk for o in active), ZERO)
            if active and (
                equity <= 0
                or total > equity * ledger.policy.limit("max_total_notional_pct") / 100
                or total / ledger.policy.limit("max_leverage")
                > equity * ledger.policy.limit("max_margin_usage_pct") / 100
                or charged > equity * ledger.policy.limit("max_correlated_risk_pct") / 100
                or any(
                    o.notional > equity * ledger.policy.limit("max_single_notional_pct") / 100
                    or o.actual_risk + o.pending_risk > o.budget
                    for o in active
                )
            ):
                ledger.risk_state = RiskState.EMERGENCY

        self.store.change(self.lease, now, "ACTUAL_EXPOSURE_RECHECK", recheck_actual)
        ledger = self.store.read(self.lease.account_id)
        if ledger.risk_state == RiskState.EMERGENCY:
            self._flatten(now)
        fresh = self.exchange.query(now)

        def finish(ledger: Ledger) -> None:
            # Any manual/unknown position blocks entry even if all managed stops exist.
            matched = not any(fresh.manual_positions.values()) and set(fresh.orders) <= {
                o.client_id for o in ledger.orders.values()
            }
            consistent = all(
                (
                    o.client_id not in fresh.orders
                    and o.state in {OrderState.RISK_RESERVED, OrderState.REJECTED}
                )
                or (
                    o.client_id in fresh.orders
                    and fresh.orders[o.client_id].open_quantity == o.open_quantity
                    and fresh.orders[o.client_id].state == o.state
                    and len(fresh.orders[o.client_id].fills) == len(o.seen_fills)
                    and (not o.open_quantity or verified_stop(o, fresh) is not None)
                )
                for o in ledger.orders.values()
            )
            ledger.reconciled = (
                matched
                and consistent
                and all(not o.open_quantity or o.protected for o in ledger.orders.values())
            )
            if ledger.reconciled:
                ledger.venue_revision = fresh.revision
            ledger.last_reconciled = now

        self.store.change(self.lease, now, "RECONCILIATION_FINISHED", finish)

    def _flatten(self, now: datetime) -> None:
        ledger = self.store.read(self.lease.account_id)
        for key, order in ledger.orders.items():
            if order.remaining:
                try:
                    self.exchange.cancel(order.client_id, now)
                except TimeoutError:
                    pass  # Retain pending reservation until a later authoritative query.
            if order.open_quantity and order.exit_id is None:
                exit_id = "PX-" + order.client_id[3:]

                def stage(ledger: Ledger, key: str = key, exit_id: str = exit_id) -> None:
                    ledger.orders[key].exit_id = exit_id
                    ledger.orders[key].exit_state = "SUBMITTING"
                    ledger.orders[key].position_state = PositionState.EXIT_PENDING

                self.store.change(self.lease, now, "REDUCE_ONLY_INTENT", stage)
                try:
                    self.exchange.reduce(order, exit_id, now)
                except TimeoutError:

                    def unknown_exit(ledger: Ledger, key: str = key) -> None:
                        ledger.orders[key].exit_state = "UNKNOWN"

                    self.store.change(self.lease, now, "EXIT_UNKNOWN", unknown_exit)
        # Completion requires the next authoritative position query, never the reduce ACK.

    def command(self, operation_id: str, command: str, now: datetime) -> str:
        if command not in {"PAUSE_ENTRIES", "SAFE_STOP", "EMERGENCY_FLATTEN"} or not operation_id:
            raise ValueError("Explicit operation ID and safe command required")

        def stage(ledger: Ledger) -> bool:
            if operation_id in ledger.operations:
                if ledger.operations[operation_id] != command:
                    raise ValueError("Conflicting operation ID")
                return False
            ledger.operations[operation_id] = command
            ledger.paused = True
            if command == "EMERGENCY_FLATTEN":
                ledger.risk_state = RiskState.EMERGENCY
            return True

        self.store.change(self.lease, now, "OPERATION_INTENT", stage)
        # Repeating a command resumes its durable work; each side effect has its own ID.
        ledger = self.store.read(self.lease.account_id)
        if command in {"PAUSE_ENTRIES", "EMERGENCY_FLATTEN"}:
            for key, o in ledger.orders.items():
                if o.remaining:
                    self.cancel(key, now)
        self.reconcile(now)
        ledger = self.store.read(self.lease.account_id)
        if command == "EMERGENCY_FLATTEN":
            return (
                "VERIFIED_FLAT"
                if all(not o.remaining and not o.open_quantity for o in ledger.orders.values())
                and ledger.reconciled
                else "UNKNOWN"
            )
        return "ENTRIES_BLOCKED"

    def expire(self, now: datetime) -> None:
        ledger = self.store.read(self.lease.account_id)
        for key, order in ledger.orders.items():
            if order.remaining and now >= order.signal.expires_at:
                self.cancel(key, now)
