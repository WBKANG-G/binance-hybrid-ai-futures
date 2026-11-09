"""DB-backed guard tests; pre uses byte-pinned approved parser, post uses installed parser."""

# ruff: noqa: F811
import importlib.util
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from integrations.binance_usdm import demo_snapshot
from integrations.binance_usdm.demo import DemoRateLimited
from integrations.binance_usdm.demo_read_routing import TPReadRouting
from packages.persistence.demo import accounts, events
from services.execution.demo_empty_db_restart import DBRestartBlocked
from tests.fixtures import approved_snapshot_v1
from tests.fixtures.closed_history_probe import setup  # noqa: F401
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_tp_rate_install_guard import closed  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "account_config_guard", Path("guard_account_config_install.py")
)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def bind(setup, monkeypatch, phase):
    store, old, _, opts, fake, _ = setup
    monkeypatch.setattr(g, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(
        g,
        "operational_snapshot",
        approved_snapshot_v1.operational_snapshot
        if phase == "pre"
        else demo_snapshot.operational_snapshot,
    )

    class Pool:
        def __call__(self, *args):
            return old.send.route(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    route = TPReadRouting(old.send.route, pool=Pool())
    monitor = g.Monitor(route)
    c = g.ReadClient(old.settings, old.credentials, send=monitor, clock_ms=old.clock_ms)
    monitor.server_ms = lambda: c.clock_ms() + c.offset
    return store, c, monitor, opts, fake


def rows(store):
    with store.engine.connect() as conn:
        return conn.execute(select(accounts)).mappings().all(), conn.execute(
            select(events)
        ).mappings().all()


def pre(setup, monkeypatch):
    store, c, m, _, _ = bind(setup, monkeypatch, "pre")
    return g.compare(store, c, m, "pre")


def test_pre_recheck_post_bind_same_state_and_original_history(setup, monkeypatch):
    store, _, _, _, fake = bind(setup, monkeypatch, "pre")
    before = rows(store)
    r = pre(setup, monkeypatch)
    assert r["pair_signed_reads"] == 28 and r["old_mode_reads"] == 4
    store, c, m, _, _ = bind(setup, monkeypatch, "pre")
    again = g.compare(
        store, c, m, "pre", r["account_digest"], r["exchange_digest"], r["pair_signed_reads"]
    )
    assert again["account_digest"] == r["account_digest"]
    store, c, m, _, _ = bind(setup, monkeypatch, "post")
    report = g.compare(
        store, c, m, "post", r["account_digest"], r["exchange_digest"], r["pair_signed_reads"]
    )
    assert (
        report["pair_signed_reads"] == 26
        and report["account_config_reads"] == 2
        and report["old_mode_reads"] == 0
    )
    assert (
        report["financial_history_and_transfer_fields_preserved"]
        and report["installation_postcheck_passed"]
    )
    assert (
        not report["trading_readiness_checked"]
        and report["paused"]
        and not report["database_changed"]
    )
    assert rows(store) == before
    assert all(method == "GET" for method, path, params in fake.calls)
    assert any(p.get("origClientOrderId") == "DE-unadopted" for _, _, p in fake.calls)


@pytest.mark.parametrize("phase", ["pre", "post"])
@pytest.mark.parametrize("kind", ["account", "remote", "reads"])
def test_binding_mismatch_blocks_without_own_writes(setup, monkeypatch, phase, kind):
    r = pre(setup, monkeypatch)
    store, c, m, _, _ = bind(setup, monkeypatch, phase)
    before = rows(store)
    args = [r["account_digest"], r["exchange_digest"], r["pair_signed_reads"]]
    args[{"account": 0, "remote": 1, "reads": 2}[kind]] = "0" * 64 if kind != "reads" else 30
    with pytest.raises(g.Blocked):
        g.compare(store, c, m, phase, *args)
    assert rows(store) == before
    if kind == "account":
        assert not m.records


def test_changed_account_between_pre_and_post_blocks_before_exchange(setup, monkeypatch):
    r = pre(setup, monkeypatch)
    store, c, m, _, _ = bind(setup, monkeypatch, "post")
    with store.engine.begin() as conn:
        conn.execute(
            accounts.update()
            .where(accounts.c.account_id == "DEMO-test")
            .values(fence=accounts.c.fence + 1)
        )
    with pytest.raises(g.Blocked, match="PRE_POST_ACCOUNT_CHANGED"):
        g.compare(
            store, c, m, "post", r["account_digest"], r["exchange_digest"], r["pair_signed_reads"]
        )
    assert not m.records


@pytest.mark.parametrize(
    "fault",
    [
        "position",
        "open_order",
        "open_algo",
        "owner",
        "unpaused",
        "risk",
        "receipt",
        "marker",
        "transfer",
        "leverage",
        "dual",
        "multi",
        "header",
        "headroom",
        "rate",
    ],
)
def test_faults_block_and_leave_db_unchanged(setup, monkeypatch, fault):
    store, c, m, opts, fake = bind(setup, monkeypatch, "pre")
    if fault == "position":
        fake.positions["BTCUSDT"] = Decimal("0.0007")
    if fault == "open_order":
        fake.orders["DE-test"]["status"] = "NEW"
    if fault == "open_algo":
        fake.algos["DS-test"]["algoStatus"] = "NEW"
    if fault == "dual":
        fake.dual = True
    if fault == "multi":
        fake.multi = True
    if fault == "header":
        opts["header"] = False
    if fault == "headroom":
        opts["weight"] = 3550
    if fault == "rate":
        opts["rate_at"] = "/fapi/v3/account"
    if fault == "transfer":
        fake.incomes = [{"incomeType": "TRANSFER", "tranId": "new", "asset": "USDT", "income": "1"}]
    if fault == "leverage":
        fake.leverage = "3"
    if fault in {"owner", "unpaused", "risk", "receipt", "marker"}:
        lease = store.acquire("DEMO-test", uuid4())
        if fault != "owner":

            def mutate(s):
                if fault == "unpaused":
                    s.paused = False
                if fault == "risk":
                    s.risk_state = "EMERGENCY"
                if fault == "receipt":
                    s.operations[g.RECEIPT] = "{}"
                if fault == "marker":
                    s.operations.pop(str(g.uuid5(g.UUID(g.INCIDENT), "tp-trigger-observation")))

            store.change(lease, "TEST_FAULT", mutate)
            store.release(lease)
    before = rows(store)
    with pytest.raises((g.Blocked, g.DemoFailure, DBRestartBlocked)):
        g.compare(store, c, m, "pre")
    assert rows(store) == before
    assert all(r["path"] != "/fapi/v1/listenKey" for r in m.records)


def test_day_and_week_mismatch_only_reported_never_repaired_or_approved(setup, monkeypatch):
    store, c, m, _, fake = bind(setup, monkeypatch, "pre")
    fake.at += timedelta(days=8)
    before = rows(store)
    r = g.compare(store, c, m, "pre")
    assert not r["period_keys_match"] and not r["trading_readiness_checked"]
    assert not r["period_baselines_changed"] and r["paused"]
    assert rows(store) == before


def test_state_change_during_check_is_detected(setup, monkeypatch):
    store, c, m, _, _ = bind(setup, monkeypatch, "pre")
    original = m.route
    changed = []

    def route(method, path, q, key):
        result = original(method, path, q, key)
        if path == "/fapi/v3/account" and not changed:
            changed.append(True)
            with store.engine.begin() as conn:
                conn.execute(
                    accounts.update()
                    .where(accounts.c.account_id == "DEMO-test")
                    .values(fence=accounts.c.fence + 1)
                )
        return result

    m.route = route
    with pytest.raises(g.Blocked, match="LOCAL_STATE_CHANGED"):
        g.compare(store, c, m, "pre")


def test_route_mixing_is_not_accepted(setup, monkeypatch):
    store, c, m, _, _ = bind(setup, monkeypatch, "pre")
    monkeypatch.setattr(g, "operational_snapshot", demo_snapshot.operational_snapshot)
    with pytest.raises(g.Blocked, match="INSTALLED_MODE_ROUTE_UNVERIFIED"):
        g.compare(store, c, m, "pre")


def test_source_versions_strict_and_only_two_files_change():
    assert len(g.OLD_PINS) == 45
    assert [p for p in g.OLD_PINS if g.OLD_PINS[p] != g.NEW_PINS[p]] == [
        "integrations/binance_usdm/demo.py",
        "integrations/binance_usdm/demo_snapshot.py",
    ]
    g.verify_sources("pre", dict(g.OLD_PINS))
    g.verify_sources("post")
    for phase, pins in [("pre", g.NEW_PINS), ("post", g.OLD_PINS)]:
        with pytest.raises(g.Blocked):
            g.verify_sources(phase, dict(pins))
    mixed = dict(g.NEW_PINS)
    mixed["integrations/binance_usdm/demo.py"] = g.OLD_PINS["integrations/binance_usdm/demo.py"]
    for phase in ["pre", "post"]:
        with pytest.raises(g.Blocked):
            g.verify_sources(phase, mixed)


@pytest.mark.parametrize(
    "phase,args",
    [
        ("post", []),
        ("post", ["--expected-account", "0" * 64]),
        (
            "post",
            ["--expected-account", "0" * 64, "--expected-remote", "1" * 64, "--pre-reads", "27"],
        ),
        ("pre", ["--expected-account", "0" * 64]),
    ],
)
def test_missing_or_invalid_binding_stops_before_resources(monkeypatch, capsys, phase, args):
    monkeypatch.setattr("sys.argv", ["guard", "--phase", phase] + args)
    monkeypatch.setattr(g, "create_engine", lambda *a, **k: pytest.fail("no DB"))
    monkeypatch.setattr(g, "credentials", lambda **k: pytest.fail("no keys"))
    with pytest.raises(SystemExit) as e:
        g.main()
    assert (
        e.value.code == 2
        and json.loads(capsys.readouterr().out)["stop_reason"] == "PRECHECK_BINDING_REQUIRED"
    )


def test_pre_wrong_source_stops_before_resources(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["guard", "--phase", "pre"])
    monkeypatch.setattr(g, "create_engine", lambda *a, **k: pytest.fail("no DB"))
    monkeypatch.setattr(g, "credentials", lambda **k: pytest.fail("no keys"))
    with pytest.raises(SystemExit):
        g.main()
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "INSTALLED_SOURCE_HASH_MISMATCH"


def test_raw_errors_are_redacted():
    for e in [ValueError("private-key"), g.Blocked("unknown-private"), RuntimeError("signed-url")]:
        assert g.safe_reason(e) == "CHECK_UNAVAILABLE"
    assert g.safe_reason(DemoRateLimited("private-body")) == "RATE_LIMITED_NO_RETRY"


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/fapi/v1/accountConfig"),
        ("DELETE", "/fapi/v1/order"),
        ("PUT", "/fapi/v1/listenKey"),
        ("GET", "/fapi/v1/userTrades"),
    ],
)
def test_guard_client_rejects_nonreview_routes(setup, monkeypatch, method, path):
    store, c, m, _, _ = bind(setup, monkeypatch, "post")
    c.bind(g.local(store)[-1])
    with pytest.raises(g.Blocked):
        c.request(method, path)
    assert not m.records


