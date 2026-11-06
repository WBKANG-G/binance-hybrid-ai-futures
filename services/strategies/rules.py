"""Two frozen hypotheses producing research candidates, never executable orders."""

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from backtest.data import MarketData
from backtest.models import ResearchCandidate, ResearchProfile
from services.strategies.indicators import adx, atr, confirmed_pivots, ema

StrategyName = Literal["TREND_FOLLOWING", "BREAKOUT"]


def grid(value: Decimal, tick: Decimal, *, up: bool = False) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * tick


class RuleStrategy:
    def __init__(self, data: MarketData, profile: ResearchProfile, name: StrategyName) -> None:
        if name not in {"TREND_FOLLOWING", "BREAKOUT"}:
            raise ValueError("Unsupported V1 strategy")
        self.data = data
        self.profile = profile
        self.name = name
        self.hour = [f.bar for f in data.frames["1h"]]
        self.middle = [f.bar for f in data.frames["15m"]]
        self.execution = [f.bar for f in data.frames["5m"]]
        self.fast = ema([b.close for b in self.hour], profile.ema_fast)
        self.slow = ema([b.close for b in self.hour], profile.ema_slow)
        self.adx = adx(self.hour, profile.adx_period)
        self.pull = ema([b.close for b in self.middle], profile.ema_pullback)
        self.atr = atr(self.middle, profile.atr_period)
        self.pivots = confirmed_pivots(self.execution, profile.pivot_left, profile.pivot_right)

    def evaluate(self, at: datetime) -> ResearchCandidate | None:
        p = self.profile
        d = self.data
        h = d.index("1h", at)
        m = d.index("15m", at)
        e = d.index("5m", at)
        if min(h, m, e) < 0 or e < p.execution_lookback:
            return None
        fast, slow, strength = self.fast[h], self.slow[h], self.adx[h]
        volatility = self.atr[m]
        if (
            fast is None
            or slow is None
            or strength is None
            or volatility is None
            or strength < p.adx_threshold
            or fast == slow
        ):
            return None
        bar = self.execution[e]
        mid = self.middle[m]
        if at != bar.close_time + timedelta(milliseconds=1 + p.latency_ms):
            return None
        if at - self.hour[h].close_time > timedelta(
            hours=1, milliseconds=1 + p.latency_ms
        ) or at - mid.close_time > timedelta(minutes=15, milliseconds=1 + p.latency_ms):
            return None
        if not p.min_atr_ratio <= volatility / mid.close <= p.max_atr_ratio:
            return None
        long = fast > slow
        previous = self.execution[e - p.execution_lookback : e]
        if (long and bar.close <= max(b.high for b in previous)) or (
            not long and bar.close >= min(b.low for b in previous)
        ):
            return None
        if self.name == "TREND_FOLLOWING":
            average = self.pull[m]
            if (
                average is None
                or (long and mid.close <= average)
                or (not long and mid.close >= average)
            ):
                return None
            found = False
            for i in range(max(0, m - p.pullback_bars + 1), m + 1):
                avg, span = self.pull[i], self.atr[i]
                edge = self.middle[i].low if long else self.middle[i].high
                if (
                    avg is not None
                    and span is not None
                    and abs(edge - avg) <= p.pullback_atr_distance * span
                ):
                    found = True
            if not found:
                return None
            reward = p.trend_reward_r
            hold = p.trend_hold_seconds
        else:
            if m < p.breakout_lookback:
                return None
            preceding = self.middle[m - p.breakout_lookback : m]
            average_volume = sum((b.volume for b in preceding), Decimal(0)) / p.breakout_lookback
            if average_volume <= 0 or mid.volume < average_volume * p.volume_multiplier:
                return None
            if (long and mid.close <= max(b.high for b in preceding)) or (
                not long and mid.close >= min(b.low for b in preceding)
            ):
                return None
            reward = p.breakout_reward_r
            hold = p.breakout_hold_seconds
        tick = d.instrument.tick_size
        entry = grid(bar.close, tick, up=not long)
        pivot = self.pivots[e][0 if long else 1]
        baseline = (
            entry - volatility * p.stop_atr_multiple
            if long
            else entry + volatility * p.stop_atr_multiple
        )
        stop = (
            min(baseline, pivot)
            if long and pivot is not None
            else max(baseline, pivot)
            if not long and pivot is not None
            else baseline
        )
        stop = grid(stop, tick, up=not long)
        risk = abs(entry - stop)
        target = grid(entry + risk * reward if long else entry - risk * reward, tick, up=not long)
        if (
            min(entry, stop, target) <= 0
            or risk == 0
            or (long and not stop < entry < target)
            or (not long and not target < entry < stop)
        ):
            return None
        gross_target = abs(target - entry)
        estimated_cost = (
            entry * p.entry_fee_bps + target * (p.exit_fee_bps + p.slippage_bps)
        ) / 10000
        if gross_target <= estimated_cost:
            return None
        identity = (
            f"{p.digest}:{d.instrument.symbol}:{self.name}:{bar.open_time.isoformat()}:{long}"
        )
        return ResearchCandidate(
            candidate_id=uuid5(NAMESPACE_URL, identity),
            strategy=self.name,
            symbol=d.instrument.symbol,
            direction="LONG" if long else "SHORT",
            decision_at=at,
            expires_at=at + timedelta(seconds=p.entry_ttl_seconds),
            limit_price=entry,
            stop_price=stop,
            target_price=target,
            max_hold_seconds=hold,
            profile_version=p.version,
            based_on=(
                ("1h", self.hour[h].close_time),
                ("15m", mid.close_time),
                ("5m", bar.close_time),
            ),
        )

    def candidates(self, start: datetime, end: datetime) -> tuple[ResearchCandidate, ...]:
        result = []
        for frame in self.data.frames["5m"]:
            at = frame.bar.close_time + timedelta(milliseconds=1 + self.profile.latency_ms)
            if start <= at < end:
                candidate = self.evaluate(at)
                if candidate is not None:
                    result.append(candidate)
        return tuple(result)
