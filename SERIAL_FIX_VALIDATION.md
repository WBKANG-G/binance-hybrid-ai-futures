# Validation —2026-11-06

- Reproduction against prior deployed runtime0e702745... using its own pytest config/import path: same new regression fails with recorded SQLSTATE55P03 when the readiness pause holds the actual PostgreSQL account row2.4s and event dispatch overlaps it. The new source passes the same assertions. No original test was weakened and the database2s lock_timeout is unchanged.
-100 focused execution/scope/TP/opposite-quote/worker-drain/serialization tests passed in233.20s on fixed runtime, including3 real child SIGKILL/natural60s PostgreSQL lease scenarios with synthetic HTTP/local WS. Original scene acceptance unchanged: steady handoff success; protection trigger during wait and recovery-session failure remain not accepted with flat cleanup.
-8 diagnostic read-only/projection tests passed in1.34s, now explicitly asserting normal STOP/TARGET VERIFIED statuses and excluding a later owner's failures.
-18 fix-installer guard tests passed in4.79s: pre/post source hashes, static review-only subprocess without apply flags, invalid/moving candidate rejection, token/exception redaction; real PG accepts only inactive-owner/scoped closed fill and refuses wrong operation or active owner, with no exchange call/write from local guard.
-126 unique focused tests passed across these runs. Not a monolithic full-repository suite.
-Ruff/format passed for all modified/new Python files. Strict mypy passed the runtime and its existing CLI. No dependency/config/migration/global execution policy changes. Root main64e9e7b remains clean.

Windows input: ready marker absent/finally present, exit2, kill0, original fence91/new parent92 with no natural wait, history preserved, parent transport attempts0, VERIFIED_FLAT/leader released, EMERGENCY/paused. Session EVENT/DB_LOCK_TIMEOUT/ALGO_UPDATE, ready callback20740ms, received6/coalesced5, event age2494ms, receive age151ms. These are user-reported local durable observations; current diagnostic did not query exchange. Raw child exceptions from that old run were not retained. The fix does not retroactively fill missing evidence.

No actual exchange, recipient, order, production DB mutation or risk acknowledgement was performed during cloud work. Installer's actual Windows pre/post canonical reviews and deployment are pending. On ambiguous results it stops; no retry/unlock/new trial is bundled.
