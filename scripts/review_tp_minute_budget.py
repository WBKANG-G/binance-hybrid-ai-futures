"""Offline synthetic trace review. Never performs HTTP, DB access or admission.

A fitting measured trace is not a cost upper bound, capacity reservation or
execution approval. Unknown fields, bodies, query strings and keys are not echoed.
"""

import argparse
import json
from pathlib import Path
from typing import Any

WINDOW_MS = 60_000
MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 20_000
GET_WEIGHTS = {
    "/fapi/v1/accountConfig": 5,
    "/fapi/v1/time": 1,
    "/fapi/v1/exchangeInfo": 1,
    "/fapi/v3/account": 5,
    "/fapi/v1/symbolConfig": 5,
    "/fapi/v3/positionRisk": 5,
    "/fapi/v1/openOrders": 40,
    "/fapi/v1/openAlgoOrders": 40,
    "/fapi/v1/order": 1,
    "/fapi/v1/algoOrder": 1,
    "/fapi/v1/income": 30,
    "/fapi/v1/ticker/bookTicker": 2,
    "/fapi/v1/premiumIndex": 1,
    "/fapi/v1/klines": 1,
}
WRITE_ROUTES = {
    ("POST", "/fapi/v1/order"),
    ("DELETE", "/fapi/v1/order"),
    ("POST", "/fapi/v1/algoOrder"),
    ("DELETE", "/fapi/v1/algoOrder"),
    ("POST", "/fapi/v1/listenKey"),
    ("PUT", "/fapi/v1/listenKey"),
    ("DELETE", "/fapi/v1/listenKey"),
}
MODEL_WEIGHTS = {("GET", path): weight for path, weight in GET_WEIGHTS.items()} | {
    route: 40 for route in WRITE_ROUTES
}


