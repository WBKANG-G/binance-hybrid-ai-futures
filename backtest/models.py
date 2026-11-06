"""Research-only contracts. No values in these models authorize any trading mode."""

import hashlib
import json
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, model_validator

from packages.domain.config import ImmutableModel, NonNegativeDecimal, PositiveDecimal, reject_float
from packages.domain.contracts import Symbol, UTCDateTime

FiniteDecimal = Annotated[Decimal, BeforeValidator(reject_float), Field(allow_inf_nan=False)]
BasisPoints = Annotated[
    Decimal, BeforeValidator(reject_float), Field(ge=0, le=1000, allow_inf_nan=False)
]
Period = Annotated[int, Field(strict=True, ge=2, le=2000)]


class ResearchProfile(ImmutableModel):
    mode: Literal["BACKTEST"] = "BACKTEST"
    research_only: Literal[True] = True
    version: str = "research-v1"
    cost_filter: Literal["POSITIVE_TARGET_AFTER_FIXED_COSTS"] = "POSITIVE_TARGET_AFTER_FIXED_COSTS"
    availability: Literal["historical_close", "captured"] = "historical_close"
    latency_ms: int = Field(default=0, strict=True, ge=0, le=60000)
    ema_fast: Period = 50
    ema_slow: Period = 200
    adx_period: Period = 14
    adx_threshold: NonNegativeDecimal = Decimal("23")
    ema_pullback: Period = 20
    atr_period: Period = 14
    breakout_lookback: Period = 20
    volume_multiplier: PositiveDecimal = Decimal("1.5")
    execution_lookback: Period = 3
    pullback_bars: Period = 4
    pullback_atr_distance: PositiveDecimal = Decimal("0.5")
    min_atr_ratio: PositiveDecimal = Decimal("0.001")
    max_atr_ratio: PositiveDecimal = Decimal("0.05")
    stop_atr_multiple: PositiveDecimal = Decimal("1.5")
    trend_reward_r: PositiveDecimal = Decimal("2")
    breakout_reward_r: PositiveDecimal = Decimal("1.5")
    pivot_left: Period = 2
    pivot_right: Period = 2
    entry_ttl_seconds: int = Field(default=300, strict=True, ge=60, le=3600, multiple_of=60)
    trend_hold_seconds: int = Field(default=14400, strict=True, ge=60, multiple_of=60)
    breakout_hold_seconds: int = Field(default=7200, strict=True, ge=60, multiple_of=60)
    initial_capital: PositiveDecimal = Decimal("10000")
    quantity: PositiveDecimal = Decimal("1")
    entry_fee_bps: BasisPoints = Decimal("6")
    exit_fee_bps: BasisPoints = Decimal("6")
    slippage_bps: BasisPoints = Decimal("5")
    penetration_ticks: int = Field(default=1, strict=True, ge=1, le=100)

    @model_validator(mode="after")
    def ordered_parameters(self) -> "ResearchProfile":
        if self.ema_fast >= self.ema_slow or self.min_atr_ratio > self.max_atr_ratio:
            raise ValueError("Invalid research parameter bounds")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()


class Instrument(ImmutableModel):
    symbol: Symbol
    listed_at: UTCDateTime
    tick_size: PositiveDecimal
    quantity_step: PositiveDecimal
    provenance: str = "DECLARED"


class FundingPoint(ImmutableModel):
    at: UTCDateTime
    rate: Annotated[Decimal, BeforeValidator(reject_float), Field(ge=-1, le=1, allow_inf_nan=False)]
    mark_price: PositiveDecimal


class FundingLedger(ImmutableModel):
    covered_from: UTCDateTime
    covered_until: UTCDateTime
    events: tuple[FundingPoint, ...]

    @model_validator(mode="after")
    def valid_coverage(self) -> "FundingLedger":
        if self.covered_until <= self.covered_from:
            raise ValueError("Invalid funding coverage")
        previous = None
        for event in self.events:
            if not self.covered_from <= event.at < self.covered_until or (
                previous is not None and event.at <= previous
            ):
                raise ValueError("Funding timestamps must be unique, ordered and covered")
            previous = event.at
        return self


class ResearchCandidate(ImmutableModel):
    mode: Literal["BACKTEST"] = "BACKTEST"
    research_only: Literal[True] = True
    candidate_id: UUID
    strategy: Literal["TREND_FOLLOWING", "BREAKOUT"]
    symbol: Symbol
    direction: Literal["LONG", "SHORT"]
    decision_at: UTCDateTime
    expires_at: UTCDateTime
    limit_price: PositiveDecimal
    stop_price: PositiveDecimal
    target_price: PositiveDecimal
    max_hold_seconds: int = Field(strict=True, gt=0, multiple_of=60)
    profile_version: str
    based_on: tuple[tuple[str, UTCDateTime], ...]

    @model_validator(mode="after")
    def valid_geometry(self) -> "ResearchCandidate":
        if self.expires_at <= self.decision_at or any(
            at > self.decision_at for _, at in self.based_on
        ):
            raise ValueError("Candidate times contain future information or invalid expiry")
        if self.direction == "LONG":
            valid = self.stop_price < self.limit_price < self.target_price
        else:
            valid = self.target_price < self.limit_price < self.stop_price
        if not valid:
            raise ValueError("Invalid research stop/target geometry")
        return self


class Trade(ImmutableModel):
    candidate_id: UUID
    strategy: str
    direction: Literal["LONG", "SHORT"]
    entry_at: UTCDateTime
    exit_at: UTCDateTime
    entry_price: PositiveDecimal
    exit_price: PositiveDecimal
    quantity: PositiveDecimal
    reason: Literal["STOP", "TARGET", "TIMEOUT", "END_OF_DATA"]
    gross_pnl: FiniteDecimal
    fees: NonNegativeDecimal
    funding_cost: FiniteDecimal
    slippage_cost: NonNegativeDecimal
    net_pnl: FiniteDecimal


class EquityPoint(ImmutableModel):
    at: UTCDateTime
    equity: FiniteDecimal


class BacktestResult(ImmutableModel):
    mode: Literal["BACKTEST"] = "BACKTEST"
    research_only: Literal[True] = True
    scenario: Literal["NEVER", "TRADE_THROUGH"]
    profile_hash: str
    dataset_hash: str
    candidate_count: int
    filled_entries: int
    expired_entries: int
    rejected_entries: int
    cancelled_entries: int = 0
    blocked_entries: int
    initial_equity: PositiveDecimal
    final_equity: FiniteDecimal
    trades: tuple[Trade, ...]
    equity_curve: tuple[EquityPoint, ...]
    net_pnl: FiniteDecimal
    fees: NonNegativeDecimal
    funding_cost: FiniteDecimal
    slippage_cost: NonNegativeDecimal
    max_drawdown_pct: NonNegativeDecimal
    win_rate: NonNegativeDecimal | None
    profit_factor: NonNegativeDecimal | None
