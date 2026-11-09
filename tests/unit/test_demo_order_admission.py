"""Missing counters, all advertised windows and conservative paired evidence."""

import copy

import pytest

from services.execution.demo_order_admission import (
    AdmissionEvidenceInvalid,
    OrderSample,
    assess_order_admission,
)


def limits():
    return {
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


def usage(count=0, body=True):
    rows = copy.deepcopy(limits()["rateLimits"][1:])
    if body:
        for row in rows:
            row["count"] = count
    return rows


def assess(a=None, b=None, info=None, first=1.0, second=1.5, now=2.0, headers=None):
    return assess_order_admission(
        limits() if info is None else info,
        OrderSample(first, usage() if a is None else a, headers or {}),
        OrderSample(second, usage() if b is None else b, headers or {}),
        now=now,
    )


def test_explicit_zero_is_sampled_but_never_authorizes_execution():
    report = assess()
    assert report["order_quota_evidence"] == "SAMPLED_HEADROOM_FITS"
    assert report["required_modeled_order_slots"] == 6
    assert not report["execution_approved"] and not report["account_capacity_reserved"]
    assert all(row["first_counter_source"] == "BODY" for row in report["windows"])


def test_official_limits_only_response_never_implies_zero_usage():
    report = assess(usage(body=False), usage(body=False))
    assert report["order_quota_evidence"] == "LIMITS_VERIFIED_USAGE_UNAVAILABLE"
    assert report["order_quota_blockers"] == ["ORDER_COUNTER_NOT_PROVIDED"]
    assert all(
        row["first_count"] is row["available_observed_slots"] is row["six_slot_plan_fits"] is None
        for row in report["windows"]
    )


def test_count_headers_can_supply_explicit_usage_without_body_field():
    r = assess(
        usage(body=False),
        usage(body=False),
        headers={"x-mbx-order-count-10s": 2, "x-mbx-order-count-1m": 5},
    )
    assert r["order_quota_evidence"] == "SAMPLED_HEADROOM_FITS"
    assert [row["conservative_observed_count"] for row in r["windows"]] == [2, 5]
    assert all(row["first_counter_source"] == "HEADER" for row in r["windows"])


def test_body_and_header_use_larger_observation():
    r = assess(
        usage(8), usage(10), headers={"x-mbx-order-count-10s": 7, "x-mbx-order-count-1m": 11}
    )
    assert [row["conservative_observed_count"] for row in r["windows"]] == [10, 11]
    assert all(row["first_counter_source"] == "BODY_AND_HEADER" for row in r["windows"])


@pytest.mark.parametrize("count,fit", [(294, True), (295, False), (300, False), (301, False)])
def test_protection_trigger_and_exit_slots_are_reserved(count, fit):
    r = assess(usage(count), usage(count))
    assert (not r["order_quota_blockers"]) is fit
    assert not r["execution_approved"]


def test_lower_sample_does_not_refund_or_silently_assume_window_reset():
    r = assess(usage(299), usage(0))
    assert r["windows"][0]["conservative_observed_count"] == 299
    assert "ORDER_COUNTER_DECREASE_OBSERVED" in r["order_quota_blockers"]
    assert "ORDER_SLOT_HEADROOM_INSUFFICIENT" in r["order_quota_blockers"]


def test_public_and_account_limits_use_smaller_value_and_detect_change():
    a, b = usage(), usage()
    for row in a:
        row["limit"] = 10000
    for row in b:
        row["limit"] = 20000
    r = assess(a, b)
    assert [row["effective_limit"] for row in r["windows"]] == [300, 1200]
    assert "ACCOUNT_ORDER_LIMIT_CHANGED" in r["order_quota_blockers"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("count", True),
        ("count", "0"),
        ("count", None),
        ("count", -1),
        ("count", 1.0),
        ("count", 10**12),
        ("limit", 0),
        ("limit", True),
        ("intervalNum", False),
        ("intervalNum", -1),
        ("interval", []),
        ("interval", "WEEK"),
    ],
)
def test_malformed_fields_are_rejected(field, value):
    rows = usage()
    rows[0][field] = value
    with pytest.raises(AdmissionEvidenceInvalid):
        assess(rows, rows)


@pytest.mark.parametrize(
    "change",
    [
        "missing_required",
        "duplicate_public",
        "duplicate_usage",
        "missing_usage",
        "extra_usage",
        "extra_public",
        "wrong_kind",
        "empty",
    ],
)
def test_all_advertised_order_windows_must_be_covered(change):
    info, a, b = limits(), usage(), usage()
    if change == "missing_required":
        info["rateLimits"].pop()
    elif change == "duplicate_public":
        info["rateLimits"].append(copy.deepcopy(info["rateLimits"][1]))
    elif change == "duplicate_usage":
        a.append(copy.deepcopy(a[0]))
    elif change == "missing_usage":
        a.pop()
    elif change == "extra_usage":
        a.append(
            {
                "rateLimitType": "ORDERS",
                "interval": "DAY",
                "intervalNum": 1,
                "limit": 10000,
                "count": 0,
            }
        )
    elif change == "extra_public":
        info["rateLimits"].append(
            {"rateLimitType": "ORDERS", "interval": "DAY", "intervalNum": 1, "limit": 10000}
        )
    elif change == "wrong_kind":
        a[0]["rateLimitType"] = "REQUEST_WEIGHT"
    else:
        a = []
    with pytest.raises(AdmissionEvidenceInvalid):
        assess(a, b, info)


@pytest.mark.parametrize(
    "first,second,now",
    [
        (2, 1, 3),
        (1, 2, 7),
        (1, 7, 7),
        (float("nan"), 2, 3),
        (1, 2, float("inf")),
        (True, 2, 3),
        (1, 2, 1),
    ],
)
def test_stale_or_untrusted_sample_time_never_passes(first, second, now):
    with pytest.raises(AdmissionEvidenceInvalid):
        assess(first=first, second=second, now=now)


def test_unknown_payload_fields_never_appear_in_output():
    rows = usage()
    rows[0]["private_raw_field"] = "SYNTHETIC_SECRET_NOT_FOR_REPORT"
    assert "SYNTHETIC_SECRET" not in str(assess(rows, rows))