class TraceReviewInvalid(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def integer(value: Any, minimum: int, maximum: int, code: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise TraceReviewInvalid(code)
    return value


def peak(rows: list[dict[str, Any]], window_ms: int) -> dict[str, int]:
    left = total = best = best_left = best_right = 0
    for right, row in enumerate(rows):
        total += row["weight"]
        while row["elapsed_ms"] - rows[left]["elapsed_ms"] >= window_ms:
            total -= rows[left]["weight"]
            left += 1
        if total > best:
            best, best_left, best_right = total, left, right
    return {
        "weight": best,
        "first_seq": best_left + 1,
        "last_seq": best_right + 1,
        "first_elapsed_ms": rows[best_left]["elapsed_ms"],
        "last_elapsed_ms": rows[best_right]["elapsed_ms"],
    }


def review(
    data: Any, *, limit: int = 6000, reserve: int = 300, timing_uncertainty_ms: int = 0
) -> dict[str, Any]:
    limit = integer(limit, 1, 1_000_000, "LIMIT_INVALID")
    reserve = integer(reserve, 0, limit - 1, "RESERVE_INVALID")
    uncertainty = integer(timing_uncertainty_ms, 0, 1000, "UNCERTAINTY_INVALID")
    if not isinstance(data, dict) or data.get("synthetic_only") is not True:
        raise TraceReviewInvalid("SYNTHETIC_TRACE_REQUIRED")
    raw = data.get("trace")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ROWS:
        raise TraceReviewInvalid("TRACE_SIZE_INVALID")
    rows: list[dict[str, Any]] = []
    previous_time = previous_minute = -1
    counter_conflicts = unresolved = 0
    observed: dict[int, dict[str, int]] = {}
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise TraceReviewInvalid("ROW_INVALID")
        seq = integer(item.get("seq"), index, index, "SEQUENCE_INVALID")
        stamp = integer(item.get("elapsed_ms"), 0, 86_400_000, "TIMESTAMP_INVALID")
        minute = integer(item.get("minute"), 0, 1_000_000_000, "MINUTE_INVALID")
        if stamp < previous_time or minute < previous_minute:
            raise TraceReviewInvalid("CLOCK_ORDER_INVALID")
        method, path = item.get("method"), item.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            raise TraceReviewInvalid("ROUTE_INVALID")
        expected = MODEL_WEIGHTS.get((method, path))
        if expected is None:
            raise TraceReviewInvalid("UNMODELED_ROUTE")
        weight = integer(
            item.get("modeled_request_weight"), expected, expected, "WEIGHT_MODEL_MISMATCH"
        )
        counter = integer(item.get("used_weight_1m_model"), 0, 1_000_000_000, "COUNTER_INVALID")
        status = item.get("status")
        if status is None:
            unresolved += 1
        else:
            integer(status, 100, 599, "STATUS_INVALID")
        group = observed.setdefault(
            minute,
            {
                "minute": minute,
                "own_weight": 0,
                "counter_peak": 0,
                "last_counter": 0,
                "external_counter_extra_lower_bound": 0,
            },
        )
        group["own_weight"] += weight
        if counter < group["last_counter"] or counter < group["own_weight"]:
            counter_conflicts += 1
        group["counter_peak"] = max(group["counter_peak"], counter)
        group["last_counter"] = counter
        group["external_counter_extra_lower_bound"] = max(
            group["external_counter_extra_lower_bound"], counter - group["own_weight"]
        )
        rows.append({"seq": seq, "elapsed_ms": stamp, "weight": weight})
        previous_time, previous_minute = stamp, minute
    strict = peak(rows, WINDOW_MS)
    # Floor-to-ms traces can hide a sub-ms overlap at an exact60,000ms span.
    # Additional uncertainty is symmetric; this remains a model, not venue proof.
    guard_window = WINDOW_MS + 1 + 2 * uncertainty
    guarded = peak(rows, guard_window)
    offset = (-strict["first_elapsed_ms"]) % WINDOW_MS
    witness: dict[int, int] = {}
    for row in rows:
        bucket = (row["elapsed_ms"] + offset) // WINDOW_MS
        witness[bucket] = witness.get(bucket, 0) + row["weight"]
    witness_peak = max(witness.values())
    if witness_peak != strict["weight"]:
        raise TraceReviewInvalid("PHASE_WITNESS_INCONSISTENT")
    observed_peak = max(g["own_weight"] for g in observed.values())
    counter_peak = max(g["counter_peak"] for g in observed.values())
    if strict["weight"] > limit:
        conclusion = "MEASURED_START_PHASE_CAN_EXCEED_LIMIT"
    elif counter_peak > limit:
        conclusion = "OBSERVED_COUNTER_EXCEEDS_LIMIT"
    elif guarded["weight"] > limit:
        conclusion = "TIMESTAMP_UNCERTAINTY_CAN_EXCEED_LIMIT"
    elif guarded["weight"] > limit - reserve:
        conclusion = "MEASURED_HEADROOM_INSUFFICIENT"
    else:
        conclusion = "MEASURED_REQUESTS_FIT_ASSUMPTIONS_ONLY"
    return {
        "review": "OFFLINE_SYNTHETIC_MINUTE_TRACE_ONLY",
        "version": 1,
        "limit_assumption": limit,
        "reserve_assumption": reserve,
        "request_count": len(rows),
        "episode_weight": sum(r["weight"] for r in rows),
        "elapsed_ms": rows[-1]["elapsed_ms"] - rows[0]["elapsed_ms"],
        "observed_fixed_minute_peak_own_weight": observed_peak,
        "observed_counter_peak": counter_peak,
        "observed_minutes": [
            {k: v for k, v in g.items() if k != "last_counter"} for g in observed.values()
        ],
        "rolling_recorded_60s_peak": strict,
        "phase_witness_offset_ms": offset,
        "phase_witness_peak": witness_peak,
        "rounding_guard_window_ms": guard_window,
        "timing_uncertainty_ms": uncertainty,
        "guarded_peak": guarded,
        "counter_conflicts": counter_conflicts,
        "unresolved_responses": unresolved,
        "counter_evidence_consistent": counter_conflicts == 0,
        "conclusion": conclusion,
        "source_query_scopes_verified_by_this_reader": False,
        "cost_upper_bounds_proven": False,
        "shared_ip_reserved": False,
        "order_count_limits_proven": False,
        "runtime_admission_changed": False,
        "actual_execution_approved": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline synthetic minute review; never grants execution"
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--limit", type=int, default=6000)
    parser.add_argument("--reserve", type=int, default=300)
    parser.add_argument("--timing-uncertainty-ms", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        with args.report.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise TraceReviewInvalid("FILE_TOO_LARGE")

        def reject(value: str) -> Any:
            raise TraceReviewInvalid("JSON_NONFINITE")

        data = json.loads(raw, parse_constant=reject)
        result = review(
            data,
            limit=args.limit,
            reserve=args.reserve,
            timing_uncertainty_ms=args.timing_uncertainty_ms,
        )
    except TraceReviewInvalid as error:
        print(
            json.dumps(
                {"review": "INVALID_INPUT", "code": error.code, "actual_execution_approved": False}
            )
        )
        return 2
    except (OSError, ValueError, UnicodeError):
        print(
            json.dumps(
                {
                    "review": "INVALID_INPUT",
                    "code": "INPUT_UNREADABLE_OR_INVALID_JSON",
                    "actual_execution_approved": False,
                }
            )
        )
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
