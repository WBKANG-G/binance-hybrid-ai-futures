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
from services.risk.paper import AccountValue
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_tp_rate_install_guard import closed  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "manual_close_day_preview", Path(__file__).with_name("adopt_manual_close_day.py")
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
    fake.at = datetime(2026, 11, 6, 16, 0, tzinfo=UTC)
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
        s.daily_key = "2026-11-06"
        s.weekly_key = "2026-11-02"
        s.daily_base = fake.wallet + 1
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

    store.change(lease, "SYNTHETIC_ADOPTED_MANUAL_CLOSE_ANCHOR", populate)
    store.release(lease)
    initial = store.read("DEMO-test")
    monkeypatch.setattr(
        m,
        "EXPECTED",
        {
            "old_day_kst": "2026-11-06",
            "proposed_day_kst": "2026-11-07",
            "current_week_kst": "2026-11-02",
            "old_daily_base_usdt": "10001",
            "proposed_daily_base_usdt": "10000",
            "weekly_base_usdt_preserved": "10002",
            "high_water_usdt_preserved": "10005",
            "adoption_snapshot_anchor_utc": anchor.isoformat(),
        },
    )
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
        elif fault == "hedge_mode" and path.endswith("/dual"):
            data = {"dualSidePosition": True}
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


def test_external_close_day_preview_is_read_only_and_preserves_week_highwater_and_timestamps(
    setup, monkeypatch
):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    before = rows(store)
    original = store.read("DEMO-test")
    for method in ("create", "acquire", "change", "renew", "release"):
        monkeypatch.setattr(store, method, lambda *a, **k: pytest.fail("no mutation or lease"))
    r = m.preview(store, c, monitor, attest=True)
    assert r["proposed_day_kst"] == "2026-11-07" and r["proposed_daily_base_usdt"] == "10000"
    assert r["old_day_kst"] == "2026-11-06" and r["old_daily_base_usdt"] == "10001"
    assert r["weekly_base_usdt_preserved"] == "10002" and r["high_water_usdt_preserved"] == "10005"
    assert r["signed_btc_trade_cohort_reverified"] and r["post_anchor_income_empty"]
    assert not r["baseline_adoption_approved"] and not r["review_token_printed"]
    assert (
        not r["exact_midnight_valuation_verified"] and not r["account_wide_trade_export_verified"]
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
        m.preview(store, c, monitor, attest=True)
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
            s.daily_key = "2026-11-07"
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
        m.preview(store, c, monitor, attest=True)
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
        m.preview(store, c, monitor, attest=True)
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
        m.preview(store, c, monitor, attest=True)


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
    for arguments in [[], ["--apply"], ["--attest-no-activity-since-manual-close"]]:
        monkeypatch.setattr("sys.argv", ["preview", *arguments])
        monkeypatch.setattr(m, "create_engine", lambda *a, **k: pytest.fail("no DB"))
        monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys"))
        with pytest.raises(SystemExit) as e:
            m.main()
        assert e.value.code == 2


def test_source_failure_precedes_credentials_or_database(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["preview", "--attest-no-activity-since-manual-close", "--apply"]
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
        m.preview(store, c, monitor, attest=True)
    assert rows(store) == before
    assert all(x[0] == "GET" for x in calls)


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_main_cleanup_attempts_both_resources_and_redacts_errors(
    monkeypatch, capsys, cleanup_fails
):
    monkeypatch.setattr(
        "sys.argv", ["preview", "--attest-no-activity-since-manual-close", "--apply"]
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
    monkeypatch.setattr(m, "local", lambda s: (None,))
    monkeypatch.setattr(m, "recorded", lambda s: False)
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
    assert len(m.NEW_PINS) == 29


def test_apply_is_audited_preserves_all_other_fields_and_repeat_is_noop(setup, monkeypatch):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    before = store.read("DEMO-test")
    original_rows = rows(store)
    progress = {}
    r = m.adopt(store, c, monitor, attest=True, progress=progress)
    assert r["manual_close_day_adoption"] == "OPERATOR_ATTESTED_DAY_ADOPTED_ENTRIES_STILL_PAUSED"
    assert r["database_changed"] and r["leader_released"]
    after = store.read("DEMO-test")
    assert m.preserved(after) == m.preserved(before)
    assert after.daily_key == "2026-11-07" and after.daily_base == Decimal("10000")
    assert m.recorded(after) and after.paused and after.risk_state.value == "NORMAL"
    assert after.value == before.value and after.requests == before.requests
    assert (
        after.orders == before.orders
        and after.operations[m.RECEIPT] == before.operations[m.RECEIPT]
    )
    finalrows = rows(store)
    assert len(finalrows[1]) == len(original_rows[1]) + 3
    assert finalrows[0][0]["owner"] is None
    assert finalrows[0][0]["fence"] == original_rows[0][0]["fence"] + 1
    assert {r["kind"] for r in finalrows[1]} >= {
        "DEMO_LEADER_ACQUIRED",
        "DEMO_EXTERNAL_CLOSE_ATTESTED_DAY_BASELINE",
        "DEMO_LEADER_RELEASED",
    }
    count = len(calls)
    monkeypatch.setattr(store, "acquire", lambda *a: pytest.fail("repeat must not acquire"))
    again = m.adopt(store, c, monitor, attest=True)
    assert again["manual_close_day_adoption"] == "ALREADY_RECORDED_NO_ACTION"
    assert not again["database_changed"] and len(calls) == count and rows(store) == finalrows
    assert all(x[0] == "GET" for x in calls)


@pytest.mark.parametrize(
    "fault", ["late_income", "wrong_fee", "wallet_change", "extra_trade", "wrong_close"]
)
def test_apply_query_failure_rolls_back_day_and_releases_lease(setup, fault):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    before = store.read("DEMO-test")
    opt[fault] = True
    with pytest.raises((m.Blocked, m.ProbeStop)):
        m.adopt(store, c, monitor, attest=True)
    after = store.read("DEMO-test")
    assert m.preserved(after) == m.preserved(before)
    assert after.daily_key == before.daily_key and after.daily_base == before.daily_base
    assert m.APPLY_KEY not in after.operations and m.APPLY_KEY not in after.attested_day_baselines
    assert rows(store)[0][0]["owner"] is None
    assert not any(r["kind"] == "DEMO_EXTERNAL_CLOSE_ATTESTED_DAY_BASELINE" for r in rows(store)[1])


@pytest.mark.parametrize(
    "key",
    [
        "old_daily_base_usdt",
        "proposed_daily_base_usdt",
        "weekly_base_usdt_preserved",
        "high_water_usdt_preserved",
        "proposed_day_kst",
        "adoption_snapshot_anchor_utc",
    ],
)
def test_reviewed_value_mismatch_blocks_before_lease_or_network(setup, monkeypatch, key):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    expected = dict(m.EXPECTED)
    expected[key] = "20000" if "usdt" in key or key.endswith("preserved") else "invalid"
    monkeypatch.setattr(m, "EXPECTED", expected)
    before = rows(store)
    with pytest.raises(m.Blocked):
        m.adopt(store, c, monitor, attest=True)
    assert rows(store) == before and not calls


def test_failure_during_callback_rolls_back_both_receipts_and_baseline(setup, monkeypatch):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    before = store.read("DEMO-test")
    original = m.recorded

    def fail_after_mutation(s):
        if m.APPLY_KEY in s.operations:
            raise m.Blocked("UNRELATED_LEDGER_CHANGE")
        return original(s)

    monkeypatch.setattr(m, "recorded", fail_after_mutation)
    with pytest.raises(m.Blocked):
        m.adopt(store, c, monitor, attest=True)
    after = store.read("DEMO-test")
    assert after.daily_key == before.daily_key and after.daily_base == before.daily_base
    assert m.APPLY_KEY not in after.operations and m.APPLY_KEY not in after.attested_day_baselines
    assert rows(store)[0][0]["owner"] is None


def test_receipt_conflict_never_overwrites_existing_entry(setup):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    damage(store, lambda s: s.operations.update({m.APPLY_KEY: "unrelated"}))
    before = rows(store)
    with pytest.raises(m.Blocked, match="DAY_OPERATION_CONFLICT"):
        m.adopt(store, c, monitor, attest=True)
    assert rows(store) == before and not calls


def test_already_committed_but_postcheck_failed_is_recognized_without_second_apply(
    setup, monkeypatch
):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    original = m.local

    def fail_postcheck(s, lease=None):
        if lease is None and m.APPLY_KEY in s.read("DEMO-test").operations:
            raise m.Blocked("POSTCHECK_FAILED")
        return original(s, lease)

    monkeypatch.setattr(m, "local", fail_postcheck)
    progress = {}
    with pytest.raises(m.Blocked, match="POSTCHECK_FAILED"):
        m.adopt(store, c, monitor, attest=True, progress=progress)
    assert progress["application_may_have_completed"] and m.recorded(store.read("DEMO-test"))
    monkeypatch.setattr(m, "local", original)
    count = len(calls)
    assert not m.adopt(store, c, monitor, attest=True)["database_changed"]
    assert count == len(calls)


def test_preexisting_active_leader_blocks_without_network(setup):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    lease = store.acquire("DEMO-test", uuid4())
    before = rows(store)
    try:
        with pytest.raises(m.Blocked, match="ACTIVE_OWNER_BLOCKED"):
            m.adopt(store, c, monitor, attest=True)
        assert rows(store) == before and not calls
    finally:
        store.release(lease)


def test_apply_requires_operator_attestation_before_any_database_mutation(setup):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    before = rows(store)
    with pytest.raises(m.Blocked, match="OPERATOR_ATTESTATION_REQUIRED"):
        m.adopt(store, c, monitor)
    assert rows(store) == before and not calls


@pytest.mark.parametrize("delay", [31, 61])
def test_stale_final_proof_or_expired_lease_cannot_commit_baseline(setup, monkeypatch, delay):  # noqa: F811
    store, c, monitor, opt, calls, _, advance, _ = setup
    original = m.read_state
    counter = [0]

    def delayed(client, ledger):
        r = original(client, ledger)
        counter[0] += 1
        if counter[0] == 2:
            advance(delay)
        return r

    monkeypatch.setattr(m, "read_state", delayed)
    before = store.read("DEMO-test")
    with pytest.raises((m.Blocked, m.ProbeStop)):
        m.adopt(store, c, monitor, attest=True)
    after = store.read("DEMO-test")
    assert after.daily_key == before.daily_key and after.daily_base == before.daily_base
    assert m.APPLY_KEY not in after.operations and rows(store)[0][0]["owner"] is None


def test_modified_committed_receipt_is_not_treated_as_noop(setup):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    m.adopt(store, c, monitor, attest=True)

    def corrupt(s):
        s.attested_day_baselines[m.APPLY_KEY].baseline_usdt = Decimal("9999")

    damage(store, corrupt)
    before = rows(store)
    count = len(calls)
    with pytest.raises(m.Blocked, match="DAY_OPERATION_CONFLICT"):
        m.adopt(store, c, monitor, attest=True)
    assert rows(store) == before and len(calls) == count


def test_competing_leader_cannot_acquire_during_fenced_proof(setup, monkeypatch):  # noqa: F811
    from sqlalchemy.exc import DBAPIError

    store, c, monitor, opt, calls, *_ = setup
    original = m.preview
    blocked = []

    def proving(*args, **kwargs):
        with pytest.raises(DBAPIError):
            store.acquire("DEMO-test", uuid4())
        blocked.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(m, "preview", proving)
    r = m.adopt(store, c, monitor, attest=True)
    assert r["database_changed"] and blocked == [True]
    assert rows(store)[0][0]["owner"] is None


def test_apply_rate_limit_releases_lease_without_baseline_change_or_retry(setup):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    opt["rate_at"] = "/fapi/v1/income"
    before = store.read("DEMO-test")
    with pytest.raises(DemoRateLimited):
        m.adopt(store, c, monitor, attest=True)
    after = store.read("DEMO-test")
    assert after.daily_key == before.daily_key and after.daily_base == before.daily_base
    assert m.APPLY_KEY not in after.operations
    assert calls[-1][1] == "/fapi/v1/income" and rows(store)[0][0]["owner"] is None


def test_main_recognizes_committed_result_without_credentials_or_network(
    setup, monkeypatch, capsys
):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    m.adopt(store, c, monitor, attest=True)
    before = rows(store)
    monkeypatch.setattr("sys.argv", ["adopt", "--apply", "--attest-no-activity-since-manual-close"])
    monkeypatch.setattr(m, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(m, "DemoStore", lambda e: store)
    monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys for completed action"))
    monkeypatch.setattr(
        m, "TPReadRouting", lambda *a, **k: pytest.fail("no network for completed action")
    )
    with pytest.raises(SystemExit) as e:
        m.main()
    assert e.value.code == 0 and rows(store) == before
    assert (
        json.loads(capsys.readouterr().out)["manual_close_day_adoption"]
        == "ALREADY_RECORDED_NO_ACTION"
    )
