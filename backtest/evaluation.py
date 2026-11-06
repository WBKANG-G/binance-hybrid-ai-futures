"""Frozen hypotheses evaluated on chronological windows; no tuning on test outcomes."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from backtest.data import MarketData
from backtest.engine import simulate
from backtest.models import BacktestResult, FundingLedger, ResearchCandidate, ResearchProfile
from services.strategies.rules import RuleStrategy, StrategyName


@dataclass(frozen=True)
class Fold:
    ordinal: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


@dataclass(frozen=True)
class ResearchRun:
    fold: Fold
    strategy: StrategyName
    candidates: tuple[ResearchCandidate, ...]
    result: BacktestResult


def chronological_folds(
    data: MarketData, profile: ResearchProfile, *, count: int, test_hours: int
) -> tuple[Fold, ...]:
    if not 1 <= count <= 100 or not 1 <= test_hours <= 8760:
        raise ValueError("Invalid evaluation budget")
    warmup = max(profile.ema_slow, 2 * profile.adx_period, (profile.ema_pullback + 3) // 4) + 1
    train_end = data.start + timedelta(hours=warmup)
    embargo = timedelta(
        seconds=max(profile.trend_hold_seconds, profile.breakout_hold_seconds)
        + profile.entry_ttl_seconds
    )
    result = []
    for i in range(count):
        test_start = train_end + embargo
        test_end = test_start + timedelta(hours=test_hours)
        if test_end > data.end:
            raise ValueError("Insufficient data for requested forward windows and embargo")
        result.append(Fold(i + 1, data.start, train_end, test_start, test_end))
        train_end = test_end
    return tuple(result)


def evaluate(
    data: MarketData,
    profile: ResearchProfile,
    funding: FundingLedger,
    *,
    folds: int = 2,
    test_hours: int = 24,
) -> tuple[ResearchRun, ...]:
    windows = chronological_folds(data, profile, count=folds, test_hours=test_hours)
    result = []
    for name in ("TREND_FOLLOWING", "BREAKOUT"):
        strategy_name: StrategyName = name
        strategy = RuleStrategy(data, profile, strategy_name)
        for window in windows:
            candidates = strategy.candidates(window.test_start, window.test_end)
            for scenario in ("NEVER", "TRADE_THROUGH"):
                simulation = simulate(
                    data,
                    profile,
                    candidates,
                    funding,
                    scenario=scenario,
                    start=window.test_start,
                    end=window.test_end,
                )
                result.append(ResearchRun(window, strategy_name, candidates, simulation))
    return tuple(result)
