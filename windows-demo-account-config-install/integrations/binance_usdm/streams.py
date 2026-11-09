"""Routed stream transport with fail-closed reconnect and injected test connections."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Literal, Protocol, cast

from pydantic import SecretStr
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import WebSocketException

from integrations.binance_usdm.rest import RateLimited
from packages.domain.config import ImmutableModel, NonNegativeDecimal
from packages.domain.contracts import UTCDateTime


def stream_url(streams: list[str], *, listen_key: SecretStr | None = None) -> str | SecretStr:
    if listen_key is not None:
        key = listen_key.get_secret_value()
        if streams or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", key):
            raise ValueError("Invalid private stream")
        return SecretStr("wss://fstream.binance.com/private/ws/" + key)
    if not streams or len(streams) > 20:
        raise ValueError("Invalid subscription count")
    routes = set()
    for stream in streams:
        if re.fullmatch(r"[a-z0-9]+@(depth(@100ms|@250ms|@500ms)?|bookTicker)", stream):
            routes.add("public")
        elif re.fullmatch(
            r"[a-z0-9]+@(kline_(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)|aggTrade|markPrice(@1s)?)",
            stream,
        ):
            routes.add("market")
        else:
            raise ValueError("Unsupported or malformed stream")
    if len(routes) != 1:
        raise ValueError("Separate connections are required for distinct routes")
    return "wss://fstream.binance.com/" + routes.pop() + "/stream?streams=" + "/".join(streams)


class PrivateOrderEvent(ImmutableModel):
    event_type: Literal["ORDER_TRADE_UPDATE", "ALGO_UPDATE"]
    as_of: UTCDateTime
    symbol: str
    status: str
    order_id: str | None = None
    client_order_id: str | None = None
    algo_id: str | None = None
    client_algo_id: str | None = None
    filled_quantity: NonNegativeDecimal | None = None
    reconciliation_required: Literal[True] = True


def normalize_private(raw: dict[str, Any]) -> PrivateOrderEvent:
    from services.market_data.normalization import utc_ms

    event = raw.get("e")
    order = raw.get("o", {})
    if event == "ORDER_TRADE_UPDATE":
        return PrivateOrderEvent(
            event_type=event,
            as_of=utc_ms(raw["E"]),
            symbol=order["s"],
            status=order["X"],
            order_id=str(order["i"]),
            client_order_id=order["c"],
            filled_quantity=order["z"],
        )
    if event == "ALGO_UPDATE":
        return PrivateOrderEvent(
            event_type=event,
            as_of=utc_ms(raw["E"]),
            symbol=order["s"],
            status=order["X"],
            algo_id=str(order["aid"]),
            client_algo_id=order["caid"],
        )
    raise ValueError("Unsupported private order event")


class StreamConnection(Protocol):
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


Connector = Callable[[str], AbstractAsyncContextManager[StreamConnection]]


def public_connect(url: str) -> AbstractAsyncContextManager[StreamConnection]:
    if not url.startswith(
        (
            "wss://fstream.binance.com/public/stream?streams=",
            "wss://fstream.binance.com/market/stream?streams=",
        )
    ):
        raise ValueError("Only routed public data connections are available")
    # Logs must never contain account listen keys. This connector accepts no private URL.
    return cast(
        AbstractAsyncContextManager[StreamConnection],
        websocket_connect(
            url,
            open_timeout=10,
            ping_interval=120,
            ping_timeout=30,
            max_size=1_048_576,
            max_queue=128,
            logger=logging.getLogger("binance.public_stream"),
        ),
    )


async def run_reconnecting(
    url: str,
    *,
    reconcile: Callable[[], Awaitable[None]],
    consume: Callable[[str], Awaitable[None]],
    disconnect: Callable[[], Awaitable[None]],
    connect: Connector = public_connect,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_sessions: int | None = None,
) -> None:
    if not url.startswith(
        (
            "wss://fstream.binance.com/public/stream?streams=",
            "wss://fstream.binance.com/market/stream?streams=",
        )
    ):
        raise ValueError("Phase 1 only opens public market data streams")
    attempt = 0
    if max_sessions is not None and max_sessions < 1:
        raise ValueError("Session budget must be positive")
    while max_sessions is None or attempt < max_sessions:
        cooldown = min(30.0, 0.5 * 2 ** min(attempt, 6))
        try:
            async with connect(url) as socket:
                # Connection buffers messages before the REST snapshot is taken.
                await reconcile()
                async for frame in socket:
                    await consume(frame.decode() if isinstance(frame, bytes) else frame)
        except asyncio.CancelledError:
            raise
        except RateLimited:
            cooldown = 60.0
        except (OSError, ValueError, RuntimeError, TimeoutError, WebSocketException):
            pass  # Caller receives failure through disconnect state; never log raw frames/URLs.
        finally:
            await disconnect()
        attempt += 1
        if max_sessions is None or attempt < max_sessions:
            await sleep(cooldown)
