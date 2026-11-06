"""Deterministic plumbing dataset, not a model of any actual market or returns."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backtest.data import MarketData
from backtest.models import FundingLedger, FundingPoint, Instrument, ResearchProfile
from services.market_data.normalization import Candle


def synthetic_dataset(
    profile: ResearchProfile, *, days: int = 12
) -> tuple[MarketData, FundingLedger]:
    if not 1 <= days <= 60:
        raise ValueError("Synthetic fixture budget must be 1..60 days")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    count = days * 1440
    result = []
    price = Decimal("100")
    for i in range(count):
        at = start + timedelta(minutes=i)
        before = price
        price += Decimal("0.002")
        if (i + 1) % 120 == 0:
            price += Decimal("0.2")
        result.append(
            Candle(
                symbol="BTCUSDT",
                interval="1m",
                open_time=at,
                close_time=at + timedelta(minutes=1) - timedelta(milliseconds=1),
                open=before,
                high=max(before, price) + Decimal("0.1"),
                low=min(before, price) - Decimal("0.1"),
                close=price,
                volume=Decimal("110") if (i + 1) % 120 == 0 else Decimal("10"),
                quote_volume=price * (110 if (i + 1) % 120 == 0 else 10),
                closed=True,
                as_of=at + timedelta(minutes=1) - timedelta(milliseconds=1),
                received_at=at + timedelta(minutes=1),
            )
        )
    end = start + timedelta(days=days)
    events = tuple(
        FundingPoint(
            at=start + timedelta(hours=i), rate=Decimal("0.0001"), mark_price=result[i * 60].open
        )
        for i in range(8, days * 24, 8)
    )
    instrument = Instrument(
        symbol="BTCUSDT",
        listed_at=start,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        provenance="SYNTHETIC",
    )
    return MarketData(result, instrument, profile), FundingLedger(
        covered_from=start, covered_until=end, events=events
    )
