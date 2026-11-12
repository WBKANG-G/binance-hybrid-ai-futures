"""Configured PG/SQLite review with synthetic optional evidence and immutable state."""

# ruff: noqa: F811
import importlib.util
import json
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "storage_fixture", BASE / "runtime-storage-setup-20261111/test_runtime_storage_setup.py"
)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
setup = previous.setup
closed = previous.closed
demo_system = previous.demo_system
applied = previous.applied
spec = importlib.util.spec_from_file_location(
    "runtime_review", Path(__file__).with_name("review_runtime_readiness.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def configured(applied, monkeypatch):
    previous.prepare(applied)
    applied[4](70.002)
    previous.finalize(applied)
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    return applied


def review(a, **kwargs):
    store, anchor, parent, clocks, *_ = a
    return m.review_controls(store, anchor, parent=parent, clocks=clocks, **kwargs)


def test_configured_controls_and_documented_costs_are_reported_without_writes(
    configured, monkeypatch
):
    store, _, parent, *_ = configured
    before = previous.rows(store)
    files = previous.disk(parent)
    for name in ("acquire", "change", "renew", "release", "create"):
        monkeypatch.setattr(store, name, lambda *a, **k: pytest.fail("mutation forbidden"))
    report = review(configured)
    assert report["runtime_readiness_review"] == "LOCAL_CONTROLS_VERIFIED_KEEP_PAUSED"
    assert report["installed_controls_verified"] and report["storage_configuration_verified"]
    assert report["documented_cost_contract"]["model_matches_reviewed_policy"]
    assert report["documented_cost_contract"]["documented_routes_covered"] == 21
    assert report["modeled_window_capacity"]["historical_orders"] == 1
    assert report["modeled_window_capacity"]["proof_fits_empty_window"]
    assert report["modeled_window_capacity"]["entry_fits_empty_window"]
    assert report["exchange_order_evidence"]["collection_status"] == "NOT_REQUESTED"
    assert "ACCOUNT_ORDER_USAGE_NOT_VERIFIED" in report["execution_blockers"]
    assert (
        not report["execution_approved"]
        and report["http_attempts"] == 0
        and report["order_writes"] == 0
    )
    assert previous.rows(store) == before and previous.disk(parent) == files


@pytest.mark.parametrize(
    "status",
    ["DEMO_SESSION_UNAVAILABLE", "LIMITS_ONLY_COUNTERS_UNAVAILABLE", "RATE_LIMITED_NO_RETRY"],
)
def test_unavailable_external_evidence_never_grants_credit(configured, status):
    evidence = {
        "collection_status": status,
        "http_attempts": 0 if status == "DEMO_SESSION_UNAVAILABLE" else 4,
        "usage_counts_verified": False,
    }
    report = review(configured, collect=True, collector=lambda: evidence)
    assert not report["external_account_usage_verified"] and not report["execution_approved"]
    assert "ACCOUNT_ORDER_USAGE_NOT_VERIFIED" in report["execution_blockers"]
    assert not report["leader_acquired"] and not report["session_started"]


def test_observed_order_usage_cannot_enable_execution_or_claim_ip_reservation(configured):
    report = review(
        configured,
        collect=True,
        collector=lambda: {"usage_counts_verified": True, "http_attempts": 4},
    )
    assert report["external_account_usage_verified"] and not report["execution_approved"]
    assert "ACCOUNT_ORDER_USAGE_NOT_VERIFIED" not in report["execution_blockers"]
    assert "SHARED_IP_CAPACITY_NOT_RESERVED" in report["execution_blockers"]
    assert "VENUE_REQUEST_COST_UPPER_BOUNDS_UNPROVEN" in report["execution_blockers"]
    assert "PRODUCTION_DEFAULT_EXECUTION_DISABLED" in report["execution_blockers"]
    assert "FRESH_EXCHANGE_EXPOSURE_READINESS_NOT_CHECKED" in report["execution_blockers"]


def test_concurrent_database_metadata_change_invalidates_review(configured, monkeypatch):
    store, *_ = configured
    before = previous.rows(store)
    original = m.readonly_token
    count = [0]

    def changed(engine):
        count[0] += 1
        return original(engine) if count[0] == 1 else "0" * 64

    monkeypatch.setattr(m, "readonly_token", changed)
    with pytest.raises(m.Blocked, match="REVIEW_DATABASE_CHANGED_DURING_READ"):
        review(configured)
    assert previous.rows(store) == before


def test_concurrent_disk_change_invalidates_review(configured, monkeypatch):
    original = m.readonly_files
    count = [0]

    def changed(root):
        count[0] += 1
        files = original(root)
        return files if count[0] == 1 else files | {"changed": "0" * 64}

    monkeypatch.setattr(m, "readonly_files", changed)
    with pytest.raises(m.Blocked, match="REVIEW_STORAGE_CHANGED_DURING_READ"):
        review(configured)


def test_missing_configuration_cannot_run_external_probe(applied, monkeypatch):
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    with pytest.raises(m.Blocked, match="REVIEW_STORAGE_CONFIGURATION_REQUIRED"):
        review(applied, collect=True, collector=lambda: pytest.fail("probe forbidden"))


def test_invalid_cost_model_fails_before_optional_network_collection(configured, monkeypatch):
    from integrations.binance_usdm import demo_tp_weight_model
    from packages.persistence.demo_order_quota import LocalQuotaStopped

    monkeypatch.setattr(demo_tp_weight_model, "MODELED_TP_ROUTE_COSTS", {})
    with pytest.raises(LocalQuotaStopped):
        review(configured, collect=True, collector=lambda: pytest.fail("probe forbidden"))


def test_missing_demo_session_is_detected_without_http_or_raw_secrets(monkeypatch):
    import scripts.demo_key_session as key_session

    def missing(**kwargs):
        raise ValueError("PRIVATE_SESSION_DETAIL")

    monkeypatch.setattr(key_session, "credentials", missing)
    report = m.collect_exchange_evidence()
    assert (
        report["collection_status"] == "DEMO_SESSION_UNAVAILABLE" and report["http_attempts"] == 0
    )
    assert "PRIVATE_" not in json.dumps(report)


def test_embedded_probe_tamper_is_rejected_before_imports(monkeypatch):
    monkeypatch.setattr(m, "PINNED_PROBE_SOURCE", 'raise RuntimeError("must never execute")')
    with pytest.raises(m.Blocked, match="REVIEW_PROBE_SOURCE_CHANGED"):
        m.collect_exchange_evidence()


@pytest.mark.parametrize("mode", ["body", "header", "missing", "rate429"])
def test_collector_through_synthetic_http_boundary(monkeypatch, mode):
    import time
    import urllib.request
    from email.message import Message
    from urllib.parse import urlsplit

    from pydantic import SecretStr

    import scripts.demo_key_session as key_session
    from integrations.binance_usdm.demo import DemoCredentials
    from tests.unit.test_demo_order_admission import limits, usage

    monkeypatch.setattr(
        key_session,
        "credentials",
        lambda **kwargs: DemoCredentials(
            SecretStr("SYNTHETIC_PRIVATE_KEY"), SecretStr("SYNTHETIC_PRIVATE_SECRET")
        ),
    )
    calls = []

    class Response:
        status = 200

        def __init__(self, request):
            path = urlsplit(request.full_url).path
            assert request.method == "GET"
            assert urlsplit(request.full_url).netloc == "demo-fapi.binance.com"
            calls.append(path)
            self.headers = Message()
            self.headers["x-mbx-used-weight-1m"] = str(len(calls))
            if mode == "rate429":
                self.status = 429
                self.headers["Retry-After"] = "60"
                self.body = {"code": -1003, "msg": "SYNTHETIC_PRIVATE_BODY"}
            elif path == "/fapi/v1/time":
                self.body = {"serverTime": int(time.time() * 1000)}
            elif path == "/fapi/v1/exchangeInfo":
                self.body = limits()
            else:
                assert path == "/fapi/v1/rateLimit/order"
                self.body = usage(body=mode == "body")
                if mode == "header":
                    self.headers["X-MBX-ORDER-COUNT-10S"] = "2"
                    self.headers["X-MBX-ORDER-COUNT-1M"] = "5"

        def read(self, maximum):
            return json.dumps(self.body).encode()[:maximum]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Opener:
        def open(self, request, timeout):
            assert timeout == 5
            return Response(request)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    report = m.collect_exchange_evidence()
    assert "SYNTHETIC_PRIVATE" not in json.dumps(report)
    if mode == "rate429":
        assert report["stop_reason"] == "RATE_LIMITED_NO_RETRY"
        assert len(calls) == report["http_attempts"] == 1
    else:
        assert calls == [
            "/fapi/v1/time",
            "/fapi/v1/exchangeInfo",
            "/fapi/v1/rateLimit/order",
            "/fapi/v1/rateLimit/order",
        ]
        assert report["http_attempts"] == 4
        assert report["usage_counts_verified"] == (mode != "missing")
        assert not report["execution_approved"] and not report["exposure_checked"]
        assert not report["repeat_same_limits_only_probe_recommended"]
        if mode == "missing":
            assert report["order_quota_blockers"] == ["ORDER_COUNTER_NOT_PROVIDED"]
            assert all(row["first_count"] is None for row in report["windows"])


def test_partial_probe_import_failure_is_redacted(monkeypatch):
    import hashlib

    source = 'raise ValueError("PRIVATE_IMPORT_FAILURE")'
    monkeypatch.setattr(m, "PINNED_PROBE_SOURCE", source)
    monkeypatch.setattr(m, "PINNED_PROBE_SHA256", hashlib.sha256(source.encode()).hexdigest())
    report = m.collect_exchange_evidence()
    assert report["collection_status"] == "BLOCKED_NO_RETRY"
    assert report["http_attempts"] == 0 and "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("days,week_changes", [(1, False), (7, True)])
def test_stale_period_reviews_controls_without_writes_or_network(configured, days, week_changes):
    store, anchor, parent, clocks, advance, *_ = configured
    before = previous.rows(store)
    files = previous.disk(parent)
    advance(days * 86400)
    with pytest.raises(m.Blocked, match="SETUP_PERIOD_MISMATCH"):
        m.setup_status(store, anchor, parent=parent, clocks=clocks)
    report = review(
        configured, collect=True, collector=lambda: pytest.fail("stale period HTTP forbidden")
    )
    assert (
        report["runtime_readiness_review"] == "LOCAL_CONTROLS_VERIFIED_PERIOD_MISMATCH_KEEP_PAUSED"
    )
    assert report["installed_controls_verified"] and report["financial_receipts_verified"]
    assert not report["period_keys_match"] and not report["period_baselines_changed"]
    periods = report["periods"]
    assert periods["stored_day_kst"] == "2026-11-11"
    assert (periods["stored_week_kst"] != periods["current_week_kst"]) == week_changes
    assert "PERIOD_KEYS_MISMATCH" in report["execution_blockers"]
    assert report["exchange_order_evidence"]["collection_status"] == "NOT_COLLECTED_PERIOD_MISMATCH"
    assert report["http_attempts"] == report["order_writes"] == 0
    assert not report["trading_readiness_verified"] and not report["execution_approved"]
    assert previous.rows(store) == before and previous.disk(parent) == files


def test_midnight_during_review_invalidates_combined_report(configured):
    with pytest.raises(m.Blocked, match="REVIEW_PERIOD_CHANGED_DURING_READ"):
        review(
            configured,
            collect=True,
            collector=lambda: (
                configured[4](86400) or {"http_attempts": 0, "usage_counts_verified": False}
            ),
        )


def test_stale_period_does_not_skip_receipt_validation(configured, monkeypatch):
    configured[4](86400)
    monkeypatch.setattr(m, "day_recorded", lambda ledger: False)
    with pytest.raises(m.Blocked, match="SETUP_DAY_RECEIPT_REQUIRED"):
        review(configured, collect=True, collector=lambda: pytest.fail("HTTP forbidden"))


@pytest.mark.parametrize(
    "kind,reason,category",
    [
        ("clock", "SHARED_CLOCK_OR_BOOT_CHANGED", "CONTROL_REFUSAL"),
        ("binding", "SETUP_ANCHOR_CHANGED", "CONTROL_REFUSAL"),
        ("sql", "REVIEW_DATABASE_READ_FAILED", "DATABASE_READ"),
        ("missing", "REVIEW_REQUIRED_FILE_MISSING", "FILESYSTEM_READ"),
        ("permission", "REVIEW_FILESYSTEM_PERMISSION_DENIED", "FILESYSTEM_READ"),
        ("unknown", "REVIEW_UNEXPECTED_FAILURE", "UNEXPECTED_FAILURE"),
        ("private_code", "REVIEW_STATE_OR_FORMAT_UNAVAILABLE", "STATE_OR_FORMAT"),
    ],
)
def test_cli_failure_reports_stage_without_private_exception_text(
    monkeypatch, capsys, kind, reason, category
):
    import sys

    from sqlalchemy.exc import OperationalError

    from packages.persistence.demo_shared_weight import SharedWeightStopped

    errors = {
        "clock": SharedWeightStopped("SHARED_CLOCK_OR_BOOT_CHANGED"),
        "binding": m.Blocked("SETUP_ANCHOR_CHANGED"),
        "sql": OperationalError("PRIVATE_SQL", {}, ValueError("PRIVATE_DATABASE")),
        "missing": FileNotFoundError("PRIVATE_PATH"),
        "permission": PermissionError("PRIVATE_PATH"),
        "unknown": RuntimeError("PRIVATE_UNKNOWN"),
        "private_code": m.Blocked("PRIVATE_SESSION_SECRET"),
    }
    engines = []

    class Engine:
        def __init__(self):
            self.disposed = False

        def dispose(self):
            self.disposed = True

    def engine(*args, **kwargs):
        item = Engine()
        engines.append(item)
        return item

    def fail(*args, **kwargs):
        kwargs["progress"]("SHARED_JOURNAL_VALIDATION")
        raise errors[kind]

    monkeypatch.setattr(sys, "argv", ["review"])
    monkeypatch.setattr(m, "source_cohort_valid", lambda *a: True)
    monkeypatch.setattr(m, "verify_install_files", lambda *a: None)
    monkeypatch.setattr(m, "create_engine", engine)
    monkeypatch.setattr(m, "DemoStore", lambda e: e)
    import packages.persistence.demo_shared_checkpoint as checkpoint

    monkeypatch.setattr(checkpoint, "SharedWeightAnchorStore", lambda e: e)
    monkeypatch.setattr(m, "review_controls", fail)
    with pytest.raises(SystemExit) as exc:
        m.main()
    assert exc.value.code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["stop_reason"] == reason and report["failure_category"] == category
    assert report["stage"] == "SHARED_JOURNAL_VALIDATION"
    assert "PRIVATE" not in json.dumps(report)
    assert report["order_writes"] == 0 and not report["execution_approved"]
    assert not report["automatic_repair_attempted"]
    assert len(engines) == 2 and all(e.disposed for e in engines)


def test_real_shared_boot_refusal_preserves_files_and_reports_validation_stage(configured):
    from dataclasses import replace

    from packages.persistence.demo_shared_weight import SharedWeightStopped

    store, anchor, parent, clocks, *_ = configured
    before = previous.rows(store)
    files = previous.disk(parent)
    stages = []
    bad_clocks = replace(clocks, boot=lambda: "00000000-0000-0000-0000-000000000002")
    with pytest.raises(SharedWeightStopped) as error:
        m.review_controls(store, anchor, parent=parent, clocks=bad_clocks, progress=stages.append)
    assert m.safe_failure(error.value) == ("SHARED_CLOCK_OR_BOOT_CHANGED", "CONTROL_REFUSAL")
    assert stages[-1] == "SHARED_JOURNAL_VALIDATION"
    assert previous.rows(store) == before and previous.disk(parent) == files
