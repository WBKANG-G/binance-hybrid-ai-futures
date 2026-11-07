"""Deterministic OHLC research simulator. This module has no exchange transport."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Literal
from uuid import UUID

from backtest.data import MarketData
from backtest.models import (
    BacktestResult,
    EquityPoint,
    FundingLedger,
    ResearchCandidate,
    ResearchProfile,
    Trade,
)
from services.strategies.rules import grid

ExitReason = Literal["STOP", "TARGET", "TIMEOUT", "END_OF_DATA"]
Scenario = Literal["NEVER", "TRADE_THROUGH"]


@dataclass
class Position:
    candidate: ResearchCandidate
    at: datetime
    quantity: Decimal
    entry_fee: Decimal
    funding: Decimal = Decimal(0)


def simulate(
    data: MarketData,
    profile: ResearchProfile,
    candidates: Sequence[ResearchCandidate],
    funding: FundingLedger,
    *,
    scenario: Scenario,
    start: datetime | None = None,
    end: datetime | None = None,
) -> BacktestResult:
    with localcontext() as context:
        context.prec = 50
        return _simulate(
            data,
            profile,
            candidates,
            funding,
            scenario=scenario,
            start=start or data.start,
            end=end or data.end,
        )


def _simulate(
    data: MarketData,
    profile: ResearchProfile,
    candidates: Sequence[ResearchCandidate],
    funding: FundingLedger,
    *,
    scenario: Scenario,
    start: datetime,
    end: datetime,
) -> BacktestResult:
    profile = ResearchProfile.model_validate(profile.model_dump())
    funding = FundingLedger.model_validate(funding.model_dump())
    if scenario not in {"NEVER", "TRADE_THROUGH"} or start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Invalid simulation mode or timestamps")
    if (
        not data.start <= start < end <= data.end
        or min(start.second, end.second) != 0
        or start.microsecond
        or end.microsecond
    ):
        raise ValueError("Simulation range must lie within complete minute boundaries")
    if start.second != 0 or end.second != 0:
        raise ValueError("Minute-aligned simulation required")
    if funding.covered_from > start or funding.covered_until < end:
        raise ValueError("Funding coverage does not cover simulation")
    unique: dict[UUID, ResearchCandidate] = {}
    for raw in candidates:
        candidate = ResearchCandidate.model_validate(raw.model_dump())
        if (
            candidate.symbol != data.instrument.symbol
            or candidate.profile_version != profile.version
        ):
            raise ValueError("Candidate research context mismatch")
        if candidate.candidate_id in unique and unique[candidate.candidate_id] != candidate:
            raise ValueError("Conflicting candidate identity")
        if start <= candidate.decision_at < end:
            unique[candidate.candidate_id] = candidate
    queue = sorted(unique.values(), key=lambda c: (c.decision_at, str(c.candidate_id)))
    tick = data.instrument.tick_size
    quantity = grid(profile.quantity, data.instrument.quantity_step)
    cash = profile.initial_capital
    trades: list[Trade] = []
    curve = [EquityPoint(at=start, equity=cash)]
    pending: ResearchCandidate | None = None
    position: Position | None = None
    cursor = 0
    funding_cursor = 0
    filled = expired = rejected = blocked = 0
    for bar in data.bars:
        if bar.open_time < start or bar.open_time >= end:
            continue
        close_at = bar.close_time + timedelta(milliseconds=1)
        if pending is not None and close_at > pending.expires_at:
            expired += 1
            pending = None
        while cursor < len(queue) and queue[cursor].decision_at <= bar.open_time:
            candidate = queue[cursor]
            cursor += 1
            if position is not None or pending is not None:
                blocked += 1
            elif close_at > candidate.expires_at:
                expired += 1
            elif quantity <= 0 or any(
                price % tick != 0
                for price in [candidate.limit_price, candidate.stop_price, candidate.target_price]
            ):
                rejected += 1
            elif candidate.limit_price * quantity * (1 + profile.entry_fee_bps / 10000) > cash:
                rejected += 1
            else:
                pending = candidate
        filled_now = False
        if pending is not None and position is None and scenario == "TRADE_THROUGH":
            distance = tick * profile.penetration_ticks
            penetrated = (
                bar.low <= pending.limit_price - distance
                if pending.direction == "LONG"
                else bar.high >= pending.limit_price + distance
            )
            if penetrated:
                entry_fee = pending.limit_price * quantity * profile.entry_fee_bps / 10000
                cash -= entry_fee
                position = Position(pending, bar.open_time, quantity, entry_fee)
                pending = None
                filled += 1
                filled_now = True
        payments = []
        while funding_cursor < len(funding.events) and funding.events[funding_cursor].at < close_at:
            point = funding.events[funding_cursor]
            funding_cursor += 1
            if point.at >= bar.open_time:
                payments.append(point)
        if position is not None:
            candidate = position.candidate
            long = candidate.direction == "LONG"
            side = Decimal(1) if long else Decimal(-1)
            reason: ExitReason | None = None
            raw_exit = bar.close
            exit_at = close_at
            known_open_exit = False
            if bar.open_time >= position.at + timedelta(seconds=candidate.max_hold_seconds):
                reason = "TIMEOUT"
                raw_exit = bar.open
                exit_at = bar.open_time
                known_open_exit = True
            elif (long and bar.open <= candidate.stop_price) or (
                not long and bar.open >= candidate.stop_price
            ):
                reason = "STOP"
                raw_exit = bar.open
                exit_at = bar.open_time
                known_open_exit = True
            elif (long and bar.low <= candidate.stop_price) or (
                not long and bar.high >= candidate.stop_price
            ):
                reason = "STOP"
                raw_exit = candidate.stop_price
            elif not filled_now and (
                (long and bar.high >= candidate.target_price)
                or (not long and bar.low <= candidate.target_price)
            ):
                reason = "TARGET"
                raw_exit = candidate.target_price
            elif close_at >= end:
                reason = "END_OF_DATA"
            for payment in payments:
                if known_open_exit and payment.at > bar.open_time:
                    continue
                cost = side * position.quantity * payment.mark_price * payment.rate
                guaranteed_held = not filled_now and reason in {None, "END_OF_DATA"}
                if cost > 0 or guaranteed_held:
                    cash -= cost
                    position.funding += cost
            if reason is not None:
                if reason in {"TIMEOUT", "END_OF_DATA"}:
                    # Do not select a more profitable outcome by ignoring an existing target.
                    raw_exit = (
                        min(raw_exit, candidate.target_price)
                        if long
                        else max(raw_exit, candidate.target_price)
                    )
                exit_price = raw_exit * (1 - side * profile.slippage_bps / 10000)
                exit_fee = exit_price * position.quantity * profile.exit_fee_bps / 10000
                gross = side * (exit_price - candidate.limit_price) * position.quantity
                cash += gross - exit_fee
                fees = position.entry_fee + exit_fee
                trades.append(
                    Trade(
                        candidate_id=candidate.candidate_id,
                        strategy=candidate.strategy,
                        direction=candidate.direction,
                        entry_at=position.at,
                        exit_at=exit_at,
                        entry_price=candidate.limit_price,
                        exit_price=exit_price,
                        quantity=position.quantity,
                        reason=reason,
                        gross_pnl=gross,
                        fees=fees,
                        funding_cost=position.funding,
                        slippage_cost=abs(raw_exit - exit_price) * position.quantity,
                        net_pnl=gross - fees - position.funding,
                    )
                )
                position = None
        equity = cash
        if position is not None:
            direction = Decimal(1) if position.candidate.direction == "LONG" else Decimal(-1)
            equity += direction * (bar.close - position.candidate.limit_price) * position.quantity
        curve.append(EquityPoint(at=close_at, equity=equity))
    peak = profile.initial_capital
    drawdown = Decimal(0)
    for equity_point in curve:
        peak = max(peak, equity_point.equity)
        drawdown = max(drawdown, (peak - equity_point.equity) / peak * 100)
    wins = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losses = [-trade.net_pnl for trade in trades if trade.net_pnl < 0]
    if position is not None:
        raise ValueError("Simulation ended with unresolved position")
    return BacktestResult(
        scenario=scenario,
        profile_hash=profile.digest,
        dataset_hash=data.digest,
        candidate_count=len(queue),
        filled_entries=filled,
        expired_entries=expired,
        rejected_entries=rejected,
        blocked_entries=blocked,
        cancelled_entries=len(queue) - cursor + int(pending is not None),
        initial_equity=profile.initial_capital,
        final_equity=cash,
        trades=tuple(trades),
        equity_curve=tuple(curve),
        net_pnl=cash - profile.initial_capital,
        fees=sum((t.fees for t in trades), Decimal(0)),
        funding_cost=sum((t.funding_cost for t in trades), Decimal(0)),
        slippage_cost=sum((t.slippage_cost for t in trades), Decimal(0)),
        max_drawdown_pct=drawdown,
        win_rate=Decimal(len(wins)) / len(trades) * 100 if trades else None,
        profit_factor=sum(wins, Decimal(0)) / sum(losses, Decimal(0)) if losses else None,
    )
