"""DEMO-only ledger and strictly parsed exchange observations (never credentials)."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from packages.domain.config import NonNegativeDecimal, PositiveDecimal
from services.execution.models import Ledger, PaperOrder, State
from services.risk.paper import DemoPolicy

type InitialFillSettleReason = Literal[
    "NONE",
    "NOT_ELIGIBLE",
    "UNSTABLE_THIRD",
    "FILL_PROOF_MISMATCH",
    "ORDER_PROOF_MISMATCH",
    "POSITION_PROOF_MISMATCH",
    "SNAPSHOT_PROOF_MISMATCH",
    "COLLATERAL_PROOF_MISMATCH",
    "CASH_PROOF_MISMATCH",
    "NO_INITIAL_TRANSITION",
    "EXECUTIONS_PROOF_MISMATCH",
    "PROOF_QUERY_FAILED",
    "VERIFIED_INITIAL_FILL",
]


class CancelledExposureCorrection(State):
    review_token: str
    evidence_hash: str
    exchange_fingerprint: str
    corrected_at: datetime
    original_orders: dict[str, PaperOrder]


class AttestedDayBaseline(State):
    review_token: str
    source: Literal["OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE"]
    boundary_utc: datetime
    baseline_usdt: PositiveDecimal
    old_daily_key: str
    old_daily_base: PositiveDecimal
    anchor_utc: datetime
    known_exit_utc: datetime
    attested_until_utc: datetime
    signed_evidence_hash: str
    exchange_fingerprint: str
    account_wide_exchange_export_verified: Literal[False] = False


class ExitAnchoredDayBaseline(State):
    review_token: str
    source: Literal["OPERATOR_ATTESTED_PRE_MIDNIGHT_EXIT_WITH_SIGNED_USDT_EVIDENCE"]
    boundary_utc: datetime
    baseline_usdt: PositiveDecimal
    old_daily_key: str
    old_daily_base: PositiveDecimal
    observation_utc: datetime
    known_exit_utc: datetime
    attested_until_utc: datetime
    signed_evidence_hash: str
    exchange_fingerprint: str
    account_wide_exchange_export_verified: Literal[False] = False
    income_time_basis: Literal["EXACT_FILL_TIMESTAMP", "SECOND_BUCKET_OR_EXACT_FILL_TIMESTAMP"] = (
        "EXACT_FILL_TIMESTAMP"
    )


class AttestedPeriodBaseline(State):
    review_token: str
    source: Literal["OPERATOR_ATTESTED_FLAT_PERIODS_WITH_SIGNED_USDT_EVIDENCE"]
    day_boundary_utc: datetime
    week_boundary_utc: datetime
    baseline_usdt: PositiveDecimal
    old_daily_key: str
    old_daily_base: PositiveDecimal
    old_weekly_key: str
    old_weekly_base: PositiveDecimal
    anchor_utc: datetime
    signed_evidence_until_utc: datetime
    attested_until_utc: datetime
    signed_evidence_hash: str
    exchange_fingerprint: str
    account_wide_exchange_export_verified: Literal[False] = False


class PrivateSessionDiagnostic(State):
    stage: Literal[
        "NONE",
        "SESSION_CREATE",
        "CONNECT",
        "RECONCILE",
        "READY_CALLBACK",
        "HEARTBEAT",
        "KEY_RENEW",
        "RECEIVE",
        "PARSE",
        "EVENT",
        "PING",
        "EXPIRY",
    ] = "NONE"
    code: Literal[
        "NONE",
        "EVENT_UNKNOWN",
        "EVENT_TIME_INVALID",
        "EVENT_OVERSIZED",
        "EVENT_JSON_INVALID",
        "EVENT_OBJECT_INVALID",
        "SOCKET_CLOSED",
        "TIMEOUT",
        "RATE_LIMITED",
        "CLOCK_OR_COOLDOWN",
        "SNAPSHOT_CHANGED",
        "SNAPSHOT_INVALID",
        "LEASE_OR_RISK",
        "DB_LOCK_TIMEOUT",
        "DB_DEADLOCK",
        "DB_CONNECTION",
        "OTHER_DATABASE",
        "DEMO_QUERY_OR_SESSION",
        "OTHER_SESSION_FAILURE",
        "EVENT_BUFFER_OVERFLOW",
        "LISTEN_KEY_EXPIRED",
        "EVENT_CLOCK_UNTRUSTED",
        "EVENT_CLOCK_INCOMPLETE",
    ] = "NONE"
    event_kind: Literal[
        "NONE",
        "UNKNOWN",
        "ORDER_TRADE_UPDATE",
        "ALGO_UPDATE",
        "ACCOUNT_UPDATE",
        "ACCOUNT_CONFIG_UPDATE",
        "MARGIN_CALL",
        "listenKeyExpired",
        "TRADE_LITE",
    ] = "NONE"
    event_age_ms: int | None = None
    ready_callback_ms: int = 0
    event_receive_age_ms: int | None = None
    received_events: int = 0
    coalesced_events: int = 0
    event_clock_offset_ms: int | None = None
    event_clock_rtt_ms: int | None = None
    event_clock_wait_ms: int = 0
    event_local_age_ms: int | None = None


class DemoLedger(Ledger):
    mode: Literal["DEMO"] = "DEMO"
    policy: DemoPolicy
    reconciliation_failure_code: Literal[
        "NONE", "SNAPSHOT_CHANGED", "SNAPSHOT_INVALID", "OTHER_DEMO_FAILURE"
    ] = "NONE"
    last_reconciliation_mismatch_fields: tuple[str, ...] = ()
    reconciliation_settle_reason: InitialFillSettleReason = "NONE"
    initial_fill_settlements: dict[str, str] = Field(default_factory=dict)
    last_private_session_failure: PrivateSessionDiagnostic | None = None
    stream_ready: bool = False
    stream_at: datetime | None = None
    fingerprint: str = ""
    available_balance: NonNegativeDecimal = Decimal(0)
    margin_used: NonNegativeDecimal = Decimal(0)
    positions: dict[str, Decimal] = Field(default_factory=dict)
    algo_server_ids: dict[str, str] = Field(default_factory=dict)
    income_seen: dict[str, str] = Field(default_factory=dict)
    net_transfers: Decimal = Decimal(0)
    income_since_ms: int | None = None
    recovery_receipts: dict[str, str] = Field(default_factory=dict)
    cancelled_exposure_corrections: dict[str, CancelledExposureCorrection] = Field(
        default_factory=dict
    )
    attested_day_baselines: dict[str, AttestedDayBaseline] = Field(default_factory=dict)
    exit_anchored_day_baselines: dict[str, ExitAnchoredDayBaseline] = Field(default_factory=dict)
    attested_period_baselines: dict[str, AttestedPeriodBaseline] = Field(default_factory=dict)
    requests: dict[str, str] = Field(default_factory=dict)
    request_no_send_evidence: dict[
        str,
        Literal["LOCAL_VALIDATION_FAILED", "POSITION_ALREADY_FLAT", "POSITION_DIRECTION_MISMATCH"],
    ] = Field(default_factory=dict)
    margin_mode: Literal["CROSSED", "ISOLATED"] = "CROSSED"
    symbols: tuple[str, ...] = ()
    margin_types: dict[str, str] = Field(default_factory=dict)
    verified_leverage: dict[str, Decimal] = Field(default_factory=dict)
    isolated_wallets: dict[str, Decimal] = Field(default_factory=dict)
    isolated_margins: dict[str, Decimal] = Field(default_factory=dict)
    entry_quantity_caps: dict[str, PositiveDecimal] = Field(default_factory=dict)
    validation_evidence: dict[str, dict[str, bool]] = Field(default_factory=dict)
    validation_entry_styles: dict[str, Literal["PASSIVE", "OPPOSITE_QUOTE"]] = Field(
        default_factory=dict
    )


class DemoSnapshot(State):
    as_of: datetime
    equity: Decimal
    wallet_balance: Decimal | None = None
    available_balance: Decimal
    margin_used: Decimal
    positions: dict[str, Decimal]
    entry_prices: dict[str, Decimal]
    leverage: dict[str, Decimal]
    orders: dict[str, dict[str, str]]
    algos: dict[str, dict[str, str]]
    fingerprint: str
    transfers: dict[str, str] = Field(default_factory=dict)
    margin_types: dict[str, str] = Field(default_factory=dict)
    isolated_wallets: dict[str, Decimal] = Field(default_factory=dict)
    isolated_margins: dict[str, Decimal] = Field(default_factory=dict)
    liquidation_prices: dict[str, Decimal] = Field(default_factory=dict)
