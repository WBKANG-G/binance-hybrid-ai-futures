"""Pagination and strict income parsing with no network or persistence."""

from types import SimpleNamespace

import pytest

from services.execution.demo_day_review import DayReviewBlocked, income_window


def row(**updates):
    return dict(
        {"asset": "USDT", "incomeType": "COMMISSION", "income": "-0.1", "tranId": 1, "time": 101},
        **updates,
    )


class Client:
    settings = SimpleNamespace(demo_trading_enabled=False)

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def request(self, method, path, params):
        assert method == "GET" and path == "/fapi/v1/income"
        assert params["startTime"] == "100" and params["endTime"] == "200"
        self.calls.append(params)
        return self.pages[min(int(params["page"]) - 1, len(self.pages) - 1)]


@pytest.mark.parametrize(
    "update,code",
    [
        ({"asset": "BNB"}, "UNSUPPORTED_INCOME_ASSET_OR_TYPE"),
        ({"incomeType": "UNKNOWN"}, "UNSUPPORTED_INCOME_ASSET_OR_TYPE"),
        ({"income": "NaN"}, "INVALID_INCOME_NUMBER"),
        ({"income": True}, "INVALID_INCOME_NUMBER"),
        ({"time": True}, "INVALID_INCOME_ID_OR_TIME"),
        ({"tranId": True}, "INVALID_INCOME_ID_OR_TIME"),
        ({"time": 99}, "INVALID_INCOME_ID_OR_TIME"),
        ({"time": 201}, "INVALID_INCOME_ID_OR_TIME"),
        ({"time": 100}, "BOUNDARY_TIMESTAMP_AMBIGUOUS"),
    ],
)
def test_invalid_income_stops_instead_of_guessing(update, code):
    with pytest.raises(DayReviewBlocked, match=code):
        income_window(Client([[row(**update)]]), 100, 200)


def test_exact_duplicates_count_once_and_conflicts_block():
    assert len(income_window(Client([[row(), row()]]), 100, 200)) == 1
    with pytest.raises(DayReviewBlocked, match="CONFLICTING_INCOME_ID"):
        income_window(Client([[row(), row(income="-0.2")]]), 100, 200)


def test_pagination_reads_to_short_page_and_never_assumes_full_page_complete():
    full = [row(tranId=i) for i in range(1000)]
    client = Client([full, []])
    assert len(income_window(client, 100, 200)) == 1000 and len(client.calls) == 2
    with pytest.raises(DayReviewBlocked, match="INCOME_PAGINATION_INCOMPLETE"):
        income_window(Client([full]), 100, 200)


def test_unknown_error_text_is_not_exported():
    assert str(DayReviewBlocked("private-secret-url")) == "DAY_EVIDENCE_BLOCKED"
