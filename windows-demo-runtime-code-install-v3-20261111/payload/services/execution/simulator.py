"""Durable, offline PAPER venue. No sockets, keys or Binance order transport.

Fills require explicit liquidity evidence; candle touches never guarantee a fill.
Stop semantics model one-way close-position MARK_PRICE without priceProtect.
This is a simulator contract, not a verified Binance DEMO behavior.
"""

from datetime import datetime
from decimal import Decimal

from packages.domain.contracts import OrderState
from packages.persistence.paper import PaperStore
from services.execution.models import Lease, PaperOrder, Stop, Venue, VenueOrder
from services.risk.paper import AccountValue


class PaperExchange:
    def __init__(self, store: PaperStore, lease: Lease):
        self.store, self.lease = store, lease
        self.lose_submit_response = False
        self.lose_cancel_response = False
        self.fee_rate = store.read(lease.account_id).policy.fee_rate
        self.fail_stop = False
        self.defer_exit = False

    def query(self, now: datetime) -> Venue:
        return self.store.venue_change(
            self.lease, now, "VENUE_QUERY", lambda v: v.model_copy(deep=True)
        )

    def submit(self, order: PaperOrder, now: datetime) -> None:
        def apply(v: Venue) -> None:
            if order.client_id in v.orders:
                known = v.orders[order.client_id]
                if known.signal != order.signal or known.quantity != order.quantity:
                    raise ValueError("Conflicting client order ID")
                return
            v.submit_count += 1
            v.orders[order.client_id] = VenueOrder(
                client_id=order.client_id, signal=order.signal, quantity=order.quantity
            )

        self.store.venue_change(self.lease, now, "VENUE_LIMIT_SUBMIT", apply)
        if self.lose_submit_response:
            raise TimeoutError("Injected lost PAPER submit response")

    def fill(
        self, client_id: str, fill_id: str, quantity: Decimal, price: Decimal, now: datetime
    ) -> None:
        def apply(v: Venue) -> None:
            order = v.orders[client_id]
            if fill_id in order.fills:
                if order.fills[fill_id] != (quantity, price):
                    raise ValueError("Conflicting fill ID")
                return
            # Cancel ACK is terminal; late *delivery* is represented by querying already committed fills.
            if order.state not in {OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED}:
                raise ValueError("Cannot fill terminal order")
            if (
                not isinstance(quantity, Decimal)
                or not isinstance(price, Decimal)
                or not quantity.is_finite()
                or not price.is_finite()
                or quantity <= 0
                or price <= 0
            ):
                raise ValueError("Finite positive Decimal fill required")
            if (
                order.signal.direction == "LONG" and price > order.signal.suggested_limit_price
            ) or (order.signal.direction == "SHORT" and price < order.signal.suggested_limit_price):
                raise ValueError("Fill violates LIMIT price")
            total = sum((q for q, _ in order.fills.values()), Decimal(0)) + quantity
            if total > order.quantity:
                raise ValueError("Overfill")
            # The ledger intent carries exchange filter precision used at reservation.
            intent = self.store.read(self.lease.account_id).orders[str(order.signal.signal_id)]
            if quantity % intent.quantity_step or price % intent.price_tick:
                raise ValueError("Fill violates tick/step precision")
            order.fills[fill_id] = (quantity, price)
            v.marks.setdefault(order.signal.symbol, price)
            self._value(v, now, fees=quantity * price * self.fee_rate)
            order.open_quantity += quantity
            self._mark_equity(v, now)
            order.state = (
                OrderState.FILLED if total == order.quantity else OrderState.PARTIALLY_FILLED
            )

        self.store.venue_change(self.lease, now, "VENUE_FILL", apply)

    def cancel(self, client_id: str, now: datetime) -> None:
        if self.lose_cancel_response:
            raise TimeoutError("Injected cancel uncertainty; venue may still fill")

        def apply(v: Venue) -> None:
            order = v.orders.get(client_id)
            if order and order.state in {OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED}:
                order.state = OrderState.CANCELED

        self.store.venue_change(self.lease, now, "VENUE_CANCEL", apply)

    def protect(self, order: PaperOrder, now: datetime) -> None:
        if self.fail_stop:
            raise TimeoutError("Injected Algo stop failure")

        def apply(v: Venue) -> None:
            # Stable, separate namespace; atomic close-position update in PAPER only.
            algo_id = "PA-" + order.client_id[3:]
            v.stops[algo_id] = Stop(
                algo_id=algo_id,
                symbol=order.signal.symbol,
                side="SELL" if order.signal.direction == "LONG" else "BUY",
                trigger=order.signal.initial_stop_price,
                quantity=order.open_quantity,
            )

        self.store.venue_change(self.lease, now, "VENUE_ALGO_STOP", apply)

    def reduce(self, order: PaperOrder, exit_id: str, now: datetime) -> None:
        def apply(v: Venue) -> None:
            if exit_id in v.exits:
                return
            v.exit_count += 1
            v.exits[exit_id] = "ACKNOWLEDGED"
            if not self.defer_exit:
                self._close(v, v.orders[order.client_id], now)
                v.exits[exit_id] = "FILLED"

        self.store.venue_change(self.lease, now, "VENUE_REDUCE_ONLY_EXIT", apply)

    def settle_exit(self, exit_id: str, client_id: str, now: datetime) -> None:
        def apply(v: Venue) -> None:
            if exit_id not in v.exits:
                raise ValueError("Unknown exit")
            self._close(v, v.orders[client_id], now)
            v.exits[exit_id] = "FILLED"

        self.store.venue_change(self.lease, now, "VENUE_EXIT_FILL", apply)

    def cancel_stop_if_flat(self, algo_id: str, now: datetime) -> None:
        def apply(v: Venue) -> None:
            stop = v.stops.get(algo_id)
            if stop is None:
                return
            if (
                any(
                    o.signal.symbol == stop.symbol and o.open_quantity > 0
                    for o in v.orders.values()
                )
                or v.manual_positions.get(stop.symbol, Decimal(0)) != 0
            ):
                raise ValueError("Cannot remove protection before verified flat")
            stop.active = False

        self.store.venue_change(self.lease, now, "VENUE_FLAT_STOP_CLEANUP", apply)

    def mark_account(self, value: AccountValue, now: datetime) -> None:
        self.store.venue_change(
            self.lease,
            now,
            "VENUE_ACCOUNT_MARK",
            lambda v: setattr(v, "value", AccountValue.model_validate(value.model_dump())),
        )

    @staticmethod
    def _value(
        venue: Venue, now: datetime, *, fees: Decimal = Decimal(0), realized: Decimal = Decimal(0)
    ) -> None:
        raw = venue.value.model_dump()
        raw.update(
            as_of=now, fees=venue.value.fees + fees, realized=venue.value.realized + realized
        )
        venue.value = AccountValue.model_validate(raw)

    @staticmethod
    def _average(order: VenueOrder) -> Decimal:
        quantity = sum((q for q, _ in order.fills.values()), Decimal(0))
        return sum((q * p for q, p in order.fills.values()), Decimal(0)) / quantity

    def _mark_equity(self, venue: Venue, now: datetime) -> None:
        unrealized = sum(
            (
                (venue.marks[o.signal.symbol] - self._average(o))
                * o.open_quantity
                * (1 if o.signal.direction == "LONG" else -1)
                for o in venue.orders.values()
                if o.open_quantity
            ),
            Decimal(0),
        )
        raw = venue.value.model_dump()
        raw.update(as_of=now, unrealized=unrealized)
        venue.value = AccountValue.model_validate(raw)

    def _close(
        self, venue: Venue, order: VenueOrder, now: datetime, price: Decimal | None = None
    ) -> None:
        if not order.open_quantity:
            return
        average = self._average(order)
        price = price if price is not None else venue.marks.get(order.signal.symbol, average)
        realized = (
            (price - average)
            * order.open_quantity
            * (1 if order.signal.direction == "LONG" else -1)
        )
        self._value(venue, now, fees=price * order.open_quantity * self.fee_rate, realized=realized)
        order.open_quantity = Decimal(0)
        self._mark_equity(venue, now)

    def mark(self, symbol: str, mark_price: Decimal, now: datetime) -> None:
        """Explicit ordered MARK_PRICE tick: SL first, then local PAPER TP.

        Gaps execute SL at the adverse mark; TP gets no improvement beyond target.
        No inference of liquidity or intrabar ordering from OHLC candles.
        """
        if not isinstance(mark_price, Decimal) or not mark_price.is_finite() or mark_price <= 0:
            raise ValueError("Finite positive Decimal mark required")

        def apply(venue: Venue) -> None:
            venue.marks[symbol] = mark_price
            for order in venue.orders.values():
                if order.signal.symbol != symbol or not order.open_quantity:
                    continue
                long = order.signal.direction == "LONG"
                stop_hit = any(
                    stop.active
                    and stop.symbol == symbol
                    and (
                        (long and mark_price <= stop.trigger)
                        or (not long and mark_price >= stop.trigger)
                    )
                    for stop in venue.stops.values()
                )
                target_hit = (
                    mark_price >= order.signal.target_price
                    if long
                    else mark_price <= order.signal.target_price
                )
                if stop_hit or target_hit:
                    self._close(
                        venue, order, now, mark_price if stop_hit else order.signal.target_price
                    )
                    # Residual entries are canceled atomically by this PAPER fixture only.
                    if order.state == OrderState.PARTIALLY_FILLED:
                        order.state = OrderState.CANCELED
            self._mark_equity(venue, now)

        self.store.venue_change(self.lease, now, "VENUE_MARK_SL_FIRST_TP", apply)
