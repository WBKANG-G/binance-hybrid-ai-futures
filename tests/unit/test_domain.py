from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.domain.contracts import AlgoOrder, DomainEvent, EntryOrder, Position, Signal


def signal_data() -> dict[str, object]:
    now = datetime.now(UTC)
    return dict(
        signal_id=uuid4(),
        environment="PAPER",
        symbol="BTCUSDT",
        strategy="TREND_FOLLOWING",
        direction="LONG",
        signal_closed_candle_at=now,
        expires_at=now + timedelta(minutes=1),
        suggested_limit_price="100",
        initial_stop_price="99",
        target_price="102",
        risk_profile_version="unassigned",
    )


def test_signal_serializes_money_as_strings() -> None:
    signal = Signal(**signal_data())
    assert signal.suggested_limit_price == Decimal("100")
    assert signal.model_dump(mode="json")["suggested_limit_price"] == "100"
    assert signal.entry_policy == "LIMIT_ONLY"


@pytest.mark.parametrize(
    "field,value",
    [
        ("entry_policy", "MARKET"),
        ("suggested_limit_price", 100.1),
        ("initial_stop_price", "NaN"),
        ("suggested_limit_price", "0"),
        ("strategy", "MARTINGALE"),
        ("approved", True),
        ("max_leverage", "100"),
    ],
)
def test_invalid_signal_contract(field: str, value: object) -> None:
    data = signal_data() | {field: value}
    with pytest.raises(ValidationError):
        Signal(**data)


def test_naive_time_and_bad_expiry_rejected() -> None:
    for data in [
        signal_data() | {"signal_closed_candle_at": datetime(2026, 1, 1)},
        signal_data() | {"expires_at": datetime(2020, 1, 1, tzinfo=UTC)},
    ]:
        with pytest.raises(ValidationError):
            Signal(**data)


def test_event_is_immutable_and_normalizes_utc() -> None:
    event = DomainEvent(
        event_type="signal.created",
        environment="PAPER",
        account_id="paper",
        source="test",
        external_event_id="abc",
        correlation_id=uuid4(),
        causation_id=uuid4(),
        event_time_utc="2026-10-19T12:00:00+09:00",
        ingest_time_utc="2026-10-19T03:00:01Z",
        payload_json='{"price":"1"}',
    )
    assert event.event_time_utc.hour == 3
    assert event.idempotency_key == ("PAPER", "paper", "test", "abc")
    with pytest.raises(ValidationError):
        event.event_id = uuid4()  # type: ignore[misc]


def test_order_and_algo_identifiers_are_separate() -> None:
    order = EntryOrder(
        client_order_id="entry-1",
        signal_id=uuid4(),
        environment="PAPER",
        account_id="paper",
        symbol="BTCUSDT",
        side="BUY",
        quantity="0.1",
        limit_price="100",
    )
    assert order.state == "CREATED"
    with pytest.raises(ValidationError):
        EntryOrder(**(order.model_dump() | {"order_type": "MARKET"}))
    with pytest.raises(ValidationError):
        EntryOrder(**(order.model_dump() | {"algo_id": "1"}))
    algo = AlgoOrder(
        client_algo_id="stop-1",
        position_id=uuid4(),
        environment="PAPER",
        account_id="paper",
        symbol="BTCUSDT",
        side="SELL",
        trigger_price="99",
        order_type="STOP_MARKET",
    )
    assert algo.state == "UNKNOWN"
    with pytest.raises(ValidationError):
        AlgoOrder(**(algo.model_dump() | {"order_id": "1"}))


def test_position_does_not_default_to_protected() -> None:
    position = Position(
        position_id=uuid4(),
        environment="PAPER",
        account_id="paper",
        symbol="BTCUSDT",
        side="LONG",
        quantity="0.1",
        average_entry_price="100",
    )
    assert position.state == "OPEN_UNPROTECTED"


def test_flat_position_has_zero_quantity_and_no_claim_of_protection() -> None:
    flat = Position(
        position_id=uuid4(),
        environment="PAPER",
        account_id="paper",
        symbol="BTCUSDT",
        side="LONG",
        quantity="0",
        average_entry_price=None,
        state="FLAT",
    )
    assert flat.quantity == Decimal("0")
    with pytest.raises(ValidationError):
        Position(**(flat.model_dump() | {"state": "PROTECTED"}))


@pytest.mark.parametrize("payload", ["[]", '{"price":NaN}', "not-json"])
def test_invalid_event_payload_rejected(payload: str) -> None:
    with pytest.raises(ValidationError):
        DomainEvent(
            event_type="test",
            environment="PAPER",
            account_id="paper",
            source="test",
            external_event_id="1",
            correlation_id=uuid4(),
            causation_id=uuid4(),
            event_time_utc=datetime.now(UTC),
            ingest_time_utc=datetime.now(UTC),
            payload_json=payload,
        )
