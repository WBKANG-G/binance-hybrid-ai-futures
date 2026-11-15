"""Read-only account-content and proposed-evidence input review, never acceptance.

An archived account response cannot establish identity, open-order absence,
configuration, complete history, midnight valuation or operator consent.
"""

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from packages.persistence.demo_read_audit import require
from packages.persistence.demo_read_recovery import _file
from packages.persistence.demo_response_evidence import MAX_BODY, EvidenceReadJournal
from services.execution.demo_copy_response_archive import review_response_archive

KST = timezone(timedelta(hours=9))
ZERO_FIELDS = (
    "initialMargin",
    "maintMargin",
    "positionInitialMargin",
    "openOrderInitialMargin",
)
REMAINING = (
    "REMOTE_ACCOUNT_IDENTITY_AND_CREDENTIAL_CONTEXT_PROOF",
    "ACCOUNT_AND_SYMBOL_CONFIGURATION_PROOF",
    "REGULAR_AND_ALGO_OPEN_ORDER_PROOF",
    "COMPLETE_TRADE_INCOME_TRANSFER_COVERAGE",
    "DAY_BOUNDARY_VALUATION_OR_RECONSTRUCTION_PROOF",
    "EXPLICIT_OPERATOR_STATEMENT_AND_SOURCE_ACCEPTANCE",
    "HISTORICAL_REQUESTS_REQUIRE_INDEPENDENT_REMOTE_PROOF",
    "CURRENT_PERIOD_AND_LOSS_HIGH_WATER_RECHECK",
)


def _reject(*args: Any) -> Any:
    require(False, "COPY_CONTENT_JSON_INVALID")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    require(len(pairs) <= 128, "COPY_CONTENT_OBJECT_LIMIT")
    for key, value in pairs:
        require(key not in result, "COPY_CONTENT_DUPLICATE_KEY")
        result[key] = value
    return result


def _number(value: Any) -> Decimal:
    require(
        isinstance(value, str)
        and re.fullmatch(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,18})?", value) is not None,
        "COPY_CONTENT_DECIMAL_STRING_REQUIRED",
    )
    return Decimal(value)


