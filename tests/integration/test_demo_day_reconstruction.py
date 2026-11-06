"""Manual-source provenance is explicit; signed evidence overrides the attestation."""

import json
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from sqlalchemy import select

from integrations.binance_usdm.demo import DemoFailure
from packages.persistence.demo import events
from services.execution.demo_cancelled_correction import apply_correction, review_correction
from services.execution.demo_day_reconstruction import (
    SOURCE,
    DayReconstructionBlocked,
    apply_day_reconstruction,
    review_day_reconstruction,
)
from services.risk.paper import RiskRejected
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction, event_count  # noqa: F401
from tests.unit.test_demo_trade_history_window import row


@pytest.fixture
def day_case(correction):  # noqa: F811
    store, c, fake, sig, order, rows = correction
    token = review_correction(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")[
        "review_token"
    ]
    apply_correction(store, c.lease, c.client, uuid4(), token)
    # Synthetic fees were charged before the flat anchor. Nothing changed after it.
    fake.wallet -= Decimal("0.14725051")

    def fees_at_anchor(ledger):
        ledger.value = ledger.value.model_copy(update={"realized": -Decimal("0.14725051")})

    store.change(c.lease, "TEST_FEES_AT_VERIFIED_ANCHOR", fees_at_anchor)
    original = c.client.send
    raw_updates = {}
    incomes = []

    def send(method, path, query, key):
        assert method == "GET"
        assert path not in {"/fapi/v1/trade/asyn", "/fapi/v1/trade/asyn/id"}
        if path == "/fapi/v1/income":
            params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
            return fake.response(
                [
                    r
                    for r in incomes
                    if int(params["startTime"]) <= r["time"] <= int(params["endTime"])
                ]
            )
        result = original(method, path, query, key)
        if path == "/fapi/v3/account":
            raw = json.loads(result.body)
            raw["totalMarginBalance"] = str(fake.wallet)
            raw["assets"] = [
                {
                    "asset": "USDT",
                    "walletBalance": str(fake.wallet),
                    "marginBalance": str(fake.wallet),
                    "unrealizedProfit": "0",
                },
                {"asset": "USDC", "walletBalance": "5000", "unrealizedProfit": "0"},
                {"asset": "BTC", "walletBalance": "0.01", "unrealizedProfit": "0"},
            ]
            raw.update(raw_updates)
            return fake.response(raw)
        return result

    c.client.send = send
    return store, c, fake, rows, incomes, raw_updates


def review(case, attest=True, account="DEMO-test"):
    store, c, *_ = case
    return review_day_reconstruction(
        store, c.client, store.read("DEMO-test"), account_id=account, attest_no_other_trades=attest
    )


def apply(case, token, op=None, attest=True, accept=True, lease=None):
    store, c, *_ = case
    return apply_day_reconstruction(
        store,
        lease or c.lease,
        c.client,
        op or uuid4(),
        token,
        attest_no_other_trades=attest,
        accept_operator_evidence=accept,
    )


def test_preview_is_conditional_manual_source_not_exchange_wide_proof(day_case):
    store, c, fake, *_ = day_case
    before = store.read("DEMO-test").model_dump_json()
    count = event_count(store)
    start = len(fake.calls)
    report = review(day_case)
    assert report["source"] == SOURCE
    assert report["operator_attestation_used"] is True
    assert report["account_wide_exchange_export_verified"] is False
    assert report["proposed_daily_base_usdt"] == "9999.85274949"
    assert report["old_daily_base_usdt"] == "10000"
    assert report["proposed_day_kst"] == "2026-10-21"
    assert report["boundary_utc"] == "2026-10-20T15:00:00+00:00"
    assert report["recovery_eligible"] is False
    assert store.read("DEMO-test").model_dump_json() == before
    assert event_count(store) == count
    assert all(method == "GET" for method, _, _ in fake.calls[start:])


def test_apply_changes_only_day_basis_and_audit_keeps_lock_and_old_valuation(day_case):
    store, c, *_ = day_case
    before = store.read("DEMO-test")
    report = review(day_case)
    op = uuid4()
    assert (
        apply(day_case, report["review_token"], op)
        == "OPERATOR_ATTESTED_DAILY_BASELINE_ADOPTED_KEEP_LOCK"
    )
    after = store.read("DEMO-test")
    excluded = {
        "daily_key",
        "daily_base",
        "attested_day_baselines",
        "operations",
        "reconciled",
        "stream_ready",
    }
    assert before.model_dump(exclude=excluded) == after.model_dump(exclude=excluded)
    assert after.value == before.value and after.last_reconciled == before.last_reconciled
    assert after.orders == before.orders and after.requests == before.requests
    assert after.weekly_base == before.weekly_base and after.high_water == before.high_water
    assert after.daily_key == "2026-10-21" and after.daily_base == Decimal("9999.85274949")
    assert after.risk_state.value == "EMERGENCY" and after.paused
    receipt = after.attested_day_baselines[str(op)]
    assert receipt.old_daily_key == before.daily_key and receipt.old_daily_base == before.daily_base
    assert receipt.source == SOURCE and receipt.account_wide_exchange_export_verified is False
    assert receipt.anchor_utc == before.last_reconciled
    with store.engine.connect() as conn:
        payload = conn.execute(
            select(events.c.payload).where(events.c.kind == "DEMO_OPERATOR_ATTESTED_DAY_BASELINE")
        ).scalar_one()
    assert SOURCE in payload and report["review_token"] in payload
    assert apply(day_case, report["review_token"], op) == "ALREADY_RECORDED_NO_ACTION"
    assert store.read("DEMO-test") == after


