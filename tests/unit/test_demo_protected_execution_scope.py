"""The new harness cannot submit a second entry or mutate unrelated orders."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoClient, DemoCredentials
from scripts import check_demo_protected_worker as cli
from services.execution import demo_protected_worker_check as module
from tests.integration.test_demo import settings


@pytest.fixture
def client(monkeypatch):
    calls = []
    monkeypatch.setattr(DemoClient, "request", lambda self, m, p, v=None: calls.append((m, p, v)))
    c = module.ScopedClient(
        settings(),
        DemoCredentials(SecretStr("synthetic"), SecretStr("synthetic")),
        account="DEMO-test",
        operation=uuid4(),
        entry_enabled=True,
    )
    return c, calls


def entry(c):
    return dict(
        symbol="BTCUSDT",
        side="BUY",
        type="LIMIT",
        quantity="0.0007",
        price="80000",
        newClientOrderId=c.entry_id,
    )


def test_one_entry_is_scoped_and_second_attempt_never_reaches_transport(client):
    c, calls = client
    c.request("POST", "/fapi/v1/order", entry(c))
    with pytest.raises(module.CheckBlocked):
        c.request("POST", "/fapi/v1/order", entry(c))
    assert len(calls) == 1 and c.entry_attempts == 2


@pytest.mark.parametrize(
    "change",
    [
        dict(symbol="ETHUSDT"),
        dict(side="SELL"),
        dict(type="MARKET"),
        dict(quantity="0.0008"),
        dict(quantity="0"),
        dict(quantity="NaN"),
        dict(price="200000"),
        dict(price="NaN"),
        dict(newClientOrderId="foreign"),
    ],
)
def test_bad_entry_stops_before_transport(client, change):
    c, calls = client
    with pytest.raises(module.CheckBlocked):
        c.request("POST", "/fapi/v1/order", entry(c) | change)
    assert not calls


def test_parent_client_cannot_submit_entry(client):
    c, calls = client
    c.entry_enabled = False
    with pytest.raises(module.CheckBlocked):
        c.request("POST", "/fapi/v1/order", entry(c))
    assert not calls


def test_first_generation_own_exit_once(client):
    c, calls = client
    p = dict(
        symbol="BTCUSDT",
        side="SELL",
        type="MARKET",
        reduceOnly="true",
        quantity="0.0007",
        newClientOrderId="DX-" + c.entry_id[3:],
    )
    c.request("POST", "/fapi/v1/order", p)
    with pytest.raises(module.CheckBlocked):
        c.request("POST", "/fapi/v1/order", p)
    assert len(calls) == 1


@pytest.mark.parametrize("suffix", ["-2", "-3", "foreign"])
def test_later_or_foreign_exit_id_forbidden(client, suffix):
    c, calls = client
    p = dict(
        symbol="BTCUSDT",
        side="SELL",
        type="MARKET",
        reduceOnly="true",
        quantity="0.0007",
        newClientOrderId="DX-" + c.entry_id[3:] + suffix,
    )
    with pytest.raises(module.CheckBlocked):
        c.request("POST", "/fapi/v1/order", p)
    assert not calls


@pytest.mark.parametrize(
    "method,path,params",
    [
        ("GET", "/fapi/v1/trade/asyn", {}),
        ("POST", "/fapi/v1/leverage", {"symbol": "BTCUSDT"}),
        ("DELETE", "/fapi/v1/order", {"symbol": "BTCUSDT", "origClientOrderId": "foreign"}),
        ("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": "foreign"}),
        (
            "POST",
            "/fapi/v1/algoOrder",
            {
                "symbol": "BTCUSDT",
                "side": "SELL",
                "type": "STOP_MARKET",
                "closePosition": "true",
                "clientAlgoId": "foreign",
            },
        ),
    ],
)
def test_other_scope_and_export_requests_never_reach_transport(client, method, path, params):
    c, calls = client
    with pytest.raises(module.CheckBlocked):
        c.request(method, path, params)
    assert not calls


def test_parent_cannot_repost_original_protection(client):
    c, calls = client
    c.entry_enabled = False
    p = dict(
        symbol="BTCUSDT",
        side="SELL",
        type="STOP_MARKET",
        closePosition="true",
        clientAlgoId="DS-" + c.entry_id[3:],
    )
    with pytest.raises(module.CheckBlocked):
        c.request("POST", "/fapi/v1/algoOrder", p)
    assert not calls


@pytest.mark.parametrize("mode", ["parent_lost", "eof", "stop", "idle"])
def test_control_pipe_parent_loss_is_cooperative_stop(monkeypatch, mode):
    monkeypatch.setattr(module.os, "getppid", lambda: 1 if mode == "parent_lost" else 123)
    monkeypatch.setattr(
        module.select, "select", lambda *args: ([] if mode == "idle" else [0], [], [])
    )
    monkeypatch.setattr(module.os, "read", lambda *args: b"" if mode == "eof" else b"S")
    assert module.control_stop(123) == (mode != "idle")


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--enable-demo"],
        ["--enable-demo", "--enable-protected-worker-kill"],
        [
            "--enable-demo",
            "--enable-protected-worker-kill",
            "--operation-id",
            str(uuid4()),
            "--worker",
        ],
    ],
)
def test_execution_flags_and_internal_scope_required_before_credentials(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["check"] + args)
    monkeypatch.setattr(
        cli, "credentials", lambda **k: pytest.fail("must reject before credentials")
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_live_context_never_reads_key_or_spawns(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "check",
            "--enable-demo",
            "--enable-protected-worker-kill",
            "--operation-id",
            str(uuid4()),
        ],
    )
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(cli, "credentials", lambda **k: pytest.fail("must not read"))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2 and "DEMO_CONTEXT_REQUIRED" in capsys.readouterr().out


def test_cli_private_failure_is_redacted_and_no_db_started(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "check",
            "--enable-demo",
            "--enable-protected-worker-kill",
            "--operation-id",
            str(uuid4()),
        ],
    )

    def fail(**kwargs):
        assert kwargs == {"prompt": False}
        raise ValueError("synthetic-secret-do-not-print")

    monkeypatch.setattr(cli, "credentials", fail)
    monkeypatch.setattr(
        cli, "create_engine", lambda *a, **k: pytest.fail("no DB before credentials")
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    text = capsys.readouterr().out
    assert "synthetic-secret" not in text and "DETAILS_REDACTED" in text


def test_spawn_passes_only_scope_ids_and_control_pipe_no_new_key_arguments(monkeypatch):
    captured = []

    class Process:
        pid = 999
        stdin = None

        def poll(self):
            return None

    def spawn(args, **kwargs):
        captured.append((args, kwargs))
        return Process()

    monkeypatch.setattr(cli.subprocess, "Popen", spawn)
    operation, owner = uuid4(), uuid4()
    worker = cli.Worker(operation, owner)
    assert worker.pid == 999
    args, kwargs = captured[0]
    assert str(operation) in args and str(owner) in args
    assert "--enable-protected-worker-kill" in args and "--worker" in args
    assert kwargs == {
        "stdin": cli.subprocess.PIPE,
        "stdout": cli.subprocess.DEVNULL,
        "stderr": cli.subprocess.DEVNULL,
    }
    assert not any("SECRET" in str(a) or "API_KEY" in str(a) for a in args)


def test_child_protection_cannot_be_reposted(client):
    c, calls = client
    p = dict(
        symbol="BTCUSDT",
        side="SELL",
        type="STOP_MARKET",
        closePosition="true",
        clientAlgoId="DS-" + c.entry_id[3:],
    )
    c.request("POST", "/fapi/v1/algoOrder", p)
    with pytest.raises(module.CheckBlocked):
        c.request("POST", "/fapi/v1/algoOrder", p)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "changes,cap,style",
    [
        ({}, None, "OPPOSITE_QUOTE"),
        ({}, Decimal("0.0008"), "OPPOSITE_QUOTE"),
        ({}, Decimal("0.0007"), "PASSIVE"),
        ({"symbol": "ETHUSDT"}, Decimal("0.0007"), "OPPOSITE_QUOTE"),
        ({"direction": "SHORT"}, Decimal("0.0007"), "OPPOSITE_QUOTE"),
    ],
)
def test_coordinator_invalid_reservation_stops_before_store_or_request(client, changes, cap, style):
    from tests.integration.test_paper import signal, snapshot

    c, calls = client
    coordinator = object.__new__(module.CappedCoordinator)
    coordinator.client = c
    sig = signal(environment="DEMO", purpose="DEMO_VALIDATION", strategy=None).model_copy(
        update=changes
    )
    with pytest.raises(module.CheckBlocked):
        coordinator.reserve(sig, snapshot(), quantity_cap=cap, entry_style=style)
    assert not calls
