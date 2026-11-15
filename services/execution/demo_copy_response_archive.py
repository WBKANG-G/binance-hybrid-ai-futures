"""Uninstalled copy-only, one-shot account response archive candidate.

No built-in network sender, source acceptance, identity proof or day adoption.
A failed attempt retains its private directory and cannot be resumed/retried.
"""

import hashlib
import hmac
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from uuid import UUID

from pydantic import SecretStr

from packages.persistence.demo_order_quota import canonical
from packages.persistence.demo_read_audit import require
from packages.persistence.demo_read_recovery import (
    _file,
    _private_directory,
    _sync_directory,
    _write_new,
)
from packages.persistence.demo_response_evidence import (
    MAX_BODY,
    EvidenceReadJournal,
    request_metadata,
    response_metadata,
)
from services.execution.demo_response_evidence_transport import EvidenceReadTransport

POLICY = "COPY_SIGNED_ACCOUNT_RESPONSE_ARCHIVE_V1"


def _json(value: Any) -> bytes:
    return (canonical(value) + "\n").encode()


def _time(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    require(
        moment.tzinfo is not None and moment.utcoffset() == timedelta(0),
        "COPY_ARCHIVE_UTC_REQUIRED",
    )
    return moment


def _fresh(request: Any, now: datetime) -> None:
    _time(now.isoformat())
    require(
        abs(int(now.timestamp() * 1000) - int(request["timestamp"])) <= 5000,
        "COPY_ARCHIVE_FRESH_SIGNATURE_REQUIRED",
    )


def _window(start: str, end: str) -> None:
    first, last = _time(start), _time(end)
    kst = timezone(timedelta(hours=9))
    require(
        0 <= (last - first).total_seconds() <= 75
        and first.astimezone(kst).date() == last.astimezone(kst).date(),
        "COPY_ARCHIVE_CAPTURE_WINDOW_CHANGED",
    )


def _observed(shared: EvidenceReadJournal, token: str, request: Any, body: bytes) -> dict[str, Any]:
    store = shared.evidence
    with store.raw(shared.directory, shared.scope, shared.identity) as (cp, local):
        old, state = store.audit._verified_history(cp, local)
        _, rows, pending, _ = store.verify(cp, shared.directory, shared.account, old)
        require(not pending and not state[1][0][8], "COPY_ARCHIVE_PENDING_OR_STOPPED")
        matches = [r for r in rows if r["request_token"] == token and r["phase"] == "FINISH"]
        require(len(matches) == 1, "COPY_ARCHIVE_COMPLETED_RESPONSE_REQUIRED")
        row = matches[0]
        require(
            row["response_state"] == "OBSERVED"
            and row["request"] == request
            and len(body) == row["response"]["body_bytes"]
            and hashlib.sha256(body).hexdigest() == row["response"]["body_sha256"],
            "COPY_ARCHIVE_RESPONSE_MISMATCH",
        )
        return {
            k: row[k]
            for k in (
                "scope",
                "sequence",
                "audit_hash",
                "revision",
                "checkpoint_digest",
                "request_token",
                "request",
                "response",
                "hash",
            )
        }


def review_response_archive(
    shared: EvidenceReadJournal,
    directory: Path,
    receipt_sha256: str,
) -> dict[str, Any]:
    """Read-only verification against the complete V1/V2 chain, with pinned receipt."""
    require(
        isinstance(receipt_sha256, str)
        and len(receipt_sha256) == 64
        and all(c in "0123456789abcdef" for c in receipt_sha256),
        "COPY_ARCHIVE_RECEIPT_PIN_REQUIRED",
    )
    _private_directory(directory)
    require(
        {p.name for p in directory.iterdir()} == {"intent.json", "response.bin", "receipt.json"},
        "COPY_ARCHIVE_INCOMPLETE_OR_EXTRA_FILES",
    )
    raw = _file(directory / "receipt.json", 16384)
    require(hashlib.sha256(raw).hexdigest() == receipt_sha256, "COPY_ARCHIVE_RECEIPT_CHANGED")
    receipt = json.loads(raw)
    intent_raw = _file(directory / "intent.json", 16384)
    intent = json.loads(intent_raw)
    require(
        raw == _json(receipt)
        and intent_raw == _json(intent)
        and set(intent)
        == {
            "policy",
            "credential_context_id",
            "scope",
            "journal",
            "account",
            "request",
            "captured_start_utc",
        }
        and intent["policy"] == POLICY
        and intent["scope"] == shared.scope
        and intent["journal"] == str(shared.identity)
        and intent["account"] == shared.account
        and intent["request"]["path"] == "/fapi/v3/account"
        and intent["request"]["authenticated"] is True
        and receipt["intent_sha256"] == hashlib.sha256(intent_raw).hexdigest(),
        "COPY_ARCHIVE_CONTEXT_MISMATCH",
    )
    _window(intent["captured_start_utc"], receipt["captured_end_utc"])
    _fresh(intent["request"], _time(intent["captured_start_utc"]))
    require(
        str(UUID(intent["credential_context_id"])) == intent["credential_context_id"],
        "COPY_ARCHIVE_CONTEXT_MISMATCH",
    )
    body = _file(directory / "response.bin", MAX_BODY)
    observed = _observed(shared, receipt["evidence"]["request_token"], intent["request"], body)
    require(
        receipt
        == dict(
            policy=POLICY,
            intent_sha256=hashlib.sha256(intent_raw).hexdigest(),
            evidence=observed,
            captured_end_utc=receipt["captured_end_utc"],
        ),
        "COPY_ARCHIVE_RECEIPT_MISMATCH",
    )
    return dict(
        copy_response_archive="BODY_AND_COMPLETED_AUDIT_LINK_VERIFIED",
        response_status=observed["response"]["status"],
        response_body_bytes=len(body),
        response_body_sha256=hashlib.sha256(body).hexdigest(),
        response_body_artifact_stored=True,
        request_signature_or_key_stored=False,
        credential_context_caller_attested=True,
        credential_context_operator_attested=False,
        remote_account_identity_verified=False,
        copy_isolation_independently_verified=False,
        whole_history_coverage_verified=False,
        midnight_valuation_verified=False,
        baseline_source_accepted=False,
        baseline_adoption_approved=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )


class CopyAccountArchiveTransport(EvidenceReadTransport):
    """Explicit single signed account GET; injected sender only, never automatic resume."""

    def __init__(
        self,
        settings: Any,
        shared: EvidenceReadJournal,
        directory: Path,
        *,
        copy_only: bool = False,
        enable_capture: bool = False,
        credential_context_id: str,
        expected_api_key: SecretStr,
        sender: Any,
        **kwargs: Any,
    ):
        require(copy_only is True and enable_capture is True, "COPY_ARCHIVE_DISABLED")
        require(isinstance(shared, EvidenceReadJournal), "COPY_ARCHIVE_EVIDENCE_PROVIDER_REQUIRED")
        require(
            isinstance(expected_api_key, SecretStr)
            and bool(expected_api_key.get_secret_value())
            and str(UUID(credential_context_id)) == credential_context_id,
            "COPY_ARCHIVE_CREDENTIAL_CONTEXT_REQUIRED",
        )
        require(
            directory.is_absolute() and directory.parent.resolve() == directory.parent,
            "COPY_ARCHIVE_ABSOLUTE_PRIVATE_PARENT_REQUIRED",
        )
        _private_directory(directory.parent)
        require(not directory.exists() and not directory.is_symlink(), "COPY_ARCHIVE_NO_RESUME")
        self._archive_directory = directory
        self._archive_context = credential_context_id
        self._expected_api_key = expected_api_key
        self._archive_used = False
        self._archive_lock = threading.Lock()
        self._archive_token: str | None = None
        self.archive_report: dict[str, Any] | None = None
        super().__init__(
            settings,
            shared,
            enable_evidence_reads=True,
            sender=sender,
            max_requests=1,
            **kwargs,
        )

    def _capture_response(self, method: Any, path: Any, query: Any, key: Any) -> Any:
        _fresh(request_metadata(method, path, query, key), self._evidence_shared.wall_clock())
        response = super()._capture_response(method, path, query, key)
        response_metadata(response)
        # Neither key nor signed query/signature may leak through an echoed error.
        signature = dict(parse_qsl(query.get_secret_value()))["signature"]
        require(
            key.get_secret_value().encode() not in response.body
            and signature.encode() not in response.body,
            "COPY_ARCHIVE_CREDENTIAL_ECHO_FORBIDDEN",
        )
        capture = self._evidence_shared.capture.get()
        require(capture is not None and capture.token is not None, "COPY_ARCHIVE_TOKEN_REQUIRED")
        assert capture is not None
        self._archive_token = capture.token
        _write_new(self._archive_directory / "response.bin", response.body)
        _sync_directory(self._archive_directory)
        return response

    def __call__(self, method: Any, path: Any, query: Any, key: Any) -> Any:
        with self._archive_lock:
            require(not self._archive_used, "COPY_ARCHIVE_NO_RETRY")
            request = request_metadata(method, path, query, key)
            require(
                path == "/fapi/v3/account"
                and isinstance(key, SecretStr)
                and hmac.compare_digest(
                    key.get_secret_value(), self._expected_api_key.get_secret_value()
                ),
                "COPY_ARCHIVE_ACCOUNT_CREDENTIAL_MISMATCH",
            )
            start = self._evidence_shared.wall_clock()
            _fresh(request, start)
            self._archive_used = True
            directory = self._archive_directory
            directory.mkdir(mode=0o700, exist_ok=False)
            _sync_directory(directory.parent)
            _private_directory(directory)
            intent = _json(
                dict(
                    policy=POLICY,
                    credential_context_id=self._archive_context,
                    scope=self._evidence_shared.scope,
                    journal=str(self._evidence_shared.identity),
                    account=self._evidence_shared.account,
                    request=request,
                    captured_start_utc=start.isoformat(),
                )
            )
            _write_new(directory / "intent.json", intent)
            _sync_directory(directory)
            response = super().__call__(method, path, query, key)
            require(self._archive_token is not None, "COPY_ARCHIVE_TOKEN_REQUIRED")
            assert self._archive_token is not None
            body = _file(directory / "response.bin", MAX_BODY)
            observed = _observed(self._evidence_shared, self._archive_token, request, body)
            require(
                observed["response"] == response_metadata(response),
                "COPY_ARCHIVE_RESPONSE_MISMATCH",
            )
            end = self._evidence_shared.wall_clock()
            _window(start.isoformat(), end.isoformat())
            receipt = _json(
                dict(
                    policy=POLICY,
                    intent_sha256=hashlib.sha256(intent).hexdigest(),
                    evidence=observed,
                    captured_end_utc=end.isoformat(),
                )
            )
            _write_new(directory / "receipt.json", receipt)
            _sync_directory(directory)
            pin = hashlib.sha256(receipt).hexdigest()
            self.archive_report = review_response_archive(self._evidence_shared, directory, pin)
            self.archive_report["receipt_sha256"] = pin
            return response
