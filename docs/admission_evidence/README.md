# Read-only TP entry evidence — Phase4

The installed runtime5abc9f6 remains unchanged. Its default TP admission refusal
still runs before leadership or reservation. The new probe is a standalone stdin
bundle, not an installation or an execution capability. It checks the exact55
installed runtime file hashes, reads existing private tmpfs session credentials,
and performs at most four GETs: time, exchangeInfo and two signed
`/fapi/v1/rateLimit/order` queries. It never contacts a LIVE host, modifies a DB,
acquires a lease, opens listen-key/WSS sessions, changes baselines or sends orders.

The new endpoint has a dedicated GET-only transport boundary with a fixed DEMO
host, fixed paths, exact signed-parameter validation, verified TLS, no redirects,
5-second request timeouts, bounded response bodies, a four-attempt cap and a
10-second monitored review deadline. A request already in progress can take its
5-second timeout to finish; there is no claim of cancelling an in-flight HTTP
request at an exact wall-clock deadline. Missing weight headers, pressure at60%
of the published minute limit including100 units of probe headroom, and418/429
stop the review. The original SDK sets and retains its cooldown for418/429; no
fallback or retries are added. Generic SDK request allowlists are unchanged.

## What the account query can actually establish

Official SDK source pinned atf75b4a52 describes the signed order-limit query as
USER_DATA with IP weight1. New regular orders and new algo orders are documented
as1 on the10-second and1 on the1-minute order rate limits. The query response
model declares `rateLimitType`, `interval`, `intervalNum` and `limit`. It does **not**
guarantee a `count` field. The source references and content hashes are in
OFFICIAL_SOURCE_PROOF.json. These source contracts are not a captured DEMO
response and do not prove availability for a particular account.

The probe therefore distinguishes limits from usage. Counts are accepted only if
explicitly returned in the JSON body or the two allowlisted numeric
`X-MBX-ORDER-COUNT-10S` / `X-MBX-ORDER-COUNT-1M` headers. Missing usage remains null,
never zero. The dedicated boundary preserves these two headers without changing
SDK metadata rules for the installed runtime. Other headers/private fields are
not emitted. Duplicate or malformed count headers stop the probe.

Both required windows and every additional advertised ORDERS window must be
covered by the account response. Public/account limits use the smaller value.
Both samples must be monotonic and no older than5 seconds; a falling counter or
changing account limit blocks the sampled headroom result. A lower sample never
refunds the higher observation. Six modeled slots are checked: one entry, two
protection creates, two possible algo-trigger executions and one risk-reducing
exit. The two trigger slots are conservative assumptions, not a proven exchange
counter upper bound. Sampling does not reserve capacity against other processes.

Possible results:

- `SAMPLED_HEADROOM_FITS`: both windows have explicit sampled usage and six slots.
- `LIMITS_VERIFIED_USAGE_UNAVAILABLE`: limits are available but usage is missing;
  counts and derived headroom remain null. Do not retry merely to turn missing
  metadata into zero, and do not infer usage from an empty position/order list.
- `SAMPLED_CONSTRAINT_BLOCKED`: observed counters/limits do not satisfy the plan.

Every outcome keeps `execution_approved=false`. The probe does not recheck
readiness, claim shared-IP capacity reservation, install the new source or enable
production trading. The installed default refusal is not relaxed.

## Verification

The builder concatenates the pure policy and the probe after removing exactly
one policy import. A test compares their complete ASTs and runs the standalone
bundle against the installed SDK. Unit tests cover counts present in body/headers,
missing counters, changing/decreasing limits/counts, all advertised windows,
malformed/duplicate headers, stale/nonfinite/backward clocks, exact four-request
limit, method/path denial, LIVE/mixed context denial, cooldown and redaction.
Docker smoke runs the bundle in the installed reference image with network=none,
explicit fake HTTP and no DB. It checks body-count, header-count and limits-only
responses separately. No real account requests or orders run in cloud verification.
