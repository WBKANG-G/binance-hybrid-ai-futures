"""Canonical evidence contains no signature, key, headers, or response body."""

import hashlib

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_response_evidence import (
    MAX_BODY,
    request_metadata,
    response_metadata,
    validate_request,
    validate_response,
)


def test_public_metadata_has_no_authentication_fields():
    value = request_metadata("GET", "/fapi/v1/time", SecretStr(""), None)
    assert value == dict(
        method="GET",
        path="/fapi/v1/time",
        params={},
        timestamp=None,
        recvWindow=None,
        authenticated=False,
    )
    validate_request(value)


def test_original_identifier_is_preserved_without_signing_material():
    value = request_metadata(
        "GET",
        "/fapi/v1/order",
        SecretStr(
            "symbol=BTCUSDT&origClientOrderId=synthetic-1&timestamp=17&recvWindow=5000&signature="
            + "a" * 64
        ),
        SecretStr("PRIVATE_KEY"),
    )
    assert value["params"] == {"symbol": "BTCUSDT", "origClientOrderId": "synthetic-1"}
    assert value["timestamp"] == "17" and value["authenticated"]
    assert "signature" not in str(value) and "PRIVATE_KEY" not in str(value)


@pytest.mark.parametrize("body", [b"", b' {"a":1}', b'{"a":1}'])
def test_hash_is_over_exact_body_bytes(body):
    value = response_metadata(RESTResponse(200, body, {"set-cookie": "private"}))
    assert value == dict(
        status=200, body_bytes=len(body), body_sha256=hashlib.sha256(body).hexdigest()
    )
    validate_response(value)


@pytest.mark.parametrize(
    "response",
    [
        RESTResponse(99, b"", {}),
        RESTResponse(True, b"", {}),
        RESTResponse(200, "bad", {}),
        RESTResponse(200, b"x" * (MAX_BODY + 1), {}),
    ],
)
def test_invalid_response_shape_has_no_evidence(response):
    with pytest.raises(LocalQuotaStopped, match="RESPONSE_INVALID"):
        response_metadata(response)


@pytest.mark.parametrize(
    "query",
    [
        "timestamp=1&recvWindow=5000",
        "timestamp=1&timestamp=2&recvWindow=5000&signature=" + "a" * 64,
        "timestamp=1&recvWindow=5000&signature=" + "a" * 64 + "&unknown=secret",
    ],
)
def test_noncanonical_or_unsigned_wire_query_is_rejected(query):
    with pytest.raises(LocalQuotaStopped):
        request_metadata("GET", "/fapi/v3/account", SecretStr(query), SecretStr("PRIVATE"))
