# Validation — 2026-11-05

Parent: windows-demo-opposite-tp-review-20261105, artifact commit79eb040; source staged separately from clean main64e9e7b.

- Backend: 98 tests passed, including real PostgreSQL isolated-schema alert snapshot/no-write/account scoping, API503/secret redaction, public private-RAM expiry metadata, original email-session/SMTP wire/outbox/delivery/API readiness regressions. No actual exchange or recipient: synthetic fixtures and loopback SMTP/HTTP only.
- Frontend: 18 tests passed (12 new panel tests +6 unchanged App tests). Missing/expired config, separate TEST counts, historical85min fixture delay, unsafe flags, invalid data, refresh failure removing stale values, timeout and unmount cleanup verified in jsdom.
- Ruff and strict mypy passed for four changed/new runtime Python files; Ruff passed new tests. Frontend TypeScript, ESLint and production build passed. Dependencies and lockfiles unchanged.
- DB reads executed SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY; SET LOCAL timeout; exactly two SELECT. Full accounts/events/order outbox/urgent alerts/delivery attempts snapshots and synthetic exchange calls unchanged by status queries.
- Actual Windows API/web build, source verification and page inspection remain pending user execution. No browser screenshot or visual-layout check was performed in cloud. The endpoint does not measure mail worker liveness, SMTP connectivity or inbox arrival.

User evidence preceding this patch: real historical EMERGENCY_ENTERED alert34c33ac4-c293-5fca-ba25-2d96ee8694de reached the mailbox after the missing SMTP RAM configuration was registered. Event10:21:45UTC; mailbox20:47KST, about85min later. This proves that historical message arrived, not real-time delivery performance. No new alert/mail/trade was generated for this patch.
