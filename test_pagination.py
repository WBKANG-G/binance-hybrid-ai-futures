import sys
from datetime import timedelta
from uuid import uuid4

import pytest

from test_tp_history import case, demo_system, m  # noqa: F401


@pytest.fixture
def cohort(case):  # noqa: F811
    store, source, rows, calls, template = case
    orders = {}
    for index in range(16):
        item = template.model_copy(deep=True)
        item.signal = item.signal.model_copy(
            update={
                "signal_id": uuid4(),
                "signal_closed_candle_at": item.signal.signal_closed_candle_at
                + timedelta(seconds=index),
            }
        )
        item.client_id, item.algo_id, item.tp_algo_id = f"DE-{index}", f"DS-{index}", f"DT-{index}"
        orders[str(item.signal.signal_id)] = item
        # Only the older four have transport responses. Fetching an already-reviewed
        # head order would fail, rather than silently looking like a second page.
        if index < 4:
            for old, identifier in [("DS-owned", item.algo_id), ("DT-owned", item.tp_algo_id)]:
                rows[identifier] = {
                    **rows[old],
                    "clientAlgoId": identifier,
                    "algoStatus": "EXPIRED",
                }
    lease = store.acquire("DEMO-test", uuid4())
    store.change(lease, "TEST_PAGINATED_HISTORY", lambda state: setattr(state, "orders", orders))
    store.release(lease)
    head, _ = m.selected(store.read("DEMO-test"))
    return store, source, calls, m.operation_ref(head[-1])


def test_second_page_queries_only_remaining_four_without_mutation(cohort):
    store, source, calls, cursor = cohort
    before = m.read_state(store.engine, "DEMO-test")
    report = m.review(
        store.engine, source, "DEMO-test", after_ref=cursor, expected_total=16, expected_skipped=12
    )
    assert report["eligible_owned_orders"] == 16
    assert report["skipped_orders"] == 12 and report["selected_orders"] == 4
    assert report["remaining_after_page"] == 0 and not report["coverage_truncated"]
    assert report["covers_all_eligible_in_this_run"] is False
    assert report["signed_queries"] == 8 and len(calls) == 9
    assert m.read_state(store.engine, "DEMO-test") == before
    assert report["t06_ordering_verified"] is False and report["order_writes"] == 0


@pytest.mark.parametrize("fault", ["cursor", "total", "skipped"])
def test_changed_cursor_total_or_rank_blocks_before_transport(cohort, fault):
    store, source, calls, cursor = cohort
    with pytest.raises(ValueError):
        m.review(
            store.engine,
            source,
            "DEMO-test",
            after_ref="0" * 16 if fault == "cursor" else cursor,
            expected_total=17 if fault == "total" else 16,
            expected_skipped=11 if fault == "skipped" else 12,
        )
    assert calls == []


def test_two_pages_have_no_overlap_even_with_equal_candle_times(cohort):
    store, _, _, _ = cohort
    ledger = store.read("DEMO-test")
    stamp = next(iter(ledger.orders.values())).signal.signal_closed_candle_at
    for order in ledger.orders.values():
        order.signal = order.signal.model_copy(update={"signal_closed_candle_at": stamp})
    head, total = m.selected(ledger)
    tail, second_total = m.selected(
        ledger, after_ref=m.operation_ref(head[-1]), expected_total=16, expected_skipped=12
    )
    head_ids, tail_ids = {o.signal.signal_id for o in head}, {o.signal.signal_id for o in tail}
    assert total == second_total == 16 and len(head) == 12 and len(tail) == 4
    assert not head_ids.intersection(tail_ids) and len(head_ids | tail_ids) == 16


def test_ambiguous_reference_cannot_choose_a_page(cohort, monkeypatch):
    store, _, _, _ = cohort
    monkeypatch.setattr(m, "operation_ref", lambda _: "a" * 16)
    with pytest.raises(ValueError):
        m.selected(
            store.read("DEMO-test"), after_ref="a" * 16, expected_total=16, expected_skipped=12
        )


@pytest.mark.parametrize(
    "extra",
    [
        ["--after-operation-ref", "a" * 16],
        ["--expected-total", "16"],
        ["--expected-skipped", "12"],
        ["--after-operation-ref", "invalid", "--expected-total", "16", "--expected-skipped", "12"],
        ["--after-operation-ref", "a" * 16, "--expected-total", "16", "--expected-skipped", "-1"],
    ],
)
def test_incomplete_or_invalid_pagination_cli_blocks_before_credentials(monkeypatch, extra):
    monkeypatch.setattr(sys, "argv", ["review", "--review-only", *extra])
    monkeypatch.setattr(
        m, "credentials", lambda **_: pytest.fail("no credentials for invalid scope")
    )
    with pytest.raises(SystemExit) as error:
        m.main()
    assert error.value.code == 2
