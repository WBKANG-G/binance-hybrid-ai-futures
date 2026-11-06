"""Regenerate committed public contracts; CI checks them without rewriting."""

import json
from pathlib import Path

from pydantic import BaseModel

from backtest.models import ResearchProfile
from integrations.binance_usdm.demo import DemoSettings
from packages.domain.config import AppConfig
from packages.domain.contracts import AlgoOrder, DomainEvent, EntryOrder, Position, Signal
from services.risk.paper import PaperPolicy


def main() -> None:
    targets: dict[Path, type[BaseModel]] = {
        Path("configs/config.schema.json"): AppConfig,
        Path("configs/demo.schema.json"): DemoSettings,
        Path("configs/paper-sandbox.schema.json"): PaperPolicy,
        Path("configs/research.schema.json"): ResearchProfile,
        Path("docs/schemas/AlgoOrder.json"): AlgoOrder,
        Path("docs/schemas/DomainEvent.json"): DomainEvent,
        Path("docs/schemas/EntryOrder.json"): EntryOrder,
        Path("docs/schemas/Position.json"): Position,
        Path("docs/schemas/Signal.json"): Signal,
    }
    for path, model in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
