# TP request budget integration — Phase 4

This branch connects the canonical TP trigger CLI to the integrated window budget
and coordinator. It retains default execution refusal before a lease, operation
marker or reservation, without an environment or command-line override. The new
paired installation guard permits only a paused, exchange-flat code update. The
application remains in Phase4 and does not resume entries or adopt period values.

The prior integration source99ef514 was synthetic-only and did not select the new
path from the canonical CLI. Its natural-time evidence remains model evidence;
this branch additionally tests the CLI selection and source-pinned deployment.

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


## Paused code installation

`guard_tp_budget_install.py` accepts exactly the45-file installed account-config
cohort before installation and the55-file new runtime cohort after installation.
The postcheck requires the precheck account digest, exchange digest and signed-read
count. Both phases use the installed accountConfig snapshot parser and require the
same request count, complete ledger equality (including owner/fence/lease), manual
close receipt, failed trial marker, period keys/baselines and remote financial and
transfer fields. The guard never acquires a lease, writes an event, applies recovery,
changes a baseline or sends a non-GET exchange request. Period mismatch is reported,
not repaired or treated as trading readiness. It is still acceptable for code-only
installation while paused and flat.

The postcheck also verifies the default budget refusal with an offline transport;
that selfcheck sends no real exchange requests. Production source pins are fixed,
not regenerated by the Windows command. Missing or mixed source files block the
update before access to credentials or the database. The existing account-config
and earlier installation guards retain their original pins; they are not reused
as authorization for this new source.

The canonical CLI keeps its previous explicit flags but removes selection of the
old unbudgeted coordinators. Every HTTP attempt, including initial clock discovery,
passes the budget. It binds the historical count from the read-only ledger and then
performs admission before acquiring leadership. Real execution remains disabled.
Only the synthetic lifecycle test patches the budget constructor to permit its
fake transport; no production flag or environment variable performs that patch.

Docker smoke uses the already verified installed runtime image with copied source
layers and no build network. It checks both pinned image cohorts, default refusal
and API readiness. It is not a fresh dependency download or access to Windows.
