# AGENTS.md — Development Rules

1. `MASTER_SPEC_V1_0.md` is the source of truth. `[LOCKED]` requirements must not be changed without user approval.
2. Never enable live trading or place real orders. Treat all `OPEN` live risk parameters as unresolved.
3. Prioritize risk controls, idempotency, exchange reconciliation, and audited state transitions above performance enhancements.
4. Write tests for every safety-critical path; never delete or weaken a failing test to pass CI.
5. No market orders for **new entries** in V1; emergency risk-reducing exits may be exceptions.
6. Stop-loss algo orders are separate from regular orders. Handle fills, partial fills, cancels, and unknown states explicitly.
7. Use UTC internally, Decimal for financial amounts, and immutable event IDs/versioned config.
8. Every phase needs user approval before starting the next phase.
9. Secrets must never be committed, logged, or sent to the browser.
10. Report actual test results and remaining limitations; do not claim that a successful test implies trading profitability.
