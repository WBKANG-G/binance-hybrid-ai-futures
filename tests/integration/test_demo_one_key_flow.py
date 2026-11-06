"""One hidden pair drives preview, apply, and later readonly readiness in real PG."""

import json
import sys
from uuid import uuid4

import pytest

from scripts import check_demo_session_ready as ready
from scripts import demo_key_session as keys
from scripts import review_demo_paused_day as day
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.integration.test_demo_paused_day import case  # noqa: F401


@pytest.fixture
def binding(case, tmp_path, monkeypatch):  # noqa: F811
    store, c, *_ = case
    store.release(c.lease)
    root = tmp_path / "ram"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(keys, "DIRECTORY", root)
    monkeypatch.setattr(keys, "_tmpfs", lambda: True)
    answers = iter(["SYNTHETIC_ONE_KEY", "SYNTHETIC_ONE_SECRET"])
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: next(answers))
    for cli in [day, ready]:
        monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: store.engine)
        monkeypatch.setattr(cli, "DemoStore", lambda *a, **kw: store)

        def client(config, credential):
            assert credential.api_key.get_secret_value() == "SYNTHETIC_ONE_KEY"
            assert credential.secret.get_secret_value() == "SYNTHETIC_ONE_SECRET"
            return c.client

        monkeypatch.setattr(cli, "DemoClient", client)
    monkeypatch.setattr(day, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(ready, "ACCOUNT", "DEMO-test")
    return store, c, root


def test_first_preview_registers_then_apply_and_readiness_never_prompt(
    binding, monkeypatch, capsys
):
    store, c, root = binding
    monkeypatch.setattr(sys, "argv", ["review", "--attest-no-other-trades-since-exit"])
    day.main()
    report = json.loads(capsys.readouterr().out)
    original = (root / keys.FILENAME).read_bytes()
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: pytest.fail("must not re-enter keys"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review",
            "--attest-no-other-trades-since-exit",
            "--accept-operator-evidence",
            "--apply-review",
            report["review_token"],
            "--operation-id",
            str(uuid4()),
        ],
    )
    day.main()
    result = json.loads(capsys.readouterr().out)
    assert result["day_baseline"] == "OPERATOR_ATTESTED_DAY_BASELINE_ADOPTED_ENTRIES_STILL_PAUSED"
    before = store.read("DEMO-test")
    ready.main()
    after = json.loads(capsys.readouterr().out)
    assert after["session_ready"] == "READ_ONLY_READY_ENTRIES_STILL_PAUSED"
    assert after["credential_source"] == "PRIVATE_TMPFS_SESSION" and after["period_keys_match"]
    assert store.read("DEMO-test") == before and (root / keys.FILENAME).read_bytes() == original
    assert "SYNTHETIC_ONE" not in json.dumps(after)


def test_readiness_date_mismatch_is_readonly_and_does_not_prompt(binding, monkeypatch, capsys):
    store, c, root = binding
    keys.ensure()
    before = store.read("DEMO-test")
    monkeypatch.setattr(keys.getpass, "getpass", lambda *a: pytest.fail("must not prompt"))
    with pytest.raises(SystemExit):
        ready.main()
    result = json.loads(capsys.readouterr().out)
    assert result["failure"] == "PERIOD_BASELINE_MISSING"
    assert store.read("DEMO-test") == before
