"""Read-only order-limit assessment; missing usage is never interpreted as zero.

The official Query User Rate Limit response model guarantees limits, not counts.
Accept usage only when explicitly returned in the body or known order-count
headers. This assessment cannot reserve account capacity or authorize execution.
"""

import math
from dataclasses import dataclass, field
from typing import Any

ORDER_SLOTS = 6
INTERVAL_SECONDS = {"SECOND": 1, "MINUTE": 60, "HOUR": 3600, "DAY": 86400}
COUNTER_HEADERS = {("SECOND", 10): "x-mbx-order-count-10s", ("MINUTE", 1): "x-mbx-order-count-1m"}


class AdmissionEvidenceInvalid(ValueError):
    pass


def quota_int(value: Any, minimum: int, maximum: int = 1_000_000_000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise AdmissionEvidenceInvalid("ORDER_INTEGER_INVALID")
    return value


def quota_key(row: Any) -> tuple[str, int]:
    if not isinstance(row, dict) or row.get("rateLimitType") != "ORDERS":
        raise AdmissionEvidenceInvalid("ORDER_WINDOW_INVALID")
    interval = row.get("interval")
    if not isinstance(interval, str) or interval not in INTERVAL_SECONDS:
        raise AdmissionEvidenceInvalid("ORDER_INTERVAL_UNSUPPORTED")
    number = quota_int(row.get("intervalNum"), 1, 86400)
    if number * INTERVAL_SECONDS[interval] > 86400:
        raise AdmissionEvidenceInvalid("ORDER_INTERVAL_UNSUPPORTED")
    return interval, number


def published_order_windows(info: Any) -> dict[tuple[str, int], int]:
    if not isinstance(info, dict) or not isinstance(info.get("rateLimits"), list):
        raise AdmissionEvidenceInvalid("ORDER_LIMITS_UNAVAILABLE")
    if not 1 <= len(info["rateLimits"]) <= 32:
        raise AdmissionEvidenceInvalid("ORDER_LIMITS_UNAVAILABLE")
    result = {}
    for row in info["rateLimits"]:
        if not isinstance(row, dict):
            raise AdmissionEvidenceInvalid("ORDER_WINDOW_INVALID")
        if row.get("rateLimitType") != "ORDERS":
            continue
        key = quota_key(row)
        if key in result:
            raise AdmissionEvidenceInvalid("ORDER_WINDOW_DUPLICATE")
        result[key] = quota_int(row.get("limit"), 1)
    if not {("SECOND", 10), ("MINUTE", 1)} <= result.keys():
        raise AdmissionEvidenceInvalid("REQUIRED_ORDER_WINDOWS_MISSING")
    return result


@dataclass(frozen=True)
class OrderSample:
    received_at: float
    body: Any
    headers: dict[str, int] = field(default_factory=dict)


def order_usage(sample: OrderSample) -> dict[tuple[str, int], tuple[int, int | None, str]]:
    rows = sample.body
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        raise AdmissionEvidenceInvalid("ORDER_USAGE_UNAVAILABLE")
    result = {}
    for row in rows:
        key = quota_key(row)
        if key in result:
            raise AdmissionEvidenceInvalid("ORDER_WINDOW_DUPLICATE")
        limit = quota_int(row.get("limit"), 1)
        header = COUNTER_HEADERS.get(key)
        counts = []
        sources = []
        if "count" in row:
            counts.append(quota_int(row["count"], 0))
            sources.append("BODY")
        if header is not None and header in sample.headers:
            counts.append(quota_int(sample.headers[header], 0))
            sources.append("HEADER")
        result[key] = (
            limit,
            max(counts) if counts else None,
            "_AND_".join(sources) or "NOT_PROVIDED",
        )
    return result


def assess_order_admission(
    info: Any, first: OrderSample, second: OrderSample, *, now: float
) -> dict[str, Any]:
    stamps = first.received_at, second.received_at, now
    if (
        any(type(t) not in {int, float} or not math.isfinite(t) for t in stamps)
        or not 0 <= second.received_at - first.received_at <= 5
        or now < second.received_at
        or not 0 <= now - first.received_at <= 5
    ):
        raise AdmissionEvidenceInvalid("ORDER_SAMPLE_STALE_OR_CLOCK_INVALID")
    advertised = published_order_windows(info)
    before, after = order_usage(first), order_usage(second)
    if before.keys() != advertised.keys() or after.keys() != advertised.keys():
        raise AdmissionEvidenceInvalid("ORDER_WINDOW_COVERAGE_MISMATCH")
    windows = []
    nondecreasing = limits_unchanged = enough = all_counts = True
    for key in sorted(advertised, key=lambda k: INTERVAL_SECONDS[k[0]] * k[1]):
        old_limit, old_count, old_source = before[key]
        new_limit, new_count, new_source = after[key]
        effective = min(advertised[key], old_limit, new_limit)
        available = observed = None
        fits = None
        if old_count is not None and new_count is not None:
            observed = max(old_count, new_count)
            available = effective - observed
            fits = available >= ORDER_SLOTS
            enough &= fits
            nondecreasing &= new_count >= old_count
        else:
            all_counts = False
        limits_unchanged &= old_limit == new_limit
        windows.append(
            {
                "interval": key[0],
                "interval_num": key[1],
                "seconds": INTERVAL_SECONDS[key[0]] * key[1],
                "advertised_limit": advertised[key],
                "account_limit": new_limit,
                "effective_limit": effective,
                "first_count": old_count,
                "second_count": new_count,
                "first_counter_source": old_source,
                "second_counter_source": new_source,
                "conservative_observed_count": observed,
                "available_observed_slots": available,
                "six_slot_plan_fits": fits,
            }
        )
    blockers = []
    if not all_counts:
        blockers.append("ORDER_COUNTER_NOT_PROVIDED")
    if not nondecreasing:
        blockers.append("ORDER_COUNTER_DECREASE_OBSERVED")
    if not limits_unchanged:
        blockers.append("ACCOUNT_ORDER_LIMIT_CHANGED")
    if not enough:
        blockers.append("ORDER_SLOT_HEADROOM_INSUFFICIENT")
    return {
        "order_quota_evidence": "SAMPLED_HEADROOM_FITS"
        if not blockers
        else "LIMITS_VERIFIED_USAGE_UNAVAILABLE"
        if not all_counts
        else "SAMPLED_CONSTRAINT_BLOCKED",
        "sample_age_ms": round((now - first.received_at) * 1000),
        "required_modeled_order_slots": ORDER_SLOTS,
        "order_plan": {
            "entry": 1,
            "protection_creates": 2,
            "possible_algo_trigger_executions": 2,
            "risk_reducing_exit": 1,
        },
        "windows": windows,
        "order_quota_blockers": blockers,
        "account_capacity_reserved": False,
        "algo_trigger_counter_upper_bound_proven": False,
        "execution_approved": False,
    }
