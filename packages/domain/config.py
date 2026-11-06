"""Immutable configuration. Phase 4 cannot authorize exchange execution."""

import os
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SecretStr


def reject_float(value: object) -> object:
    if isinstance(value, (float, bool)):
        raise ValueError(
            "Financial values must be decimal strings, integers or Decimal; never float"
        )
    return value


PositiveDecimal = Annotated[
    Decimal, BeforeValidator(reject_float), Field(gt=0, allow_inf_nan=False)
]
NonNegativeDecimal = Annotated[
    Decimal, BeforeValidator(reject_float), Field(ge=0, allow_inf_nan=False)
]

Percentage = Annotated[
    Decimal, BeforeValidator(reject_float), Field(gt=0, le=100, allow_inf_nan=False)
]


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class RiskProfile(ImmutableModel):
    version: str = Field(default="unassigned", min_length=1)
    risk_per_trade_pct: Percentage | None = None
    max_leverage: PositiveDecimal | None = None
    max_concurrent_positions: Annotated[int, Field(strict=True, gt=0)] | None = None
    max_single_notional_pct: PositiveDecimal | None = None
    max_total_notional_pct: PositiveDecimal | None = None
    max_margin_usage_pct: Percentage | None = None
    daily_loss_limit_pct: Percentage | None = None
    weekly_loss_limit_pct: Percentage | None = None
    max_drawdown_pct: Percentage | None = None
    max_correlated_risk_pct: PositiveDecimal | None = None
    max_slippage_pct: Percentage | None = None
    max_spread_pct: Percentage | None = None
    allow_averaging_down: Literal[False] = False
    allow_martingale: Literal[False] = False
    protective_stop_required: Literal[True] = True


class AppConfig(ImmutableModel):
    mode: Literal["BACKTEST", "PAPER", "DEMO", "LIVE"] = "PAPER"
    live_trading_enabled: bool = Field(default=False, strict=True)
    risk: RiskProfile = Field(default_factory=RiskProfile)


RISK_FIELDS = tuple(
    name
    for name in RiskProfile.model_fields
    if name
    not in {"version", "allow_averaging_down", "allow_martingale", "protective_stop_required"}
)


def validate_startup(config: AppConfig) -> None:
    if config.mode == "LIVE":
        missing = [name for name in RISK_FIELDS if getattr(config.risk, name) is None]
        if missing:
            raise ValueError(
                "LIVE rejected: unresolved required risk fields: " + ", ".join(missing)
            )
        raise ValueError("LIVE execution is unavailable in Phase 4 regardless of risk settings")
    if config.live_trading_enabled:
        raise ValueError("live_trading_enabled must remain false in Phase 4")
    if config.mode == "DEMO":
        raise ValueError("DEMO API startup disabled; use the isolated explicit DEMO CLI")


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject ambiguous configuration instead of accepting the last duplicate key."""


def unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in mapping:
            raise ValueError("Configuration keys must be unique strings")
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_config(path: Path | None = None) -> AppConfig:
    selected = path or (
        Path(os.environ["APP_CONFIG_FILE"]) if os.getenv("APP_CONFIG_FILE") else None
    )
    data: dict[str, Any] = {}
    if selected is not None:
        raw = yaml.load(selected.read_text(), Loader=UniqueKeyLoader)
        if not isinstance(raw, dict):
            raise ValueError("Configuration must be a YAML mapping")
        data = raw
    if "TRADING_MODE" in os.environ:
        data["mode"] = os.environ["TRADING_MODE"]
    if "LIVE_TRADING_ENABLED" in os.environ:
        enabled = os.environ["LIVE_TRADING_ENABLED"].lower()
        if enabled not in {"true", "false"}:
            raise ValueError("LIVE_TRADING_ENABLED must be true or false")
        data["live_trading_enabled"] = enabled == "true"
    return AppConfig.model_validate(data)


def database_url() -> SecretStr:
    # Never serialize this value into health responses or exception logs.
    return SecretStr(
        os.getenv("DATABASE_URL", "postgresql+psycopg://dev:dev@127.0.0.1:5432/hybrid")
    )
