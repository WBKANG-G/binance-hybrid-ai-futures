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
    "manual_close_period_preview", Path(__file__).with_name("preview_manual_close_periods.py")
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
    return store, client, monitor, opt, calls, fake, advance, route


def damage(store, fn):
    lease = store.acquire("DEMO-test", uuid4())
    store.change(lease, "SYNTHETIC_LOCAL_FAULT", fn)
    store.release(lease)


def test_external_close_period_preview_proposes_day_week_without_changing_highwater_or_timestamps(
    setup, monkeypatch
):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    before = rows(store)
    original = store.read("DEMO-test")
    for method in ("create", "acquire", "change", "renew", "release"):
        monkeypatch.setattr(store, method, lambda *a, **k: pytest.fail("no mutation or lease"))
    r = m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")
    assert r["proposed_day_kst"] == "2026-11-09" and r["proposed_daily_base_usdt"] == "10000"
    assert r["old_day_kst"] == "2026-11-07" and r["old_daily_base_usdt"] == "10000"
    assert r["old_weekly_base_usdt"] == "10002" and r["proposed_weekly_base_usdt"] == "10000"
    assert r["old_week_kst"] == "2026-11-02" and r["proposed_week_kst"] == "2026-11-09"
    assert r["high_water_usdt_preserved"] == "10005"
    assert r["signed_btc_trade_cohort_reverified"] and r["post_anchor_income_empty"]
    assert not r["baseline_adoption_approved"] and not r["review_token_printed"]
    assert (
        not r["exact_day_midnight_valuation_verified"]
        and not r["exact_week_midnight_valuation_verified"]
        and not r["account_wide_trade_export_verified"]
    )
    assert rows(store) == before and store.read("DEMO-test").value.as_of == original.value.as_of
    assert all(x[0] == "GET" for x in calls) and not any("page" in x[2] for x in calls)
    assert opt["income_calls"] == 2


def test_explicit_attestation_required_before_local_or_network_work(setup):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    with pytest.raises(m.Blocked, match="OPERATOR_ATTESTATION_REQUIRED"):
        m.preview(store, c, monitor)
    assert not calls


@pytest.mark.parametrize(
    "fault",
    [
        "late_income",
        "extra_trade",
        "wrong_fee",
        "wrong_close",
        "wallet_change",
        "day_change",
        "change_local",
    ],
)
def test_changed_activity_or_evidence_cannot_produce_baseline_candidate(setup, fault):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    opt[fault] = True
    with pytest.raises((m.Blocked, m.ProbeStop)) as e:
        m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")
    assert m.safe_reason(e.value) != "CHECK_UNAVAILABLE"
    assert all(x[0] == "GET" for x in calls)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_ack",
        "bad_anchor",
        "wrong_week",
        "already_today",
        "transfer_basis",
        "receipt_identity",
        "receipt_hash",
        "loss_gate",
    ],
)
def test_incompatible_local_basis_or_receipt_is_not_reset(setup, fault):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup

    def alter(s):
        r = json.loads(s.operations[m.RECEIPT])
        if fault == "missing_ack":
            r.pop("acknowledged_at_utc")
        elif fault == "bad_anchor":
            s.value = s.value.model_copy(
                update={"as_of": datetime(2026, 11, 6, 15, 30, tzinfo=UTC)}
            )
        elif fault == "wrong_week":
            s.weekly_key = "2026-10-26"
        elif fault == "already_today":
            s.daily_key = "2026-11-09"
        elif fault == "transfer_basis":
            s.income_seen = {"TRANSFER:1": "1"}
        elif fault == "receipt_identity":
            r["manual_close_client_id"] = "api_other"
        elif fault == "receipt_hash":
            r["prior_ledger_sha256"] = "not-a-hash"
        elif fault == "loss_gate":
            s.high_water = Decimal("20000")
        s.operations[m.RECEIPT] = json.dumps(r)

    damage(store, alter)
    before = rows(store)
    with pytest.raises((m.Blocked, m.ProbeStop)):
        m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")
    assert rows(store) == before
    if fault != "loss_gate":
        assert not calls