def review_account_body(body: bytes) -> dict[str, Any]:
    """Check a deliberately narrow flat-USDT shape; ignore extra fields as proof."""
    require(isinstance(body, bytes) and len(body) <= MAX_BODY, "COPY_CONTENT_BODY_LIMIT")
    try:
        raw = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_object,
            parse_float=_reject,
            parse_constant=_reject,
        )
    except (ValueError, UnicodeError, RecursionError):
        require(False, "COPY_CONTENT_JSON_INVALID")
        raise AssertionError("unreachable") from None
    require(isinstance(raw, dict) and "code" not in raw, "COPY_CONTENT_ACCOUNT_OBJECT_REQUIRED")
    for field in ("dualSidePosition", "multiAssetsMargin"):
        if field in raw:
            require(raw[field] is False, "COPY_CONTENT_UNSUPPORTED_ACCOUNT_MODE")
    wallet = _number(raw.get("totalWalletBalance"))
    require(wallet > 0, "COPY_CONTENT_POSITIVE_USDT_REQUIRED")
    require(
        _number(raw.get("totalMarginBalance")) == wallet
        and _number(raw.get("totalUnrealizedProfit")) == 0
        and _number(raw.get("availableBalance")) == wallet,
        "COPY_CONTENT_TOTAL_MISMATCH",
    )
    for field in ZERO_FIELDS:
        require(
            _number(raw.get("total" + field[0].upper() + field[1:])) == 0,
            "COPY_CONTENT_TOTAL_MARGIN_NONZERO",
        )
    for field, expected in (("totalCrossWalletBalance", wallet), ("totalCrossUnPnl", Decimal(0))):
        if field in raw:
            require(_number(raw[field]) == expected, "COPY_CONTENT_CROSS_TOTAL_MISMATCH")
    assets = raw.get("assets")
    positions = raw.get("positions")
    require(isinstance(assets, list) and 1 <= len(assets) <= 32, "COPY_CONTENT_ASSET_LIMIT")
    require(isinstance(positions, list) and len(positions) <= 500, "COPY_CONTENT_POSITION_LIMIT")
    seen: set[str] = set()
    for asset in assets:
        require(
            isinstance(asset, dict) and isinstance(asset.get("asset"), str),
            "COPY_CONTENT_ASSET_INVALID",
        )
        name = asset["asset"]
        require(
            re.fullmatch(r"[A-Z0-9]{1,20}", name) is not None and name not in seen,
            "COPY_CONTENT_ASSET_DUPLICATE_OR_INVALID",
        )
        seen.add(name)
        expected = wallet if name == "USDT" else Decimal(0)
        require(
            all(
                _number(asset.get(f)) == expected
                for f in ("walletBalance", "marginBalance", "availableBalance")
            )
            and _number(asset.get("unrealizedProfit")) == 0,
            "COPY_CONTENT_ASSET_TOTAL_MISMATCH",
        )
        for field in ZERO_FIELDS:
            require(_number(asset.get(field)) == 0, "COPY_CONTENT_ASSET_MARGIN_NONZERO")
    require("USDT" in seen, "COPY_CONTENT_USDT_REQUIRED")
    symbols: set[str] = set()
    for position in positions:
        require(
            isinstance(position, dict) and isinstance(position.get("symbol"), str),
            "COPY_CONTENT_POSITION_INVALID",
        )
        symbol = position["symbol"]
        require(
            re.fullmatch(r"[A-Z0-9]{1,30}", symbol) is not None and symbol not in symbols,
            "COPY_CONTENT_POSITION_DUPLICATE_OR_INVALID",
        )
        symbols.add(symbol)
        require(
            position.get("positionSide") == "BOTH" and _number(position.get("positionAmt")) == 0,
            "COPY_CONTENT_EXPOSURE_OR_MODE_UNSUPPORTED",
        )
        for field in (*ZERO_FIELDS, "unrealizedProfit"):
            require(_number(position.get(field)) == 0, "COPY_CONTENT_POSITION_RISK_NONZERO")
        for field in ("notional", "isolatedWallet", "isolatedMargin"):
            if field in position:
                require(_number(position[field]) == 0, "COPY_CONTENT_POSITION_RISK_NONZERO")
    return dict(
        account_content_review="NARROW_FLAT_USDT_ARITHMETIC_CONSISTENT",
        response_body_sha256=hashlib.sha256(body).hexdigest(),
        asset_rows_checked=len(assets),
        position_rows_checked=len(positions),
        positive_usdt_wallet_observed=True,
        account_configuration_verified=False,
        open_orders_verified=False,
        remote_account_identity_verified=False,
        baseline_amount_proposed=False,
        baseline_source_accepted=False,
        baseline_adoption_approved=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )


def _utc(value: Any) -> datetime:
    require(isinstance(value, str) and len(value) <= 40, "COPY_PROPOSAL_UTC_REQUIRED")
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        require(False, "COPY_PROPOSAL_UTC_REQUIRED")
        raise AssertionError("unreachable") from None
    require(
        result.tzinfo is not None and result.utcoffset() == timedelta(0),
        "COPY_PROPOSAL_UTC_REQUIRED",
    )
    return result


def _pin(value: Any) -> None:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "COPY_PROPOSAL_REFERENCE_PIN_REQUIRED",
    )


