"""Receipt-bound day preview, isolated PG and synthetic signed GET responses."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from sqlalchemy import select

from integrations.binance_usdm.demo import DemoRateLimited
from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo import accounts, events
from services.execution.demo_models import AttestedDayBaseline
from services.risk.paper import AccountValue
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_tp_rate_install_guard import closed  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "manual_close_period_preview", Path(__file__).with_name("adopt_manual_close_periods.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def rows(store):
    with store.engine.connect() as c:
        return c.execute(select(accounts)).mappings().all(), c.execute(
            select(events)
        ).mappings().all()


@pytest.fixture
def setup(closed, monkeypatch):  # noqa: F811
    store, old, fake = closed
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    anchor = datetime.fromtimestamp(m.CLOSE_MS / 1000, UTC) + timedelta(minutes=10)
    fake.at = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)
    lease = store.acquire("DEMO-test", uuid4())

    def populate(s):
        o = s.orders[m.INCIDENT]
        o.signal = o.signal.model_copy(
            update={
                "suggested_limit_price": Decimal("75725.10"),
                "initial_stop_price": Decimal("75344.40"),
                "target_price": Decimal("76482.40"),
            }
        )
        s.value = AccountValue(as_of=anchor, initial=fake.wallet)
        s.last_reconciled = anchor - timedelta(minutes=1)  # Old anchor must not be re-labelled.
        s.daily_key = "2026-11-07"
        s.weekly_key = "2026-11-02"
        s.daily_base = fake.wallet
        s.weekly_base = fake.wallet + 2
        s.high_water = fake.wallet + 5
        r = json.loads(s.operations[m.RECEIPT])
        r.update(
            {
                "manual_close_client_id": "web_synthetic_close",
                "original_order": o.model_dump(mode="json"),
                "prior_ledger_sha256": "a" * 64,
                "exchange_fingerprint": "b" * 64,
                "acknowledged_at_utc": (anchor + timedelta(seconds=1)).isoformat(),
            }
        )
        s.operations[m.RECEIPT] = json.dumps(r)
        values = {
            "old_day_kst": "2026-11-06",
            "proposed_day_kst": "2026-11-07",
            "current_week_kst": "2026-11-02",
            "old_daily_base_usdt": str(fake.wallet + 1),
            "proposed_daily_base_usdt": str(fake.wallet),
            "weekly_base_usdt_preserved": str(s.weekly_base),
            "high_water_usdt_preserved": str(s.high_water),
            "adoption_snapshot_anchor_utc": anchor.isoformat(),
        }
        prior = AttestedDayBaseline(
            review_token=m.digest(values),
            source="OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE",
            boundary_utc=datetime(2026, 11, 6, 15, tzinfo=UTC),
            baseline_usdt=fake.wallet,
            old_daily_key="2026-11-06",
            old_daily_base=fake.wallet + 1,
            anchor_utc=anchor,
            known_exit_utc=datetime.fromtimestamp(m.CLOSE_MS / 1000, UTC),
            attested_until_utc=datetime(2026, 11, 7, 0, tzinfo=UTC),
            signed_evidence_hash="c" * 64,
            exchange_fingerprint="d" * 64,
        )
        s.attested_day_baselines[m.PRIOR_DAY_KEY] = prior
        s.operations[m.PRIOR_DAY_KEY] = json.dumps(
            {
                "operation": m.PRIOR_DAY_OPERATION,
                "reviewed_values": values,
                "evidence_source": "OPERATOR_ATTESTED_SIGNED_EXTERNAL_CLOSE_FLAT_ANCHOR",
                "preserved_ledger_sha256": m.prior_preserved(s),
                "typed_receipt_sha256": m.digest(prior.model_dump(mode="json")),
                "exact_midnight_valuation_verified": False,
                "account_wide_exchange_export_verified": False,
            }
        )

    store.change(lease, "SYNTHETIC_ADOPTED_MANUAL_CLOSE_ANCHOR", populate)
    store.release(lease)
    initial = store.read("DEMO-test")
    moment = [0.0]
    calls = []
    opt = {
        "weight": 100,
        "limit": 6000,
        "header": True,
        "late_income": False,
        "extra_trade": False,
        "wrong_fee": False,
        "wrong_close": False,
        "rate_at": None,
        "count": 0,
        "account_calls": 0,
        "wallet_change": False,
        "change_local": False,
        "day_change": False,
        "income_calls": 0,
        "response_fault": None,
    }
    sender = old.send

    def advance(sec):
        moment[0] += sec
        fake.at += timedelta(seconds=sec)

    def send(method, path, query, key):
        p = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        calls.append(
            (
                method,
                path,
                {k: v for k, v in p.items() if k not in {"signature", "timestamp", "recvWindow"}},
            )
        )
        opt["count"] += 1
        advance(0.01)
        opt["weight"] += {
            "/fapi/v1/accountConfig": 5,
            "/fapi/v1/positionSide/dual": 30,
            "/fapi/v1/multiAssetsMargin": 30,
            "/fapi/v1/openOrders": 40,
            "/fapi/v1/openAlgoOrders": 40,
            "/fapi/v3/account": 5,
            "/fapi/v1/symbolConfig": 5,
            "/fapi/v3/positionRisk": 5,
            "/fapi/v1/userTrades": 5,
            "/fapi/v1/income": 30,
        }.get(path, 1)
        h = {"X-MBX-USED-WEIGHT-1M": str(opt["weight"])} if opt["header"] else {}
        if opt["rate_at"] == path:
            return RESTResponse(429, b"not-json", h | {"Retry-After": "120"})
        if path == "/fapi/v1/order":
            close = p["orderId"] == m.CLOSE
            r = {
                "orderId": m.CLOSE if close else m.ENTRY,
                "clientOrderId": "web_synthetic_close"
                if close
                else initial.orders[m.INCIDENT].client_id,
                "symbol": "BTCUSDT",
                "side": "SELL" if close else "BUY",
                "positionSide": "BOTH",
                "type": "MARKET" if close else "LIMIT",
                "status": "FILLED",
                "reduceOnly": close,
                "origQty": "0.0007",
                "executedQty": "0.0007",
                "avgPrice": "75685.20" if close else "75725.10",
                "price": "0" if close else "75725.10",
                "time": m.CLOSE_MS if close else 1793947690123,
                "updateTime": m.CLOSE_MS if close else 1793947690123,
            }
            if opt["wrong_close"] and close:
                r["reduceOnly"] = False
            response = fake.response(r)
        elif path == "/fapi/v1/userTrades":
            data = []
            for identity, (order, side, price, pnl, fee, stamp) in m.EXPECTED_TRADES.items():
                data.append(
                    {
                        "id": identity,
                        "orderId": order,
                        "symbol": "BTCUSDT",
                        "side": side,
                        "positionSide": "BOTH",
                        "qty": "0.0007",
                        "price": price,
                        "realizedPnl": pnl,
                        "commission": fee,
                        "commissionAsset": "USDT",
                        "time": stamp,
                    }
                )
            if opt["wrong_fee"]:
                data[-1]["commission"] = "0.02111985"
            if opt["extra_trade"]:
                data.append(dict(data[-1], id="999", time=m.CLOSE_MS + 10000))
            response = fake.response(data)
        elif path == "/fapi/v1/income":
            opt["income_calls"] += 1
            if opt["change_local"] and opt["income_calls"] == 2:
                with store.engine.begin() as c:
                    c.execute(
                        accounts.update()
                        .where(accounts.c.account_id == "DEMO-test")
                        .values(fence=accounts.c.fence + 1)
                    )
            if opt["day_change"] and opt["income_calls"] == 2:
                fake.at += timedelta(days=1)
            response = fake.response(
                [{"incomeType": "COMMISSION", "income": "-0.01"}] if opt["late_income"] else []
            )
        else:
            response = sender(method, path, query, key)
            if path == "/fapi/v3/account":
                opt["account_calls"] += 1
                wallet = fake.wallet + (
                    1 if opt["wallet_change"] and opt["account_calls"] == 2 else 0
                )
                response = fake.response(
                    {
                        "totalWalletBalance": str(wallet),
                        "totalMarginBalance": str(wallet),
                        "totalUnrealizedProfit": "0",
                        "totalInitialMargin": "0",
                        "availableBalance": str(wallet),
                        "assets": [
                            {
                                "asset": "USDT",
                                "walletBalance": str(wallet),
                                "marginBalance": str(wallet),
                                "unrealizedProfit": "0",
                            }
                        ],
                    }
                )
            if path == "/fapi/v1/exchangeInfo":
                r = json.loads(response.body)
                r["rateLimits"] = [
                    {
                        "rateLimitType": "REQUEST_WEIGHT",
                        "interval": "MINUTE",
                        "intervalNum": 1,
                        "limit": opt["limit"],
                    }
                ]
                response = fake.response(r)
        fault = opt["response_fault"]
        data = json.loads(response.body)
        if fault == "full_income" and path.endswith("/income"):
            data = [{}] * 1000
        elif fault == "full_trades" and path.endswith("/userTrades"):
            data = [data[0]] * 1000
        elif fault == "open_algo" and path.endswith("/openAlgoOrders"):
            data = [{"algoId": "other"}]
        elif fault == "open_regular" and path.endswith("/openOrders"):
            data = [{"orderId": "other"}]
        elif fault == "hedge_mode" and path.endswith("/accountConfig"):
            data = {"dualSidePosition": True, "multiAssetsMargin": False}
        elif fault == "non_usdt_fee" and path.endswith("/userTrades"):
            data[-1]["commissionAsset"] = "BNB"
        elif fault == "late_second_pass" and path.endswith("/income") and opt["income_calls"] == 2:
            data = [{"incomeType": "TRANSFER", "income": "1"}]
        return RESTResponse(response.status, json.dumps(data).encode(), h)

    class Pool:
        def __call__(self, *args):
            return send(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    route = TPReadRouting(send, pool=Pool())
    monitor = m.Monitor(route, clock=lambda: moment[0])
    client = m.DayPreviewClient(old.settings, old.credentials, send=monitor, clock_ms=old.clock_ms)
    monitor.server_ms = lambda: client.clock_ms() + client.offset
    expected = dict(m.EXPECTED)
    expected.update(
        {
            "old_daily_base_usdt": str(fake.wallet),
            "proposed_daily_base_usdt": str(fake.wallet),
            "old_weekly_base_usdt": str(fake.wallet + 2),
            "proposed_weekly_base_usdt": str(fake.wallet),
            "high_water_usdt_preserved": str(fake.wallet + 5),
            "adoption_snapshot_anchor_utc": anchor.isoformat(),
        }
    )
    monkeypatch.setattr(m, "EXPECTED", expected)
    return store, client, monitor, opt, calls, fake, advance, route


def damage(store, fn):
    lease = store.acquire("DEMO-test", uuid4())
    store.change(lease, "SYNTHETIC_LOCAL_FAULT", fn)
    store.release(lease)


def run(setup, **kwargs):
    store, c, monitor, *_ = setup
    return m.adopt(
        store,
        c,
        monitor,
        attest=True,
        attested_day="2026-11-09",
        accept_operator_evidence=True,
        **kwargs,
    )


def test_apply_only_baselines_receipt_and_lease_flags_change_and_replay_is_noop(setup):
    store, c, monitor, opt, calls, *_ = setup
    before = store.read("DEMO-test")
    result = run(setup)
    after = store.read("DEMO-test")
    assert (
        result["manual_close_period_adoption"]
        == "OPERATOR_ATTESTED_DAY_WEEK_ADOPTED_ENTRIES_STILL_PAUSED"
    )
    assert result["database_changed"] and result["leader_released"]
    assert after.daily_key == after.weekly_key == "2026-11-09"
    assert after.daily_base == after.weekly_base == Decimal("10000")
    assert after.high_water == before.high_water and after.value == before.value
    assert m.preserved(before) == m.preserved(after) and m.recorded(after)
    assert after.attested_day_baselines == before.attested_day_baselines
    assert all(after.operations[k] == v for k, v in before.operations.items())
    assert after.requests == before.requests and after.orders == before.orders
    assert (
        after.paused
        and after.risk_state.value == "NORMAL"
        and not after.stream_ready
        and not after.reconciled
    )
    with store.engine.connect() as conn:
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == "DEMO-test"))
            .mappings()
            .one()
        )
        kinds = (
            conn.execute(select(events.c.kind).where(events.c.account_id == "DEMO-test"))
            .scalars()
            .all()
        )
    assert (
        row["owner"] is None and kinds.count("DEMO_EXTERNAL_CLOSE_ATTESTED_PERIOD_BASELINES") == 1
    )
    before_replay = rows(store)
    call_count = len(calls)
    result = run(setup)
    assert (
        result["manual_close_period_adoption"] == "ALREADY_RECORDED_NO_ACTION"
        and not result["database_changed"]
    )
    assert rows(store) == before_replay and len(calls) == call_count
    assert all(method == "GET" for method, path, params in calls)


@pytest.mark.parametrize(
    "field",
    [
        "old_daily_base_usdt",
        "proposed_daily_base_usdt",
        "old_weekly_base_usdt",
        "proposed_weekly_base_usdt",
        "high_water_usdt_preserved",
        "adoption_snapshot_anchor_utc",
        "old_day_kst",
        "old_week_kst",
        "proposed_week_kst",
        "day_boundary_utc",
    ],
)
def test_changed_review_values_block_before_lease_or_exchange(setup, monkeypatch, field):
    store, c, monitor, _, calls, *_ = setup
    expected = dict(m.EXPECTED)
    expected[field] = (
        "20000" if field.endswith("usdt") or field.endswith("preserved") else "changed"
    )
    monkeypatch.setattr(m, "EXPECTED", expected)
    before = rows(store)
    with pytest.raises(m.Blocked, match="REVIEWED_VALUES_CHANGED"):
        run(setup)
    assert rows(store) == before and not calls


@pytest.mark.parametrize("day", [None, "2026-11-08", "2026-11-10"])
def test_stale_or_missing_attestation_cannot_take_lease(setup, day):
    store, c, monitor, _, calls, *_ = setup
    before = rows(store)
    with pytest.raises(m.Blocked, match="CURRENT_DAY_ATTESTATION_REQUIRED"):
        m.adopt(store, c, monitor, attest=True, attested_day=day, accept_operator_evidence=True)
    assert rows(store) == before and not calls


def test_no_attestation_before_db_or_lease(setup, monkeypatch):
    store, c, monitor, *_ = setup
    monkeypatch.setattr(m, "local", lambda *a: pytest.fail("no local read"))
    with pytest.raises(m.Blocked, match="OPERATOR_ATTESTATION_REQUIRED"):
        m.adopt(store, c, monitor)


@pytest.mark.parametrize(
    "fault",
    ["late_income", "extra_trade", "wrong_fee", "wrong_close", "wallet_change", "day_change"],
)
def test_failed_fresh_proof_rolls_back_baselines_and_preserves_history(setup, fault):
    store, c, monitor, opts, calls, *_ = setup
    before = store.read("DEMO-test")
    opts[fault] = True
    with pytest.raises((m.Blocked, m.ProbeStop)):
        run(setup)
    after = store.read("DEMO-test")
    assert after.daily_key == before.daily_key and after.weekly_key == before.weekly_key
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    assert m.preserved(after) == m.preserved(before) and m.APPLY_KEY not in after.operations
    with store.engine.connect() as conn:
        assert (
            conn.execute(
                select(accounts.c.owner).where(accounts.c.account_id == "DEMO-test")
            ).scalar_one()
            is None
        )
    assert all(method == "GET" for method, path, params in calls)


@pytest.mark.parametrize(
    "path", ["/fapi/v1/accountConfig", "/fapi/v1/order", "/fapi/v1/userTrades", "/fapi/v1/income"]
)
def test_rate_limit_does_not_write_baselines_or_retry(setup, path):
    store, c, monitor, opts, calls, *_ = setup
    before = store.read("DEMO-test")
    opts["rate_at"] = path
    with pytest.raises(DemoRateLimited):
        run(setup)
    after = store.read("DEMO-test")
    assert m.preserved(after) == m.preserved(before) and m.APPLY_KEY not in after.operations
    assert calls[-1][1] == path and c.cooldown_until > c.clock_ms()


@pytest.mark.parametrize("kind", ["stale", "expired_lease"])
def test_stale_final_proof_or_expired_lease_never_commits(setup, monkeypatch, kind):
    store, c, monitor, _, _, fake, *_ = setup
    original = m.preview

    def preview(*a, **k):
        report = original(*a, **k)
        fake.at += timedelta(seconds=31 if kind == "stale" else 61)
        if kind == "expired_lease":
            report["final_state_started_at_utc"] = fake.at.isoformat()
        return report

    monkeypatch.setattr(m, "preview", preview)
    before = store.read("DEMO-test")
    from services.risk.paper import RiskRejected

    with pytest.raises(m.Blocked if kind == "stale" else RiskRejected):
        run(setup)
    after = store.read("DEMO-test")
    assert (
        m.APPLY_KEY not in after.operations
        and after.daily_key == before.daily_key
        and after.weekly_base == before.weekly_base
    )


def test_lost_commit_response_is_detected_and_replay_does_not_write_or_query(setup, monkeypatch):
    store, c, monitor, _, calls, *_ = setup
    original = store.change

    def change(lease, kind, apply):
        result = original(lease, kind, apply)
        if kind == "DEMO_EXTERNAL_CLOSE_ATTESTED_PERIOD_BASELINES":
            raise RuntimeError("private-unknown-commit")
        return result

    monkeypatch.setattr(store, "change", change)
    progress = {}
    with pytest.raises(RuntimeError):
        run(setup, progress=progress)
    assert progress["application_may_have_completed"] and m.recorded(store.read("DEMO-test"))
    monkeypatch.setattr(store, "change", original)
    before = rows(store)
    count = len(calls)
    result = run(setup)
    assert (
        result["manual_close_period_adoption"] == "ALREADY_RECORDED_NO_ACTION"
        and rows(store) == before
        and len(calls) == count
    )


@pytest.mark.parametrize(
    "kind",
    [
        "missing_receipt",
        "missing_operation",
        "wrong_operation",
        "wrong_token",
        "changed_history",
        "wrong_week_value",
    ],
)
def test_inconsistent_recorded_outcome_is_blocked_without_reapplication(setup, kind):
    store, c, monitor, _, calls, *_ = setup
    run(setup)

    def corrupt(s):
        if kind == "missing_receipt":
            s.attested_period_baselines.pop(m.APPLY_KEY)
        if kind == "missing_operation":
            s.operations.pop(m.APPLY_KEY)
        if kind == "wrong_operation":
            s.operations[m.APPLY_KEY] = "{}"
        if kind == "wrong_token":
            s.attested_period_baselines[m.APPLY_KEY].review_token = "0" * 64
        if kind == "changed_history":
            s.operations[m.RECEIPT] += " "
        if kind == "wrong_week_value":
            s.weekly_base += 1

    damage(store, corrupt)
    before = rows(store)
    count = len(calls)
    with pytest.raises(m.Blocked, match="PERIOD_OPERATION_CONFLICT"):
        run(setup)
    assert rows(store) == before and len(calls) == count


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--apply"],
        ["--attest-no-activity-since-manual-close", "--attested-day-kst", "2026-11-09"],
        ["--apply", "--attest-no-activity-since-manual-close"],
    ],
)
def test_missing_cli_consent_flags_do_not_access_resources(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["adopt", *args])
    monkeypatch.setattr(m, "create_engine", lambda *a, **k: pytest.fail("no DB"))
    monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys"))
    with pytest.raises(SystemExit) as e:
        m.main()
    assert e.value.code == 2


def test_reviewed_live_values_are_exact():
    assert (
        m.EXPECTED["old_daily_base_usdt"]
        == m.EXPECTED["proposed_daily_base_usdt"]
        == m.EXPECTED["proposed_weekly_base_usdt"]
        == "4998.74183608"
    )
    assert m.EXPECTED["old_weekly_base_usdt"] == "4998.99761493"
    assert m.EXPECTED["high_water_usdt_preserved"] == "5000.00000000"
    m.verify_sources()


def test_explicit_manual_source_acceptance_is_required_before_local_read(setup, monkeypatch):
    store, c, monitor, *_ = setup
    monkeypatch.setattr(m, "local", lambda *a: pytest.fail("no local read"))
    with pytest.raises(m.Blocked, match="MANUAL_SOURCE_ACCEPTANCE_REQUIRED"):
        m.adopt(store, c, monitor, attest=True, attested_day="2026-11-09")


def arguments():
    return [
        "adopt",
        "--apply",
        "--accept-operator-evidence",
        "--attest-no-activity-since-manual-close",
        "--attested-day-kst",
        "2026-11-09",
    ]


@pytest.mark.parametrize("fault", [None, "expired_key", "cleanup"])
def test_main_actual_apply_and_safe_failure_reporting(setup, monkeypatch, capsys, fault):
    store, c, monitor, _, calls, _, _, route = setup
    cleaned = []
    before = store.read("DEMO-test")

    class Engine:
        def dispose(self):
            cleaned.append("engine")

    original_close = route.close

    def close():
        original_close()
        cleaned.append("route")
        if fault == "cleanup":
            raise RuntimeError("private-key-error")

    monkeypatch.setattr(route, "close", close)
    original_client = m.DayPreviewClient
    monkeypatch.setattr(m, "create_engine", lambda *a, **k: Engine())
    monkeypatch.setattr(m, "DemoStore", lambda *a: store)
    monkeypatch.setattr(m, "TPReadRouting", lambda *a: route)
    monkeypatch.setattr(m, "Monitor", lambda *a: monitor)
    monkeypatch.setattr(
        m,
        "DayPreviewClient",
        lambda cfg, key, send: original_client(cfg, key, send=send, clock_ms=c.clock_ms),
    )
    monkeypatch.setattr(m.yaml, "load", lambda *a, **k: c.settings.model_dump(mode="json"))

    def creds(**kwargs):
        assert kwargs == {"prompt": False}
        if fault == "expired_key":
            raise ValueError("private-key")
        return c.credentials

    monkeypatch.setattr(m, "credentials", creds)
    monkeypatch.setattr("sys.argv", arguments())
    with pytest.raises(SystemExit) as e:
        m.main()
    report = json.loads(capsys.readouterr().out)
    assert "private-" not in json.dumps(report) and cleaned == ["route", "engine"]
    assert e.value.code == (0 if fault is None else 2)
    after = store.read("DEMO-test")
    if fault == "expired_key":
        assert after == before and not report["lease_change_possible"] and not calls
        assert report["stop_reason"] == "DEMO_CREDENTIAL_SESSION_UNAVAILABLE"
    else:
        assert m.recorded(after) and m.preserved(after) == m.preserved(before)
        if fault == "cleanup":
            assert (
                report["application_may_have_completed"]
                and report["stop_reason"] == "RESOURCE_CLEANUP_UNVERIFIED"
            )


def test_main_replay_needs_no_keys_no_network_no_extra_lease(setup, monkeypatch, capsys):
    store, *_ = setup
    run(setup)
    before = rows(store)

    class Engine:
        def dispose(self):
            pass

    monkeypatch.setattr(m, "create_engine", lambda *a, **k: Engine())
    monkeypatch.setattr(m, "DemoStore", lambda *a: store)
    monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys"))
    monkeypatch.setattr(m, "TPReadRouting", lambda *a: pytest.fail("no transport"))
    monkeypatch.setattr("sys.argv", arguments())
    with pytest.raises(SystemExit) as e:
        m.main()
    assert (
        e.value.code == 0
        and json.loads(capsys.readouterr().out)["manual_close_period_adoption"]
        == "ALREADY_RECORDED_NO_ACTION"
    )
    assert rows(store) == before


def test_source_failure_precedes_resources(monkeypatch, capsys):
    monkeypatch.setattr(m, "NEW_PINS", {"MISSING": "0" * 64})
    monkeypatch.setattr(m, "create_engine", lambda *a, **k: pytest.fail("no DB"))
    monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys"))
    monkeypatch.setattr("sys.argv", arguments())
    with pytest.raises(SystemExit):
        m.main()
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "INSTALLED_SOURCE_HASH_MISMATCH"


def test_lost_fence_cannot_commit_baseline(setup, monkeypatch):
    store, c, monitor, *_ = setup
    original_change = store.change
    original_release = store.release

    def lose(lease, kind, apply):
        original_release(lease)
        return original_change(lease, kind, apply)

    monkeypatch.setattr(store, "change", lose)
    from services.risk.paper import RiskRejected

    before = store.read("DEMO-test")
    with pytest.raises(RiskRejected):
        run(setup)
    after = store.read("DEMO-test")
    assert (
        m.APPLY_KEY not in after.operations
        and after.daily_key == before.daily_key
        and after.weekly_base == before.weekly_base
    )


@pytest.mark.parametrize(
    "missing",
    [
        "--apply",
        "--accept-operator-evidence",
        "--attest-no-activity-since-manual-close",
        "--attested-day-kst",
    ],
)
def test_each_required_cli_flag_is_enforced_before_resources(monkeypatch, missing):
    args = arguments()
    index = args.index(missing)
    del args[index : index + (2 if missing == "--attested-day-kst" else 1)]
    monkeypatch.setattr("sys.argv", args)
    monkeypatch.setattr(m, "create_engine", lambda *a, **k: pytest.fail("no DB"))
    monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys"))
    with pytest.raises(SystemExit) as e:
        m.main()
    assert e.value.code == 2
