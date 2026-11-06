"""Expiry cancellation still reconciles a fill racing with an unfilled ledger."""

from datetime import timedelta
from decimal import Decimal

import pytest

from tests.integration.test_demo import (
    enter,
    posts,
    system,  # noqa: F401
)


@pytest.mark.parametrize("partial", [False, True])
def test_expiry_reconciles_racing_partial_fill_and_preserves_protection(system, partial):  # noqa: F811
    store, c, fake = system
    sig, order = enter(system)
    filled = order.quantity / 2 if partial else Decimal(0)
    if partial:
        # No prior event/reconcile: the cancellation races an unseen venue fill.
        fake.fill(order.client_id, filled)
    fake.at = sig.expires_at + timedelta(milliseconds=1)
    store.renew(c.lease)
    c.client.sync_clock()
    c.expire()
    current = store.read("DEMO-test").orders[str(sig.signal_id)]
    assert current.remaining == 0 and current.filled == filled
    assert len(posts(fake)) == 1  # Never creates a replacement entry.
    if partial:
        assert current.protected and current.algo_id and current.tp_algo_id
        assert c._stop_ok(
            current,
            {
                k: str(v).lower() if isinstance(v, bool) else v
                for k, v in fake.algos[current.algo_id].items()
            },
        )
        assert fake.algos[current.tp_algo_id]["algoStatus"] == "NEW"
        assert fake.algos[current.tp_algo_id]["orderType"] == "TAKE_PROFIT_MARKET"
        assert fake.algos[current.tp_algo_id]["closePosition"] is True
        assert current.open_quantity == filled
        assert len(posts(fake, "/fapi/v1/algoOrder")) == 2
        assert not any(m == "DELETE" and p == "/fapi/v1/algoOrder" for m, p, _ in fake.calls)
    else:
        assert not current.open_quantity
        assert not posts(fake, "/fapi/v1/algoOrder")
    before = len(fake.calls)
    c.expire()
    assert len(fake.calls) == before