@pytest.mark.parametrize("attest,accept", [(False, True), (True, False), (False, False)])
def test_explicit_operator_attestation_and_source_acceptance_are_both_required(
    day_case, attest, accept
):
    store, c, fake, *_ = day_case
    token = review(day_case)["review_token"]
    before = store.read("DEMO-test").model_dump_json()
    start = len(fake.calls)
    with pytest.raises(DayReconstructionBlocked, match="MANUAL_SOURCE_ACCEPTANCE"):
        apply(day_case, token, attest=attest, accept=accept)
    assert len(fake.calls) == start
    assert store.read("DEMO-test").model_dump_json() == before


def test_no_preview_attestation_means_no_network(day_case):
    fake = day_case[2]
    start = len(fake.calls)
    with pytest.raises(DayReconstructionBlocked, match="ATTESTATION_REQUIRED"):
        review(day_case, attest=False)
    assert len(fake.calls) == start


@pytest.mark.parametrize(
    "change",
    [
        "net_transfer",
        "deposit",
        "transfer_record",
        "tracking",
        "week",
        "daily_loss",
        "weekly_loss",
        "drawdown",
        "high_water",
    ],
)
def test_unknown_basis_or_existing_risk_limits_cannot_be_reset(day_case, change):
    store, c, *_ = day_case

    def modify(ledger):
        if change == "net_transfer":
            ledger.net_transfers = Decimal(1)
        elif change == "deposit":
            ledger.value = ledger.value.model_copy(update={"net_deposits": Decimal(1)})
        elif change == "transfer_record":
            ledger.income_seen["TRANSFER:1"] = "0"
        elif change == "tracking":
            ledger.income_since_ms = None
        elif change == "week":
            ledger.weekly_key = "2026-10-12"
        elif change == "daily_loss":
            ledger.daily_base = Decimal(20000)
        elif change == "weekly_loss":
            ledger.weekly_base = Decimal(20000)
        elif change == "drawdown":
            ledger.high_water = Decimal(20000)
        elif change == "high_water":
            ledger.high_water = Decimal(9999)

    store.change(c.lease, "TEST_UNAPPROVABLE_BASIS", modify)
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(DayReconstructionBlocked):
        review(day_case)
    assert store.read("DEMO-test").model_dump_json() == before


@pytest.mark.parametrize(
    "change",
    [
        "new_trade",
        "net_zero_income",
        "wallet",
        "missing_asset",
        "foreign_assets_in_total",
        "usdt_unrealized",
    ],
)
def test_signed_disagreement_blocks_even_if_user_attests_no_trades(day_case, change):
    store, c, fake, rows, incomes, updates = day_case
    if change == "new_trade":
        rows.append(row(id=3, time=int(fake.at.timestamp() * 1000)))
    elif change == "net_zero_income":
        stamp = int(fake.at.timestamp() * 1000) - 1000
        incomes.extend(
            [
                {
                    "asset": "USDT",
                    "incomeType": "COMMISSION",
                    "income": "-0.1",
                    "time": stamp,
                    "tranId": 101,
                },
                {
                    "asset": "USDT",
                    "incomeType": "REALIZED_PNL",
                    "income": "0.1",
                    "time": stamp,
                    "tranId": 102,
                },
            ]
        )
    elif change == "wallet":
        fake.wallet += 1
    elif change == "missing_asset":
        updates["assets"] = []
    elif change == "foreign_assets_in_total":
        updates["totalWalletBalance"] = str(fake.wallet + 5000)
    elif change == "usdt_unrealized":
        updates["assets"] = [
            {
                "asset": "USDT",
                "walletBalance": str(fake.wallet),
                "marginBalance": str(fake.wallet),
                "unrealizedProfit": "1",
            }
        ]
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(DemoFailure):
        review(day_case)
    assert store.read("DEMO-test").model_dump_json() == before


def test_stale_token_and_wrong_account_roll_back(day_case):
    store, c, *_ = day_case
    token = review(day_case, account="DEMO-other")["review_token"]
    before = store.read("DEMO-test").model_dump_json()
    count = event_count(store)
    with pytest.raises(DayReconstructionBlocked, match="PREVIEW_CHANGED"):
        apply(day_case, token)
    assert store.read("DEMO-test").model_dump_json() == before and event_count(store) == count


