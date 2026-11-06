"""Actual PG proves the historical-order reader cannot modify a financial row."""
# ruff: noqa: F811

import importlib.util
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.integration.test_demo import (
    signal,
    snapshot,
    system,  # noqa: F401
)
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_paused_period import financial_rows
from tests.integration.test_demo_preview import case  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "tp_timing_reader", Path(__file__).with_name("inspect_demo_tp_timing.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def closed(case):
    store, c, fake, _, _ = case
    sig = signal(environment="DEMO", purpose="DEMO_VALIDATION", direction="LONG", strategy=None)
    c.client.settings = c.client.settings.model_copy(
        update={"margin_mode": "ISOLATED", "symbols": ("BTCUSDT",), "demo_trading_enabled": True}
    )
    c.reserve(sig, snapshot(), quantity_cap=Decimal("0.05"))
    c.submit(str(sig.signal_id))
    initial = int(fake.at.timestamp() * 1000)
    fake.at += timedelta(seconds=1)
    c.cancel(str(sig.signal_id))
    ledger = store.read("DEMO-test")
    order = ledger.orders[str(sig.signal_id)]
    fake.orders[order.client_id].update(time=initial, updateTime=initial + 1000)
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    return store, c, fake, sig


def test_signed_single_get_and_readonly_transactions_preserve_all_rows(closed, monkeypatch):
    store, c, fake, sig = closed
    before, count = financial_rows(store), len(fake.calls)
    original = module.read_account

    def read(conn, account):
        assert conn.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
        return original(conn, account)

    monkeypatch.setattr(module, "read_account", read)
    report = module.inspect_order(store.engine, "DEMO-test", sig.signal_id, c.client)
    assert report["remote_timing_status"] == "SIGNED_CANCELED_ZERO_FILL_ORDER_VERIFIED"
    assert report["remote_created_to_last_update_ms"] == 1000
    assert report["account_owner_fence_lease_state_unchanged"] and not report["new_order_attempted"]
    assert (
        report["audit_timestamps_mean"]
        == "LOCAL_AFTER_ROW_LOCK_BEFORE_APPLY_NOT_EXCHANGE_OR_COMMIT_TIMES"
    )
    assert financial_rows(store) == before
    calls = fake.calls[count:]
    assert len(calls) == 1 and calls[0][:2] == ("GET", "/fapi/v1/order")


def test_no_cached_client_keeps_local_evidence_and_never_queries_network(closed):
    store, _, fake, sig = closed
    before, count = financial_rows(store), len(fake.calls)
    report = module.inspect_order(store.engine, "DEMO-test", sig.signal_id, None)
    assert report["remote_timing_status"] == "NOT_CHECKED_CACHED_KEY_UNAVAILABLE"
    assert report["audit_timeline"]
    assert financial_rows(store) == before and len(fake.calls) == count


@pytest.mark.parametrize("fault", ["identity", "missing_time", "reversed_time", "query_error"])
def test_unverified_remote_evidence_cannot_be_reported_as_success(closed, monkeypatch, fault):
    store, c, _fake, sig = closed
    before = financial_rows(store)
    original = c.client.request

    def changed(*a, **kw):
        if fault == "query_error":
            raise ValueError("PRIVATE_KEY_URL_OR_RESPONSE")
        raw = original(*a, **kw)
        if fault == "identity":
            raw = {**raw, "clientOrderId": "OTHER"}
        if fault == "missing_time":
            raw = {k: v for k, v in raw.items() if k != "time"}
        if fault == "reversed_time":
            raw = {**raw, "updateTime": raw["time"] - 1}
        return raw

    monkeypatch.setattr(c.client, "request", changed)
    report = module.inspect_order(store.engine, "DEMO-test", sig.signal_id, c.client)
    assert report["remote_timing_status"] == "REMOTE_QUERY_OR_IDENTITY_OR_TIMESTAMPS_NOT_VERIFIED"
    assert "PRIVATE_KEY_URL_OR_RESPONSE" not in json.dumps(report)
    assert "remote_order_created_utc" not in report and financial_rows(store) == before


def test_write_enabled_client_is_never_used_for_remote_query(closed, monkeypatch):
    store, c, fake, sig = closed
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    count = len(fake.calls)
    report = module.inspect_order(store.engine, "DEMO-test", sig.signal_id, c.client)
    assert report["remote_timing_status"] == "REMOTE_QUERY_OR_IDENTITY_OR_TIMESTAMPS_NOT_VERIFIED"
    assert len(fake.calls) == count
