"""Completion-record schema and COPY-ONLY in-memory rehearsal, never a writer."""

import copy
import hashlib
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.execution.demo_models import DemoLedger
from services.execution.demo_period_draft_model_review import (
    HEX,
    DraftModelBlocked,
    ProposedFields,
    canonical,
    require,
)
from services.execution.demo_period_draft_model_review import review_models as review_draft_models


class PeriodCompletionAudit(BaseModel):
    """Shape validation only; claims are not proof of consent or DB commit."""

    model_config = ConfigDict(extra="forbid", strict=True)
    policy: Literal["PAUSED_SAVED_WALLET_PERIOD_ADOPTION_V1"]
    context: Literal["COPY_REHEARSAL_ONLY", "ACCEPTED_APPLICATION"]
    operation_id: str
    source_kind: Literal["SAVED_WALLET_CANDIDATE_WITH_EXPLICIT_OPERATOR_ACCEPTANCE"]
    source_acceptance_receipt_sha256: str = Field(pattern=HEX)
    prior_review_id: str = Field(pattern=r"^period-review-draft-[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=HEX)
    before_state_sha256: str = Field(pattern=HEX)
    candidate_financial_state_sha256: str = Field(pattern=HEX)
    expected_fence: int = Field(ge=0, le=2**63 - 2)
    committed_fence: int = Field(ge=1, le=2**63 - 1)
    old_fields: ProposedFields
    committed_fields: ProposedFields
    recorded_at_utc: str
    applied: Literal[True]
    source_accepted: Literal[True]
    execution_approved: Literal[False]
    midnight_equity_independently_verified: Literal[False]
    account_wide_history_verified: Literal[False]
    initial_funding_verified: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def boolean_claims_are_not_integers(cls, value):
        if isinstance(value, dict):
            for key in (
                "applied",
                "source_accepted",
                "execution_approved",
                "midnight_equity_independently_verified",
                "account_wide_history_verified",
                "initial_funding_verified",
            ):
                require(type(value.get(key)) is bool, "MODEL_COMPLETION_BOOL_INVALID")
        return value

    @model_validator(mode="after")
    def identities_and_time(self):
        op = UUID(self.operation_id)
        require(op.version == 4 and str(op) == self.operation_id, "MODEL_OPERATION_INVALID")
        require(self.committed_fence == self.expected_fence + 1, "MODEL_FENCE_INVALID")
        at = datetime.fromisoformat(self.recorded_at_utc.replace("Z", "+00:00"))
        require(
            self.recorded_at_utc.endswith("Z")
            and at.utcoffset() is not None
            and at.utcoffset().total_seconds() == 0,
            "MODEL_COMPLETION_UTC_REQUIRED",
        )
        return self


def validate_real_completion(value, *, verified_acceptance_sha256):
    """Additional caller-supplied consent binding; not a token or apply API."""
    record = PeriodCompletionAudit.model_validate(value)
    require(record.context == "ACCEPTED_APPLICATION", "MODEL_REHEARSAL_CANNOT_AUTHORIZE")
    require(
        isinstance(verified_acceptance_sha256, str)
        and record.source_acceptance_receipt_sha256 == verified_acceptance_sha256,
        "MODEL_VERIFIED_ACCEPTANCE_REQUIRED",
    )
    return record


def completion_rehearsal(before, expected):
    """Construct a marked simulation in memory; never return a completion payload."""
    fields = ProposedFields.model_validate(expected["proposed_fields"]).model_dump()
    old = {k: before[k] for k in fields}
    # Deterministic test identity/time enable repeated reviews; neither is real authority.
    seed = hashlib.sha256(("COPY_REHEARSAL_ONLY|" + expected["review_id"]).encode()).digest()
    operation = str(UUID(bytes=seed[:16], version=4))
    receipt = dict(
        policy="PAUSED_SAVED_WALLET_PERIOD_ADOPTION_V1",
        context="COPY_REHEARSAL_ONLY",
        operation_id=operation,
        source_kind="SAVED_WALLET_CANDIDATE_WITH_EXPLICIT_OPERATOR_ACCEPTANCE",
        source_acceptance_receipt_sha256=hashlib.sha256(b"COPY_REHEARSAL_NOT_APPROVAL").hexdigest(),
        prior_review_id=expected["review_id"],
        evidence_sha256=expected["evidence_sha256"],
        before_state_sha256=expected["before_state_sha256"],
        candidate_financial_state_sha256=expected["candidate_financial_state_sha256"],
        expected_fence=expected["expected_fence"],
        committed_fence=expected["proposed_fence"],
        old_fields=old,
        committed_fields=fields,
        recorded_at_utc="1970-01-01T00:00:00Z",
        applied=True,
        source_accepted=True,
        execution_approved=False,
        midnight_equity_independently_verified=False,
        account_wide_history_verified=False,
        initial_funding_verified=False,
    )
    PeriodCompletionAudit.model_validate(receipt)
    try:
        validate_real_completion(receipt, verified_acceptance_sha256=None)
    except DraftModelBlocked as error:
        require(str(error) == "MODEL_REHEARSAL_CANNOT_AUTHORIZE", "MODEL_AUTHORITY_TEST_INVALID")
    else:
        raise DraftModelBlocked("MODEL_REHEARSAL_ACCEPTED_AS_REAL")
    candidate = copy.deepcopy(before)
    candidate.update(fields)
    require(operation not in candidate["operations"], "MODEL_OPERATION_COLLISION")
    candidate["operations"][operation] = canonical(receipt)
    DemoLedger.model_validate(candidate)
    allowed = set(fields) | {"operations"}
    require(
        canonical({k: v for k, v in before.items() if k not in allowed})
        == canonical({k: v for k, v in candidate.items() if k not in allowed})
        and {k: v for k, v in candidate["operations"].items() if k != operation}
        == before["operations"],
        "MODEL_COMPLETION_PROTECTED_STATE_CHANGED",
    )
    return dict(
        completion_rehearsal_context="COPY_REHEARSAL_ONLY",
        completion_audit_model_validated=True,
        completion_candidate_model_validated=True,
        completion_protected_state_preserved=True,
        rehearsal_receipt_rejected_for_real_application=True,
        completion_payload_returned=False,
        source_acceptance_receipt_created=False,
    )


def review_models(before, after, report, expected):
    result = review_draft_models(before, after, report, expected)
    bound = dict(expected)
    bound["candidate_financial_state_sha256"] = report["candidate_financial_state_sha256"]
    result.update(completion_rehearsal(before, bound))
    return result
