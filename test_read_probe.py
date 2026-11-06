import contextlib

import pytest

from test_tp_timing import case, closed, demo_system, module, system  # noqa: F401


@pytest.fixture
def pooled(monkeypatch):
    counts = []

    @contextlib.contextmanager
    def connection(client):
        original = client.send

        def send(method, path, query, key):
            assert method == "GET"
            counts.append(path)
            return original(method, path, query, key)

        class Pool:
            def diagnostics(self):
                return {"audit_transport_mode": "POOLED_HTTPS", "audit_http_attempts": len(counts)}

        client.send = send
        try:
            yield Pool()
        finally:
            client.send = original

    monkeypatch.setattr(module, "recovery_connection", connection)
    return counts


def test_four_verified_reads_two_clock_calls_and_source_restored(closed, pooled):  # noqa: F811
    from tests.integration.test_demo_paused_period import financial_rows

    store, c, fake, sig = closed
    before, count, original = financial_rows(store), len(fake.calls), c.client.send
    report = module.compare_reads(store.engine, "DEMO-test", sig.signal_id, c.client)
    assert report["signed_order_queries"] == 4 and report["clock_sync_calls"] == 2
    assert len(report["original_sdk_request_ms"]) == len(report["pooled_get_request_ms"]) == 2
    assert len(fake.calls) - count == 6 and len(pooled) == 3
    assert all(method == "GET" for method, _, _ in fake.calls[count:])
    assert financial_rows(store) == before and c.client.send is original
    assert not report["fill_cause_proven"] and not report["execution_transport_changed"]


def test_bad_first_response_aborts_without_retry_or_pool(closed, pooled, monkeypatch):  # noqa: F811
    store, c, fake, sig = closed
    original = c.client.request

    def wrong(*args, **kwargs):
        raw = original(*args, **kwargs)
        return {**raw, "clientOrderId": "FOREIGN"}

    monkeypatch.setattr(c.client, "request", wrong)
    count = len(fake.calls)
    with pytest.raises(ValueError):
        module.compare_reads(store.engine, "DEMO-test", sig.signal_id, c.client)
    assert len(fake.calls) - count == 2 and pooled == []


def test_pooled_failure_still_restores_original_transport(closed, pooled, monkeypatch):  # noqa: F811
    store, c, _, sig = closed
    original_send, original_request = c.client.send, c.client.request

    def fail_in_pool(*args, **kwargs):
        if c.client.send is not original_send:
            raise ValueError("PRIVATE_RESPONSE_EXAMPLE")
        return original_request(*args, **kwargs)

    monkeypatch.setattr(c.client, "request", fail_in_pool)
    with pytest.raises(ValueError, match="SIGNED_ORDER_OR_TIME_NOT_VERIFIED"):
        module.compare_reads(store.engine, "DEMO-test", sig.signal_id, c.client)
    assert c.client.send is original_send


def test_write_enabled_source_blocks_before_network(closed, pooled):  # noqa: F811
    store, c, fake, sig = closed
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    count = len(fake.calls)
    with pytest.raises(ValueError, match="READ_ONLY_CLIENT_REQUIRED"):
        module.compare_reads(store.engine, "DEMO-test", sig.signal_id, c.client)
    assert len(fake.calls) == count and pooled == []


def test_deadline_stops_before_first_signed_query(closed, pooled):  # noqa: F811
    store, c, fake, sig = closed
    ticks = iter([0, 31])
    count = len(fake.calls)
    with pytest.raises(ValueError, match="TIME_BUDGET"):
        module.compare_reads(
            store.engine, "DEMO-test", sig.signal_id, c.client, clock=lambda: next(ticks)
        )
    assert len(fake.calls) - count == 1 and pooled == []


def test_custom_transport_cannot_be_mislabeled_as_real_pool(closed):  # noqa: F811
    store, c, _, sig = closed
    original = c.client.send
    with pytest.raises(ValueError, match="STANDARD_TRANSPORT_REQUIRED"):
        module.compare_reads(store.engine, "DEMO-test", sig.signal_id, c.client)
    assert c.client.send is original


def test_account_change_between_samples_is_detected(closed, pooled, monkeypatch):  # noqa: F811
    from uuid import uuid4

    store, c, _, sig = closed
    original = module.inspect_order
    changed = []

    def inspect(*args, **kwargs):
        report = original(*args, **kwargs)
        if not changed:
            store.release(c.lease)
            lease = store.acquire("DEMO-test", uuid4())
            store.release(lease)
            changed.append(True)
        return report

    monkeypatch.setattr(module, "inspect_order", inspect)
    with pytest.raises(ValueError, match="SOURCE_OR_CLOSED_ORDER_CHANGED"):
        module.compare_reads(store.engine, "DEMO-test", sig.signal_id, c.client)


def test_cli_requires_explicit_probe_flag_before_credentials(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "argv", ["probe"])
    monkeypatch.setattr(module, "credentials", lambda **_: pytest.fail("must not read credentials"))
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2


def test_cli_private_failure_is_redacted_and_returns_nonzero(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["probe", "--compare-read-transports"])

    def fail(**_):
        raise ValueError("PRIVATE_KEY_EXAMPLE")

    monkeypatch.setattr(module, "credentials", fail)
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    out = capsys.readouterr().out
    assert "PRIVATE_KEY_EXAMPLE" not in out and "BLOCKED_NO_CHANGE_NO_AUTOMATIC_RETRY" in out
