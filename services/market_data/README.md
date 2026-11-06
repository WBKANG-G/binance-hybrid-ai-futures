# Public market data — Phase 1

Normalization validates exact Decimal amounts, UTC, candle boundaries/OHLC, closed flags and depth levels.
CandleTracker detects gaps, reversals and conflicting final bars. DepthBook needs a REST snapshot and
sequence bridge, then requires pu continuity. Both expose as_of/freshness; reconnects invalidate trust.
MarketCollector archives public raw payloads, writes canonical closed bars and synchronized best quotes,
and persists status separately. It never executes orders. Public/market sockets use separate routes.

Use scripts.collect_market or scripts.backfill_market after migration; see docs/runbook.md.
