"""Protection verification never treats a submission ACK as proof of coverage."""

from services.execution.models import PaperOrder, Venue


def verified_stop(order: PaperOrder, venue: Venue) -> str | None:
    for stop in venue.stops.values():
        if (
            stop.active
            and stop.symbol == order.signal.symbol
            and stop.side == ("SELL" if order.signal.direction == "LONG" else "BUY")
            and stop.trigger == order.signal.initial_stop_price
            and stop.quantity >= order.open_quantity
            and order.open_quantity > 0
            and stop.order_type == "STOP_MARKET"
            and stop.working_type == "MARK_PRICE"
        ):
            return stop.algo_id
    return None
