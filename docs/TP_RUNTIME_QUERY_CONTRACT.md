# Modeled query-shape contract — Phase 4 candidate

The modeled request cost was keyed by method/path, while the owned client could
send other parameter forms. On parent `912372460afeb5c328ee61ccc523f65026bbafa3`,
six regression cases reached the synthetic sender: klines limit=1500 and omitted
symbol on premiumIndex/bookTicker, through both the client and direct budget.
They were accepted with the same small modeled cost as the intended requests.
No real exchange usage or actual over-limit event was measured by this test.

## Documentation evidence and approved scope

The pinned official SDK commit `f75b4a52` documents klines weight by LIMIT
(2 bars: 1; 1500 bars: 10), premiumIndex weight 1 with symbol/10 without,
and bookTicker weight 2 with symbol/5 without. The immutable source URL, checksum,
method names, and line references are in
`request_cost_evidence/PINNED_PUBLIC_QUERY_PROOF.json`.

The narrow runtime contract now accepts only the existing isolated BTC flow:

- time/exchangeInfo: no public query parameters;
- bookTicker/premiumIndex: exactly symbol=BTCUSDT;
- klines: exactly BTCUSDT, interval=1m, limit=2;
- global account/config/position/open-order reads: no symbol or filter;
- income proof: original start/end, limit=1000 and pages 1..10;
- order/algo evidence: one original client ID or one execution order ID;
- original LIMIT entry, close-position SL/TP, reduce-only MARKET exit, cancels,
  and empty-query API-key listen-key operations.

This is a contract for the candidate flow, not support for every legal exchange
request. Other historical symbols or optional parameters are refused, never
silently omitted or narrowed to manufacture a full-flat proof.

## Two checks before effects

The bound client checks public/signed business parameters before SDK signing or
implicit lookup calls. For example, malformed Algo DELETE parameters cannot
cause an extra SDK scope lookup before rejection. Existing credential and wiring
checks still run first.

The owned budget independently checks the final wire query before the shared
resource reservation and order-role authorization. It rejects duplicate keys,
unknown fields, invalid percent/UTF-8 encoding, oversized queries, non-single
signing envelopes, and forms outside the contract. The signing envelope remains
timestamp/recvWindow=5000/signature; actual authentication stays in the original
SDK and venue. This parser does not claim to verify HMAC authenticity.

Known local rejection produces no fake response, creates no new resource charge,
and does not mark transport metadata as a possible-send failure. Already committed
outer domain intents/flow reservations retain their original conservative handling;
this change does not refund them or rewrite an UNKNOWN request into success.

Ownership, quantity, price filters, fenced effects, idempotency, and full-flat
checks still apply after shape acceptance. Their original policies/deadlines are
unchanged. Valid partial-fill exits and original proof scopes remain supported.

## Deliberately unapproved execution evidence

`query_contract` diagnostics report the policy version and unresolved execution
blockers. Shape acceptance cannot set execution_approved or proven cost bounds.
The official SDK documentation is a pinned reference, not a current venue cost
certificate or reservation of actual IP/account capacity.

The same pinned SDK explicitly warns that bookTicker's used-weight header is not
accurate. This change does not correct the legacy gate's treatment of that header;
`BOOK_TICKER_HEADER_NONAUTHORITATIVE` is retained as an execution blocker. Account
ORDERS usage, outside traffic, and complete venue cost bounds also remain unproven.
The canonical CLI and production execution defaults remain disabled.

## Validation and delivery

The six baseline cases failed because expected rejection did not occur. The same
cases now reject before changes to account/event/outbox rows, file hashes, or
sender-call count. Pure tests cover independent approved examples, boundary
limits, global-vs-symbol scope, signed proof forms, duplicates/encoding, signing
metadata, original order/exit/cancel payloads and listen-key forms.

Regression and assembled TP/partial-fill lifecycles use synthetic HTTP, local WSS,
isolated PostgreSQL, accelerated startup holds and short observation. Request
weight timing stays real. These are not new 300-second natural observations,
exchange measurements, actual orders, or profitability evidence. Docker smoke
runs without network; the PostgreSQL protocol has separate integration tests.
No Windows installation, DB mutation, trading resume or additional user probe is
part of this review-only source delivery.
