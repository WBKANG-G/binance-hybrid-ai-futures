from decimal import Decimal

import pytest

from test_public_liquidity import m, make_client


def test_scope_and_summary_do_not_become_execution_instructions():
    client, _ = make_client()
    report = m.observe(client, monotonic=lambda: 0, pause=lambda _: None)
    assert m.MAX_SAMPLES == 60 and m.SAMPLING_SECONDS == 120
    assert m.MAX_QUANTITY == Decimal("0.0007") and m.MAX_NOTIONAL == 100
    assert len(report["candidate_examples"]) == 3
    assert report["minimum_ask_quantity"] == report["maximum_ask_quantity"] == "0.0003"
    assert report["first_sample"] == report["last_sample"]
    assert report["next_action"] == "REVIEW_ONLY_DO_NOT_SUBMIT_FROM_THIS_REPORT"
    assert report["signal_generated"] is False and report["partial_fill_guaranteed"] is False


def test_sleep_reaching_deadline_does_not_make_another_request():
    client, calls = make_client()
    tick = [0]

    def pause(_):
        tick[0] = 120

    report = m.observe(client, monotonic=lambda: tick[0], pause=pause)
    assert report["sample_count"] == 1 and len(calls) == 3


def test_no_samples_before_deadline_is_a_failure():
    client, calls = make_client()
    tick = iter([0, 121])
    with pytest.raises(ValueError, match="NO_SAMPLES"):
        m.observe(client, monotonic=lambda: next(tick), pause=lambda _: pytest.fail())
    assert len(calls) == 2


def test_thick_top_never_produces_candidate_examples():
    client, _ = make_client()
    original = client.public

    def thick(path, params=None):
        row = original(path, params)
        if path == "/fapi/v1/ticker/bookTicker":
            row["askQty"] = "1.0000"
        return row

    client.public = thick
    report = m.observe(client, monotonic=lambda: 0, pause=lambda _: None)
    assert report["sample_count"] == 60 and report["within_cap_thin_top_samples"] == 0
    assert report["candidate_examples"] == []