@pytest.mark.parametrize(
    "path", ["/fapi/v1/order", "/fapi/v1/userTrades", "/fapi/v1/income", "/fapi/v3/account"]
)
def test_nonjson_rate_limit_has_no_network_retry_or_mutation(setup, path):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    opt["rate_at"] = path
    before = rows(store)
    with pytest.raises(DemoRateLimited):
        m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")
    assert calls[-1][1] == path and c.cooldown_until > c.clock_ms() and rows(store) == before


@pytest.mark.parametrize("budget", ["weight", "header", "time", "http", "clock"])
def test_read_budgets_fail_closed(setup, monkeypatch, budget):  # noqa: F811
    store, c, monitor, opt, calls, _, advance, _ = setup
    if budget == "weight":
        opt["weight"] = 3550
    elif budget == "header":
        opt["header"] = False
    elif budget == "time":
        advance(80)
    elif budget == "http":
        monkeypatch.setattr(m, "MAX_HTTP_ATTEMPTS", 2)
    else:
        monkeypatch.setattr(m, "MAX_CLOCK_ATTEMPTS", 1)
    with pytest.raises(m.ProbeStop):
        m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")


@pytest.mark.parametrize(
    "method,path,params",
    [
        ("POST", "/fapi/v1/order", {}),
        ("DELETE", "/fapi/v1/algoOrder", {}),
        ("GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "orderId": "OTHER"}),
        (
            "GET",
            "/fapi/v1/income",
            {"startTime": "1", "endTime": "2", "limit": "1000", "page": "1"},
        ),
    ],
)
def test_query_scope_is_enforced_before_sender(setup, method, path, params):  # noqa: F811
    _, c, monitor, opt, calls, *_ = setup
    with pytest.raises(m.Blocked):
        c.request(method, path, params)
    assert not calls


def test_no_attestation_flag_and_no_apply_flag_have_no_key_or_database_access(monkeypatch):
    for arguments in [[], ["--apply"], ["--attest-no-activity-since-manual-close", "--apply"]]:
        monkeypatch.setattr("sys.argv", ["preview", *arguments])
        monkeypatch.setattr(m, "create_engine", lambda *a, **k: pytest.fail("no DB"))
        monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys"))
        with pytest.raises(SystemExit) as e:
            m.main()
        assert e.value.code == 2


def test_source_failure_precedes_credentials_or_database(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["preview", "--attest-no-activity-since-manual-close", "--attested-day-kst", "2026-11-09"],
    )
    monkeypatch.setattr(m, "NEW_PINS", {"MISSING": "0" * 64})
    monkeypatch.setattr(m, "create_engine", lambda *a, **k: pytest.fail("no DB"))
    monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys"))
    with pytest.raises(SystemExit) as e:
        m.main()
    assert (
        e.value.code == 2
        and json.loads(capsys.readouterr().out)["stop_reason"] == "INSTALLED_SOURCE_HASH_MISMATCH"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "full_income",
        "full_trades",
        "open_algo",
        "open_regular",
        "hedge_mode",
        "non_usdt_fee",
        "late_second_pass",
    ],
)
def test_incomplete_history_current_orders_and_late_income_are_blocked(setup, fault):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    opt["response_fault"] = fault
    before = rows(store)
    with pytest.raises(m.Blocked):
        m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")
    assert rows(store) == before
    assert all(x[0] == "GET" for x in calls)


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_main_cleanup_attempts_both_resources_and_redacts_errors(
    monkeypatch, capsys, cleanup_fails
):
    monkeypatch.setattr(
        "sys.argv",
        ["preview", "--attest-no-activity-since-manual-close", "--attested-day-kst", "2026-11-09"],
    )
    monkeypatch.setattr(m, "verify_sources", lambda: None)
    log = []

    class Engine:
        def dispose(self):
            log.append("dispose")

    class Route:
        def close(self):
            log.append("close")
            if cleanup_fails:
                raise ValueError("secret-canary-cleanup")

    monkeypatch.setattr(m, "create_engine", lambda *a, **k: Engine())
    monkeypatch.setattr(m, "DemoStore", lambda e: object())
    monkeypatch.setattr(m, "local", lambda s: None)
    monkeypatch.setattr(m, "TPReadRouting", lambda *a: Route())
    monkeypatch.setattr(m, "Monitor", lambda r: object())

    def unavailable(**kwargs):
        raise ValueError("secret-canary-key")

    monkeypatch.setattr(m, "credentials", unavailable)
    with pytest.raises(SystemExit) as e:
        m.main()
    raw = capsys.readouterr().out
    assert e.value.code == 2 and log == ["close", "dispose"]
    assert "secret-canary" not in raw
    assert json.loads(raw)["stop_reason"] == (
        "RESOURCE_CLEANUP_UNVERIFIED" if cleanup_fails else "DEMO_CREDENTIAL_SESSION_UNAVAILABLE"
    )