def test_post_new_route_rate_limit_no_retry(setup, monkeypatch):
    r = pre(setup, monkeypatch)
    store, c, m, opts, _ = bind(setup, monkeypatch, "post")
    opts["rate_at"] = "/fapi/v1/accountConfig"
    with pytest.raises(DemoRateLimited):
        g.compare(
            store, c, m, "post", r["account_digest"], r["exchange_digest"], r["pair_signed_reads"]
        )
    assert sum(x["path"] == "/fapi/v1/accountConfig" for x in m.records) == 1
    assert c.cooldown_until > c.clock_ms()


@pytest.mark.parametrize(
    "fault", [None, "expired_key", "route_cleanup", "engine_cleanup", "unexpected"]
)
def test_main_post_success_and_resource_failures(setup, monkeypatch, capsys, fault):
    r = pre(setup, monkeypatch)
    store, c, m, _, _ = bind(setup, monkeypatch, "post")
    before = rows(store)
    cleaned = []

    class Engine:
        def dispose(self):
            cleaned.append("engine")
            if fault == "engine_cleanup":
                raise RuntimeError("private-dsn")

    class Route:
        def __call__(self, *args):
            if fault == "unexpected":
                raise RuntimeError("private-signed-url")
            return c.send.route(*args)

        def close(self):
            cleaned.append("route")
            if fault == "route_cleanup":
                raise RuntimeError("private-key")

    original_client = g.ReadClient

    def client(cfg, key, send):
        return original_client(cfg, key, send=send, clock_ms=c.clock_ms)

    monkeypatch.setattr(g, "create_engine", lambda *a, **k: Engine())
    monkeypatch.setattr(g, "DemoStore", lambda *a: store)
    monkeypatch.setattr(g, "TPReadRouting", lambda *a: Route())
    monkeypatch.setattr(g, "ReadClient", client)

    def creds(**kwargs):
        assert kwargs == {"prompt": False}
        if fault == "expired_key":
            raise ValueError("private-key")
        return c.credentials

    monkeypatch.setattr(g, "credentials", creds)
    # Fixture policy is a synthetic isolated policy; keep exact equality checks active.
    monkeypatch.setattr(g.yaml, "load", lambda *a, **k: c.settings.model_dump(mode="json"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "guard",
            "--phase",
            "post",
            "--expected-account",
            r["account_digest"],
            "--expected-remote",
            r["exchange_digest"],
            "--pre-reads",
            str(r["pair_signed_reads"]),
        ],
    )
    with pytest.raises(SystemExit) as e:
        g.main()
    report = json.loads(capsys.readouterr().out)
    assert sorted(cleaned) == ["engine", "route"] and rows(store) == before
    assert "private-" not in json.dumps(report)
    assert e.value.code == (0 if fault is None else 2)
    if fault in {"route_cleanup", "engine_cleanup"}:
        assert report["stop_reason"] == "RESOURCE_CLEANUP_UNVERIFIED"
    if fault == "expired_key":
        assert report["stop_reason"] == "DEMO_CREDENTIAL_SESSION_UNAVAILABLE"
    if fault is None:
        assert report["installation_postcheck_passed"] and report["paused"]


@pytest.mark.parametrize("kind", ["time", "http", "clock", "weight", "limited"])
def test_guard_monitor_caps(kind):
    m = g.Monitor(lambda *a: pytest.fail("no request"), clock=lambda: 0)
    m.last_had_weight = True
    m.latest_weight = 1
    m.enable(6000)
    if kind == "time":
        m.clock = lambda: 75
    if kind == "http":
        m.records = [{}] * 64
    if kind == "clock":
        m.clock_attempts = 6
        with pytest.raises(g.ProbeStop):
            m("GET", "/fapi/v1/time", SecretStr(""), None)
        return
    if kind == "weight":
        m.latest_weight = 3500
    if kind == "limited":
        m.limited = True
    with pytest.raises(g.ProbeStop):
        m.check()
