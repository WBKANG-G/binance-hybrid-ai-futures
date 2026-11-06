# ruff: noqa: F811
import importlib.util
import json
from pathlib import Path

import pytest
from integrations.binance_usdm.demo import DemoFailure
from tests.integration.test_demo import enter
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_paused_period import financial_rows
from tests.integration.test_demo_recovery import recovery  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "detail_reader", Path(__file__).with_name("review_unresolved_details.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_four_unresolved_bound_to_canonical_review_no_writes_or_raw_ids(recovery):  # noqa: F811
    store, c, fake = recovery

    def extra(ledger):
        ledger.requests["DS-historical-two"] = "UNKNOWN"
        ledger.requests["DX-historical-two"] = "SUBMITTING"

    store.change(c.lease, "TEST_TWO_MORE_UNKNOWN", extra)
    before, count = financial_rows(store), len(fake.calls)
    original_client = m.recovery.RecoveryReadClient
    original_snapshot = m.recovery.snapshot
    report = m.inspect(store, c.client, "DEMO-test")
    assert report["unresolved_count"] == 4 and len(report["unresolved_requests"]) == 4
    assert report["audit_details_bound_to_canonical_review"]
    assert {r["request_kind"] for r in report["unresolved_requests"]} == {"DS", "DX"}
    assert all(r["unlinked_local_request"] for r in report["unresolved_requests"])
    assert all(
        r["absence_proves_never_sent_or_never_filled"] is False
        for r in report["unresolved_requests"]
    )
    assert "historical" not in json.dumps(report) and "review_token" not in report
    assert (
        report["unlock_applied"] is False
        and report["risk_state"] == "EMERGENCY"
        and report["paused"]
    )
    assert all(method == "GET" and "asyn" not in path for method, path, _ in fake.calls[count:])
    assert financial_rows(store) == before
    assert (
        m.recovery.RecoveryReadClient is original_client
        and m.recovery.snapshot is original_snapshot
    )


def test_token_mismatch_cannot_emit_accepted_details(recovery, monkeypatch):  # noqa: F811
    store, c, _ = recovery
    before = financial_rows(store)
    original = m.recovery.review

    def mismatch(*a, **kw):
        result = original(*a, **kw)
        result["review_token"] = "0" * 64
        return result

    monkeypatch.setattr(m.recovery, "review", mismatch)
    with pytest.raises(ValueError, match="TOKEN_BINDING_MISMATCH"):
        m.inspect(store, c.client, "DEMO-test")
    assert financial_rows(store) == before


def test_failed_transport_restores_canonical_bindings_and_does_not_write(recovery, monkeypatch):  # noqa: F811
    store, c, _ = recovery
    before = financial_rows(store)
    prior_client = m.recovery.RecoveryReadClient
    prior_snapshot = m.recovery.snapshot

    def blocked(*a, **kw):
        raise DemoFailure("PRIVATE_RESPONSE_NOT_TO_LOG")

    monkeypatch.setattr(c.client, "request", blocked)
    with pytest.raises(DemoFailure):
        m.inspect(store, c.client, "DEMO-test")
    assert m.recovery.RecoveryReadClient is prior_client and m.recovery.snapshot is prior_snapshot
    assert financial_rows(store) == before


def test_write_enabled_client_rejected_before_transport(recovery):  # noqa: F811
    store, c, fake = recovery
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    count = len(fake.calls)
    with pytest.raises(m.recovery.RecoveryBlocked):
        m.inspect(store, c.client, "DEMO-test")
    assert len(fake.calls) == count


def test_classification_links_local_order_without_claiming_remote_absence_means_no_fill(
    demo_system,
):
    store, c, fake = demo_system
    sig, order = enter(demo_system)
    ledger = store.read("DEMO-test")
    details = m.classify(ledger, {order.client_id: "NOT_FOUND_UNRESOLVED"})
    assert details[0]["request_kind"] == "DE"
    assert details[0]["linked_orders"][0]["operation_id"] == str(sig.signal_id)
    assert details[0]["linked_orders"][0]["role"] == "ENTRY"
    assert details[0]["absence_proves_never_sent_or_never_filled"] is False


def test_main_redacts_credential_failure_without_apply(monkeypatch, capsys):
    def missing(**kw):
        raise ValueError("PRIVATE_SECRET_VALUE")

    monkeypatch.setattr(m, "credentials", missing)
    with pytest.raises(SystemExit):
        m.main()
    text = capsys.readouterr().out
    assert "PRIVATE_SECRET_VALUE" not in text
    assert json.loads(text)["recovery"] == "BLOCKED_KEEP_LOCK"


def test_private_token_callback_matches_canonical_review_without_printing_token(recovery):
    store, c, _ = recovery
    tokens = []
    report = m.inspect(store, c.client, "DEMO-test", on_token=tokens.append)
    assert len(tokens) == 1 and len(tokens[0]) == 64
    assert "review_token" not in report
    assert m.recovery.review(store, c.client, store.read("DEMO-test"))["review_token"] == tokens[0]
