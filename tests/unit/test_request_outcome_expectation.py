"""Scope is narrow and caller labels cannot masquerade as submission proof."""

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_request_outcome_candidate import RequestExpectation, quantity


@pytest.mark.parametrize(
    "patch",
    [
        {"role": "LIVE"},
        {"side": "LONG"},
        {"client_id": "../secret?"},
        {"client_id": "x" * 37},
        {"quantity": "0"},
        {"quantity": "-0.1"},
        {"quantity": "NaN"},
        {"quantity": "Infinity"},
        {"quantity": "1e-4"},
        {"quantity": 0.0007},
        {"quantity": None},
        {"venue_order_id": "0"},
        {"venue_order_id": True},
        {"order_type": "MARKET"},
        {"role": "STOP_LOSS", "order_type": "STOP_MARKET", "quantity": "0.0007"},
    ],
)
def test_invalid_caller_expectation_is_rejected(patch):
    value = (
        dict(
            role="ENTRY", client_id="synthetic-1", side="BUY", order_type="LIMIT", quantity="0.0007"
        )
        | patch
    )
    with pytest.raises(LocalQuotaStopped):
        RequestExpectation(**value)


def test_decimal_comparison_preserves_small_quantity_exactly():
    assert quantity("0.00070000") == quantity("0.0007")
    assert quantity("0.0007000000000001") > quantity("0.0007")


@pytest.mark.parametrize("body", [b"\xffPRIVATE_RESPONSE", b'{"PRIVATE_RESPONSE":'])
def test_invalid_body_errors_suppress_decoder_context(body):
    import traceback

    from services.execution.demo_request_outcome_candidate import _json

    with pytest.raises(LocalQuotaStopped) as error:
        _json(body)
    assert error.value.__suppress_context__
    rendered = "".join(traceback.format_exception(error.value))
    assert "PRIVATE_RESPONSE" not in rendered