def review_account_evidence_inputs(
    shared: EvidenceReadJournal,
    directory: Path,
    receipt_sha256: str,
    *,
    now: datetime,
    proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check input syntax and pinned account artifact, not the referenced proofs.

    Proposal statement flags are caller-supplied claims, never inferred user consent.
    All referenced identity/history/valuation documents still require review.
    """
    require(
        isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() == timedelta(0),
        "COPY_PROPOSAL_UTC_REQUIRED",
    )
    archive = review_response_archive(shared, directory, receipt_sha256)
    require(archive["response_status"] == 200, "COPY_CONTENT_HTTP_200_REQUIRED")
    body = _file(directory / "response.bin", MAX_BODY)
    require(
        hashlib.sha256(body).hexdigest() == archive["response_body_sha256"],
        "COPY_CONTENT_ARCHIVE_CHANGED",
    )
    content = review_account_body(body)
    # Pin the metadata read after full archive verification as well, denying swaps.
    receipt_raw = _file(directory / "receipt.json", 16384)
    require(
        hashlib.sha256(receipt_raw).hexdigest() == receipt_sha256, "COPY_CONTENT_ARCHIVE_CHANGED"
    )
    receipt = json.loads(receipt_raw)
    intent_raw = _file(directory / "intent.json", 16384)
    require(
        hashlib.sha256(intent_raw).hexdigest() == receipt["intent_sha256"],
        "COPY_CONTENT_ARCHIVE_CHANGED",
    )
    intent = json.loads(intent_raw)
    end = _utc(receipt["captured_end_utc"])
    require(
        0 <= (now - end).total_seconds() <= 300
        and now.astimezone(KST).date() == end.astimezone(KST).date(),
        "COPY_CONTENT_FRESH_SAME_DAY_ARCHIVE_REQUIRED",
    )
    if proposal is not None:
        fields = {
            "account_receipt_sha256",
            "credential_context_id",
            "stored_day_kst",
            "stored_week_kst",
            "target_day_kst",
            "history_start_utc",
            "history_end_utc",
            "remote_identity_reference_sha256",
            "history_reference_sha256",
            "valuation_reference_sha256",
            "valuation_kind",
            "operator_statement_claimed",
            "source_acceptance_claimed",
        }
        require(type(proposal) is dict and set(proposal) == fields, "COPY_PROPOSAL_FIELDS_INVALID")
        for field in (
            "account_receipt_sha256",
            "remote_identity_reference_sha256",
            "history_reference_sha256",
            "valuation_reference_sha256",
        ):
            _pin(proposal[field])
        require(
            proposal["account_receipt_sha256"] == receipt_sha256
            and proposal["credential_context_id"] == intent["credential_context_id"],
            "COPY_PROPOSAL_CONTEXT_MISMATCH",
        )
        require(
            str(UUID(proposal["credential_context_id"])) == proposal["credential_context_id"],
            "COPY_PROPOSAL_CONTEXT_MISMATCH",
        )
        try:
            stored, week, target = (
                date.fromisoformat(proposal[k])
                for k in ("stored_day_kst", "stored_week_kst", "target_day_kst")
            )
        except (ValueError, TypeError):
            require(False, "COPY_PROPOSAL_PERIOD_INVALID")
            raise AssertionError("unreachable") from None
        require(
            [stored.isoformat(), week.isoformat(), target.isoformat()]
            == [proposal[k] for k in ("stored_day_kst", "stored_week_kst", "target_day_kst")]
            and target == now.astimezone(KST).date()
            and stored < target,
            "COPY_PROPOSAL_PERIOD_INVALID",
        )
        # This narrow input contract supports Monday-based same-week daily review only.
        require(
            week == stored - timedelta(days=stored.weekday())
            and week == target - timedelta(days=target.weekday()),
            "COPY_PROPOSAL_WEEK_REQUIRES_SEPARATE_REVIEW",
        )
        start, stop = _utc(proposal["history_start_utc"]), _utc(proposal["history_end_utc"])
        old_boundary = datetime.combine(stored, datetime.min.time(), KST)
        require(
            start <= old_boundary and end <= stop <= now,
            "COPY_PROPOSAL_HISTORY_INTERVAL_INCOMPLETE",
        )
        require(
            proposal["valuation_kind"] in {"DAY_BOUNDARY_VALUATION", "PROPOSED_RECONSTRUCTION"},
            "COPY_PROPOSAL_VALUATION_KIND_INVALID",
        )
        require(
            proposal["operator_statement_claimed"] is True
            and proposal["source_acceptance_claimed"] is True,
            "COPY_PROPOSAL_EXPLICIT_CLAIMS_REQUIRED",
        )
    return dict(
        **content,
        account_receipt_sha256=receipt_sha256,
        proposal_input_shape_complete=proposal is not None,
        caller_claims_present=proposal is not None,
        operator_statement_independently_verified=False,
        referenced_proofs_verified=False,
        whole_history_coverage_verified=False,
        midnight_valuation_verified=False,
        stored_periods_verified_against_ledger=False,
        remaining_requirements=list(REMAINING),
    )
