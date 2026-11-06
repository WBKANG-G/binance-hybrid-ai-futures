"""Explicit opposite-quote TP scope, real PG/local WS, synthetic execution only."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from scripts import check_demo_tp_trigger as cli
from services.execution import demo_tp_trigger_check as module
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import (
    case,  # noqa: F401
    exercise,
)


@pytest.fixture
def opposite_case(case):  # noqa: F811
    store, old, fake, options = case
    c = module.OppositeQuoteTPTriggerCoordinator(store, old.lease, old.client)
    c.refresh_metadata()
    c.reconcile()
    return store, c, fake, options


def exercise_opposite(scenario, monkeypatch, operation=None):
    original = module.run

    async def explicit(c, op):
        return await original(c, op, entry_style="OPPOSITE_QUOTE")

    with monkeypatch.context() as patch:
        patch.setattr(module, "run", explicit)
        return asyncio.run(exercise(scenario, patch, operation))


def test_opposite_minimum_limit_tp_original_sl_cleanup_and_history(opposite_case, monkeypatch):
    store, c, fake, _ = opposite_case
    before = store.read("DEMO-test")
    report = exercise_opposite(opposite_case, monkeypatch)
    assert report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED", (
        report
    )
    assert report["entry_style"] == "OPPOSITE_QUOTE"
    order = store.read("DEMO-test").orders[report["operation_id"]]
    assert order.signal.suggested_limit_price == Decimal("80000.1")
    assert order.signal.target_price == Decimal("80800.2")
    assert order.signal.initial_stop_price == Decimal("79600")
    assert order.quantity == Decimal("0.0007")
    assert report["entry_post_attempts"] == report["new_entry_post_count"] == 1
    assert report["operator_exit_post_attempts"] == 0 and report["sl_delete_attempts"] == 1
    assert report["signed_tp_fill_before_sl_delete_verified"] and report["sl_cancel_query_verified"]
    assert report["history_and_baselines_preserved"]
    assert module.history_preserved(before, store.read("DEMO-test"), report["operation_id"])
    assert posts(fake)[0]["type"] == "LIMIT" and Decimal(posts(fake)[0]["price"]) == Decimal(
        "80000.1"
    )


@pytest.mark.parametrize("mode", ["unfilled", "no_tp", "sl", "bad_qty", "missing_cancel"])
def test_opposite_other_outcomes_not_tp_success_and_original_cleanup(
    opposite_case, monkeypatch, mode
):
    store, _, fake, options = opposite_case
    if mode == "unfilled":
        options.update(fill=False, trigger=False)
    elif mode == "no_tp":
        options["trigger"] = False
    elif mode == "sl":
        options["trigger"] = "SL"
    elif mode == "bad_qty":
        options["tamper"] = True
    else:
        options["cancel_query"] = True
    report = exercise_opposite(opposite_case, monkeypatch)
    assert report["tp_trigger_check"] == "NOT_ACCEPTED"
    assert not any(fake.positions.values()) and store.read("DEMO-test").paused
    assert len([p for p in posts(fake) if p["type"] == "LIMIT"]) == 1
    if mode == "no_tp":
        assert report["operator_exit_post_attempts"] == 1 and report["risk_state"] == "EMERGENCY"


@pytest.mark.parametrize(
    "cap,style,direction",
    [
        ("0.0008", "OPPOSITE_QUOTE", "LONG"),
        (None, "OPPOSITE_QUOTE", "LONG"),
        ("0.0007", "PASSIVE", "LONG"),
        ("0.0007", "OPPOSITE_QUOTE", "SHORT"),
    ],
)
def test_opposite_wrong_scope_rejected_before_write_or_transport(
    opposite_case, cap, style, direction
):
    from services.risk.paper import RiskRejected
    from tests.integration.test_paper import signal, snapshot

    store, c, fake, _ = opposite_case
    sig = signal(
        environment="DEMO",
        purpose="DEMO_VALIDATION",
        strategy=None,
        direction=direction,
        initial_stop_price="110" if direction == "SHORT" else "90",
        target_price="90" if direction == "SHORT" else "120",
    )
    before, count = store.read("DEMO-test"), len(fake.calls)
    with pytest.raises(RiskRejected):
        c.reserve(sig, snapshot(), quantity_cap=Decimal(cap) if cap else None, entry_style=style)
    assert store.read("DEMO-test") == before and len(fake.calls) == count


def test_default_mode_cannot_run_opposite_coordinator_and_reuse_cannot_submit(
    opposite_case, monkeypatch
):
    store, c, fake, _ = opposite_case
    before, count = store.read("DEMO-test"), len(fake.calls)
    with pytest.raises(Exception, match="explicit coordinator"):
        asyncio.run(module.run(c, uuid4()))
    assert store.read("DEMO-test") == before and len(fake.calls) == count
    op = uuid4()
    exercise_opposite(opposite_case, monkeypatch, op)
    count = len(fake.calls)
    with pytest.raises(Exception, match="must not be submitted again"):
        asyncio.run(module.run(c, op, entry_style="OPPOSITE_QUOTE"))
    assert len(fake.calls) == count and len(posts(fake)) == 1


@pytest.mark.parametrize(
    "extra",
    [
        ["--entry-style", "OPPOSITE_QUOTE"],
        ["--enable-opposite-quote-tp-observation"],
        ["--entry-style", "UNKNOWN"],
    ],
)
def test_cli_requires_explicit_paired_flags_before_any_credentials(monkeypatch, extra):
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_demo_tp_trigger",
            "--enable-demo",
            "--enable-tp-trigger-observation",
            "--operation-id",
            str(uuid4()),
            *extra,
        ],
    )

    def forbidden():
        pytest.fail("credentials must not be consulted for invalid flags")

    monkeypatch.setattr(cli, "credentials", forbidden)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2


def test_legacy_coordinator_cannot_enable_opposite_by_keyword(opposite_case):
    store, c, fake, _ = opposite_case
    legacy = module.TPTriggerCoordinator(store, c.lease, c.client)
    before, count = store.read("DEMO-test"), len(fake.calls)
    with pytest.raises(Exception, match="explicit coordinator"):
        asyncio.run(module.run(legacy, uuid4(), entry_style="OPPOSITE_QUOTE"))
    assert store.read("DEMO-test") == before and len(fake.calls) == count


def test_cli_replay_failure_keeps_original_tp_result_and_restores_session(
    opposite_case, monkeypatch, capsys
):
    import sys

    from services.execution import demo_validation

    store, c, fake, _ = opposite_case
    store.release(c.lease)
    operation = uuid4()
    original = demo_validation.session
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_demo_tp_trigger",
            "--enable-demo",
            "--enable-tp-trigger-observation",
            "--operation-id",
            str(operation),
            "--entry-style",
            "OPPOSITE_QUOTE",
            "--enable-opposite-quote-tp-observation",
        ],
    )
    monkeypatch.setattr(cli, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(cli, "credentials", lambda **kw: c.client.credentials)
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda *a, **kw: store)
    monkeypatch.setattr(cli, "TPTriggerClient", lambda *a, **kw: c.client)

    async def result(coordinator, op, *, entry_style):
        assert isinstance(coordinator, module.OppositeQuoteTPTriggerCoordinator)
        assert op == operation and entry_style == "OPPOSITE_QUOTE"
        assert demo_validation.session != original
        return {
            "tp_trigger_check": "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED",
            "failure": "NONE",
        }

    async def unavailable(hints):
        raise ValueError("PRIVATE_RESPONSE_DO_NOT_LOG")

    monkeypatch.setattr(cli, "run", result)
    monkeypatch.setattr(cli, "replay", unavailable)
    cli.main()
    import json

    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
    assert report["hint_capture"]["replay"]["hint_replay"] == "NOT_VERIFIED_NO_AUTOMATIC_RETRY"
    assert "PRIVATE_RESPONSE_DO_NOT_LOG" not in output
    assert demo_validation.session is original
    assert not posts(fake)
