# BACKTEST-only research

models/data: frozen profile, instrument/listing/funding contracts, causal multi-timeframe views.
engine: strict trade-through or never-fill scenarios, costs/funding, adverse sequencing, no order transport.
evaluation: chronological warm-up/embargo/test windows with no tuning on test results.
report/io: hash-based audit JSON, standalone HTML, candidate/trade/equity CSV and explicit local inputs.
synthetic: deterministic pipeline fixtures, never actual market performance.

Run scripts.run_backtest from the repository root. See docs/phase2-research-protocol.md and docs/runbook.md.
