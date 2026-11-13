"""Generate an offline migration review. No Docker, credentials, database or sends."""

import hashlib
import json
from typing import Any

from services.execution.demo_history_coverage_candidate import (
    HistoricalRequest,
    classify_request,
    review_coverage,
)


def report() -> dict[str, Any]:
    # Counts and categories transcribed from the operator's Nov 13 screenshots.
    # These placeholders are not actual order IDs or newly verified observations.
    refs = tuple(
        hashlib.sha256(("offline-review-placeholder-" + str(i)).encode()).hexdigest()
        for i in range(34)
    )
    facts = [
        HistoricalRequest("ENTRY", "UNKNOWN", True, "FILLED"),
        HistoricalRequest("ENTRY", "UNKNOWN", True, "FILLED"),
        HistoricalRequest("STOP_LOSS", "UNKNOWN", True, "NOT_FOUND_UNRESOLVED", True),
        HistoricalRequest("EXIT", "SUBMITTING", True, "NOT_FOUND_UNRESOLVED", True),
    ]
    return dict(
        observation_source="OPERATOR_SCREENSHOT_TRANSCRIPTION_20261113_NOT_FRESH_VERIFICATION",
        order_references_are_synthetic_placeholders=True,
        historical_requests=[classify_request(i) for i in facts],
        current_full_history=review_coverage(refs, refs, ()),
        hypothetical_29_certificates_not_present=review_coverage(refs, refs[:5], refs[5:]),
        missing_controls=[
            "ATOMIC_REQUEST_AND_RESPONSE_BODY_BINDING_TO_FINISH_CHECKPOINT",
            "INDEPENDENT_ACCOUNT_SCOPE_BINDING",
            "HISTORICAL_UNCERTAINTY_POLICY",
            "DURABLE_TERMINAL_CERTIFICATE_VERIFIER",
            "ACTIVE_AND_ARCHIVE_COVERAGE_CHECK",
            "OPERATIONAL_DEMO_RUNTIME_AND_FRESH_ADMISSION",
        ],
        policy_changes_applied=False,
        activation_approved=False,
        http_attempts=0,
        order_writes=0,
    )


if __name__ == "__main__":
    print(json.dumps(report(), sort_keys=True, indent=2))
