# TP request budget integration — Phase 4

This branch implements an integrated path for synthetic HTTP/local WSS validation.
It does not install a Windows runtime or enable the existing TP CLI. Real execution
is disabled by default, even if exchangeInfo reports a large limit. The original
CLI and existing coordinators retain their behavior.

The earlier admission model reserved the entire episode's cleanup cost in one
minute. Its synthetic lifecycle tests used a counterfactual 100,000 capacity and
then found that delayed cancellation and recovery still exceeded 6,000. Those
results were development failures for the integrated minute target, not permission
to install or trade.

The new path uses `WindowTPBudget` and `BudgetedTPTriggerCoordinator`:

- Charge every modeled request before forwarding, including failed/possible sends.
  Retain each charge for 60.001 seconds. Lower response counters and wall-clock
  minute changes never refund an unexpired own request. A lock serializes charging;
  concurrent and out-of-order responses cannot refund own charges.
- Use the published minute limit minus 300 as the wire ceiling. Reserve 512 for
  physical risk reduction before admitting the single capped LIMIT entry. Do not
  require the whole financial verification episode to fit the entry minute.
- Freeze ordinary activity at the reserve. Clock synchronization needed to reach
  the original fenced rescue remains possible within the same total ceiling.
- Pause locally, attempt the owned remaining entry cancellation once, inspect
  global positions, and use the original fenced reduce-only exit intent/effect.
  The transport permits only the owned exit ID, direction and bounded quantity,
  once. A zero position is resource scheduling evidence; it is not financial proof.
- A zero position with a still-uncertain entry cannot release the rescue reserve.
  A foreign position, wrong direction, oversized quantity or closed historical
  ownership never authorizes a new exit.
- Keep the original full historical confirmation, including its five snapshots
  on a successful attempt. No ACK, zero position, cheap poll or local budget
  deferral can establish VERIFIED_FLAT. Persistent cancellation failure stays
  UNKNOWN. Existing uncertain-cancel tickets, SDK cooldowns and per-proof query/
  time budgets remain in force.
- If full confirmation lacks credit, wait only after observing a zero position
  and terminal remaining entry. Renew the lease and check the position while
  waiting, outside the proof/DB transaction. Bound resource waiting to 120 seconds
  and completed confirmations to four. Local resource deferrals do not become
  hidden order retries or repeatedly restart full historical queries.

The route costs, ten income page bound and historical cohort are model assumptions,
not proven exchange upper bounds. Shared-IP activity and separate order-count
limits are not reserved. An execution stop remains a failed TP acceptance even
when later budgeted rescue establishes flat; the separate diagnostic must not be
promoted to successful TP acceptance.

## Reproduce

Use an isolated PostgreSQL test database and the project's pinned development
Python environment. The fixtures create and drop unique schemas, use synthetic
credentials, route every HTTP call to FakeDemo, and connect WSS only to localhost.
No exchange endpoint is contacted.

```bash
TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:55435/hybrid \
  pytest tests/unit/test_tp_window_budget.py tests/integration/test_tp_budget_rescue.py

TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:55435/hybrid \
TP_BUDGET_DURATION=300 TP_BUDGET_REPORT_DIR=/tmp/tp-budget-reports \
  pytest tests/integration/test_tp_budget_integration.py -k 'not burst and not partial'
```

The four natural-time cases use an actual synthetic published capacity of 6,000,
300 unaccelerated seconds of observation and ten income pages during finalization.
They independently analyze every possible minute start phase, check the guarded
rolling peak against 5,700, preserve historical records/baselines, forbid 429s,
require one entry, forbid exit retries and retain full successful confirmation.
Burst and partial-fill tests deliberately exercise earlier stop/rescue paths;
their short observation windows must not be presented as natural 300 second tests.
