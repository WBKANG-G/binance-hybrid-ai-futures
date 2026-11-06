import json
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoFailure, transport
from scripts import check_demo_tp_trigger as cli


def argv():
    return [
        "check",
        "--enable-demo",
        "--enable-tp-trigger-observation",
        "--operation-id",
        str(uuid4()),
        "--entry-style",
        "OPPOSITE_QUOTE",
        "--enable-opposite-quote-tp-observation",
    ]


@pytest.mark.parametrize(
    "extra",
    [
        ["--enable-pooled-read-tp-observation"],
        ["--enable-pooled-read-tp-observation", "--enable-opposite-quote-tp-observation"],
    ],
)
def test_pooled_flag_without_complete_opposite_scope_blocks_before_keys(monkeypatch, extra):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check",
            "--enable-demo",
            "--enable-tp-trigger-observation",
            "--operation-id",
            str(uuid4()),
            *extra,
        ],
    )
    monkeypatch.setattr(cli, "credentials", lambda **_: pytest.fail("no keys"))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize("close_failure", [False, True])
def test_route_is_closed_and_source_restored_after_early_cli_failure(
    monkeypatch, capsys, close_failure
):
    monkeypatch.setattr(sys, "argv", [*argv(), "--enable-pooled-read-tp-observation"])
    monkeypatch.setattr(cli, "credentials", lambda **_: object())

    def fail():
        raise DemoFailure("SYNTHETIC_CLOCK_FAILURE")

    source = SimpleNamespace(send=transport, sync_clock=fail)
    monkeypatch.setattr(cli, "TPTriggerClient", lambda *a, **k: source)
    closed = []

    class Route:
        def __init__(self, original):
            self.original = original

        def close(self):
            closed.append(True)
            if close_failure:
                raise ValueError("PRIVATE_CLOSE_DETAIL")

        def diagnostics(self):
            return {"closed": True}

    monkeypatch.setattr(cli, "TPReadRouting", Route)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2 and closed == [True] and source.send is transport
    out = capsys.readouterr().out
    assert "PRIVATE_CLOSE_DETAIL" not in out
    if close_failure:
        assert json.loads(out)["failure"] == "TP_READ_TRANSPORT_CLEANUP_FAILED"


def test_default_keeps_custom_transport_without_constructing_pool(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", argv())
    monkeypatch.setattr(cli, "credentials", lambda **_: object())
    original = object()

    def fail():
        raise DemoFailure("SYNTHETIC_EARLY_FAILURE")

    source = SimpleNamespace(send=original, sync_clock=fail)
    monkeypatch.setattr(cli, "TPTriggerClient", lambda *a, **k: source)
    monkeypatch.setattr(cli, "TPReadRouting", lambda *_: pytest.fail("opt-in required"))
    with pytest.raises(SystemExit):
        cli.main()
    assert source.send is original
    assert "tp_read_transport_diagnostic" not in json.loads(capsys.readouterr().out)


def test_opt_in_rejects_custom_source_before_transport_or_db(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [*argv(), "--enable-pooled-read-tp-observation"])
    monkeypatch.setattr(cli, "credentials", lambda **_: object())
    source = SimpleNamespace(send=object())
    monkeypatch.setattr(cli, "TPTriggerClient", lambda *a, **k: source)
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: pytest.fail("no DB"))
    monkeypatch.setattr(cli, "TPReadRouting", lambda *_: pytest.fail("no pool"))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert (
        error.value.code == 2
        and json.loads(capsys.readouterr().out)["tp_trigger_check"] == "BLOCKED"
    )


def test_engine_dispose_failure_still_closes_pool_and_redacts_details(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [*argv(), "--enable-pooled-read-tp-observation"])
    monkeypatch.setattr(cli, "credentials", lambda **_: object())
    source = SimpleNamespace(send=transport, sync_clock=lambda: None)
    monkeypatch.setattr(cli, "TPTriggerClient", lambda *a, **k: source)
    closed = []

    class Engine:
        def connect(self):
            raise DemoFailure("SYNTHETIC_READ_FAILURE")

        def dispose(self):
            raise ValueError("PRIVATE_ENGINE_DETAIL")

    class Route:
        def __init__(self, original):
            self.original = original

        def close(self):
            closed.append(True)

        def diagnostics(self):
            return {"closed": True}

    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: Engine())
    monkeypatch.setattr(cli, "TPReadRouting", Route)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2 and closed == [True] and source.send is transport
    out = capsys.readouterr().out
    assert "PRIVATE_ENGINE_DETAIL" not in out
    assert json.loads(out)["failure"] == "ENGINE_DISPOSE_FAILED"
