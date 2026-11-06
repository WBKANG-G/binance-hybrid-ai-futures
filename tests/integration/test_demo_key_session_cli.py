"""Registered RAM credentials drive existing review CLIs without extra prompts."""

import json
import os
import sys
from uuid import uuid4

import pytest

from scripts import demo_key_session as keys
from services.execution.demo_period_review import apply_periods, review_periods
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction, event_count  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.integration.test_demo_period_review import period_case  # noqa: F401


@pytest.mark.parametrize("which", ["periods", "recovery"])
def test_registered_pair_skips_hidden_prompts_in_real_pg_review_cli(
    period_case,  # noqa: F811
    tmp_path,
    monkeypatch,
    capsys,
    which,  # noqa: F811
):
    import scripts.review_demo_periods as periods
    import scripts.review_demo_recovery as recovery

    store, c, fake, *_ = period_case
    if which == "recovery":
        report = review_periods(
            store,
            c.client,
            store.read("DEMO-test"),
            account_id="DEMO-test",
            attest_no_activity=True,
        )
        apply_periods(
            store,
            c.lease,
            c.client,
            uuid4(),
            report["review_token"],
            attest_no_activity=True,
            accept_operator_evidence=True,
        )
    before = store.read("DEMO-test")
    count = event_count(store)
    start = len(fake.calls)
    root = tmp_path / "keys"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(keys, "DIRECTORY", root)
    monkeypatch.setattr(keys, "_tmpfs", lambda: True)
    for name in ("BINANCE_DEMO_API_KEY", "BINANCE_DEMO_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    answers = iter(["SYNTHETIC_REGISTERED_KEY", "SYNTHETIC_REGISTERED_SECRET"])
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: next(answers))
    keys.register()
    monkeypatch.setattr(
        keys.getpass, "getpass", lambda *a: pytest.fail("registered review must not prompt")
    )
    cli = periods if which == "periods" else recovery
    original_read = store.read
    if which == "periods":
        monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
        args = ["review_demo_periods", "--attest-no-activity-since-anchor"]
    else:
        args = ["review_demo_recovery"]
        monkeypatch.setattr(store, "read", lambda account: original_read("DEMO-test"))
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda *a, **k: store)

    def client_factory(config, credentials):
        assert credentials.api_key.get_secret_value() == "SYNTHETIC_REGISTERED_KEY"
        assert credentials.secret.get_secret_value() == "SYNTHETIC_REGISTERED_SECRET"
        return c.client

    monkeypatch.setattr(cli, "DemoClient", client_factory)

    def no_write(*args):
        raise AssertionError("review must not write or acquire lease")

    monkeypatch.setattr(store, "acquire", no_write)
    monkeypatch.setattr(store, "change", no_write)
    monkeypatch.setattr(store, "release", no_write)
    cli.main()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert (
        "OPERATOR_ATTESTED_PERIOD_PREVIEW_NO_DATABASE_CHANGE" in output
        if which == "periods"
        else result["risk_gates"] == "PASS"
    )
    assert "SYNTHETIC_REGISTERED" not in output
    assert original_read("DEMO-test") == before and event_count(store) == count
    assert all(m == "GET" for m, _, _ in fake.calls[start:])
    assert "BINANCE_DEMO_API_KEY" not in os.environ and "BINANCE_DEMO_API_SECRET" not in os.environ
