"""The support diagnostic reads local PG and never acquires a lease or sends requests."""

import json

import scripts.diagnose_demo_ledger as diagnostic
from tests.integration.test_demo import (
    enter,
    system,  # noqa: F401
)


def test_local_diagnostic_preserves_ledger_and_never_calls_exchange(system, monkeypatch, capsys):  # noqa: F811
    store, c, fake = system
    sig, _ = enter(system)
    monkeypatch.setattr(diagnostic, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(diagnostic, "OPERATION", str(sig.signal_id))
    monkeypatch.setattr(diagnostic, "create_engine", lambda *a, **k: store.engine)
    before = store.read("DEMO-test").model_dump_json()
    calls = len(fake.calls)
    diagnostic.main()
    lines = capsys.readouterr().out.splitlines()
    report = json.loads(lines[0])
    assert report["diagnostic"] == "LOCAL_DATABASE_READ_ONLY"
    assert report["order"]["state"] == "ACKNOWLEDGED"
    assert report["live_trading_enabled"] is False
    assert len(fake.calls) == calls
    assert store.read("DEMO-test").model_dump_json() == before
