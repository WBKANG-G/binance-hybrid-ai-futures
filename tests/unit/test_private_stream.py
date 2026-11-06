from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.private import PrivateStreamLease
from services.market_data.normalization import utc_ms
from tests.fixtures.binance import BASE


def event(stamp=BASE):
    return {
        "e": "ORDER_TRADE_UPDATE",
        "E": stamp,
        "o": {
            "s": "BTCUSDT",
            "i": 42,
            "c": "synthetic-entry",
            "X": "PARTIALLY_FILLED",
            "z": "0.01",
        },
    }


def test_private_key_is_masked_and_session_requires_explicit_confirmed_renewal():
    now = utc_ms(BASE)
    session = PrivateStreamLease(SecretStr("synthetic-listen-key"), confirmed_at=now)
    assert "synthetic-listen-key" not in repr(session)
    assert session.url(now).get_secret_value().startswith("wss://fstream.binance.com/private/ws/")
    session.confirm_renewal(now + timedelta(minutes=50))
    assert session.url(now + timedelta(minutes=70))
    with pytest.raises(ValueError):
        session.url(now + timedelta(minutes=111))


def test_expiry_and_reverse_events_require_new_session_not_automatic_renewal():
    now = utc_ms(BASE)
    session = PrivateStreamLease(SecretStr("synthetic-listen-key"), confirmed_at=now)
    result = session.accept(event(), now=now)
    assert result is not None
    assert result.reconciliation_required and result.filled_quantity == Decimal("0.01")
    assert session.accept(event(), now=now) is None
    with pytest.raises(ValueError):
        session.accept(event(BASE - 1), now=now)
    with pytest.raises(ValueError):
        session.url(now)
    with pytest.raises(ValueError):
        session.confirm_renewal(now)


def test_listen_key_expired_event_invalidates_session_immediately():
    now = utc_ms(BASE)
    session = PrivateStreamLease(SecretStr("synthetic-listen-key"), confirmed_at=now)
    assert session.accept({"e": "listenKeyExpired", "E": BASE}, now=now) is None
    with pytest.raises(ValueError):
        session.accept(event(), now=now)
