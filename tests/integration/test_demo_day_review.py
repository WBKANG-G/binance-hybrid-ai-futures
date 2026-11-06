"""Day evidence is GET-only and never mistakes a wallet candidate for equity proof."""

from decimal import Decimal as D

import pytest

from services.execution.demo_day_review import DayReviewBlocked, review_day
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_recovery import recovery  # noqa: F401


def row(fake, kind, amount, ident):
    return {
        "asset": "USDT",
        "incomeType": kind,
        "income": amount,
        "tranId": ident,
        "time": int(fake.at.timestamp() * 1000) - 1,
        "symbol": "BTCUSDT",
    }


def test_day_review_reports_candidate_without_approving_or_writing(recovery):  # noqa: F811
    store, c, fake = recovery
    fake.incomes = [row(fake, "COMMISSION", "-0.1", 1), row(fake, "TRANSFER", "100", 2)]
    before = store.read("DEMO-test").model_dump_json()
    start = len(fake.calls)
    report = review_day(store, c.client, store.read("DEMO-test"))
    assert D(report["wallet_candidate_usdt_not_approved"]) == fake.wallet - D("99.9")
    assert report["income_counts"] == {"COMMISSION": 1, "TRANSFER": 1}
    assert report["recovery_eligible"] is False
    assert report["boundary_unrealized_pnl_verified"] is False
    assert report["boundary_net_deposit_basis_verified"] is False
    assert report["orders_enabled"] is False
    assert all(m == "GET" for m, _, _ in fake.calls[start:])
    assert store.read("DEMO-test").model_dump_json() == before


def test_empty_income_does_not_prove_midnight_flat(recovery):  # noqa: F811
    store, c, fake = recovery
    report = review_day(store, c.client, store.read("DEMO-test"))
    assert report["income_counts"] == {}
    assert D(report["wallet_candidate_usdt_not_approved"]) == fake.wallet
    assert report["recovery_eligible"] is False


def test_changed_income_window_blocks_without_write(recovery):  # noqa: F811
    store, c, fake = recovery
    original = c.client.send
    count = 0

    def changing(method, path, query, key):
        nonlocal count
        if path == "/fapi/v1/income":
            from urllib.parse import parse_qs

            params = parse_qs(query.get_secret_value())
            # Only mutate the explicit day window, not snapshot transfer pagination.
            if int(params["startTime"][0]) < int(fake.at.timestamp() * 1000) - 1000:
                count += 1
                if count == 2:
                    return fake.response([row(fake, "COMMISSION", "-0.1", 1)])
        return original(method, path, query, key)

    c.client.send = changing
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(DayReviewBlocked, match="INCOME_WINDOW_CHANGED"):
        review_day(store, c.client, store.read("DEMO-test"))
    assert store.read("DEMO-test").model_dump_json() == before


def test_current_position_blocks_evidence(recovery):  # noqa: F811
    store, c, fake = recovery
    fake.positions["BTCUSDT"] = D("0.1")
    fake.prices["BTCUSDT"] = D("100")
    before = store.read("DEMO-test").model_dump_json()
    with pytest.raises(DayReviewBlocked, match="CURRENT_EXPOSURE_OR_OPEN_ORDER"):
        review_day(store, c.client, store.read("DEMO-test"))
    assert store.read("DEMO-test").model_dump_json() == before


@pytest.mark.parametrize("exact", [True, False])
def test_existing_midnight_records_are_scoped_and_never_auto_applied(recovery, exact):  # noqa: F811
    from datetime import datetime, time, timedelta
    from uuid import uuid4
    from zoneinfo import ZoneInfo

    from sqlalchemy import insert

    from packages.persistence.demo import events
    from services.execution.demo_day_review import exact_boundary_records

    store, c, fake = recovery
    boundary = datetime.combine(
        fake.at.astimezone(ZoneInfo("Asia/Seoul")).date(), time(), ZoneInfo("Asia/Seoul")
    )
    recorded = store.read("DEMO-test").model_copy(deep=True)
    recorded.value = recorded.value.model_copy(
        update={"as_of": boundary if exact else boundary + timedelta(microseconds=1)}
    )
    recorded.last_reconciled = recorded.value.as_of
    recorded.reconciled = True
    with store.engine.begin() as conn:
        conn.execute(
            insert(events).values(
                event_id=str(uuid4()),
                account_id="DEMO-test",
                fence=c.lease.fence,
                as_of=boundary + timedelta(seconds=1),
                kind="TEST_BOUNDARY_EVIDENCE",
                payload=recorded.model_dump_json(),
            )
        )
    before = store.read("DEMO-test").model_dump_json()
    assert exact_boundary_records(store, "DEMO-test", boundary, 10) == int(exact)
    assert exact_boundary_records(store, "DEMO-other", boundary, 10) == 0
    report = review_day(store, c.client, store.read("DEMO-test"), account_id="DEMO-test")
    assert report["exact_boundary_record_count"] == int(exact)
    assert report["recovery_eligible"] is False
    assert store.read("DEMO-test").model_dump_json() == before


def test_day_cli_collects_only_reads_and_keeps_credentials_out_of_output(
    recovery,  # noqa: F811
    monkeypatch,
    capsys,
):  # noqa: F811
    import os
    import sys

    import scripts.review_demo_day_boundary as cli

    store, c, fake = recovery
    before = store.read("DEMO-test").model_dump_json()
    start = len(fake.calls)
    monkeypatch.setattr(sys, "argv", ["review_demo_day_boundary"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_INPUT")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda settings, credentials: c.client)
    original_read = store.read
    monkeypatch.setattr(store, "read", lambda account: original_read("DEMO-test"))

    def no_acquire(*args):
        raise AssertionError("Evidence must never acquire a lease")

    monkeypatch.setattr(store, "acquire", no_acquire)
    cli.main()
    output = capsys.readouterr().out
    assert "DAY_BOUNDARY_EVIDENCE_ONLY" in output
    assert "SYNTHETIC_PRIVATE_INPUT" not in output
    assert "BINANCE_DEMO_API_KEY" not in os.environ and "BINANCE_DEMO_API_SECRET" not in os.environ
    assert original_read("DEMO-test").model_dump_json() == before
    assert all(method == "GET" for method, _, _ in fake.calls[start:])
