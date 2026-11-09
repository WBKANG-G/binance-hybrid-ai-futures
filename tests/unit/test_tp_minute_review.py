"""Independent phase oracle, strict model inputs and secret-free offline output."""

import copy
import json
import random

import pytest

from scripts import review_tp_minute_budget as module
from tests.fixtures.tp_admission_synthetic_exchange import COSTS


def trace(times, *, path="/fapi/v1/openOrders", method="GET", external=0):
    rows = []
    used = {}
    weight = module.MODEL_WEIGHTS[method, path]
    for index, stamp in enumerate(times, 1):
        minute = stamp // 60000
        used[minute] = used.get(minute, external) + weight
        rows.append(
            {
                "seq": index,
                "elapsed_ms": stamp,
                "minute": minute,
                "method": method,
                "path": path,
                "modeled_request_weight": weight,
                "used_weight_1m_model": used[minute],
                "status": 200,
            }
        )
    return {"synthetic_only": True, "trace": rows}


def test_reader_matches_the_existing_explicit_synthetic_cost_model():
    assert module.MODEL_WEIGHTS == COSTS


def test_observed_fixed_minute_fit_cannot_hide_bad_start_phase():
    result = module.review(trace([59990, 60000, 60010]), limit=100, reserve=0)
    assert result["episode_weight"] == 120
    assert result["observed_fixed_minute_peak_own_weight"] == 80
    assert result["rolling_recorded_60s_peak"]["weight"] == result["phase_witness_peak"] == 120
    assert result["conclusion"] == "MEASURED_START_PHASE_CAN_EXCEED_LIMIT"
    assert not result["actual_execution_approved"]


def test_whole_episode_total_does_not_equal_one_minute_usage():
    result = module.review(trace([0, 61000, 122000]), limit=60, reserve=0)
    assert result["episode_weight"] == 120 and result["phase_witness_peak"] == 40
    assert result["conclusion"] == "MEASURED_REQUESTS_FIT_ASSUMPTIONS_ONLY"
    assert not result["actual_execution_approved"] and not result["runtime_admission_changed"]


def test_exact_boundary_retains_floor_to_millisecond_uncertainty():
    result = module.review(trace([0, 60000]), limit=60, reserve=0)
    assert result["rolling_recorded_60s_peak"]["weight"] == 40
    assert result["guarded_peak"]["weight"] == 80
    assert result["conclusion"] == "TIMESTAMP_UNCERTAINTY_CAN_EXCEED_LIMIT"


def test_reserve_and_additional_timing_uncertainty_are_explicit():
    result = module.review(trace([0, 1, 2]), limit=150, reserve=40, timing_uncertainty_ms=25)
    assert result["guarded_peak"]["weight"] == 120
    assert result["rounding_guard_window_ms"] == 60051
    assert result["conclusion"] == "MEASURED_HEADROOM_INSUFFICIENT"


def test_unknown_shared_ip_usage_is_not_reserved_or_shifted_as_own_traffic():
    result = module.review(trace([0], external=70), limit=100, reserve=0)
    assert result["observed_counter_peak"] == 110
    assert result["observed_minutes"][0]["external_counter_extra_lower_bound"] == 70
    assert result["conclusion"] == "OBSERVED_COUNTER_EXCEEDS_LIMIT"
    assert not result["shared_ip_reserved"]


def test_lower_counter_cannot_refund_modeled_request_cost():
    data = trace([0, 1, 2])
    data["trace"][1]["used_weight_1m_model"] = 1
    result = module.review(data, limit=100, reserve=0)
    assert result["counter_conflicts"] == 1 and not result["counter_evidence_consistent"]
    assert result["phase_witness_peak"] == 120


def test_unresolved_send_is_charged_and_never_certified():
    data = trace([0])
    data["trace"][0]["status"] = None
    result = module.review(data)
    assert result["unresolved_responses"] == 1 and result["episode_weight"] == 40
    assert not result["cost_upper_bounds_proven"] and not result["actual_execution_approved"]


@pytest.mark.parametrize("seed", range(10))
def test_linear_window_agrees_with_independent_bucket_phase_oracle(seed):
    rng = random.Random(seed)
    times = sorted(rng.randrange(0, 240000) for _ in range(100))
    data = trace(times)
    naive = 0
    for offset in {0, *[(-t) % 60000 for t in times]}:
        buckets = {}
        for stamp in times:
            key = (stamp + offset) // 60000
            buckets[key] = buckets.get(key, 0) + 40
        naive = max(naive, max(buckets.values()))
    result = module.review(data, limit=100000, reserve=0)
    assert result["phase_witness_peak"] == naive


@pytest.mark.parametrize(
    "field,value",
    [
        ("seq", True),
        ("seq", 2),
        ("elapsed_ms", False),
        ("elapsed_ms", -1),
        ("elapsed_ms", 1.1),
        ("minute", True),
        ("modeled_request_weight", 0),
        ("modeled_request_weight", 1),
        ("used_weight_1m_model", False),
        ("used_weight_1m_model", -1),
        ("status", True),
        ("status", 600),
        ("path", "SENSITIVE_UNKNOWN_ROUTE"),
        ("method", []),
    ],
)
def test_bad_input_cannot_silently_understate_cost(field, value):
    data = trace([0])
    data["trace"][0][field] = value
    with pytest.raises(module.TraceReviewInvalid):
        module.review(data)


@pytest.mark.parametrize("change", ["time", "minute", "synthetic", "empty", "not_object"])
def test_bad_clock_or_trace_scope_is_rejected(change):
    data = trace([0, 60000])
    if change == "time":
        data["trace"][0]["elapsed_ms"] = 70000
    elif change == "minute":
        data["trace"][0]["minute"] = 2
    elif change == "synthetic":
        data["synthetic_only"] = False
    elif change == "empty":
        data["trace"] = []
    else:
        data = []
    with pytest.raises(module.TraceReviewInvalid):
        module.review(data)


@pytest.mark.parametrize(
    "options",
    [
        {"limit": True},
        {"limit": 0},
        {"reserve": 6000},
        {"reserve": -1},
        {"timing_uncertainty_ms": 1001},
    ],
)
def test_invalid_review_assumptions_are_rejected(options):
    with pytest.raises(module.TraceReviewInvalid):
        module.review(trace([0]), **options)


def test_input_is_not_mutated_and_unknown_fields_are_not_echoed(tmp_path, capsys):
    data = trace([0])
    data["secret"] = "SYNTHETIC_PRIVATE_MARKER"
    data["trace"][0]["query"] = "SYNTHETIC_PRIVATE_MARKER"
    before = copy.deepcopy(data)
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(data))
    assert module.main([str(path)]) == 0
    output = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE_MARKER" not in output and data == before
    assert json.loads(output)["actual_execution_approved"] is False


@pytest.mark.parametrize(
    "raw",
    [
        '{"secret":"SYNTHETIC_PRIVATE_MARKER"}',
        '{"synthetic_only":true,"trace":NaN}',
        "SYNTHETIC_PRIVATE_MARKER",
    ],
)
def test_cli_errors_are_structured_and_do_not_echo_raw_content(tmp_path, capsys, raw):
    path = tmp_path / "invalid.json"
    path.write_text(raw)
    assert module.main([str(path)]) == 2
    output = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE_MARKER" not in output
    assert json.loads(output)["review"] == "INVALID_INPUT"