def test_deployed_source_pins_match_reference():
    m.verify_sources()
    assert len(m.NEW_PINS) == 45


def rebind_prior(s):
    r = json.loads(s.operations[m.PRIOR_DAY_KEY])
    v = r["reviewed_values"]
    v["weekly_base_usdt_preserved"] = str(s.weekly_base)
    v["high_water_usdt_preserved"] = str(s.high_water)
    typed = s.attested_day_baselines[m.PRIOR_DAY_KEY].model_copy(
        update={"review_token": m.digest(v)}
    )
    s.attested_day_baselines[m.PRIOR_DAY_KEY] = typed
    r["typed_receipt_sha256"] = m.digest(typed.model_dump(mode="json"))
    r["preserved_ledger_sha256"] = m.prior_preserved(s)
    s.operations[m.PRIOR_DAY_KEY] = json.dumps(r)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_typed",
        "missing_operation",
        "invalid_operation",
        "hash",
        "token",
        "preserved",
        "source",
        "exact_midnight",
        "account_export",
        "typed_baseline",
        "anchor",
        "boundary",
    ],
)
def test_prior_day_receipt_must_be_bound_before_new_week_proposal(setup, fault):
    store, c, monitor, _, calls, *_ = setup

    def alter(s):
        if fault == "missing_typed":
            s.attested_day_baselines.pop(m.PRIOR_DAY_KEY)
            return
        if fault == "missing_operation":
            s.operations.pop(m.PRIOR_DAY_KEY)
            return
        if fault == "invalid_operation":
            s.operations[m.PRIOR_DAY_KEY] = "[]"
            return
        r = json.loads(s.operations[m.PRIOR_DAY_KEY])
        typed = s.attested_day_baselines[m.PRIOR_DAY_KEY]
        if fault == "hash":
            r["typed_receipt_sha256"] = "0" * 64
        if fault == "token":
            typed.review_token = "0" * 64
        if fault == "preserved":
            r["preserved_ledger_sha256"] = "0" * 64
        if fault == "source":
            r["evidence_source"] = "UNKNOWN"
        if fault == "exact_midnight":
            r["exact_midnight_valuation_verified"] = True
        if fault == "account_export":
            r["account_wide_exchange_export_verified"] = True
        if fault == "typed_baseline":
            typed.baseline_usdt += 1
        if fault == "anchor":
            typed.anchor_utc += timedelta(seconds=1)
        if fault == "boundary":
            typed.boundary_utc += timedelta(hours=1)
        s.operations[m.PRIOR_DAY_KEY] = json.dumps(r)

    damage(store, alter)
    before = rows(store)
    with pytest.raises(m.Blocked):
        m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")
    assert not calls and rows(store) == before


@pytest.mark.parametrize("day", [None, "2026-11-08", "2026-11-10", "not-a-date"])
def test_fresh_attestation_day_is_required_before_signed_reads(setup, day):
    store, c, monitor, _, calls, *_ = setup
    with pytest.raises(m.Blocked, match="CURRENT_DAY_ATTESTATION_REQUIRED"):
        m.preview(store, c, monitor, attest=True, attested_day=day)
    assert not calls


