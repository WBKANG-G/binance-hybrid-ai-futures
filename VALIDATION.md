# Validation —2026-11-06

20 tests passed in0.02s. Exact fresh synthetic token forwarding, fixed operation, Nov6 KST date guard, old/proposed amount guards (including legitimate lower proposed base), latest exit/anchor/boundary guards, NORMAL/paused/disabled flags, preserved weekly/high-water/request/risk fields, unchanged net deposits, already-current read-only path, uncertain apply/no retry and post-apply readiness failure tested. Existing assertions retained from the Nov5 helper and additional candidate bindings added.

Ruff check/format passed using the application's existing rule set. No production app code/dependency changes. No actual exchange call, database application, order, SMTP delivery or process kill was performed by this cloud preparation. The existing canonical signed review/application logic is reused rather than reimplemented. Each subprocess is bounded150s; timeout after application starts remains application_may_have_completed=true, with no automatic retry.

Candidate numbers and exit/anchor times were allowlist-transcribed from the latest user screenshot. Its actual review token was not copied into code, tests, documentation or commands. All test tokens are synthetic. Windows application and final read-only readiness remain pending.
