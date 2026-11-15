"""Uninstalled local DEMO read credential continuity; not remote account identity.

Explicit credentials stay in RAM. No key-file loader, environment fallback,
network sender, durable session receipt or baseline authority exists here.
"""

import hashlib
import hmac
import math
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoCredentials
from integrations.binance_usdm.rest import RESTResponse, prepare_signed_query, sign_payload
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import require
from packages.persistence.demo_response_evidence import EvidenceReadJournal, request_metadata
from services.execution.demo_copy_response_archive import CopyAccountArchiveTransport
from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport
from services.execution.demo_runtime_storage import RuntimeClocks

LIFETIME_SECONDS = 75


@dataclass(frozen=True, repr=False)
class PreparedAccountRead:
    context_id: str
    query: SecretStr = field(repr=False)
    api_key: SecretStr = field(repr=False)

    def __repr__(self) -> str:
        return "PreparedAccountRead(<private one-shot local capability>)"

    def __reduce_ex__(self, protocol: Any) -> Any:
        raise LocalQuotaStopped("READ_CONTEXT_NOT_SERIALIZABLE")

    def __copy__(self) -> Any:
        raise LocalQuotaStopped("READ_CONTEXT_NOT_COPYABLE")

    def __deepcopy__(self, memo: Any) -> Any:
        raise LocalQuotaStopped("READ_CONTEXT_NOT_COPYABLE")


class LocalReadCredentialContext:
    def __init__(
        self,
        account: str,
        scope: str,
        credentials: DemoCredentials,
        *,
        enable_local_context: bool = False,
        copy_only: bool = False,
        clocks: RuntimeClocks | None = None,
    ):
        require(enable_local_context is True and copy_only is True, "READ_CONTEXT_DISABLED")
        AccountedDemoReadTransport._context()
        require(
            all(
                isinstance(v, str) and re.fullmatch(r"DEMO-[A-Za-z0-9_-]{1,74}", v)
                for v in (account, scope)
            ),
            "READ_CONTEXT_DEMO_SCOPE_REQUIRED",
        )
        require(
            type(credentials) is DemoCredentials and credentials.environment == "DEMO",
            "READ_CONTEXT_DEMO_CREDENTIALS_REQUIRED",
        )
        require(
            all(
                isinstance(v, SecretStr)
                and 1 <= len(v.get_secret_value()) <= 512
                and all(33 <= ord(c) <= 126 for c in v.get_secret_value())
                for v in (credentials.api_key, credentials.secret)
            ),
            "READ_CONTEXT_CREDENTIALS_INVALID",
        )
        self._key = SecretStr(credentials.api_key.get_secret_value())
        self._secret = SecretStr(credentials.secret.get_secret_value())
        self._salt = os.urandom(32)
        self._fingerprint = self._credential_fingerprint()
        self._account, self._scope, self._id = account, scope, str(uuid4())
        self._binding = (account, scope, self._id)
        self._pid = os.getpid()
        self._clocks = clocks or RuntimeClocks()
        self._clock_binding = (self._clocks.monotonic, self._clocks.wall_clock, self._clocks.boot)
        self._started = self._last = self._sample()
        self._closed = False
        self._prepared: PreparedAccountRead | None = None
        self._consumed = False
        self._prepared_guard = b""
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return "LocalReadCredentialContext(<private DEMO copy read-only capability>)"

    def __reduce_ex__(self, protocol: Any) -> Any:
        raise LocalQuotaStopped("READ_CONTEXT_NOT_SERIALIZABLE")

    def __copy__(self) -> Any:
        raise LocalQuotaStopped("READ_CONTEXT_NOT_COPYABLE")

    def __deepcopy__(self, memo: Any) -> Any:
        raise LocalQuotaStopped("READ_CONTEXT_NOT_COPYABLE")

    def _credential_fingerprint(self) -> bytes:
        return hmac.digest(
            self._salt,
            (self._key.get_secret_value() + "\0" + self._secret.get_secret_value()).encode(),
            hashlib.sha256,
        )

    def _sample(self) -> tuple[float, float, str]:
        now, wall, boot = self._clocks.monotonic(), self._clocks.wall_clock(), self._clocks.boot()
        require(
            type(now) in (int, float)
            and math.isfinite(now)
            and now >= 0
            and isinstance(wall, datetime)
            and wall.tzinfo is not None
            and wall.utcoffset() == timedelta(0)
            and isinstance(boot, str)
            and re.fullmatch(r"[A-Za-z0-9-]{1,128}", boot) is not None,
            "READ_CONTEXT_CLOCK_INVALID",
        )
        return float(now), wall.timestamp(), boot

    def _check(self) -> None:
        AccountedDemoReadTransport._context()
        require(not self._closed and self._pid == os.getpid(), "READ_CONTEXT_CLOSED_OR_FORKED")
        require(
            (self._account, self._scope, self._id) == self._binding
            and (self._clocks.monotonic, self._clocks.wall_clock, self._clocks.boot)
            == self._clock_binding
            and hmac.compare_digest(self._credential_fingerprint(), self._fingerprint),
            "READ_CONTEXT_BINDING_CHANGED",
        )
        now = self._sample()
        require(
            now[2] == self._started[2]
            and now[0] >= self._last[0]
            and 0 <= now[0] - self._started[0] < LIFETIME_SECONDS
            and abs((now[1] - self._started[1]) - (now[0] - self._started[0])) <= 1,
            "READ_CONTEXT_EXPIRED_OR_CLOCK_CHANGED",
        )
        self._last = now

    def descriptor(self) -> dict[str, Any]:
        with self._lock:
            self._check()
            return dict(
                credential_context_id=self._id,
                account=self._account,
                scope=self._scope,
                kind="PROCESS_LOCAL_COPY_ACCOUNT_READ",
                remote_account_identity_verified=False,
                credential_source_independently_verified=False,
            )

    def prepare(self) -> PreparedAccountRead:
        with self._lock:
            self._check()
            require(self._prepared is None and not self._consumed, "READ_CONTEXT_ONE_SHOT_ONLY")
            signed = prepare_signed_query(
                {}, api_key=self._key, secret=self._secret, timestamp_ms=int(self._last[1] * 1000)
            )
            self._prepared = PreparedAccountRead(self._id, signed.query, signed.api_key)
            self._prepared_guard = hmac.digest(
                self._salt,
                (
                    signed.query.get_secret_value() + "\0" + signed.api_key.get_secret_value()
                ).encode(),
                hashlib.sha256,
            )
            return self._prepared

    def _verify(self, value: PreparedAccountRead, *, fresh: bool) -> None:
        self._check()
        require(
            type(value) is PreparedAccountRead
            and value is self._prepared
            and value.context_id == self._id,
            "READ_CONTEXT_CAPABILITY_MISMATCH",
        )
        require(
            hmac.compare_digest(
                self._prepared_guard,
                hmac.digest(
                    self._salt,
                    (
                        value.query.get_secret_value() + "\0" + value.api_key.get_secret_value()
                    ).encode(),
                    hashlib.sha256,
                ),
            ),
            "READ_CONTEXT_PREPARED_QUERY_CHANGED",
        )
        req = request_metadata("GET", "/fapi/v3/account", value.query, value.api_key)
        raw = value.query.get_secret_value()
        require("&signature=" in raw, "READ_CONTEXT_SIGNATURE_INVALID")
        unsigned, signature = raw.rsplit("&signature=", 1)
        require(
            hmac.compare_digest(value.api_key.get_secret_value(), self._key.get_secret_value())
            and hmac.compare_digest(sign_payload(self._secret, unsigned), signature)
            and (not fresh or abs(int(self._last[1] * 1000) - int(req["timestamp"])) <= 5000),
            "READ_CONTEXT_SIGNATURE_OR_FRESHNESS_INVALID",
        )

    def consume(self, value: PreparedAccountRead) -> None:
        with self._lock:
            # Failed verification also spends this context. No silent correction/retry.
            require(not self._consumed, "READ_CONTEXT_ONE_SHOT_ONLY")
            self._consumed = True
            self._verify(value, fresh=True)

    def verify_consumed(self, value: PreparedAccountRead, *, require_fresh: bool = False) -> None:
        with self._lock:
            require(self._consumed, "READ_CONTEXT_NOT_CONSUMED")
            self._verify(value, fresh=require_fresh)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._prepared = None
            self._key = self._secret = SecretStr("")
            self._fingerprint = b""
            self._prepared_guard = b""


