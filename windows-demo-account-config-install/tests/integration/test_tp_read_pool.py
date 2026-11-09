"""Real PG/local WSS, synthetic venue: opt-in routing preserves execution policy."""

from decimal import Decimal

import pytest

from integrations.binance_usdm.demo import DemoUnknown
from integrations.binance_usdm.demo_read_routing import TPReadRouting
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.mark.parametrize(
    "mode", ["tp", "unfilled", "no_tp", "sl", "bad_qty", "missing_cancel", "query_failure"]
)
def test_routed_tp_same_caps_protection_cleanup_and_no_duplicate_entry(
    opposite_case,  # noqa: F811
    monkeypatch,
    mode,
):
    store, c, fake, options = opposite_case
    if mode == "unfilled":
        options.update(fill=False, trigger=False)
    elif mode == "no_tp":
        options["trigger"] = False
    elif mode == "sl":
        options["trigger"] = "SL"
    elif mode == "bad_qty":
        options["tamper"] = True
    elif mode == "missing_cancel":
        options["cancel_query"] = True
    before = store.read("DEMO-test")
    original = c.client.send

    class Pool:
        calls = closes = 0
        failed = False

        def __call__(self, method, path, query, key):
            self.calls += 1
            if (
                mode == "query_failure"
                and path == "/fapi/v1/order"
                and posts(fake)
                and not self.failed
            ):
                self.failed = True
                raise DemoUnknown("SYNTHETIC_GET_FAILURE")
            return original(method, path, query, key)

        def close(self):
            self.closes += 1

        def diagnostics(self):
            return {"audit_transport_mode": "POOLED_HTTPS", "audit_http_attempts": self.calls}

    pool = Pool()
    route = TPReadRouting(original, pool=pool)
    c.client.send = route
    try:
        report = exercise_opposite(opposite_case, monkeypatch)
    finally:
        route.close()
        c.client.send = original
    assert pool.closes == 1 and pool.calls > 0
    assert report["entry_post_attempts"] == report["new_entry_post_count"] == 1
    entries = [p for p in posts(fake) if p["type"] == "LIMIT"]
    assert len(entries) == 1 and Decimal(entries[0]["quantity"]) == Decimal("0.0007")
    assert Decimal(entries[0]["price"]) == Decimal("80000.1")
    assert store.read("DEMO-test").paused and not any(fake.positions.values())
    assert report["history_and_baselines_preserved"]
    from services.execution.demo_tp_trigger_check import history_preserved

    assert history_preserved(before, store.read("DEMO-test"), report["operation_id"])
    if mode == "tp":
        assert report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED", (
            report
        )
        assert (
            report["signed_tp_fill_before_sl_delete_verified"]
            and report["sl_cancel_query_verified"]
        )
        assert report["operator_exit_post_attempts"] == 0 and report["sl_delete_attempts"] == 1
    else:
        assert report["tp_trigger_check"] == "NOT_ACCEPTED", report


@pytest.mark.parametrize("mode", ["stale_book", "expired_signal"])
def test_routing_never_bypasses_original_quote_or_signal_expiry_gate(
    opposite_case,  # noqa: F811
    monkeypatch,
    mode,
):
    from services.execution import demo_validation as validation

    store, c, fake, options = opposite_case
    options["trigger"] = False
    original = c.client.send

    def send(method, path, query, key):
        response = original(method, path, query, key)
        if mode == "stale_book" and path == "/fapi/v1/ticker/bookTicker":
            import json

            raw = json.loads(response.body)
            raw["time"] = c.client.clock_ms() - 5001
            return fake.response(raw)
        return response

    class Pool:
        def __call__(self, *args):
            return send(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {"audit_transport_mode": "UNPOOLED_TRANSPORT"}

    if mode == "expired_signal":
        from datetime import timedelta

        prepare = validation.prepare

        def expired(*args, **kwargs):
            signal, gate, cap = prepare(*args, **kwargs)
            return (
                signal.model_copy(update={"expires_at": store.now() - timedelta(milliseconds=1)}),
                gate,
                cap,
            )

        monkeypatch.setattr(validation, "prepare", expired)
    route = TPReadRouting(send, pool=Pool())
    c.client.send = route
    try:
        report = exercise_opposite(opposite_case, monkeypatch)
    finally:
        route.close()
        c.client.send = original
    assert report["tp_trigger_check"] == "NOT_ACCEPTED", report
    assert report["entry_post_attempts"] == report["new_entry_post_count"] == 0
    assert not posts(fake) and not any(fake.positions.values())
    assert store.read("DEMO-test").paused
