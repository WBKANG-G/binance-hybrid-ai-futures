"""Pure typed validation of an unapproved in-memory draft; no persistence or acceptance."""

import json
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from services.execution.demo_models import AttestedPeriodBaseline, DemoLedger

HEX = r"^[0-9a-f]{64}$"


class DraftModelBlocked(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise DraftModelBlocked(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ProposedFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    daily_key: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    weekly_key: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    daily_base: str = Field(pattern=r"^[0-9]{1,12}\.\d{8}$")
    weekly_base: str = Field(pattern=r"^[0-9]{1,12}\.\d{8}$")
    reconciled: bool
    stream_ready: bool

    @model_validator(mode="after")
    def validate_boundaries(self):
        day, week = date.fromisoformat(self.daily_key), date.fromisoformat(self.weekly_key)
        require(week.weekday() == 0 and 0 <= (day - week).days <= 6, "MODEL_PERIOD_INVALID")
        require(
            Decimal(self.daily_base) > 0 and self.daily_base == self.weekly_base,
            "MODEL_AMOUNT_INVALID",
        )
        require(
            self.reconciled is False and self.stream_ready is False,
            "MODEL_FRESH_RECONCILIATION_REQUIRED",
        )
        return self


class UnapprovedPeriodAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    policy: Literal["UNAPPROVED_PERIOD_REVIEW_DRAFT_V1"]
    review_id: str = Field(pattern=r"^period-review-draft-[0-9a-f]{64}$")
    expected_fence: int = Field(ge=0, le=2**63 - 2)
    proposed_fence: int = Field(ge=1, le=2**63 - 1)
    before_state_sha256: str = Field(pattern=HEX)
    candidate_financial_state_sha256: str = Field(pattern=HEX)
    evidence_sha256: str = Field(pattern=HEX)
    proposed_fields: ProposedFields
    source_accepted: bool
    applied: bool
    execution_approved: bool

    @model_validator(mode="after")
    def unapproved_only(self):
        require(self.proposed_fence == self.expected_fence + 1, "MODEL_FENCE_INVALID")
        require(
            self.source_accepted is False
            and self.applied is False
            and self.execution_approved is False,
            "MODEL_DRAFT_CANNOT_AUTHORIZE",
        )
        return self


def review_models(before, after, report, expected):
    """Return safe flags/hashes only; raw ledger and identifiers never leave this function."""
    try:
        before_model = DemoLedger.model_validate(before)
    except (ValidationError, ValueError, TypeError):
        raise DraftModelBlocked("MODEL_BEFORE_LEDGER_SCHEMA_INVALID") from None
    try:
        after_model = DemoLedger.model_validate(after)
    except (ValidationError, ValueError, TypeError):
        raise DraftModelBlocked("MODEL_CANDIDATE_LEDGER_SCHEMA_INVALID") from None
    try:
        audit = UnapprovedPeriodAudit.model_validate(report["audit_draft"])
    except (ValidationError, ValueError, KeyError, TypeError):
        raise DraftModelBlocked("MODEL_AUDIT_SCHEMA_INVALID") from None
    require(
        before.get("mode") == after.get("mode") == "DEMO"
        and before.get("paused") is True
        and after.get("paused") is True,
        "MODEL_RAW_PAUSED_DEMO_REQUIRED",
    )
    require(
        before_model.mode == after_model.mode == "DEMO"
        and before_model.paused
        and after_model.paused,
        "MODEL_PAUSED_DEMO_REQUIRED",
    )
    require(
        len(before_model.orders) == len(after_model.orders) == 34
        and len(before_model.requests) == len(after_model.requests)
        and sum(
            v in {"UNKNOWN", "SUBMITTING", "CANCEL_REQUESTED", "NOT_FOUND_UNRESOLVED"}
            for v in before_model.requests.values()
        )
        == 4
        and sum(
            v in {"UNKNOWN", "SUBMITTING", "CANCEL_REQUESTED", "NOT_FOUND_UNRESOLVED"}
            for v in after_model.requests.values()
        )
        == 4,
        "MODEL_LEDGER_SCOPE_INVALID",
    )
    require(
        audit.review_id == expected["review_id"]
        and audit.before_state_sha256 == expected["before_state_sha256"]
        and report["candidate_draft_state_sha256"] == expected["candidate_draft_state_sha256"]
        and audit.evidence_sha256 == expected["evidence_sha256"]
        and audit.expected_fence == expected["expected_fence"]
        and audit.proposed_fence == expected["proposed_fence"],
        "MODEL_EXPECTED_DRAFT_MISMATCH",
    )
    require(
        audit.candidate_financial_state_sha256 == report["candidate_financial_state_sha256"],
        "MODEL_FINANCIAL_DIGEST_MISMATCH",
    )
    fields = audit.proposed_fields.model_dump()
    require(fields == expected["proposed_fields"], "MODEL_PROPOSAL_MISMATCH")
    allowed = set(fields) | {"operations"}
    require(
        canonical({k: v for k, v in before.items() if k not in allowed})
        == canonical({k: v for k, v in after.items() if k not in allowed}),
        "MODEL_PROTECTED_STATE_CHANGED",
    )
    require(
        all(canonical(after.get(k)) == canonical(v) for k, v in fields.items()),
        "MODEL_CANDIDATE_VALUE_CHANGED",
    )
    ops = after["operations"]
    require(
        audit.review_id not in before["operations"]
        and audit.review_id in ops
        and canonical({k: v for k, v in ops.items() if k != audit.review_id})
        == canonical(before["operations"]),
        "MODEL_PRIOR_OPERATIONS_CHANGED",
    )
    try:
        recorded = json.loads(ops[audit.review_id])
    except (ValueError, TypeError):
        raise DraftModelBlocked("MODEL_DRAFT_RECORD_INVALID") from None
    require(canonical(recorded) == canonical(report["audit_draft"]), "MODEL_DRAFT_RECORD_MISMATCH")
    try:
        AttestedPeriodBaseline.model_validate(recorded)
    except ValidationError:
        rejected = True
    else:
        rejected = False
    require(rejected, "MODEL_DRAFT_ACCEPTED_AS_ADOPTION")
    return dict(
        typed_draft_review="TYPED_LEDGER_AND_UNAPPROVED_AUDIT_VALIDATED_NO_APPLY",
        before_model_validated=True,
        candidate_model_validated=True,
        audit_model_validated=True,
        completed_adoption_schema_rejected_draft=True,
        protected_state_preserved=True,
        review_id=audit.review_id,
        before_state_sha256=audit.before_state_sha256,
        candidate_draft_state_sha256=report["candidate_draft_state_sha256"],
        evidence_sha256=audit.evidence_sha256,
        expected_fence=audit.expected_fence,
        proposed_fence=audit.proposed_fence,
        proposed_fields=fields,
        audit_semantics="UNAPPROVED_DRAFT_NOT_A_COMPLETED_ADOPTION",
        apply_token_created=False,
        commit_payload_verified=False,
        source_accepted=False,
        database_changed=False,
        audit_record_written=False,
        baseline_adopted=False,
        request_states_changed=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
