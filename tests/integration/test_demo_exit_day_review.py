"""Signed pre-midnight exit may corroborate manual evidence, never reset risk locks."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from integrations.binance_usdm.demo import DemoFailure
from packages.persistence.demo import events
from services.execution.demo_exit_day_review import (
    SOURCE,
    ExitDayBlocked,
    apply_exit_day,
    review_exit_day,
)
from services.risk.paper import RiskRejected
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction, event_count  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case as parent_case  # noqa: F401


@pytest.fixture
def exit_case(parent_case):  # noqa: F811
    store, c, fake, rows, incomes, raw = parent_case
    boundary = datetime.combine(
        fake.at.astimezone(ZoneInfo("Asia/Seoul")).date(), time(), ZoneInfo("Asia/Seoul")
    ).astimezone(UTC)
    stamp = int((boundary - timedelta(seconds=63, milliseconds=277)).timestamp() * 1000)
    rows[-1].update(time=stamp, commission="0.02886822", realizedPnl="-0.06111000")
    incomes.extend(
        [
            dict(
                asset="USDT",
                incomeType="COMMISSION",
                symbol="BTCUSDT",
                time=stamp,
                tranId="1001",
                income="-0.02886822",
            ),
            dict(
                asset="USDT",
                incomeType="REALIZED_PNL",
                symbol="BTCUSDT",
                time=stamp,
                tranId="1002",
                income="-0.06111000",
            ),
        ]
    )

    def post_midnight(ledger):
        ledger.last_reconciled = fake.at
        ledger.value = ledger.value.model_copy(update={"as_of": fake.at})

    store.change(c.lease, "TEST_POST_MIDNIGHT_OBSERVATION", post_midnight)
    return store, c, fake, rows, incomes, raw


def review(case, attest=True, account="DEMO-test"):
    store, c, *_ = case
    return review_exit_day(
        store, c.client, store.read("DEMO-test"), account_id=account, attest_no_activity=attest
    )


def apply(case, token, *, operation=None, attest=True, accept=True, lease=None):
    store, c, *_ = case
    return apply_exit_day(
        store,
        lease or c.lease,
        c.client,
        operation or uuid4(),
        token,
        attest_no_activity=attest,
        accept_operator_evidence=accept,
    )


def test_post_midnight_preview_is_get_only_and_preserves_source_timestamps(exit_case):
    store, c, fake, rows, *_ = exit_case
    before = store.read("DEMO-test")
    count = event_count(store)
    start = len(fake.calls)
    result = review(exit_case)
    assert result["source"] == SOURCE
    assert result["account_wide_exchange_export_verified"] is False
    assert result["operator_attestation_used"] is True
    assert (
        result["known_exit_utc"] == datetime.fromtimestamp(rows[-1]["time"] / 1000, UTC).isoformat()
    )
    assert result["observation_utc"] == before.last_reconciled.isoformat()
    assert result["proposed_daily_base_usdt"] == str(before.value.equity)
    assert result["database_changed"] is False and result["recovery_eligible"] is False
    assert store.read("DEMO-test") == before and event_count(store) == count
    assert all(m == "GET" for m, _, _ in fake.calls[start:])


def test_apply_changes_only_day_and_receipt_and_is_idempotent(exit_case):
    store, c, fake, *_ = exit_case
    before = store.read("DEMO-test")
    token = review(exit_case)["review_token"]
    operation = uuid4()
    assert (
        apply(exit_case, token, operation=operation)
        == "EXIT_ANCHORED_DAILY_BASELINE_ADOPTED_KEEP_LOCK"
    )
    after = store.read("DEMO-test")
    exclude = {
        "daily_key",
        "daily_base",
        "exit_anchored_day_baselines",
        "operations",
        "reconciled",
        "stream_ready",
    }
    assert before.model_dump(exclude=exclude) == after.model_dump(exclude=exclude)
    assert after.daily_key == "2026-10-21" and after.daily_base == before.value.equity
    receipt = after.exit_anchored_day_baselines[str(operation)]
    assert receipt.observation_utc == before.last_reconciled
    assert receipt.known_exit_utc < receipt.boundary_utc <= receipt.observation_utc
    assert receipt.old_daily_key == before.daily_key and receipt.old_daily_base == before.daily_base
    assert receipt.source == SOURCE and receipt.account_wide_exchange_export_verified is False
    assert after.risk_state.value == "EMERGENCY" and after.paused
    count = len(fake.calls)
    assert apply(exit_case, token, operation=operation) == "ALREADY_RECORDED_NO_ACTION"
    assert len(fake.calls) == count
    with store.engine.connect() as conn:
        payloads = (
            conn.execute(
                select(events.c.payload).where(
                    events.c.kind == "DEMO_OPERATOR_EXIT_ANCHORED_DAY_BASELINE"
                )
            )
            .scalars()
            .all()
        )
    assert len(payloads) == 2
    assert all(token in payload and SOURCE in payload for payload in payloads)


@pytest.mark.parametrize("attest,accept", [(False, True), (True, False)])
def test_explicit_manual_statement_and_acceptance_required(exit_case, attest, accept):
    store, c, fake, *_ = exit_case
    before = store.read("DEMO-test")
    count = len(fake.calls)
    with pytest.raises(ExitDayBlocked):
        apply(exit_case, "a" * 64, attest=attest, accept=accept)
    assert store.read("DEMO-test") == before and len(fake.calls) == count


def test_preview_without_statement_cannot_query(exit_case):
    store, c, fake, *_ = exit_case
    count = len(fake.calls)
    with pytest.raises(ExitDayBlocked, match="ATTESTATION"):
        review(exit_case, attest=False)
    assert len(fake.calls) == count


@pytest.mark.parametrize("delta", [0, 1])
def test_exit_at_or_after_midnight_is_not_flat_midnight_proof(exit_case, delta):
    store, c, fake, rows, incomes, *_ = exit_case
    boundary = datetime.fromisoformat(review(exit_case)["boundary_utc"])
    stamp = int(boundary.timestamp() * 1000) + delta
    rows[-1]["time"] = stamp
    for r in incomes:
        r["time"] = stamp
    with pytest.raises(ExitDayBlocked, match="LATEST_EXIT_NOT_BEFORE_MIDNIGHT"):
        review(exit_case)


@pytest.mark.parametrize(
    "kind,amount,asset",
    [
        ("TRANSFER", "1", "USDT"),
        ("COMMISSION", "0", "USDT"),
        ("FUNDING_FEE", "-1", "USDT"),
        ("TRANSFER", "1", "USDC"),
    ],
)
def test_any_post_exit_income_or_unsupported_asset_blocks(exit_case, kind, amount, asset):
    store, c, fake, rows, incomes, *_ = exit_case
    incomes.append(
        dict(
            asset=asset,
            incomeType=kind,
            symbol="BTCUSDT",
            time=rows[-1]["time"] + 1,
            tranId="999",
            income=amount,
        )
    )
    expected = DemoFailure if asset == "USDC" else ExitDayBlocked
    message = "Unexpected account transfer asset" if asset == "USDC" else "POST_EXIT_INCOME_OR_FLOW"
    with pytest.raises(expected, match=message):
        review(exit_case)


def test_net_zero_post_exit_records_are_not_treated_as_empty(exit_case):
    store, c, fake, rows, incomes, *_ = exit_case
    for i, amount in enumerate(["1", "-1"]):
        incomes.append(
            dict(
                asset="USDT",
                incomeType="TRANSFER",
                symbol="",
                time=rows[-1]["time"] + 1,
                tranId=str(2000 + i),
                income=amount,
            )
        )
    with pytest.raises(ExitDayBlocked, match="POST_EXIT_INCOME_OR_FLOW"):
        review(exit_case)


@pytest.mark.parametrize("change", ["same_ms_trade", "later_trade", "fee", "pnl", "exit_qty"])
def test_signed_contradictions_override_operator_statement(exit_case, change):
    store, c, fake, rows, incomes, *_ = exit_case
    if change in {"same_ms_trade", "later_trade"}:
        row = dict(rows[-1])
        row.update(id=999, orderId=9999, time=row["time"] + (change == "later_trade"))
        rows.append(row)
    elif change == "fee":
        incomes[0]["income"] = "-0.01"
    elif change == "pnl":
        incomes[1]["income"] = "-0.01"
    else:
        key = next(k for k, row in fake.orders.items() if row["type"] == "MARKET")
        fake.orders[key]["executedQty"] = "0.00001"
    with pytest.raises(ExitDayBlocked):
        review(exit_case)


@pytest.mark.parametrize(
    "change",
    [
        "weekly",
        "deposit",
        "seen",
        "daily_loss",
        "drawdown",
        "high_water",
        "local_position",
        "timestamp",
        "future_day",
    ],
)
def test_ineligible_local_context_never_changes_basis(exit_case, change):
    store, c, fake, *_ = exit_case

    def mutate(ledger):
        if change == "weekly":
            ledger.weekly_key = "2000-01-01"
        elif change == "deposit":
            ledger.net_transfers = Decimal(1)
        elif change == "seen":
            ledger.income_seen["unknown"] = "1"
        elif change == "daily_loss":
            ledger.daily_base = Decimal("100000")
        elif change == "drawdown":
            ledger.high_water = Decimal("100000")
        elif change == "high_water":
            ledger.high_water = Decimal(1)
        elif change == "local_position":
            ledger.positions["BTCUSDT"] = Decimal("0.001")
        elif change == "timestamp":
            ledger.last_reconciled = fake.at - timedelta(seconds=1)
        elif change == "future_day":
            ledger.daily_key = "2099-01-01"

    store.change(c.lease, "TEST_INELIGIBLE", mutate)
    before = store.read("DEMO-test")
    with pytest.raises(ExitDayBlocked):
        review(exit_case)
    assert store.read("DEMO-test") == before


@pytest.mark.parametrize("change", ["balance", "asset_missing", "unrealized", "multi_asset_sum"])
def test_wallet_and_asset_scope_must_match(exit_case, change):
    store, c, fake, rows, incomes, raw = exit_case
    if change == "balance":
        fake.wallet += Decimal(1)
    elif change == "asset_missing":
        raw["assets"] = []
    elif change == "unrealized":
        raw["totalUnrealizedProfit"] = "1"
    else:
        raw["totalWalletBalance"] = str(fake.wallet + Decimal(5000))
    with pytest.raises(ExitDayBlocked):
        review(exit_case)


def test_changed_preview_or_account_cannot_apply(exit_case):
    store, c, fake, *_ = exit_case
    token = review(exit_case, account="DEMO-other")["review_token"]
    before = store.read("DEMO-test")
    with pytest.raises(ExitDayBlocked, match="DAY_PREVIEW_CHANGED"):
        apply(exit_case, token)
    assert store.read("DEMO-test") == before


def test_expired_or_replaced_leader_cannot_apply(exit_case):
    store, c, fake, *_ = exit_case
    token = review(exit_case)["review_token"]
    fake.at += timedelta(seconds=61)
    store.acquire("DEMO-test", uuid4())
    before = store.read("DEMO-test")
    with pytest.raises(RiskRejected):
        apply(exit_case, token)
    assert store.read("DEMO-test") == before


def test_day_rollover_and_operation_conflict_block(exit_case):
    store, c, fake, *_ = exit_case
    token = review(exit_case)["review_token"]
    op = uuid4()
    store.change(
        c.lease, "TEST_CONFLICT", lambda ledger: ledger.operations.update({str(op): "OTHER"})
    )
    with pytest.raises(ExitDayBlocked, match="CONFLICT"):
        apply(exit_case, token, operation=op)
    fake.at += timedelta(days=1)
    store.release(c.lease)
    c.lease = store.acquire("DEMO-test", uuid4())
    with pytest.raises(ExitDayBlocked, match="EXIT_DAY_CONTEXT_MISMATCH"):
        apply(exit_case, token)


def test_cli_preview_has_no_lease_or_writes_and_clears_keys(exit_case, monkeypatch, capsys):
    import os

    import scripts.review_demo_exit_day as cli

    store, c, fake, *_ = exit_case
    original_read = store.read
    before = original_read("DEMO-test")
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr("sys.argv", ["review", "--attest-no-activity-since-exit"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda settings, credentials: c.client)

    def no_write(*args):
        raise AssertionError("preview must not write or acquire")

    monkeypatch.setattr(store, "acquire", no_write)
    monkeypatch.setattr(store, "change", no_write)
    cli.main()
    output = capsys.readouterr().out
    assert "EXIT_ANCHORED_DAY_PREVIEW_NO_DATABASE_CHANGE" in output
    assert SOURCE in output and "SYNTHETIC_PRIVATE_KEY" not in output
    assert "BINANCE_DEMO_API_KEY" not in os.environ
    assert "BINANCE_DEMO_API_SECRET" not in os.environ
    assert original_read("DEMO-test") == before


def test_cli_rejects_missing_acceptance_before_credentials(monkeypatch):
    import scripts.review_demo_exit_day as cli

    monkeypatch.setattr(
        "sys.argv",
        [
            "review",
            "--attest-no-activity-since-exit",
            "--apply-review",
            "a" * 64,
            "--operation-id",
            str(uuid4()),
        ],
    )

    def no_keys(*args):
        raise AssertionError("invalid arguments must not prompt")

    monkeypatch.setattr(cli.getpass, "getpass", no_keys)
    with pytest.raises(SystemExit):
        cli.main()


def test_original_pre_midnight_anchor_path_stays_blocked(exit_case):
    from services.execution.demo_day_reconstruction import (
        DayReconstructionBlocked,
        review_day_reconstruction,
    )

    store, c, *_ = exit_case
    with pytest.raises(DayReconstructionBlocked, match="ATTESTED_DAY_CONTEXT_MISMATCH"):
        review_day_reconstruction(
            store,
            c.client,
            store.read("DEMO-test"),
            account_id="DEMO-test",
            attest_no_other_trades=True,
        )
    assert review(exit_case)["source"] == SOURCE


@pytest.mark.parametrize(
    "field,value",
    [
        ("side", "SELL"),
        ("reduceOnly", True),
        ("clientOrderId", "wrong"),
        ("origQty", "999"),
    ],
)
def test_entry_identity_disagreement_blocks(exit_case, field, value):
    store, c, fake, *_ = exit_case
    order = next(o for o in store.read("DEMO-test").orders.values() if o.filled)
    fake.orders[order.client_id][field] = value
    with pytest.raises(ExitDayBlocked):
        review(exit_case)


def test_unknown_protection_state_blocks(exit_case):
    store, c, fake, *_ = exit_case
    next(iter(fake.algos.values()))["algoStatus"] = "UNKNOWN"
    with pytest.raises(ExitDayBlocked, match="ACCOUNT_OR_DAY_CHANGED"):
        review(exit_case)


def test_multiple_income_records_cannot_hide_net_zero_activity_at_exit(exit_case):
    store, c, fake, rows, incomes, *_ = exit_case
    for i, v in enumerate(["1", "-1"]):
        incomes.append(
            dict(
                asset="USDT",
                incomeType="REALIZED_PNL",
                symbol="BTCUSDT",
                time=rows[-1]["time"],
                tranId=str(4000 + i),
                income=v,
            )
        )
    with pytest.raises(ExitDayBlocked, match="EXIT_INCOME_DOES_NOT_MATCH_FILLS"):
        review(exit_case)


def test_lease_expiry_during_review_rolls_back_receipt_and_baseline(exit_case, monkeypatch):
    store, c, fake, *_ = exit_case
    token = review(exit_case)["review_token"]
    before = store.read("DEMO-test")
    count = event_count(store)
    original = store.change

    def change(lease, kind, callback):
        def complete(ledger):
            result = callback(ledger)
            fake.at += timedelta(seconds=61)
            return result

        return original(lease, kind, complete)

    monkeypatch.setattr(store, "change", change)
    with pytest.raises(RiskRejected):
        apply(exit_case, token)
    assert store.read("DEMO-test") == before and event_count(store) == count


def test_changing_signed_history_blocks_without_writing(exit_case):
    store, c, fake, rows, *_ = exit_case
    before = store.read("DEMO-test")
    transport = c.client.send
    calls = []

    def send(method, path, query, key):
        result = transport(method, path, query, key)
        if path == "/fapi/v1/userTrades":
            calls.append(path)
            if len(calls) == 2:
                rows[-1]["price"] = "101"
        return result

    c.client.send = send
    with pytest.raises(ExitDayBlocked, match="HISTORY_CHANGED"):
        review(exit_case)
    assert store.read("DEMO-test") == before
