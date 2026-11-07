from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.domain.config import AppConfig, RiskProfile, load_config, validate_startup

RISK_FIELDS = (
    "risk_per_trade_pct",
    "max_leverage",
    "max_concurrent_positions",
    "max_single_notional_pct",
    "max_total_notional_pct",
    "max_margin_usage_pct",
    "daily_loss_limit_pct",
    "weekly_loss_limit_pct",
    "max_drawdown_pct",
    "max_correlated_risk_pct",
    "max_slippage_pct",
    "max_spread_pct",
)


def test_defaults_are_non_trading() -> None:
    config = load_config()
    assert config.mode == "PAPER"
    assert config.live_trading_enabled is False
    assert all(getattr(config.risk, field) is None for field in RISK_FIELDS)
    validate_startup(config)


def test_live_example_remains_unresolved() -> None:
    config = load_config(Path("configs/live.example.yaml"))
    assert config.live_trading_enabled is False
    assert all(getattr(config.risk, field) is None for field in RISK_FIELDS)
    with pytest.raises(ValueError, match="LIVE"):
        validate_startup(config)


@pytest.mark.parametrize("mode", ["BACKTEST", "PAPER", "DEMO", "LIVE"])
def test_toggle_cannot_enable_trading(mode: str) -> None:
    with pytest.raises(ValueError, match="LIVE|live_trading_enabled"):
        validate_startup(AppConfig(mode=mode, live_trading_enabled=True))


def test_even_complete_risk_profile_cannot_enable_live() -> None:
    risk = RiskProfile(
        **{name: "1" if name != "max_concurrent_positions" else 1 for name in RISK_FIELDS}
    )
    with pytest.raises(ValueError, match="LIVE"):
        validate_startup(AppConfig(mode="LIVE", risk=risk))


@pytest.mark.parametrize(
    "field,value",
    [
        ("allow_averaging_down", True),
        ("allow_martingale", True),
        ("protective_stop_required", False),
        ("risk_per_trade_pct", "-1"),
        ("risk_per_trade_pct", "NaN"),
        ("risk_per_trade_pct", "Infinity"),
        ("risk_per_trade_pct", 0.1),
        ("max_concurrent_positions", 1.5),
        ("max_concurrent_positions", True),
        ("max_leverage", "0"),
    ],
)
def test_unsafe_risk_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        RiskProfile(**{field: value})


def test_risk_is_immutable_and_rejects_ai_fields() -> None:
    risk = RiskProfile()
    with pytest.raises(ValidationError):
        risk.max_leverage = 100  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RiskProfile(ai_override=True)


def test_environment_cannot_silently_override_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError):
        validate_startup(load_config())
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "not-a-boolean")
    with pytest.raises(ValueError):
        load_config()


def test_explicit_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.yaml")


def test_unknown_yaml_fields_rejected(tmp_path: Path) -> None:
    file = tmp_path / "config.yaml"
    file.write_text("mode: PAPER\nai_can_set_risk: true\n")
    with pytest.raises(ValidationError):
        load_config(file)


@pytest.mark.parametrize("missing", RISK_FIELDS)
def test_each_missing_live_risk_field_is_identified(missing: str) -> None:
    values = {name: "1" if name != "max_concurrent_positions" else 1 for name in RISK_FIELDS}
    values[missing] = None
    config = AppConfig(mode="LIVE", risk=RiskProfile(**values))
    with pytest.raises(ValueError, match=missing):
        validate_startup(config)


def test_duplicate_yaml_and_non_mapping_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ambiguous.yaml"
    for content in ["mode: PAPER\nmode: LIVE\n", "[]\n", "null\n"]:
        path.write_text(content)
        with pytest.raises(ValueError):
            load_config(path)


def test_demo_is_also_unavailable_in_phase0() -> None:
    with pytest.raises(ValueError, match="DEMO"):
        validate_startup(AppConfig(mode="DEMO"))
