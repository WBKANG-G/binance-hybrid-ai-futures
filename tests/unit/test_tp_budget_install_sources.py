"""Source cohort pins and local-only default refusal are install prerequisites."""

import copy
import hashlib
from pathlib import Path

import pytest

import guard_tp_budget_install as g
from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS
from scripts.review_tp_minute_budget import MODEL_WEIGHTS


def test_new_source_cohort_and_default_denial():
    g.verify_sources("post")
    g.verify_default_denial()
    assert MODELED_TP_ROUTE_COSTS == MODEL_WEIGHTS
    assert len(g.OLD_PINS) == 45 and len(g.NEW_PINS) == 55


@pytest.mark.parametrize("name", sorted(g.NEW_PINS))
def test_each_missing_or_mismatched_new_file_blocks(name):
    observed = copy.copy(g.NEW_PINS)
    observed[name] = "0" * 64
    with pytest.raises(g.Blocked, match="INSTALLED_SOURCE_HASH_MISMATCH"):
        g.verify_sources("post", observed)
    del observed[name]
    with pytest.raises(g.Blocked, match="INSTALLED_SOURCE_HASH_MISMATCH"):
        g.verify_sources("post", observed)


def test_pre_source_is_installed_account_config_cohort():
    import guard_account_config_install as old

    assert g.OLD_PINS == old.NEW_PINS
    g.verify_sources("pre", g.OLD_PINS)
    with pytest.raises(g.Blocked):
        g.verify_sources("post", g.OLD_PINS)


def test_local_check_catches_accidental_default_execution_enable(monkeypatch):
    from integrations.binance_usdm import demo_tp_window_budget as m

    real = m.WindowTPBudget

    def accidentally_enabled(*args, **kwargs):
        return real(*args, **kwargs, allow_synthetic_model_execution=True)

    monkeypatch.setattr(m, "WindowTPBudget", accidentally_enabled)
    with pytest.raises(g.Blocked, match="DEFAULT_GATE_UNVERIFIED"):
        g.verify_default_denial()


def test_production_default_gate_and_read_only_policy_not_environment_switches():
    source = Path("scripts/check_demo_tp_trigger.py").read_text()
    assert "allow_synthetic_model_execution=False" in source
    assert "allow_synthetic_model_execution=True" not in source
    assert (
        hashlib.sha256(Path("scripts/check_demo_tp_trigger.py").read_bytes()).hexdigest()
        == g.NEW_PINS["scripts/check_demo_tp_trigger.py"]
    )