@pytest.mark.parametrize("fault", ["weekly_loss", "drawdown", "highwater_below_wallet"])
def test_old_risk_limits_are_checked_before_proposing_new_period_values(setup, fault):
    store, c, monitor, _, calls, *_ = setup

    def alter(s):
        if fault == "weekly_loss":
            s.weekly_base = Decimal("20000")
        if fault == "drawdown":
            s.high_water = Decimal("20000")
        if fault == "highwater_below_wallet":
            s.high_water = Decimal("9999")
        rebind_prior(s)

    damage(store, alter)
    before = rows(store)
    with pytest.raises(
        m.Blocked,
        match="HIGH_WATER_REQUIRES_SEPARATE_REVIEW"
        if fault == "highwater_below_wallet"
        else "EXISTING_LOSS_LIMIT_REACHED",
    ):
        m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")
    assert calls and rows(store) == before


def test_config_endpoint_is_fresh_and_single_get_each_read_state(setup):
    store, c, monitor, _, calls, *_ = setup
    report = m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")
    assert sum(path == "/fapi/v1/accountConfig" for method, path, params in calls) == 2
    assert not any(
        path in {"/fapi/v1/positionSide/dual", "/fapi/v1/multiAssetsMargin"}
        for method, path, params in calls
    )
    assert (
        report["prior_attested_day_receipt_verified"]
        and report["operator_attested_day_kst"] == "2026-11-09"
    )
    assert report["day_boundary_utc"] == report["week_boundary_utc"] == "2026-11-08T15:00:00+00:00"
    assert report["http_attempts"] == 23 and report["clock_attempts"] == 4


@pytest.mark.parametrize(
    "body",
    [
        {"dualSidePosition": False},
        {"dualSidePosition": "false", "multiAssetsMargin": False},
        {"dualSidePosition": False, "multiAssetsMargin": 0},
        {"dualSidePosition": False, "multiAssetsMargin": True},
    ],
)
def test_new_account_config_requires_two_strict_false_fields(setup, body):
    store, c, monitor, _, calls, *_ = setup
    route = monitor.route

    def send(method, path, q, k):
        if path == "/fapi/v1/accountConfig":
            return RESTResponse(200, json.dumps(body).encode(), {"x-mbx-used-weight-1m": "200"})
        return route(method, path, q, k)

    monitor.route = send
    with pytest.raises(m.Blocked, match="ACCOUNT_MODE_UNSUPPORTED"):
        m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")


def test_missing_date_flag_cannot_reuse_old_boolean_attestation(monkeypatch):
    monkeypatch.setattr("sys.argv", ["preview", "--attest-no-activity-since-manual-close"])
    monkeypatch.setattr(m, "create_engine", lambda *a, **k: pytest.fail("no DB"))
    monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys"))
    with pytest.raises(SystemExit) as e:
        m.main()
    assert e.value.code == 2


def test_main_success_is_read_only_and_passes_current_attestation_day(setup, monkeypatch, capsys):
    store, c, monitor, _, _, _, _, route = setup
    before = rows(store)
    cleaned = []

    class Engine:
        def dispose(self):
            cleaned.append("engine")

    old_close = route.close

    def close():
        old_close()
        cleaned.append("route")

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
    monkeypatch.setattr(m, "credentials", lambda **kwargs: c.credentials)
    monkeypatch.setattr(
        "sys.argv",
        ["preview", "--attest-no-activity-since-manual-close", "--attested-day-kst", "2026-11-09"],
    )
    for method in ("create", "change", "acquire", "renew", "release"):
        monkeypatch.setattr(store, method, lambda *a, **k: pytest.fail("no lease or DB mutation"))
    with pytest.raises(SystemExit) as e:
        m.main()
    report = json.loads(capsys.readouterr().out)
    assert e.value.code == 0 and report["operator_attested_day_kst"] == "2026-11-09"
    assert report["proposed_day_kst"] == report["proposed_week_kst"] == "2026-11-09"
    assert not report["baseline_adoption_approved"] and report["paused"]
    assert cleaned == ["route", "engine"] and rows(store) == before


def test_proposed_risk_gate_failure_cannot_return_baseline_values(setup, monkeypatch):
    store, c, monitor, _, _, *_ = setup
    before = rows(store)
    monkeypatch.setattr(
        m, "account_risk", lambda ledger, now: setattr(ledger, "risk_state", "DAILY_LOCK")
    )
    with pytest.raises(m.Blocked, match="PROPOSED_RISK_GATES_FAILED"):
        m.preview(store, c, monitor, attest=True, attested_day="2026-11-09")
    assert rows(store) == before
