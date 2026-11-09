# Validation —2026-11-05

Parent deployed artifact: windows-demo-alert-visibility-20261105 commit3853dde672521a411e5c3377b62bdd21e2d27630. Existing application/config/dependency/migration files are byte-for-byte unchanged. Added dedicated runtime module/CLI and tests only; no loopback test guard was relaxed.

Final runtime Ruff/format and strict mypy for both runtime files passed. New tests retain safety assertions. Initial test-fixture wiring and a validation-signal fixture were corrected without weakening any original test or production gate. Reuse rejection is checked before state readiness so an already used operation is reported consistently even when its prior cleanup left EMERGENCY.

Testing was performed in separate runs:

-41 passed in196.97s:32 initial scope/CLI tests,6 failure-path cases and3 real-process scenarios on the final runtime sources. OS tests include real child SIGKILL, original natural60s lease and real PostgreSQL plus synthetic HTTP/local WS. Scenarios: protected steady handoff; TP triggers during natural wait (not accepted, flat cleanup); recovery session failure (not accepted, flat cleanup).
-96 passed in45.30s: final expanded38 new scope/CLI tests,7 new failure-path cases, and51 existing scope/TP/opposite-quote/worker-drain regressions. The failure cases include unfilled, partial, stop failure, parent lost before entry, parent lost after partial fill, unresolved reduce-only exit and early child exit before acquire.
-The union contains99 unique passing tests, including48 new cases. This is a focused test result across runs, not a monolithic full-repository test result.

Assertions prove no kill before full protected readiness, child cooperative cleanup, no second entry transmission, first-generation reduce-only transmission at most once per role, with core durable intents preventing re-sending a pending first exit across roles; later-generation transmissions denied, no parent entry/protection POST, original owned IDs only, real -9/no finally marker for the protected kill, natural wait not shortened, advancing fence, stale effects blocked before callback, same-ID revalidation in a second local private session, and original history/baselines preserved. An unresolved exit stays UNKNOWN/paused and is not represented as flat. No actual exchange or recipient was contacted: credentials and execution were synthetic, SMTP was not invoked.

The two earlier development OS scenarios additionally printed: steady natural wait59654ms versus59652ms remaining,30 protection probes, synthetic fence2→3, entry1/protection2/parent exit1, final flat; trigger-during-wait59683ms versus59682ms remaining,30 probes, fence2→3, no parent exit and final flat, with continuity false/not accepted. These are development observations, not Windows timings or a latency SLA.

Windows build, private-key session use, authenticated DEMO fill/protection and actual child-kill recovery remain pending execution of the packaged CMD. Existing12h credentials are reused without extension. The package does not claim that a fill, protection trigger, SMTP delivery or Phase4 overall acceptance has already occurred.
