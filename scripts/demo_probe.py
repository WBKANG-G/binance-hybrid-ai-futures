"""Read-only public DEMO REST/WSS diagnostic. No keys, signatures or test orders."""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr
from websockets.asyncio.client import connect

from integrations.binance_usdm.demo import WS_HOST, DemoFailure, transport


async def probe() -> dict[str, object]:
    report: dict[str, object] = dict(
        as_of=datetime.now(UTC).isoformat(),
        environment="DEMO",
        credentials_used=False,
        orders_sent=0,
        rest="UNTESTED",
        websocket="UNTESTED",
    )
    try:
        result = transport("GET", "/fapi/v1/time", SecretStr(""), None)
        data = json.loads(result.body)
        report["rest"] = (
            "PASS"
            if result.status == 200 and type(data.get("serverTime")) is int
            else "HTTP_" + str(result.status)
        )
    except (DemoFailure, ValueError, OSError):
        report["rest"] = "NETWORK_OR_PROXY_BLOCKED"
    logger = logging.Logger("demo-probe-redacted")
    logger.disabled = True
    try:
        async with connect(
            WS_HOST + "/market/ws/btcusdt@markPrice@1s",
            open_timeout=5,
            close_timeout=2,
            logger=logger,
        ) as socket:
            raw = await asyncio.wait_for(socket.recv(), timeout=5)
            data = json.loads(raw)
            report["websocket"] = (
                "PASS" if data.get("e") == "markPriceUpdate" else "UNEXPECTED_EVENT"
            )
    except Exception as error:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        report["websocket"] = (
            "PROXY_OR_HTTP_" + str(status)
            if isinstance(status, int)
            else "NETWORK_OR_PROXY_BLOCKED"
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe())
    # Exclusive report creation preserves previous evidence.
    with args.output.open("x") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
