"""Queries outside the modeled parameter shape must stop before reservation/send."""

# ruff: noqa: F811
from urllib.parse import urlencode

import pytest
from pydantic import SecretStr

from packages.persistence.demo_order_quota import LocalQuotaStopped
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly, open_session  # noqa: F401
from tests.integration.test_quota_startup_review import snapshot
from tests.unit.test_quota_startup_read_only import files


def prepare(session):
    b = session.budget
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    b.configure(
        {
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": 6000,
                },
                {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 300},
                {"rateLimitType": "ORDERS", "interval": "MINUTE", "intervalNum": 1, "limit": 1200},
            ]
        }
    )
    b.preflight()


@pytest.mark.parametrize("wire", [False, True])
@pytest.mark.parametrize(
    "path,params",
    [
        ("/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": "1500"}),
        ("/fapi/v1/premiumIndex", {}),
        ("/fapi/v1/ticker/bookTicker", {}),
    ],
)
def test_nonmodeled_public_shape_is_refused_before_resource_or_transport(
    assembly, path, params, wire
):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    prepare(session)
    before, disk, old_calls = snapshot(store), files(storage.order_directory), list(calls)
    try:
        with pytest.raises(LocalQuotaStopped, match="QUERY_CONTRACT"):
            if wire:
                session.budget("GET", path, SecretStr(urlencode(params)), None)
            else:
                session.coordinator.client.public(path, params)
        assert (
            snapshot(store) == before
            and files(storage.order_directory) == disk
            and calls == old_calls
        )
    finally:
        session.close()


def test_invalid_delete_shape_stops_before_sdk_scope_lookup(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    client = session.coordinator.client
    client.settings = client.settings.model_copy(update={"demo_trading_enabled": True})
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="QUERY_CONTRACT"):
            client.request(
                "DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": "DS-id", "extra": "not-modeled"}
            )
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    finally:
        client.settings = session._disabled_settings
        session.close()


def test_duplicate_wire_keys_stop_before_shared_reservation(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="QUERY_CONTRACT"):
            session.budget("GET", "/fapi/v1/time", SecretStr("x=1&x=2"), None)
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    finally:
        session.close()
