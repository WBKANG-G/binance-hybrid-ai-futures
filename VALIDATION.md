# Validation scope

This package adds a one-shot launcher only. It does not replace the installed serialized worker, change risk/lease/lock/freshness rules, or deploy/restart services.

36 offline pytest cases passed. They cover safe readiness, each missing/unsafe readiness field, period mismatch, wrong KST dates, crossing midnight during preflight, naive clocks, source mismatch/tampering, exactly one invocation, no retry after a failed trial, redacted exceptions, subprocess arguments, CMD log/hash/one-call guards, and the absence of an outer supervisor timeout. Ruff and strict mypy passed for the launcher (Ruff also checks the tests).

The launcher pins nine installed source/config hashes, including runtime SHA256 `9c264588fa7802efc0dcaa4f923153072fa85ba6fd888a23a2dda8ede4966238`. Core CLI exit zero is issued only for `PROTECTED_CHILD_SIGKILL_FENCED_RECOVERY_FLAT_VERIFIED` in the pinned CLI. Tests use injected callables and subprocess mocks; they do not read credentials, contact an exchange, send email, or submit orders.

The Windows CMD performs a launcher hash check, refuses to overwrite a prior result log, runs the launcher through Docker stdin, preserves the exit code, and displays the saved log. Windows execution and the actual new account trial have not been performed from this cloud environment. The existing runtime's prior validation is documented in the serial-fix artifact; this package does not claim another full runtime test run.
