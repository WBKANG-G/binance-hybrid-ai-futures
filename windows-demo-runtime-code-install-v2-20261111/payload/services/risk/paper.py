"""Explicit PAPER-only policies and cost-aware, fail-closed risk calculations."""

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BeforeValidator, Field, model_validator

from integrations.binance_usdm.filters import SymbolRules, number
from packages.domain.config import (
    RISK_FIELDS,
    ImmutableModel,
    NonNegativeDecimal,
    PositiveDecimal,
    RiskProfile,
    reject_float,
)
from packages.domain.contracts import Signal, UTCDateTime

Money = Annotated[Decimal, BeforeValidator(reject_float), Field(allow_inf_nan=False)]
ZERO = Decimal(0)
HUNDRED = Decimal(100)


class RiskRejected(ValueError):
    """A safety gate withheld permission for a new PAPER entry."""


class SandboxPolicy(ImmutableModel):
    mode: Literal["PAPER", "DEMO"]
    sandbox_only: Literal[True]
    rules_only: Literal[True]
    risk: RiskProfile
    fee_rate: NonNegativeDecimal
    funding_allowance_rate: NonNegativeDecimal
    stop_slippage_pct: NonNegativeDecimal
    max_snapshot_age_seconds: Annotated[int, Field(strict=True, gt=0, le=300)]
    week_start_kst: Annotated[int, Field(strict=True, ge=0, le=6)]

    @model_validator(mode="after")
    def complete(self) -> Self:
        if self.risk.version == "unassigned" or any(
            getattr(self.risk, name) is None for name in RISK_FIELDS
        ):
            raise ValueError("Sandbox requires a complete, versioned simulation policy")
        return self

    def limit(self, name: str) -> Decimal:
        value = getattr(self.risk, name)
        if value is None:
            raise RiskRejected("Unresolved policy")
        return Decimal(value)


class PaperPolicy(SandboxPolicy):
    mode: Literal["PAPER"]


class DemoPolicy(SandboxPolicy):
    mode: Literal["DEMO"]


class GateSnapshot(ImmutableModel):
    market_at: UTCDateTime
    account_at: UTCDateTime
    correlation_at: UTCDateTime
    clock_healthy: Literal[True] = True
    streams_reconciled: Literal[True] = True
    regime_allowed: Literal[True] = True
    evaluation: Literal["RULES_ONLY", "HOLD"] = "HOLD"
    bid: PositiveDecimal
    ask: PositiveDecimal
    mark: PositiveDecimal
    # Explicit conservative grouping, never inferred from a ticker name.
    correlation_group: str = Field(min_length=1)


class AccountValue(ImmutableModel):
    as_of: UTCDateTime
    initial: PositiveDecimal
    realized: Money = ZERO
    unrealized: Money = ZERO
    fees: NonNegativeDecimal = ZERO
    funding: Money = ZERO
    net_deposits: Money = ZERO

    @property
    def adjusted_equity(self) -> Decimal:
        return self.initial + self.realized + self.unrealized - self.fees + self.funding

    @property
    def equity(self) -> Decimal:
        return self.adjusted_equity + self.net_deposits


def period_keys(now: datetime, week_start: int) -> tuple[str, str]:
    local = now.astimezone(ZoneInfo("Asia/Seoul"))
    day = local.date()
    week = day - timedelta(days=(day.weekday() - week_start) % 7)
    return day.isoformat(), week.isoformat()


def unit_loss(policy: SandboxPolicy, entry: Decimal, stop: Decimal) -> Decimal:
    allowance = stop * policy.stop_slippage_pct / HUNDRED
    return (
        abs(entry - stop)
        + allowance
        + (entry + stop + allowance) * policy.fee_rate
        + entry * policy.funding_allowance_rate
    )


def capped_quantity(quantity: Decimal, cap: Decimal | None, rules: SymbolRules) -> Decimal:
    """An explicit cap can only reduce risk sizing, never increase or round it up."""
    if cap is None:
        return quantity
    verified = number(cap)
    if verified % rules.step_size != ZERO:
        raise RiskRejected("Quantity cap must be an exact exchange step")
    return min(quantity, verified)


def minimum_entry_quantity(rules: SymbolRules, price: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 80
        needed = max(rules.min_qty, rules.min_notional / number(price))
        return (needed / rules.step_size).to_integral_value(
            rounding=ROUND_CEILING
        ) * rules.step_size


def size(
    policy: SandboxPolicy,
    signal: Signal,
    snapshot: GateSnapshot,
    equity: Decimal,
    rules: SymbolRules,
    now: datetime,
    *,
    quantity_cap: Decimal | None = None,
) -> tuple[Decimal, Decimal]:
    # Revalidate even frozen objects: model_copy/model_construct are not trusted boundaries.
    policy = type(policy).model_validate(policy.model_dump())
    signal = Signal.model_validate(signal.model_dump())
    snapshot = GateSnapshot.model_validate(snapshot.model_dump())
    if (
        signal.environment.value != policy.mode
        or signal.risk_profile_version != policy.risk.version
    ):
        raise RiskRejected("Sandbox mode/policy version mismatch")
    if not signal.signal_closed_candle_at <= now < signal.expires_at:
        raise RiskRejected("Signal is future or expired")
    if rules.symbol != signal.symbol or equity <= 0:
        raise RiskRejected("Invalid equity/symbol")
    if snapshot.evaluation != "RULES_ONLY":
        raise RiskRejected("HOLD: Phase 3 sandbox requires explicit rules-only evaluation")
    for observed in (snapshot.market_at, snapshot.account_at, snapshot.correlation_at):
        if not 0 <= (now - observed).total_seconds() <= policy.max_snapshot_age_seconds:
            raise RiskRejected("Stale/future snapshot")
    if snapshot.ask < snapshot.bid:
        raise RiskRejected("Crossed book")
    spread = (snapshot.ask - snapshot.bid) / snapshot.mark * HUNDRED
    expected = snapshot.ask if signal.direction == "LONG" else snapshot.bid
    deviation = (
        abs(expected - signal.suggested_limit_price) / signal.suggested_limit_price * HUNDRED
    )
    if spread > policy.limit("max_spread_pct") or deviation > policy.limit("max_slippage_pct"):
        raise RiskRejected("Execution cost gate")
    loss = unit_loss(policy, signal.suggested_limit_price, signal.initial_stop_price)
    quantity = rules.floor_quantity(equity * policy.limit("risk_per_trade_pct") / HUNDRED / loss)
    quantity = capped_quantity(quantity, quantity_cap, rules)
    rules.validate_entry(quantity, signal.suggested_limit_price, snapshot.mark)
    rules.validate_price(signal.initial_stop_price)
    return quantity, loss
