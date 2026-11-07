import json
from pathlib import Path

from packages.domain.config import AppConfig
from packages.domain.contracts import AlgoOrder, DomainEvent, EntryOrder, Position, Signal


def test_checked_in_schemas_match_models() -> None:
    assert (
        json.loads(Path("configs/config.schema.json").read_text()) == AppConfig.model_json_schema()
    )
    for model in [AlgoOrder, DomainEvent, EntryOrder, Position, Signal]:
        assert (
            json.loads(Path(f"docs/schemas/{model.__name__}.json").read_text())
            == model.model_json_schema()
        )


def test_frozen_research_profile_example_and_schema_match():
    from backtest.io import load_profile
    from backtest.models import ResearchProfile

    assert (
        json.loads(Path("configs/research.schema.json").read_text())
        == ResearchProfile.model_json_schema()
    )
    assert load_profile(Path("configs/research.example.yaml")) == ResearchProfile()


def test_paper_sandbox_contract_and_separation():
    import yaml

    from services.risk.paper import PaperPolicy

    assert (
        json.loads(Path("configs/paper-sandbox.schema.json").read_text())
        == PaperPolicy.model_json_schema()
    )
    policy = PaperPolicy.model_validate(
        yaml.safe_load(Path("configs/paper-sandbox.example.yaml").read_text())
    )
    assert policy.mode == "PAPER" and policy.sandbox_only


def test_demo_settings_schema_and_default_write_gate():
    import yaml

    from integrations.binance_usdm.demo import DemoSettings

    assert (
        json.loads(Path("configs/demo.schema.json").read_text()) == DemoSettings.model_json_schema()
    )
    config = DemoSettings.model_validate(
        yaml.safe_load(Path("configs/demo.example.yaml").read_text())
    )
    assert (
        config.mode == "DEMO"
        and not config.demo_trading_enabled
        and not config.live_trading_enabled
    )
