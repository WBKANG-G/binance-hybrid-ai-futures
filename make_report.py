"""Synthetic contract report. No SDK client, credentials, DB or network access."""

import json
from pathlib import Path

from income_contract import ContractBlocked, scan


def item(i, stamp, kind="COMMISSION", amount="-0.01"):
    return {"tranId": i, "time": stamp, "incomeType": kind, "income": amount, "asset": "USDT"}


def compare(label, data, end):
    calls = []

    def reader(method, path, params):
        assert method == "GET" and path == "/fapi/v1/income" and "page" not in params
        calls.append(params)
        selected = [
            r for r in data if int(params["startTime"]) <= r["time"] <= int(params["endTime"])
        ]
        if "incomeType" in params:
            selected = [r for r in selected if r["incomeType"] == params["incomeType"]]
        return selected[:1000]

    reference = {
        r["incomeType"] + ":" + str(r["tranId"]): r["income"]
        for r in data
        if r["incomeType"] in {"TRANSFER", "WELCOME_BONUS"}
    }
    legacy_requests = len(data) // 1000 + 1
    result = {
        "case": label,
        "synthetic_rows": len(data),
        "reference_targets": len(reference),
        "legacy_page_model_complete": legacy_requests <= 10,
        "legacy_page_model_requests": min(10, legacy_requests),
        "strategies": {},
    }
    for strategy in ("adaptive_filtered", "unfiltered_split"):
        calls.clear()
        try:
            report = scan(reader, 0, end, strategy=strategy)
            assert report["transfers"] == reference
            result["strategies"][strategy] = {
                "status": "COMPLETE_UNDER_SYNTHETIC_READER_CONTRACT",
                "requests": len(calls),
                "income_weight30_model": 30 * len(calls),
                "target_map_equal": True,
            }
        except ContractBlocked as error:
            result["strategies"][strategy] = {
                "status": "BLOCKED_NO_PARTIAL_RESULT",
                "reason": str(error),
                "requests": len(calls),
            }
    return result


def main():
    cases = []
    for count in (12, 1001, 9001):
        data = [item(i + 1, i) for i in range(count)]
        if count > 12:
            data += [item(100001, 1, "TRANSFER", "100"), item(100001, count, "WELCOME_BONUS", "2")]
        cases.append(compare("fee_history_" + str(count), data, count + 1))
    cases.append(
        compare(
            "dense_target_history", [item(i + 1, i, "TRANSFER", "1") for i in range(2501)], 2500
        )
    )
    cases.append(
        compare(
            "1000_targets_same_millisecond",
            [item(i + 1, 0, "TRANSFER", "1") for i in range(1000)],
            0,
        )
    )
    report = {
        "synthetic_only": True,
        "runtime_deployed": False,
        "runtime_switch_approved": False,
        "actual_exchange_requests": 0,
        "database_writes": 0,
        "order_writes": 0,
        "weight30_is_endpoint_model_not_lifecycle_budget": True,
        "cases": cases,
    }
    Path(__file__).with_name("SYNTHETIC_COMPARISON.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
