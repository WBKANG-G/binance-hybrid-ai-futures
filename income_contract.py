"""Offline candidate contract only. No runtime wiring, DB, keys or network client.

Every completeness claim assumes the supplied reader honors the documented
inclusive time/type/limit contract. Synthetic success cannot verify a venue.
"""

import time
from decimal import Decimal, InvalidOperation

LIMIT = 1000
TARGETS = ("TRANSFER", "WELCOME_BONUS")


class ContractBlocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise ContractBlocked(code)


def _merge(destination, source):
    for key, value in source.items():
        require(key not in destination or destination[key] == value, "CONFLICTING_TRANSFER_ID")
        destination[key] = value


def _rows(raw, start, end, kind):
    require(isinstance(raw, list) and len(raw) <= LIMIT, "INVALID_RESPONSE_SHAPE")
    targets = {}
    for row in raw:
        require(isinstance(row, dict), "INVALID_ROW")
        category, stamp = row.get("incomeType"), row.get("time")
        require(isinstance(category, str) and bool(category), "INVALID_INCOME_TYPE")
        require(type(stamp) is int, "INVALID_INCOME_TIME")
        require(start <= stamp <= end, "ROW_OUTSIDE_FIXED_WINDOW")
        require(kind is None or category == kind, "FILTER_RESPONSE_MISMATCH")
        if category not in TARGETS:
            continue
        require(row.get("asset") == "USDT", "NON_USDT_TARGET")
        identity = row.get("tranId")
        require(
            (type(identity) is int and identity > 0)
            or (
                isinstance(identity, str)
                and identity.isascii()
                and identity.isdigit()
                and int(identity) > 0
            ),
            "INVALID_TRANSFER_ID",
        )
        value = row.get("income")
        require(type(value) in (str, int, Decimal), "INVALID_TRANSFER_AMOUNT")
        try:
            amount = Decimal(value)
        except (InvalidOperation, ValueError):
            raise ContractBlocked("INVALID_TRANSFER_AMOUNT") from None
        require(amount.is_finite(), "INVALID_TRANSFER_AMOUNT")
        key = category + ":" + str(int(identity))
        _merge(targets, {key: (amount, stamp)})
    return targets


def scan(
    reader,
    start_ms,
    end_ms,
    *,
    strategy="adaptive_filtered",
    max_requests=64,
    max_depth=20,
    clock=time.monotonic,
    max_seconds=30,
):
    """Return complete target maps under contract, or raise without partial output.

    Small histories always need one unfiltered read. Only a full initial response
    enables filtered reads or disjoint inclusive time partitions. No page parameter,
    retries, cached ledger values, cursors or advancing income_since_ms are used.
    """
    require(
        type(start_ms) is type(end_ms) is int
        and 0 <= start_ms <= end_ms
        and end_ms - start_ms <= 604800000,
        "INVALID_FIXED_WINDOW",
    )
    require(strategy in {"adaptive_filtered", "unfiltered_split"}, "INVALID_STRATEGY")
    require(type(max_requests) is int and 1 <= max_requests <= 64, "INVALID_REQUEST_BUDGET")
    require(type(max_depth) is int and 0 <= max_depth <= 20, "INVALID_DEPTH_BUDGET")
    require(type(max_seconds) in (int, float) and 0 < max_seconds <= 30, "INVALID_TIME_BUDGET")
    began = clock()
    attempts = 0

    def time_check():
        require(0 <= clock() - began < max_seconds, "TIME_BUDGET_EXHAUSTED")

    def read(start, end, kind):
        nonlocal attempts
        time_check()
        require(attempts < max_requests, "REQUEST_BUDGET_EXHAUSTED")
        params = {"startTime": str(start), "endTime": str(end), "limit": str(LIMIT)}
        if kind is not None:
            params["incomeType"] = kind
        attempts += 1
        try:
            raw = reader("GET", "/fapi/v1/income", params)
        except Exception:
            raise ContractBlocked("UPSTREAM_READ_FAILED_NO_RETRY") from None
        time_check()
        return raw, _rows(raw, start, end, kind)

    def partition(start, end, kind, depth, initial=None):
        raw, prefix = initial if initial is not None else read(start, end, kind)
        if len(raw) < LIMIT:
            return prefix
        require(start < end, "DENSE_MILLISECOND_UNRESOLVED")
        require(depth < max_depth, "DEPTH_BUDGET_EXHAUSTED")
        middle = (start + end) // 2
        combined = partition(start, middle, kind, depth + 1)
        _merge(combined, partition(middle + 1, end, kind, depth + 1))
        require(
            all(combined.get(k) == v for k, v in prefix.items()), "UNFILTERED_PREFIX_TARGET_MISSING"
        )
        return combined

    raw, prefix = read(start_ms, end_ms, None)
    used_filter = len(raw) == LIMIT and strategy == "adaptive_filtered"
    if len(raw) < LIMIT:
        targets = prefix
    elif used_filter:
        targets = {}
        for kind in TARGETS:
            _merge(targets, partition(start_ms, end_ms, kind, 0))
        require(
            all(targets.get(k) == v for k, v in prefix.items()), "UNFILTERED_PREFIX_TARGET_MISSING"
        )
    else:
        targets = partition(start_ms, end_ms, None, 0, initial=(raw, prefix))
    time_check()
    transfers = {k: str(v[0]) for k, v in sorted(targets.items())}
    return {
        "strategy": strategy,
        "window_start_ms": start_ms,
        "window_end_ms": end_ms,
        "transfers": transfers,
        "transfer_sum_usdt": str(sum((v[0] for v in targets.values()), Decimal(0))),
        "http_attempts": attempts,
        "income_weight30_model": 30 * attempts,
        "used_type_filters": used_filter,
        "target_map_complete_under_reader_contract": True,
        "server_filter_support_proven": False,
        "all_financial_types_classified": False,
        "runtime_switch_approved": False,
        "database_changed": False,
        "order_writes": 0,
    }
