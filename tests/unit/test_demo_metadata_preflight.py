"""Malformed public filters must not prevent observation or permit writes."""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.execution.demo import DemoCoordinator
from services.risk.paper import RiskRejected
from tests.fixtures.binance import exchange_info


def coordinator(enabled=False):
    info = deepcopy(exchange_info())
    bad = deepcopy(info["symbols"][0])
    bad["symbol"] = "TUSDT"
    for f in bad["filters"]:
        if f["filterType"] == "LOT_SIZE":
            f["stepSize"] = "0"
    info["symbols"].append(bad)
    settings = SimpleNamespace(
        policy="synthetic", demo_trading_enabled=enabled, margin_mode="CROSSED", symbols=()
    )
    client = SimpleNamespace(settings=settings, public=lambda path: info)
    store = SimpleNamespace(
        read=lambda account: SimpleNamespace(policy="synthetic", margin_mode="CROSSED", symbols=())
    )
    return DemoCoordinator(store, SimpleNamespace(account_id="DEMO-test"), client), info


def test_read_only_reports_invalid_symbol_without_weakening_valid_rules():
    c, info = coordinator()
    c.refresh_metadata()
    assert c.excluded_symbols == {"TUSDT": "INVALID_SYMBOL_FILTERS"}
    assert "TUSDT" not in c.rules
    assert info["symbols"][0]["symbol"] in c.rules
    assert c.rules[info["symbols"][0]["symbol"]].step_size > 0


def test_write_enabled_metadata_never_admits_excluded_symbol():
    c, info = coordinator(enabled=True)
    c.refresh_metadata()
    assert "TUSDT" not in c.rules
    assert info["symbols"][0]["symbol"] in c.rules


@pytest.mark.parametrize("kind", ["position", "order", "algo", "local"])
def test_excluded_symbol_exposure_blocks_before_merge(kind):
    c, _ = coordinator(enabled=True)
    c.refresh_metadata()
    remote = SimpleNamespace(positions={}, orders={}, algos={})
    ledger = SimpleNamespace(orders={})
    if kind == "position":
        remote.positions["TUSDT"] = Decimal("1")
    elif kind == "order":
        remote.orders["id"] = {"symbol": "TUSDT", "status": "NEW"}
    elif kind == "algo":
        remote.algos["id"] = {"symbol": "TUSDT", "algoStatus": "NEW"}
    else:
        ledger.orders["id"] = SimpleNamespace(
            signal=SimpleNamespace(symbol="TUSDT"), remaining=1, open_quantity=0
        )
    with pytest.raises(RiskRejected, match="Excluded DEMO symbol has exposure"):
        c._merge(ledger, remote)


def test_flat_excluded_symbol_does_not_block_other_symbols():
    c, _ = coordinator(enabled=True)
    c.refresh_metadata()
    c._check_metadata_exposure(
        SimpleNamespace(orders={}),
        SimpleNamespace(positions={"TUSDT": Decimal(0)}, orders={}, algos={}),
    )


def test_corrected_metadata_removes_stale_exclusions():
    c, info = coordinator()
    c.refresh_metadata()
    info["symbols"].pop()
    c.client.settings.demo_trading_enabled = True
    c.refresh_metadata()
    assert not c.excluded_symbols
    assert c.rules


def test_excluded_symbol_cannot_reserve_an_entry():
    from tests.integration.test_paper import signal, snapshot

    c, _ = coordinator(enabled=True)
    c.refresh_metadata()
    with pytest.raises(RiskRejected, match="symbol metadata unavailable"):
        c.reserve(signal(symbol="TUSDT", environment="DEMO"), snapshot())
