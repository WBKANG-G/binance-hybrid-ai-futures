"""Explicit local dataset and coverage import. Never downloads or guesses missing inputs."""

import csv
from pathlib import Path

import yaml

from backtest.data import MarketData
from backtest.models import FundingLedger, Instrument, ResearchProfile
from packages.domain.config import UniqueKeyLoader
from services.market_data.normalization import Candle


def load_profile(path: Path) -> ResearchProfile:
    return ResearchProfile.model_validate(yaml.load(path.read_text(), Loader=UniqueKeyLoader))


def load_dataset(
    bars_path: Path, instrument_path: Path, funding_path: Path, profile: ResearchProfile
) -> tuple[MarketData, FundingLedger]:
    instrument = Instrument.model_validate_json(instrument_path.read_text())
    funding = FundingLedger.model_validate_json(funding_path.read_text())
    bars = []
    with bars_path.open(newline="") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("closed") not in {"true", "false"}:
                raise ValueError("CSV must explicitly label closed bars")
            data: dict[str, object] = dict(raw)
            data["closed"] = raw["closed"] == "true"
            bars.append(Candle.model_validate(data))
    return MarketData(bars, instrument, profile), funding


def export_inputs(output: Path, data: MarketData, funding: FundingLedger) -> None:
    with (output / "input-bars.csv").open("x", newline="") as handle:
        fields = list(data.bars[0].model_dump())
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for bar in data.bars:
            row = bar.model_dump(mode="json")
            row["closed"] = "true" if bar.closed else "false"
            writer.writerow(row)
    (output / "input-instrument.json").write_text(data.instrument.model_dump_json(indent=2))
    (output / "input-funding.json").write_text(funding.model_dump_json(indent=2))