def test_expiry_after_review_rolls_back_baseline_and_receipt(day_case, monkeypatch):
    import services.execution.demo_day_reconstruction as module

    store, c, fake, *_ = day_case
    token = review(day_case)["review_token"]
    before = store.read("DEMO-test").model_dump_json()
    count = event_count(store)
    original = module.review_day_reconstruction

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        fake.at += timedelta(seconds=61)
        return result

    monkeypatch.setattr(module, "review_day_reconstruction", delayed)
    with pytest.raises(RiskRejected, match="outlived lease"):
        apply(day_case, token)
    assert store.read("DEMO-test").model_dump_json() == before and event_count(store) == count


def test_new_kst_day_invalidates_preview_even_with_fresh_lease(day_case):
    store, c, fake, *_ = day_case
    token = review(day_case)["review_token"]
    store.release(c.lease)
    fake.at += timedelta(days=1)
    c.client.sync_clock()
    fresh = store.acquire("DEMO-test", uuid4())
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(DayReconstructionBlocked, match="PREVIEW_CHANGED"):
        apply(day_case, token, lease=fresh)
    assert store.read("DEMO-test").model_dump_json() == before


def test_operation_receipt_cannot_be_reused_with_another_token(day_case):
    store, c, *_ = day_case
    token = review(day_case)["review_token"]
    op = uuid4()
    apply(day_case, token, op)
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(DayReconstructionBlocked, match="OPERATION_CONFLICT"):
        apply(day_case, "f" * 64, op)
    assert store.read("DEMO-test").model_dump_json() == before


def test_old_leader_is_fenced_before_any_manual_adoption(day_case):
    store, c, *_ = day_case
    token = review(day_case)["review_token"]
    old = c.lease
    store.release(old)
    store.acquire("DEMO-test", uuid4())
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(RiskRejected, match="fenced"):
        apply(day_case, token, lease=old)
    assert store.read("DEMO-test").model_dump_json() == before


def test_cli_preview_exposes_manual_source_without_lease_or_database_write(
    day_case, monkeypatch, capsys
):
    import os
    import sys

    import scripts.review_demo_attested_day as cli

    store, c, *_ = day_case
    original = store.read
    before = original("DEMO-test").model_dump_json()
    count = event_count(store)
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(
        sys, "argv", ["review_demo_attested_day", "--attest-no-other-trades-since-exit"]
    )
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda *a, **k: c.client)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")

    def no_write(*args):
        raise AssertionError("Preview must not acquire, change or release")

    monkeypatch.setattr(store, "acquire", no_write)
    monkeypatch.setattr(store, "change", no_write)
    monkeypatch.setattr(store, "release", no_write)
    cli.main()
    output = capsys.readouterr().out
    assert SOURCE in output and "OPERATOR_ATTESTED_DAY_PREVIEW_NO_DATABASE_CHANGE" in output
    assert '"account_wide_exchange_export_verified": false' in output
    assert "SYNTHETIC_PRIVATE_KEY" not in output
    assert "BINANCE_DEMO_API_KEY" not in os.environ and "BINANCE_DEMO_API_SECRET" not in os.environ
    assert original("DEMO-test").model_dump_json() == before and event_count(store) == count


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        [
            "--attest-no-other-trades-since-exit",
            "--apply-review",
            "f" * 64,
            "--operation-id",
            "8fae5b75-99a8-40a8-9371-50f962728dc8",
        ],
        ["--attest-no-other-trades-since-exit", "--accept-operator-evidence"],
    ],
)
def test_cli_requires_attestation_and_separate_acceptance_before_apply(
    monkeypatch, capsys, arguments
):
    import sys

    import scripts.review_demo_attested_day as cli

    monkeypatch.setattr(sys, "argv", ["review_demo_attested_day", *arguments])

    def no_prompt(*args):
        raise AssertionError("Invalid flags must not ask keys")

    monkeypatch.setattr(cli.getpass, "getpass", no_prompt)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "BLOCKED_KEEP_LOCK" in capsys.readouterr().out


def test_cli_apply_requires_acceptance_and_preserves_emergency(day_case, monkeypatch, capsys):
    import sys

    import scripts.review_demo_attested_day as cli

    store, c, *_ = day_case
    token = review(day_case)["review_token"]
    op = uuid4()
    original_read, original_acquire = store.read, store.acquire
    before = original_read("DEMO-test")
    store.release(c.lease)
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_demo_attested_day",
            "--attest-no-other-trades-since-exit",
            "--accept-operator-evidence",
            "--apply-review",
            token,
            "--operation-id",
            str(op),
        ],
    )
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda *a, **k: c.client)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")
    monkeypatch.setattr(store, "acquire", lambda account, owner: original_acquire(account, owner))
    cli.main()
    output = capsys.readouterr().out
    assert "OPERATOR_ATTESTED_DAILY_BASELINE_ADOPTED_KEEP_LOCK" in output and SOURCE in output
    assert "SYNTHETIC_PRIVATE_KEY" not in output
    after = original_read("DEMO-test")
    assert after.value == before.value and after.requests == before.requests
    assert after.paused and after.risk_state.value == "EMERGENCY"
    assert str(op) in after.attested_day_baselines
