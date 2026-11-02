"""Read-only diagnostic with real PG and the existing signed preview fixture."""
# ruff: noqa: F811

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from packages.persistence.demo import accounts
from sqlalchemy import text, update
from tests.integration.test_demo_paused_period import (
    case,  # noqa: F401
    correction,  # noqa: F401
    day_case,  # noqa: F401
    demo_system,  # noqa: F401
    financial_rows,
    period_case,  # noqa: F401
)

spec = importlib.util.spec_from_file_location(
    "period_diagnostic", Path(__file__).with_name("diagnose_demo_period_token.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("ready", [False, True])
def test_real_pg_diagnostic_has_matching_formula_and_simulation_with_no_lease_or_writes(
    case, ready, monkeypatch
):
    store, c, fake, *_ = case
    if ready:
        lease = store.acquire("DEMO-test", uuid4())

        def flags(ledger):
            ledger.reconciled = ledger.stream_ready = True

        store.change(lease, "TEST_READY_FLAGS", flags)
        store.release(lease)
    before, count = financial_rows(store), len(fake.calls)
    monkeypatch.setattr(store, "acquire", lambda *a, **kw: pytest.fail("diagnosis cannot acquire"))
    original = module.review_paused_periods

    def verified_readonly(*a, **kw):
        with store.engine.connect() as conn:
            assert (
                conn.execute(text("SELECT current_setting('transaction_read_only')")).scalar_one()
                == "on"
            )
        return original(*a, **kw)

    monkeypatch.setattr(module, "review_paused_periods", verified_readonly)
    report = module.diagnose(store, c.client, "DEMO-test")
    assert report["token_formula_reproduces_preview"]
    assert (
        report["simulated_acquire_flags_token_matches"]
        and report["simulated_json_roundtrip_token_matches"]
    )
    assert len(report["observed_token_groups_of_8"]) == 8
    assert "".join(report["observed_token_groups_of_8"]) == report["observed_review_token"]
    assert report["roundtrip_changed_top_level_fields"] == []
    assert report["account_owner_fence_lease_state_unchanged"] and not report["apply_attempted"]
    assert financial_rows(store) == before and all(m == "GET" for m, p, q in fake.calls[count:])
    monkeypatch.setattr(module, "CORRECTED_COMMAND_TOKEN", report["observed_review_token"])
    assert module.diagnose(store, c.client, "DEMO-test")["corrected_command_token_matches"]
    assert financial_rows(store) == before


def test_accidental_sql_mutation_is_blocked_and_financial_rows_unchanged(case, monkeypatch):
    store, c, *_ = case
    before = financial_rows(store)

    def mutate(*a, **kw):
        with store.engine.begin() as conn:
            conn.execute(update(accounts).values(fence=99999))

    monkeypatch.setattr(module, "review_paused_periods", mutate)
    with pytest.raises(ValueError, match="READ_ONLY_DIAGNOSTIC_SQL_GUARD"):
        module.diagnose(store, c.client, "DEMO-test")
    assert financial_rows(store) == before


@pytest.mark.parametrize("fault", ["live", "no_attestation", "cached_key_missing"])
def test_main_guards_and_safe_failure_do_not_prompt_or_leak(fault, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["diagnostic"]
        + ([] if fault == "no_attestation" else ["--attest-no-activity-since-anchor"]),
    )
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true" if fault == "live" else "false")
    monkeypatch.setenv("TRADING_MODE", "PAPER")

    def missing(*, prompt):
        assert prompt is False
        raise ValueError("PRIVATE_KEY_OR_DATABASE_SECRET")

    monkeypatch.setattr(module, "credentials", missing)
    module.main()
    output = capsys.readouterr().out
    assert "PRIVATE_KEY_OR_DATABASE_SECRET" not in output
    report = json.loads(output)
    assert report["diagnostic"] == "BLOCKED_NO_APPLY" and not report["apply_attempted"]
