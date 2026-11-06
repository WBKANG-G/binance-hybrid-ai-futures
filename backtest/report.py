"""Standalone audit outputs with explicit assumptions; no performance promises."""

import csv
import hashlib
import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backtest.data import MarketData
from backtest.evaluation import ResearchRun
from backtest.models import FundingLedger, ResearchProfile

ASSUMPTIONS = (
    "BACKTEST ONLY. No exchange orders, risk approvals, AI or account simulation.",
    "Historical-close availability is an assumption, not recovered historical revisions or receipt latency.",
    "NEVER and TRADE_THROUGH are fill assumptions, not an estimated market fill probability or guaranteed PnL bounds.",
    "Entry/exit OHLC sequencing is unknown; stops win ties, fill-bar targets are not credited, gap/slippage/costs apply.",
    "Funding credits are omitted when holding across payment time is uncertain; declared coverage is required.",
    "Fixed quantity with cash-limited notional; queue priority, partial fills, liquidation and actual margin are unmodeled.",
    "Drawdown uses 1m-close equity, not exact intrabar maximum drawdown.",
    "No hyperparameter fitting; each test has an earlier warm-up prefix and holding-period/TTL embargo.",
)


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    output: Path,
    data: MarketData,
    profile: ResearchProfile,
    funding: FundingLedger,
    runs: tuple[ResearchRun, ...],
) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError("Refusing to overwrite existing research outputs")
    if not runs:
        raise ValueError("Cannot report zero evaluation runs")
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for run in runs:
        stem = f"fold-{run.fold.ordinal}-{run.strategy.lower()}-{run.result.scenario.lower()}"
        trade_rows = [trade.model_dump(mode="json") for trade in run.result.trades]
        trade_fields = (
            list(run.result.trades[0].model_dump())
            if run.result.trades
            else [
                "candidate_id",
                "strategy",
                "direction",
                "entry_at",
                "exit_at",
                "entry_price",
                "exit_price",
                "quantity",
                "reason",
                "gross_pnl",
                "fees",
                "funding_cost",
                "slippage_cost",
                "net_pnl",
            ]
        )
        write_csv(output / f"{stem}.trades.csv", trade_rows, trade_fields)
        write_csv(
            output / f"{stem}.equity.csv",
            [point.model_dump(mode="json") for point in run.result.equity_curve],
            ["at", "equity"],
        )
        candidates = []
        for candidate in run.candidates:
            row = candidate.model_dump(mode="json")
            row["based_on"] = json.dumps(row["based_on"])
            candidates.append(row)
        fields = (
            list(candidates[0])
            if candidates
            else [
                "candidate_id",
                "strategy",
                "direction",
                "decision_at",
                "limit_price",
                "stop_price",
                "target_price",
                "based_on",
            ]
        )
        write_csv(output / f"{stem}.candidates.csv", candidates, fields)
        records.append(
            {
                "strategy": run.strategy,
                "fold": {
                    k: v.isoformat() if hasattr(v, "isoformat") else v
                    for k, v in asdict(run.fold).items()
                },
                "metrics": run.result.model_dump(mode="json", exclude={"trades", "equity_curve"}),
                "trades_file": f"{stem}.trades.csv",
                "equity_file": f"{stem}.equity.csv",
                "candidates_file": f"{stem}.candidates.csv",
            }
        )
    root = Path(__file__).resolve().parents[1]
    sources = sorted(
        [
            *root.glob("backtest/*.py"),
            *root.glob("services/strategies/*.py"),
            root / "services/market_data/normalization.py",
            root / "packages/domain/config.py",
            root / "packages/domain/contracts.py",
            root / "scripts/run_backtest.py",
        ]
    )
    implementation = hashlib.sha256()
    for path in sources:
        implementation.update(str(path.relative_to(root)).encode())
        implementation.update(path.read_bytes())
    ledger = funding.model_dump(mode="json")
    summary = {
        "research_only": True,
        "implementation_hash": implementation.hexdigest(),
        "source": data.instrument.provenance,
        "data_range": {
            "start": data.start.isoformat(),
            "end": data.end.isoformat(),
            "bars": len(data.bars),
        },
        "profile": profile.model_dump(mode="json"),
        "profile_hash": profile.digest,
        "dataset_hash": data.digest,
        "instrument": data.instrument.model_dump(mode="json"),
        "funding_hash": hashlib.sha256(json.dumps(ledger, sort_keys=True).encode()).hexdigest(),
        "funding_coverage": {
            "from": funding.covered_from.isoformat(),
            "until": funding.covered_until.isoformat(),
            "events": len(funding.events),
        },
        "assumptions": ASSUMPTIONS,
        "runs": records,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    title = (
        "SYNTHETIC FIXTURE — NOT MARKET PERFORMANCE"
        if data.instrument.provenance == "SYNTHETIC"
        else "RESEARCH ONLY — UNVALIDATED MARKET HYPOTHESIS"
    )
    rows = []
    for record in records:
        metrics = record["metrics"]
        values = [
            record["fold"]["ordinal"],
            record["strategy"],
            metrics["scenario"],
            metrics["candidate_count"],
            metrics["filled_entries"],
            metrics["net_pnl"],
            metrics["fees"],
            metrics["funding_cost"],
            metrics["max_drawdown_pct"],
        ]
        rows.append(
            "<tr>"
            + "".join("<td>" + html.escape(str(value)) + "</td>" for value in values)
            + "</tr>"
        )
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Research audit</title><style>body{font:15px system-ui;margin:40px;max-width:1200px}h1{color:#a22}table{border-collapse:collapse}th,td{padding:10px;border:1px solid #ccc}code{overflow-wrap:anywhere}</style><h1>'
        + html.escape(title)
        + "</h1><p>Profile: <code>"
        + profile.digest
        + "</code></p><p>Dataset: <code>"
        + data.digest
        + "</code></p><table><thead><tr>"
        + "".join(
            "<th>" + name + "</th>"
            for name in [
                "Fold",
                "Strategy",
                "Fill assumption",
                "Candidates",
                "Fills",
                "Net PnL",
                "Fees",
                "Funding cost",
                "1m MDD %",
            ]
        )
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table><ul>"
        + "".join("<li>" + html.escape(note) + "</li>" for note in ASSUMPTIONS)
        + "</ul><p>See summary.json and per-run CSV files for full UTC timestamps, data assumptions and trades.</p></html>"
    )
    (output / "report.html").write_text(document)
