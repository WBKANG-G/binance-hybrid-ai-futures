"""Canonical installed-path account configuration; all HTTP responses synthetic."""
# ruff: noqa: F811

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from integrations.binance_usdm import demo_snapshot
from integrations.binance_usdm.demo import DemoFailure, DemoRateLimited, DemoUnknown
from integrations.binance_usdm.rest import RESTResponse
from scripts import check_demo_disconnect
from services.execution import (
    demo,
    demo_api_restart_check,
    demo_empty_db_restart,
    demo_empty_worker_recovery,
    demo_preview,
)
from tests.fixtures import approved_snapshot_v1 as approved
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_readiness_closed_history_cli import installed  # noqa: F401
from tests.unit.test_demo_transport import client
from tests.unit.test_readiness_closed_history import basis  # noqa: F401

NEW = "/fapi/v1/accountConfig"
OLD = {"/fapi/v1/positionSide/dual", "/fapi/v1/multiAssetsMargin"}


def test_all_seven_operational_bindings():
    assert (
        demo.snapshot
        is demo_preview.snapshot
        is demo_api_restart_check.operational_snapshot
        is demo_empty_db_restart.operational_snapshot
        is demo_empty_worker_recovery.operational_snapshot
        is check_demo_disconnect.operational_snapshot
        is demo_snapshot.operational_snapshot
    )
    assert demo.historical_snapshot is demo_snapshot.snapshot


def test_approved_parser_bytes():
    assert (
        hashlib.sha256(Path(approved.__file__).read_bytes()).hexdigest()
        == "02e5d789280f3ed8aa2667319248982d5cd4eedd2fb8b72e70aef8785e57ed6a"
    )


@pytest.mark.parametrize("full", [True, False])
def test_real_full_snapshot_equivalence_one_read_removed_no_financial_or_history_change(
    installed, full
):
    store, c, fake = installed
    ledger = store.read("DEMO-ready")
    before = ledger.model_dump_json()
    start = len(fake.calls)
    old = approved.snapshot(c, ledger, store.now(), include_closed_history=full)
    old_calls = fake.calls[start:]
    start = len(fake.calls)
    new = demo_snapshot.snapshot(c, ledger, store.now(), include_closed_history=full)
    new_calls = fake.calls[start:]
    assert old.model_dump() == new.model_dump()
    assert before == ledger.model_dump_json()
    assert len(old_calls) == len(new_calls) + 1
    assert sum(p == NEW for m, p, v in new_calls) == 1
    assert not any(p in OLD for m, p, v in new_calls)
    assert [(m, p, v) for m, p, v in old_calls if p not in OLD] == [
        (m, p, v) for m, p, v in new_calls if p != NEW
    ]
    assert all(m == "GET" for m, p, v in old_calls + new_calls)


@pytest.mark.parametrize(
    "response",
    [
        {},
        [],
        None,
        {"dualSidePosition": False},
        {"multiAssetsMargin": False},
        {"dualSidePosition": "false", "multiAssetsMargin": False},
        {"dualSidePosition": 0, "multiAssetsMargin": False},
        {"dualSidePosition": False, "multiAssetsMargin": "false"},
        {"dualSidePosition": False, "multiAssetsMargin": 0},
        {"dualSidePosition": True, "multiAssetsMargin": False},
        {"dualSidePosition": False, "multiAssetsMargin": True},
    ],
)
def test_invalid_or_unsupported_modes_block_before_financial_reads(installed, response):
    store, c, fake = installed
    original = c.send
    start = len(fake.calls)

    def bad(m, p, q, k):
        if p == NEW:
            fake.calls.append((m, p, {}))
            return RESTResponse(200, json.dumps(response).encode(), {})
        return original(m, p, q, k)

    c.send = bad
    with pytest.raises(DemoFailure):
        demo_snapshot.operational_snapshot(c, store.read("DEMO-ready"), store.now())
    assert [p for m, p, v in fake.calls[start:]] == [NEW]


@pytest.mark.parametrize(
    "status,body",
    [
        (404, b'{"code":-404}'),
        (500, b"private-body"),
        (429, b"not-json"),
        (418, b"not-json"),
        (200, b"not-json"),
    ],
)
def test_new_route_failure_never_falls_back_or_retries(installed, status, body):
    store, c, fake = installed
    original = c.send
    attempts = []

    def fail(m, p, q, k):
        attempts.append(p)
        if p == NEW:
            return RESTResponse(status, body, {"retry-after": "60"})
        return original(m, p, q, k)

    c.send = fail
    with pytest.raises(DemoRateLimited if status in {418, 429} else DemoUnknown):
        demo_snapshot.operational_snapshot(c, store.read("DEMO-ready"), store.now())
    assert attempts == [NEW]
    if status in {418, 429}:
        assert c.cooldown_until > c.clock_ms()


def test_each_snapshot_reads_fresh_modes(installed):
    store, c, fake = installed
    demo_snapshot.operational_snapshot(c, store.read("DEMO-ready"), store.now())
    fake.dual = True
    start = len(fake.calls)
    with pytest.raises(DemoFailure):
        demo_snapshot.operational_snapshot(c, store.read("DEMO-ready"), store.now())
    assert [p for m, p, v in fake.calls[start:]] == [NEW]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_no_configuration_mutations(method):
    c, fake = client()
    before = len(fake.calls)
    with pytest.raises(ValueError):
        c.request(method, NEW, {})
    assert len(fake.calls) == before


def test_new_get_uses_approved_signing_and_clock_checks():
    c, fake = client(False)
    assert c.request("GET", NEW) == {"dualSidePosition": False, "multiAssetsMargin": False}
    before = len(fake.calls)
    c.synced_at = None
    with pytest.raises(DemoFailure):
        c.request("GET", NEW)
    assert len(fake.calls) == before


@pytest.mark.parametrize(
    "guard_path",
    [
        "guard_readiness_history_install.py",
        "guard_tp_rate_install.py",
    ],
)
def test_old_install_guards_reject_source_before_access(guard_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("guard_for_config", Path(guard_path))
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    monkeypatch.setattr("sys.argv", ["guard", "--phase", "pre"])
    for name in ("credentials", "create_engine"):
        if hasattr(guard, name):
            monkeypatch.setattr(
                guard, name, lambda *a, **k: pytest.fail("no credentials or DB access")
            )
    with pytest.raises(SystemExit) as e:
        guard.main()
    assert e.value.code == 2
    output = json.loads(capsys.readouterr().out)
    assert "HASH" in json.dumps(output) or "SOURCE" in json.dumps(output)
