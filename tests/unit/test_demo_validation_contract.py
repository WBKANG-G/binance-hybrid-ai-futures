"""Diagnostic intents cannot masquerade as strategy or non-DEMO trading."""

import pytest

from tests.integration.test_paper import signal


@pytest.mark.parametrize("mode", ["LIVE", "PAPER", "BACKTEST"])
def test_validation_purpose_rejects_non_demo(mode):
    with pytest.raises(ValueError, match="DEMO-only"):
        signal(environment=mode, purpose="DEMO_VALIDATION", strategy=None)


def test_validation_cannot_claim_a_trend_strategy():
    with pytest.raises(ValueError, match="no strategy claim"):
        signal(environment="DEMO", purpose="DEMO_VALIDATION")


def test_regular_strategy_intent_cannot_omit_strategy():
    with pytest.raises(ValueError, match="approved strategy"):
        signal(strategy=None)
