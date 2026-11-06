"""Versioned local PAPER ledger. Amounts serialize as decimal strings."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.config import NonNegativeDecimal, PositiveDecimal
from packages.domain.contracts import OrderState, PositionState, RiskState, Signal
from services.risk.paper import AccountValue, DemoPolicy, GateSnapshot, PaperPolicy


class State(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PaperOrder(State):
    client_id: str
    signal: Signal
    snapshot: GateSnapshot
    group: str
    quantity: PositiveDecimal
    quantity_step: PositiveDecimal
    price_tick: PositiveDecimal
    loss_per_unit: PositiveDecimal
    budget: PositiveDecimal
    state: OrderState = OrderState.RISK_RESERVED
    position_state: PositionState = PositionState.ENTRY_PENDING
    filled: NonNegativeDecimal = Decimal(0)
    remaining: NonNegativeDecimal
    open_quantity: NonNegativeDecimal = Decimal(0)
    average_price: NonNegativeDecimal = Decimal(0)
    actual_risk: NonNegativeDecimal = Decimal(0)
    protected: bool = False
    algo_id: str | None = None
    tp_algo_id: str | None = None
    server_order_id: str | None = None
    exit_id: str | None = None
    exit_generation: int = 0
    exit_state: Literal["NONE", "SUBMITTING", "UNKNOWN", "ACKNOWLEDGED", "FILLED"] = "NONE"
    seen_fills: dict[str, str] = Field(default_factory=dict)

    @property
    def pending_risk(self) -> Decimal:
        return self.remaining * self.loss_per_unit

    @property
    def charged_risk(self) -> Decimal:
        if self.open_quantity and not self.protected:
            return self.open_quantity * self.average_price + self.pending_risk
        return self.actual_risk + self.pending_risk

    @property
    def notional(self) -> Decimal:
        return (
            self.remaining * self.signal.suggested_limit_price
            + self.open_quantity * self.average_price
        )


class Ledger(State):
    schema_version: Literal[1] = 1
    policy: PaperPolicy | DemoPolicy
    value: AccountValue
    risk_state: RiskState = RiskState.UNHEALTHY
    reconciled: bool = False
    venue_revision: int = 0
    paused: bool = False
    daily_key: str = ""
    weekly_key: str = ""
    daily_base: PositiveDecimal
    weekly_base: PositiveDecimal
    high_water: PositiveDecimal
    last_reconciled: datetime | None = None
    orders: dict[str, PaperOrder] = Field(default_factory=dict)
    operations: dict[str, str] = Field(default_factory=dict)


class VenueOrder(State):
    client_id: str
    signal: Signal
    quantity: PositiveDecimal
    state: OrderState = OrderState.ACKNOWLEDGED
    fills: dict[str, tuple[Decimal, Decimal]] = Field(default_factory=dict)
    open_quantity: NonNegativeDecimal = Decimal(0)


class Stop(State):
    algo_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    trigger: PositiveDecimal
    quantity: PositiveDecimal
    active: bool = True
    order_type: Literal["STOP_MARKET"] = "STOP_MARKET"
    working_type: Literal["MARK_PRICE"] = "MARK_PRICE"


class Venue(State):
    revision: int = 0
    orders: dict[str, VenueOrder] = Field(default_factory=dict)
    stops: dict[str, Stop] = Field(default_factory=dict)
    exits: dict[str, str] = Field(default_factory=dict)
    manual_positions: dict[str, Decimal] = Field(default_factory=dict)
    marks: dict[str, Decimal] = Field(default_factory=dict)
    value: AccountValue
    submit_count: int = 0
    exit_count: int = 0


class Lease(State):
    account_id: str
    owner: UUID
    fence: int
