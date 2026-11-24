"""Disabled original-write entry and copy-only prototype of the fenced SQL engine.

No host admission or original write consent is issued here. Copy rehearsal callers
must independently own/isolate their disposable database. Synthetic witnesses and
completion records can never authorize original application.
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, timedelta
from uuid import UUID

from services.execution.demo_models import DemoLedger
from services.execution.demo_period_adoption_model import PeriodCompletionAudit
from services.execution.demo_period_draft_model_review import ProposedFields

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
FIELDS = ("daily_key", "weekly_key", "daily_base", "weekly_base", "reconciled", "stream_ready")
LOCK_KEY = int.from_bytes(
    hashlib.sha256(("offline-period|" + ACCOUNT).encode()).digest()[:8], "big", signed=True
)
COPY_ACCEPTANCE = hashlib.sha256(b"COPY_REHEARSAL_NOT_APPROVAL").hexdigest()


class PeriodWriteBlocked(ValueError):
    pass


class CommitOutcomeUnknown(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise PeriodWriteBlocked(code)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Plan:
    operation_id: str
    before_state_sha256: str
    ownership_row_sha256: str
    candidate_financial_state_sha256: str
    expected_fence: int
    proposed_fields: dict
    evidence_sha256: str


@dataclass(frozen=True)
class SyntheticCopyWitness:
    observation_end_ms: int
    server_now_ms: int
    reference_start_ms: int
    captured_monotonic: float
    source_acceptance_sha256: str = COPY_ACCEPTANCE


def apply_original_period(*args, **kwargs):
    """Unconditionally disabled before connection acquisition or SQL execution."""
    raise PeriodWriteBlocked("ORIGINAL_PERIOD_WRITE_APPROVAL_AND_HOST_BOUNDARY_REQUIRED")


def _plan(plan):
    op = UUID(plan.operation_id)
    require(op.version == 4 and str(op) == plan.operation_id, "PERIOD_OPERATION_ID_INVALID")
    require(
        type(plan.expected_fence) is int and 0 <= plan.expected_fence < 2**63 - 1,
        "PERIOD_FENCE_INVALID",
    )
    for h in (
        plan.before_state_sha256,
        plan.ownership_row_sha256,
        plan.candidate_financial_state_sha256,
        plan.evidence_sha256,
    ):
        require(
            type(h) is str and len(h) == 64 and all(c in "0123456789abcdef" for c in h),
            "PERIOD_HASH_INVALID",
        )
    return ProposedFields.model_validate(plan.proposed_fields).model_dump()


def _fresh(witness, now, fields):
    require(
        type(witness) is SyntheticCopyWitness
        and witness.source_acceptance_sha256 == COPY_ACCEPTANCE,
        "PERIOD_COPY_WITNESS_REQUIRED",
    )
    end = witness.observation_end_ms
    server = witness.server_now_ms
    reference = witness.reference_start_ms
    require(all(type(v) is int for v in (end, server, reference)), "PERIOD_CLOCK_INVALID")
    elapsed = int((time.monotonic() - witness.captured_monotonic) * 1000)
    now_ms = int(now.timestamp() * 1000)
    require(
        0 <= elapsed <= 300000
        and abs(now_ms - server - elapsed) <= 5000
        and 0 <= server - end <= 300000
        and 0 <= now_ms - end <= 300000,
        "PERIOD_SOURCE_STALE_OR_CLOCK_SKEW",
    )
    require(
        0 <= end - reference <= 604800000 and 0 <= now_ms - reference <= 604800000,
        "PERIOD_REFERENCE_GAP_INVALID",
    )
    at = now.astimezone(UTC) + timedelta(hours=9)
    require(
        str(at.date()) == fields["daily_key"]
        and str((at - timedelta(days=at.weekday())).date()) == fields["weekly_key"],
        "PERIOD_SOURCE_PERIOD_CHANGED",
    )


def _meta(cur, text, fence, owner, lease):
    cur.execute(
        "SELECT encode(sha256(convert_to(jsonb_build_object('owner',%s::text,'lease_until',%s::timestamptz,'fence',%s::bigint,'state_sha256',%s::text)::text,'UTF8')),'hex')",
        (owner, lease, fence, sha(text)),
    )
    return cur.fetchone()[0]


def _read(cur, lock=False):
    cur.execute(
        "SELECT state::text,fence,owner,lease_until,clock_timestamp() FROM public.demo_accounts WHERE account_id=%s"
        + (" FOR UPDATE" if lock else ""),
        (ACCOUNT,),
    )
    rows = cur.fetchall()
    require(len(rows) == 1, "PERIOD_ACCOUNT_NOT_UNIQUE")
    return rows[0]


def _candidate(cur, text, fields, op, record):
    cur.execute(
        "SELECT jsonb_set(%s::jsonb || %s::jsonb,'{operations}',(%s::jsonb->'operations') || jsonb_build_object(%s::text,%s::text))::text",
        (
            text,
            json.dumps(fields),
            text,
            op,
            json.dumps(record, sort_keys=True, separators=(",", ":")),
        ),
    )
    return cur.fetchone()[0]


def _protected(cur, before, after, op):
    allowed = list(FIELDS) + ["operations"]
    cur.execute(
        "SELECT (%s::jsonb - %s::text[])::text=(%s::jsonb - %s::text[])::text AND ((%s::jsonb->'operations') - %s)::text=(%s::jsonb->'operations')::text",
        (before, allowed, after, allowed, after, op, before),
    )
    require(cur.fetchone()[0] is True, "PERIOD_PROTECTED_JSONB_CHANGED")


def _verify(cur, plan, row, fields):
    text, fence, owner, lease, _ = row
    after = json.loads(text)
    require(plan.operation_id in after["operations"], "PERIOD_OPERATION_NOT_FOUND")
    audit = PeriodCompletionAudit.model_validate(json.loads(after["operations"][plan.operation_id]))
    require(
        audit.context == "COPY_REHEARSAL_ONLY"
        and audit.source_acceptance_receipt_sha256 == COPY_ACCEPTANCE
        and audit.operation_id == plan.operation_id
        and audit.before_state_sha256 == plan.before_state_sha256
        and audit.candidate_financial_state_sha256 == plan.candidate_financial_state_sha256
        and audit.expected_fence == plan.expected_fence
        and audit.committed_fence == fence == plan.expected_fence + 1
        and audit.evidence_sha256 == plan.evidence_sha256
        and audit.committed_fields.model_dump() == fields,
        "PERIOD_COMMITTED_RECORD_MISMATCH",
    )
    cur.execute(
        "SELECT (jsonb_set(%s::jsonb,'{operations}',(%s::jsonb->'operations') - %s) || %s::jsonb)::text",
        (text, text, plan.operation_id, json.dumps(audit.old_fields.model_dump())),
    )
    inverse = cur.fetchone()[0]
    require(
        sha(inverse) == plan.before_state_sha256
        and _meta(cur, inverse, plan.expected_fence, owner, lease) == plan.ownership_row_sha256,
        "PERIOD_COMMITTED_PROTECTED_STATE_OR_OWNERSHIP_MISMATCH",
    )
    _protected(cur, inverse, text, plan.operation_id)
    require(
        all(after[k] == v for k, v in fields.items()) and after["paused"] is True,
        "PERIOD_COMMITTED_FIELDS_MISMATCH",
    )
    DemoLedger.model_validate(after)
    cur.execute(
        "SELECT count(*) FROM public.demo_events e JOIN public.demo_outbox o USING(event_id) WHERE e.event_id=%s AND e.account_id=%s AND e.fence=%s AND e.kind='DEMO_COPY_PERIOD_REHEARSAL' AND e.payload=%s AND o.topic='DEMO_AUDIT'",
        (plan.operation_id, ACCOUNT, fence, text),
    )
    require(cur.fetchone()[0] == 1, "PERIOD_EVENT_OUTBOX_MISMATCH")
    return sha(text)


def rehearse_copy_transaction(conn, plan, witness, *, copy_only=False, fault=None):
    """Caller attestation is not independent host isolation; original path stays disabled."""
    require(copy_only is True, "PERIOD_EXPLICIT_COPY_SCOPE_REQUIRED")
    require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
        "PERIOD_LIVE_CONTEXT_FORBIDDEN",
    )
    require(conn.autocommit is True, "PERIOD_AUTOCOMMIT_CONNECTION_REQUIRED")
    fields = _plan(plan)
    body_finished = False
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, READ WRITE")
                cur.execute(
                    "SET LOCAL lock_timeout='2s'; SET LOCAL statement_timeout='5s'; SET LOCAL timezone='UTC'; SET LOCAL search_path=pg_catalog,public"
                )
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                original = _read(cur, True)
                text, fence, owner, lease, now = original
                before = json.loads(text)
                require(
                    isinstance(before.get("operations"), dict), "PERIOD_OPERATIONS_SHAPE_INVALID"
                )
                if plan.operation_id in before["operations"]:
                    _verify(cur, plan, original, fields)
                    return "ALREADY_COMMITTED"
                require(
                    sha(text) == plan.before_state_sha256 and fence == plan.expected_fence,
                    "PERIOD_STALE_STATE_OR_FENCE",
                )
                require(
                    owner is None and (lease is None or lease <= now),
                    "PERIOD_OWNER_OR_ACTIVE_LEASE_BLOCKED",
                )
                require(
                    _meta(cur, text, fence, owner, lease) == plan.ownership_row_sha256,
                    "PERIOD_OWNERSHIP_CHANGED",
                )
                require(
                    before["paused"] is True and before["risk_state"] == "NORMAL",
                    "PERIOD_PAUSED_NORMAL_REQUIRED",
                )
                require(
                    fields["daily_key"] >= before["daily_key"]
                    and fields["weekly_key"] >= before["weekly_key"]
                    and (fields["daily_key"], fields["weekly_key"])
                    != (before["daily_key"], before["weekly_key"]),
                    "PERIOD_NONADVANCING_PERIOD",
                )
                DemoLedger.model_validate(before)
                _fresh(witness, now, fields)
                cur.execute("SELECT (%s::jsonb || %s::jsonb)::text", (text, json.dumps(fields)))
                require(
                    sha(cur.fetchone()[0]) == plan.candidate_financial_state_sha256,
                    "PERIOD_FINANCIAL_CANDIDATE_MISMATCH",
                )
                record = dict(
                    policy="PAUSED_SAVED_WALLET_PERIOD_ADOPTION_V1",
                    context="COPY_REHEARSAL_ONLY",
                    operation_id=plan.operation_id,
                    source_kind="SAVED_WALLET_CANDIDATE_WITH_EXPLICIT_OPERATOR_ACCEPTANCE",
                    source_acceptance_receipt_sha256=COPY_ACCEPTANCE,
                    prior_review_id="period-review-draft-" + sha(plan.operation_id),
                    evidence_sha256=plan.evidence_sha256,
                    before_state_sha256=plan.before_state_sha256,
                    candidate_financial_state_sha256=plan.candidate_financial_state_sha256,
                    expected_fence=fence,
                    committed_fence=fence + 1,
                    old_fields={k: before[k] for k in FIELDS},
                    committed_fields=fields,
                    recorded_at_utc=now.astimezone(UTC)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                    applied=True,
                    source_accepted=True,
                    execution_approved=False,
                    midnight_equity_independently_verified=False,
                    account_wide_history_verified=False,
                    initial_funding_verified=False,
                )
                PeriodCompletionAudit.model_validate(record)
                candidate = _candidate(cur, text, fields, plan.operation_id, record)
                _protected(cur, text, candidate, plan.operation_id)
                DemoLedger.model_validate(json.loads(candidate))
                cur.execute(
                    "UPDATE public.demo_accounts SET state=%s::jsonb,fence=%s WHERE account_id=%s AND fence=%s AND owner IS NULL AND lease_until IS NOT DISTINCT FROM %s AND state=%s::jsonb RETURNING state::text",
                    (candidate, fence + 1, ACCOUNT, fence, lease, text),
                )
                rows = cur.fetchall()
                require(
                    len(rows) == 1 and rows[0][0] == candidate, "PERIOD_COMPARE_AND_SWAP_FAILED"
                )
                if fault == "AFTER_ACCOUNT":
                    raise PeriodWriteBlocked("PERIOD_INJECTED_FAILURE")
                cur.execute(
                    "INSERT INTO public.demo_events(event_id,account_id,fence,as_of,kind,payload) VALUES(%s,%s,%s,%s,'DEMO_COPY_PERIOD_REHEARSAL',%s)",
                    (plan.operation_id, ACCOUNT, fence + 1, now, candidate),
                )
                if fault == "AFTER_EVENT":
                    raise PeriodWriteBlocked("PERIOD_INJECTED_FAILURE")
                cur.execute(
                    "INSERT INTO public.demo_outbox(event_id,topic) VALUES(%s,'DEMO_AUDIT')",
                    (plan.operation_id,),
                )
                if fault == "AFTER_OUTBOX":
                    raise PeriodWriteBlocked("PERIOD_INJECTED_FAILURE")
                current = _read(cur)
                _verify(cur, plan, current, fields)
                _fresh(witness, current[-1], fields)
                body_finished = True
        return "COMMITTED"
    except Exception:
        if body_finished:
            raise CommitOutcomeUnknown(
                "PERIOD_COMMIT_OUTCOME_UNCERTAIN_QUERY_ONLY_NO_RETRY"
            ) from None
        raise


def inspect_copy_outcome(conn, plan):
    """Read-only observation of a potentially lost COMMIT reply, never resubmits."""
    fields = _plan(plan)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute(
                "SET LOCAL statement_timeout='5s'; SET LOCAL timezone='UTC'; SET LOCAL search_path=pg_catalog,public"
            )
            row = _read(cur)
            if plan.operation_id not in json.loads(row[0]).get("operations", {}):
                return dict(
                    status="NOT_FOUND_UNRESOLVED_NO_RETRY",
                    retry_authorized=False,
                    original_apply_ready=False,
                )
            try:
                after = _verify(cur, plan, row, fields)
            except Exception:
                return dict(
                    status="PRESENT_BUT_UNRESOLVED_NO_RETRY",
                    retry_authorized=False,
                    original_apply_ready=False,
                )
            return dict(
                status="COPY_COMMIT_VERIFIED",
                after_state_sha256=after,
                retry_authorized=False,
                original_apply_ready=False,
            )