def capture_bound_copy_account(
    settings: Any,
    shared: EvidenceReadJournal,
    directory: Path,
    context: LocalReadCredentialContext,
    *,
    enable_capture: bool = False,
    copy_only: bool = False,
    sender: Any,
) -> dict[str, Any]:
    """Connect a single RAM signing context to V2 archive; sender is explicit.

    Tests supply synthetic responses. A real network/credential loader integration
    is a separate unimplemented boundary, and this function never imports one.
    """
    require(enable_capture is True and copy_only is True, "READ_CONTEXT_CAPTURE_DISABLED")
    require(
        type(context) is LocalReadCredentialContext
        and isinstance(shared, EvidenceReadJournal)
        and callable(sender),
        "READ_CONTEXT_CAPTURE_WIRING_INVALID",
    )
    try:
        descriptor = context.descriptor()
        require(
            (shared.account, shared.scope) == (descriptor["account"], descriptor["scope"]),
            "READ_CONTEXT_ACCOUNT_SCOPE_MISMATCH",
        )
        value = context.prepare()
        calls = 0

        def guarded_sender(method: Any, path: Any, query: Any, key: Any) -> Any:
            nonlocal calls
            require(
                calls == 0
                and method == "GET"
                and path == "/fapi/v3/account"
                and query is value.query
                and key is value.api_key,
                "READ_CONTEXT_WIRE_MISMATCH",
            )
            context.verify_consumed(value, require_fresh=True)
            calls += 1
            response = sender(method, path, query, key)
            context.verify_consumed(value)
            require(
                isinstance(response, RESTResponse)
                and isinstance(response.body, bytes)
                and context._secret.get_secret_value().encode() not in response.body,
                "READ_CONTEXT_SECRET_ECHO_FORBIDDEN",
            )
            return response

        transport = CopyAccountArchiveTransport(
            settings,
            shared,
            directory,
            copy_only=True,
            enable_capture=True,
            credential_context_id=descriptor["credential_context_id"],
            expected_api_key=context._key,
            sender=guarded_sender,
            monotonic=context._clocks.monotonic,
        )
        try:
            context.consume(value)
            transport("GET", "/fapi/v3/account", value.query, value.api_key)
        finally:
            transport.close()
        context.verify_consumed(value)
        require(
            calls == 1 and transport.archive_report is not None, "READ_CONTEXT_ARCHIVE_REQUIRED"
        )
        return dict(
            local_credential_context="SIGNATURE_AND_SINGLE_ARCHIVE_CONTEXT_MATCHED",
            credential_context_id=descriptor["credential_context_id"],
            local_signature_checked=True,
            sender_calls=calls,
            archive=transport.archive_report,
            credential_source_independently_verified=False,
            remote_account_identity_verified=False,
            baseline_adoption_approved=False,
            installation_ready=False,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
        )
    finally:
        context.close()
