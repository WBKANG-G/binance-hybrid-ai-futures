"""Explicit opt-in GET-only candidate. No automatic deployment or archive authority."""

import threading
from typing import Any

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_read_audit import require
from packages.persistence.demo_response_evidence import (
    Capture,
    EvidenceReadJournal,
    request_metadata,
    response_metadata,
)
from services.execution.demo_runtime_read_transport import (
    AccountedDemoReadTransport,
    _demo_get_http,
)


class EvidenceReadTransport(AccountedDemoReadTransport):
    def __init__(
        self,
        settings: Any,
        shared: Any,
        *,
        enable_evidence_reads: bool = False,
        sender: Any = _demo_get_http,
        **kwargs: Any,
    ):
        require(enable_evidence_reads is True, "RESPONSE_EVIDENCE_TRANSPORT_DISABLED")
        require(
            isinstance(shared, EvidenceReadJournal) and not shared.read_only,
            "RESPONSE_EVIDENCE_WRITABLE_PROVIDER_REQUIRED",
        )
        self._evidence_lifetime = threading.RLock()
        self._evidence_shared = shared
        self._evidence_sender = sender
        self._configured_evidence_sender = sender
        self._active_query: Any = None
        self._active_key: Any = None
        super().__init__(
            settings, shared, enable_reads=True, sender=self._capture_response, **kwargs
        )
        report = shared.evidence.review(
            shared.directory, shared.scope, shared.identity, shared.account
        )
        require(
            report["pending_requests"] == 0 and not report["shared_stopped"],
            "RESPONSE_EVIDENCE_UNFINISHED_REQUESTS",
        )

    def _capture_response(self, method: Any, path: Any, query: Any, key: Any) -> RESTResponse:
        capture = self._evidence_shared.capture.get()
        require(
            capture is not None
            and query is self._active_query
            and key is self._active_key
            and self._evidence_sender is self._configured_evidence_sender,
            "RESPONSE_EVIDENCE_WIRE_CONTEXT_INVALID",
        )
        assert capture is not None
        require(
            request_metadata(method, path, query, key) == capture.request,
            "RESPONSE_EVIDENCE_REQUEST_CHANGED",
        )
        capture.wire_started = True
        response = self._evidence_sender(method, path, query, key)
        require(
            request_metadata(method, path, query, key) == capture.request,
            "RESPONSE_EVIDENCE_REQUEST_CHANGED",
        )
        capture.response = response_metadata(response)
        assert isinstance(response, RESTResponse)
        return response

    def __call__(self, method: Any, path: Any, query: Any, key: Any) -> RESTResponse:
        with self._evidence_lifetime:
            require(self._shared is self._evidence_shared, "RESPONSE_EVIDENCE_PROVIDER_CHANGED")
            capture = Capture(request_metadata(method, path, query, key))
            self._active_query, self._active_key = query, key
            token = self._evidence_shared.capture.set(capture)
            try:
                return super().__call__(method, path, query, key)
            finally:
                self._evidence_shared.capture.reset(token)
                self._active_query = self._active_key = None
