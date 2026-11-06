"""Functional checks against running development services. No exchange traffic."""

import json
from urllib.request import urlopen


def read(url: str) -> bytes:
    with urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("Development HTTP check failed")
        return bytes(response.read())


def main() -> None:
    demo = json.loads(read("http://127.0.0.1:8000/api/v1/demo/status"))
    if demo["demo_trading_enabled"] or demo["api_order_routes_enabled"] or not demo["is_stale"]:
        raise RuntimeError("DEMO must remain explicitly disabled in the web/API")
    paper = json.loads(read("http://127.0.0.1:8000/api/v1/paper/status"))
    if paper["worker_enabled"] or paper["live_trading_enabled"] or not paper["is_stale"]:
        raise RuntimeError("PAPER must require explicit offline startup")
    health = json.loads(read("http://127.0.0.1:8000/api/v1/system/health"))
    ready = json.loads(read("http://127.0.0.1:8000/api/v1/system/ready"))
    proxied = json.loads(read("http://127.0.0.1:5173/api/v1/system/ready"))
    for result in [health, ready, proxied]:
        if (
            result.get("phase") != 4
            or result.get("environment") != "PAPER"
            or result.get("live_trading_enabled") is not False
            or result.get("orders_enabled") is not False
        ):
            raise RuntimeError("Unexpected or unsafe development status")
    if ready.get("database") != "ready" or proxied.get("database") != "ready":
        raise RuntimeError("PostgreSQL is not ready")
    market = json.loads(read("http://127.0.0.1:8000/api/v1/market-data/status"))
    if (
        market.get("orders_enabled") is not False
        or not isinstance(market.get("is_stale"), bool)
        or "as_of" not in market
    ):
        raise RuntimeError("Market data status contract is invalid")
    html = read("http://127.0.0.1:5173/").decode()
    if '<div id="root"></div>' not in html or 'lang="ko"' not in html:
        raise RuntimeError("Web application HTML is missing")
    print(
        "PASS: API health, PostgreSQL readiness, Korean web HTML, web-to-API proxy, market-data status; orders disabled"
    )


if __name__ == "__main__":
    main()
