import json
import socket
import sys
from pathlib import Path

from backtest.io import load_dataset, load_profile
from scripts.run_backtest import main


def test_offline_cli_executes_real_forward_windows_and_exports_reusable_inputs(
    tmp_path, monkeypatch
):
    def deny(*args, **kwargs):
        raise AssertionError("Research must not open any network connection")

    monkeypatch.setattr(socket, "create_connection", deny)
    output = tmp_path / "research"
    monkeypatch.setattr(
        sys,
        "argv",
        ["research", "--demo", "--folds", "1", "--test-hours", "4", "--output", str(output)],
    )
    assert main() == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["research_only"] and summary["source"] == "SYNTHETIC"
    assert len(summary["runs"]) == 4
    assert sum(run["metrics"]["filled_entries"] for run in summary["runs"]) > 0
    assert "NOT MARKET PERFORMANCE" in (output / "report.html").read_text()
    profile = load_profile(Path("configs/research.example.yaml"))
    reloaded, ledger = load_dataset(
        output / "input-bars.csv",
        output / "input-instrument.json",
        output / "input-funding.json",
        profile,
    )
    assert reloaded.digest == summary["dataset_hash"]
    assert ledger.events
    assert all((output / run["trades_file"]).exists() for run in summary["runs"])
