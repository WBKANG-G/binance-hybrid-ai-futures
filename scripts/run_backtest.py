"""Run frozen BACKTEST-only hypotheses on explicit local inputs; no network or orders."""

import argparse
from pathlib import Path

import yaml

from backtest.evaluation import evaluate
from backtest.io import export_inputs, load_dataset, load_profile
from backtest.report import write_report
from backtest.synthetic import synthetic_dataset
from packages.domain.config import load_config, validate_startup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--demo", action="store_true", help="Deterministic synthetic fixture, never market returns"
    )
    source.add_argument("--bars", type=Path, help="Explicit closed 1m Candle CSV")
    parser.add_argument("--instrument", type=Path)
    parser.add_argument("--funding", type=Path)
    parser.add_argument("--profile", type=Path, default=Path("configs/research.example.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--demo-days", type=int, default=12)
    parser.add_argument("--folds", type=int, default=2)
    parser.add_argument("--test-hours", type=int, default=24)
    args = parser.parse_args()
    try:
        validate_startup(load_config())
        if args.output.exists() and any(args.output.iterdir()):
            raise ValueError("Existing report files must be preserved")
        profile = load_profile(args.profile)
        if args.demo:
            if args.instrument or args.funding:
                raise ValueError("Demo must not silently ignore real metadata or funding inputs")
            data, funding = synthetic_dataset(profile, days=args.demo_days)
        else:
            if not args.instrument or not args.funding:
                raise ValueError("Local bars require explicit instrument and funding coverage")
            data, funding = load_dataset(args.bars, args.instrument, args.funding, profile)
        runs = evaluate(data, profile, funding, folds=args.folds, test_hours=args.test_hours)
        write_report(args.output, data, profile, funding, runs)
        if args.demo:
            export_inputs(args.output, data, funding)
        print(
            f"BACKTEST ONLY: source={data.instrument.provenance}, bars={len(data.bars)}, runs={len(runs)}, candidate_evaluations={sum(len(run.candidates) for run in runs)}, simulated_fills={sum(run.result.filled_entries for run in runs)}"
        )
        print(f"Research artifacts: {args.output / 'summary.json'}")
        return 0
    except (ValueError, OSError, yaml.YAMLError):
        print(
            "Research failed: check the frozen profile, closed-bar continuity, listing/funding coverage, forward-window budget and empty output directory. No orders were sent."
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
