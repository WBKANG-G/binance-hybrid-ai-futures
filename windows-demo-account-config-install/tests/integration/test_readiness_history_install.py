"""Pre/post source-and-account binding for the readiness-only update."""

import importlib.util
from pathlib import Path

import pytest

from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_tp_rate_install_guard import closed  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "readiness_guard", Path("guard_readiness_history_install.py")
)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
# The packaged independent fixture lives alongside this guard for reproducibility.
spec2 = importlib.util.spec_from_file_location(
    "guard_probe_fixture", Path("tests/fixtures/closed_history_probe.py")
)
p = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(p)
setup = p.setup


def bind(setup, monkeypatch):  # noqa: F811
    store, old, _, opt, fake, start = setup
    monkeypatch.setattr(g, "ACCOUNT", "DEMO-test")
    from integrations.binance_usdm.demo_read_routing import TPReadRouting

    class Pool:
        def __call__(self, *args):
            return old.send.route(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    route = TPReadRouting(old.send.route, pool=Pool())
    monitor = g.Monitor(route)
    client = g.ReadClient(old.settings, old.credentials, send=monitor, clock_ms=old.clock_ms)
    monitor.server_ms = lambda: client.clock_ms() + client.offset
    return store, client, monitor


def test_pre_post_keep_same_digest_and_use_installed_view(setup, monkeypatch):  # noqa: F811
    store, c, monitor = bind(setup, monkeypatch)
    r = g.compare(store, c, monitor, "pre")
    # New bounded transport per invocation, as in actual separate Docker runs.
    store, c, monitor = bind(setup, monkeypatch)
    post = g.compare(store, c, monitor, "post", r["account_digest"])
    assert post["readiness_history_install"] == "POSTCHECKED_READ_ONLY_READY_KEEP_PAUSED"
    assert (
        post["account_digest"] == r["account_digest"] and post["candidate_pair_signed_reads"] == 22
    )
    assert post["installation_postcheck_passed"] and not post["active_tp_cost_reduction_proven"]


def test_wrong_pre_digest_blocks_before_exchange(setup, monkeypatch):  # noqa: F811
    store, c, monitor = bind(setup, monkeypatch)
    with pytest.raises(g.Blocked, match="PRE_POST_ACCOUNT_CHANGED"):
        g.compare(store, c, monitor, "post", "0" * 64)
    assert not monitor.records


def test_post_refuses_installed_view_that_does_not_omit_reviewed_record(setup, monkeypatch):  # noqa: F811
    from services.execution import readiness_closed_history as installed

    store, c, monitor = bind(setup, monkeypatch)
    r = g.compare(store, c, monitor, "pre")
    store, c, monitor = bind(setup, monkeypatch)
    monkeypatch.setattr(installed, "readiness_view", lambda cfg, s, now: (s, 0))
    with pytest.raises(g.Blocked, match="INSTALLED_READINESS_VIEW_NOT_MATCHED"):
        g.compare(store, c, monitor, "post", r["account_digest"])


def test_current_new_sources_pass_pre_and_post():
    g.verify_sources("pre")
    g.verify_sources("post")


def test_old_sources_only_allowed_before_install_and_mixed_sources_rejected():
    g.verify_sources("pre", dict(g.OLD_PINS))
    for phase in ("pre", "post"):
        g.verify_sources(phase, dict(g.NEW_PINS))
    with pytest.raises(g.Blocked):
        g.verify_sources("post", dict(g.OLD_PINS))
    mixed = dict(g.NEW_PINS)
    mixed["scripts/check_demo_session_ready.py"] = g.OLD_PINS["scripts/check_demo_session_ready.py"]
    for phase in ("pre", "post"):
        with pytest.raises(g.Blocked):
            g.verify_sources(phase, mixed)


def test_post_missing_digest_does_not_access_database_or_keys(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["guard", "--phase", "post"])
    monkeypatch.setattr(g, "create_engine", lambda *a, **k: pytest.fail("no database"))
    monkeypatch.setattr(g, "credentials", lambda **k: pytest.fail("no keys"))
    with pytest.raises(SystemExit) as error:
        g.main()
    import json

    assert (
        error.value.code == 2
        and json.loads(capsys.readouterr().out)["stop_reason"] == "PRECHECK_DIGEST_REQUIRED"
    )
