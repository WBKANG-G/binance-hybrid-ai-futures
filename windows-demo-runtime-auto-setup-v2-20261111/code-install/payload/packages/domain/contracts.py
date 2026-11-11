"""Phase 0 wire contracts, not an execution engine or risk approval API."""

import json
from datetime import UTC
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AfterValidator, AwareDatetime, Field, field_validator, model_validator

from packages.domain.config import ImmutableModel, NonNegativeDecimal, PositiveDecimal


class TradingMode(StrEnum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    DEMO = "DEMO"
    LIVE = "LIVE"


class OrderState(StrEnum):
    CREATED = "CREATED"
    RISK_RESERVED = "RISK_RESERVED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"


class AlgoState(StrEnum):
    UNKNOWN = "UNKNOWN"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    TRIGGERED = "TRIGGERED"
    FINISHED = "FINISHED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    RECONCILING = "RECONCILING"


class PositionState(StrEnum):
    FLAT = "FLAT"
    ENTRY_PENDING = "ENTRY_PENDING"
    PARTIALLY_OPEN = "PARTIALLY_OPEN"
    OPEN_UNPROTECTED = "OPEN_UNPROTECTED"
    PROTECTED = "PROTECTED"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"


class RiskState(StrEnum):
    NORMAL = "NORMAL"
    COOLDOWN = "COOLDOWN"
    DAILY_LOCK = "DAILY_LOCK"
    WEEKLY_LOCK = "WEEKLY_LOCK"
    DRAWDOWN_LOCK = "DRAWDOWN_LOCK"
    UNHEALTHY = "UNHEALTHY"
    EMERGENCY = "EMERGENCY"


UTCDateTime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]
Symbol = Annotated[str, Field(pattern=r"^[A-Z0-9]{5,30}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class DomainEvent(ImmutableModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: Identifier
    schema_version: Literal[1] = 1
    environment: TradingMode
    account_id: Identifier
    strategy_id: UUID | None = None
    signal_id: UUID | None = None
    position_id: UUID | None = None
    event_time_utc: UTCDateTime
    ingest_time_utc: UTCDateTime
    correlation_id: UUID
    causation_id: UUID
    source: Identifier
    external_event_id: Identifier
    # JSON text is immutable, unlike a nested mutable dictionary on a frozen model.
    payload_json: str

    @field_validator("payload_json")
    @classmethod
    def validate_payload(cls, value: str) -> str:
        def invalid_constant(constant: str) -> None:
            raise ValueError("Non-finite JSON number: " + constant)

        if not isinstance(json.loads(value, parse_constant=invalid_constant), dict):
            raise ValueError("Event payload must encode a JSON object")
        return value

    @property
    def idempotency_key(self) -> tuple[str, str, str, str]:
        return (self.environment.value, self.account_id, self.source, self.external_event_id)


class Signal(ImmutableModel):
    signal_id: UUID
    environment: TradingMode
    symbol: Symbol
    strategy: Literal["TREND_FOLLOWING", "BREAKOUT"] | None
    purpose: Literal["STRATEGY", "DEMO_VALIDATION"] = "STRATEGY"
    direction: Literal["LONG", "SHORT"]
    signal_closed_candle_at: UTCDateTime
    expires_at: UTCDateTime
    entry_policy: Literal["LIMIT_ONLY"] = "LIMIT_ONLY"
    suggested_limit_price: PositiveDecimal
    initial_stop_price: PositiveDecimal
    target_price: PositiveDecimal
    risk_profile_version: Identifier
    # AI assessments will have separate contracts in Phase 5; never risk mutation fields.

    @model_validator(mode="after")
    def validate_prices_and_expiry(self) -> "Signal":
        if self.purpose == "DEMO_VALIDATION":
            if self.environment != TradingMode.DEMO or self.strategy is not None:
                raise ValueError("Validation signal must be DEMO-only with no strategy claim")
        elif self.strategy is None:
            raise ValueError("Strategy signal requires an approved strategy")
        if self.expires_at <= self.signal_closed_candle_at:
            raise ValueError("Signal must expire after its closed candle")
        if self.direction == "LONG":
            valid = self.initial_stop_price < self.suggested_limit_price < self.target_price
        else:
            valid = self.target_price < self.suggested_limit_price < self.initial_stop_price
        if not valid:
            raise ValueError("Stop and target must bracket the entry in the signal direction")
        return self


class EntryOrder(ImmutableModel):
    client_order_id: Identifier
    order_id: Identifier | None = None
    signal_id: UUID
    environment: TradingMode
    account_id: Identifier
    symbol: Symbol
    side: Literal["BUY", "SELL"]
    quantity: PositiveDecimal
    limit_price: PositiveDecimal
    order_type: Literal["LIMIT"] = "LIMIT"
    time_in_force: Literal["GTC", "GTX"] = "GTC"
    state: OrderState = OrderState.CREATED


class AlgoOrder(ImmutableModel):
    client_algo_id: Identifier
    algo_id: Identifier | None = None
    position_id: UUID
    environment: TradingMode
    account_id: Identifier
    symbol: Symbol
    side: Literal["BUY", "SELL"]
    algo_type: Literal["CONDITIONAL"] = "CONDITIONAL"
    order_type: Literal["STOP_MARKET", "TAKE_PROFIT_MARKET"]
    trigger_price: PositiveDecimal
    close_position: Literal[True] = True
    state: AlgoState = AlgoState.UNKNOWN


class Position(ImmutableModel):
    position_id: UUID
    environment: TradingMode
    account_id: Identifier
    symbol: Symbol
    side: Literal["LONG", "SHORT"]
    quantity: NonNegativeDecimal
    average_entry_price: PositiveDecimal | None = None
    state: PositionState = PositionState.OPEN_UNPROTECTED
    risk_state: RiskState = RiskState.UNHEALTHY
    reconciliation_required: bool = True
    protection_failure: bool = False
    emergency: bool = False

    @model_validator(mode="after")
    def validate_exposure(self) -> "Position":
        if self.state in {PositionState.FLAT, PositionState.CLOSED, PositionState.ENTRY_PENDING}:
            if self.quantity != 0:
                raise ValueError(
                    "Flat, closed or entry-pending positions cannot claim filled exposure"
                )
        elif self.quantity <= 0 or self.average_entry_price is None:
            raise ValueError("An open position requires positive filled quantity and entry price")
        return self
