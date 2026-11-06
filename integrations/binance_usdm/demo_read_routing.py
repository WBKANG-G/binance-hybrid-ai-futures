"""Opt-in TP CLI GET keep-alive; public market reads and all writes retain transport."""

import threading
from typing import Protocol

from pydantic import SecretStr

from integrations.binance_usdm.demo import PUBLIC, DemoFailure, Transport
from integrations.binance_usdm.recovery_transport import (
    GET_PATHS,
    RecoveryTransport,
    safe_transport_diagnostics,
)
from integrations.binance_usdm.rest import RESTResponse


class ReadPool(Protocol):
    def __call__(
        self, method: str, path: str, query: SecretStr, api_key: SecretStr | None
    ) -> RESTResponse: ...
    def close(self) -> None: ...
    def diagnostics(self) -> dict[str, str | int]: ...


class TPReadRouting:
    def __init__(self, original: Transport, *, pool: ReadPool | None = None):
        self.original = original
        self.pool = pool if pool is not None else RecoveryTransport()
        self._connection = threading.Lock()
        self._state = threading.Lock()
        self._closed = False
        self._pooled = self._public = self._mutations = 0

    def _attempt(self, kind: str) -> None:
        with self._state:
            if self._closed:
                raise DemoFailure("TP read transport already closed")
            if kind == "pooled":
                self._pooled += 1
            elif kind == "public":
                self._public += 1
            else:
                self._mutations += 1

    def __call__(
        self, method: str, path: str, query: SecretStr, api_key: SecretStr | None
    ) -> RESTResponse:
        if method == "GET":
            if path in GET_PATHS:
                # WSS clock validation and the execution consumer may run on
                # different threads. HTTPSConnection must never overlap calls.
                with self._connection:
                    self._attempt("pooled")
                    return self.pool(method, path, query, api_key)
            if path in PUBLIC:
                self._attempt("public")
                return self.original(method, path, query, api_key)
            raise DemoFailure("TP read transport GET route forbidden")
        if method not in {"POST", "PUT", "DELETE"}:
            raise DemoFailure("TP read transport method forbidden")
        # SDK authorization, scoped order caps, fencing and unknown-write policy
        # remain upstream. Forward the same arguments once; never retry/fallback.
        self._attempt("mutation")
        return self.original(method, path, query, api_key)

    def diagnostics(self) -> dict[str, object]:
        with self._connection:
            pooled = safe_transport_diagnostics(self.pool.diagnostics())
            with self._state:
                return {
                    "tp_read_transport": "THREAD_SERIALIZED_POOLED_GET",
                    "pooled_get_attempts": self._pooled,
                    "original_public_get_attempts": self._public,
                    "original_mutation_attempts": self._mutations,
                    "added_request_retries": 0,
                    "closed": self._closed,
                    "pooled_diagnostic": pooled,
                }

    def close(self) -> None:
        with self._connection:
            with self._state:
                if self._closed:
                    return
                self._closed = True
            self.pool.close()
