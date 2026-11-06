"""Causal as-of views; full frame construction never exposes future aggregates."""

import hashlib
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from backtest.models import Instrument, ResearchProfile
from services.market_data.normalization import Candle, epoch_ms, interval_ms


@dataclass(frozen=True)
class AvailableBar:
    bar: Candle
    available_at: datetime


class MarketData:
    def __init__(
        self, bars: list[Candle], instrument: Instrument, profile: ResearchProfile
    ) -> None:
        self.instrument = Instrument.model_validate(instrument.model_dump())
        self.profile = ResearchProfile.model_validate(profile.model_dump())
        instrument = self.instrument
        profile = self.profile
        if not bars:
            raise ValueError("Research data cannot be empty")
        validated = tuple(Candle.model_validate(bar.model_dump()) for bar in bars)
        previous = None
        releases = []
        for bar in validated:
            if (
                bar.symbol != instrument.symbol
                or bar.interval != "1m"
                or not bar.closed
                or bar.open_time < instrument.listed_at
            ):
                raise ValueError("Expected listed-symbol closed 1m bars")
            if previous is not None and bar.open_time - previous != timedelta(minutes=1):
                raise ValueError("Research data has duplicate, reverse or missing bars")
            available = max(bar.close_time + timedelta(milliseconds=1), bar.as_of)
            if profile.availability == "captured":
                available = max(available, bar.received_at)
            releases.append(available + timedelta(milliseconds=profile.latency_ms))
            previous = bar.open_time
        self.bars = validated
        self.start = validated[0].open_time
        self.end = validated[-1].close_time + timedelta(milliseconds=1)
        self.frames: dict[str, tuple[AvailableBar, ...]] = {
            "1m": tuple(
                AvailableBar(bar, release) for bar, release in zip(validated, releases, strict=True)
            )
        }
        for interval in ["5m", "15m", "1h"]:
            step = interval_ms(interval)
            count = step // 60000
            groups: dict[int, list[int]] = {}
            for i, bar in enumerate(validated):
                groups.setdefault(epoch_ms(bar.open_time) // step, []).append(i)
            frames = []
            for key, indices in groups.items():
                if len(indices) != count or epoch_ms(validated[indices[0]].open_time) != key * step:
                    continue
                group = [validated[i] for i in indices]
                aggregate = Candle(
                    symbol=instrument.symbol,
                    interval=interval,
                    open_time=group[0].open_time,
                    close_time=group[-1].close_time,
                    open=group[0].open,
                    high=max(b.high for b in group),
                    low=min(b.low for b in group),
                    close=group[-1].close,
                    volume=sum((b.volume for b in group), start=Decimal(0)),
                    quote_volume=sum((b.quote_volume for b in group), start=Decimal(0)),
                    closed=True,
                    as_of=max(b.as_of for b in group),
                    received_at=max(b.received_at for b in group),
                )
                frames.append(AvailableBar(aggregate, max(releases[i] for i in indices)))
            self.frames[interval] = tuple(frames)
        self.releases: dict[str, tuple[datetime, ...]] = {}
        for interval, available_frames in self.frames.items():
            ordered = []
            last = None
            for frame in available_frames:
                last = frame.available_at if last is None else max(last, frame.available_at)
                ordered.append(last)
            self.releases[interval] = tuple(ordered)
        self.digest = hashlib.sha256(
            json.dumps(
                {
                    "instrument": instrument.model_dump(mode="json"),
                    "bars": [bar.model_dump(mode="json") for bar in validated],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def index(self, interval: str, at: datetime) -> int:
        if at.tzinfo is None:
            raise ValueError("Timezone-aware decision required")
        return bisect_right(self.releases[interval], at) - 1
