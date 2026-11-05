"""Observe an existing worker heartbeat and local read-only API; never dispatch."""

import json
import math
import os
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

HEARTBEAT = Path("/tmp/demo-alert-watch-health")
STATES = {"CONFIGURED_PRIVATE_RAM", "NOT_CONFIGURED", "BLOCKED_EXPIRED_OR_INVALID"}


def heartbeat(path: Path, now: float) -> tuple[float, int]:
    with path.open("rb") as stream:
        raw = stream.read(65)
    if not 1 <= len(raw) <= 64:
        raise ValueError("INVALID_HEARTBEAT")
    stamp = float(raw)
    age = now - stamp
    if not math.isfinite(stamp) or not math.isfinite(age) or not 0 <= age < 30:
        raise ValueError("HEARTBEAT_NOT_RECENT")
    return stamp, int(age * 1000)


def public_view(body: object) -> dict[str, object]:
    if not isinstance(body, dict):
        raise TypeError("INVALID_API_STATUS")
    required = {
        "status": "READ_ONLY_ALERT_STATUS",
        "is_stale": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "worker_liveness_checked": False,
        "inbox_confirmation_available": False,
        "confirmation_scope": "RECEIVER_ACK_ONLY",
    }
    if any(type(body.get(k)) is not type(v) or body.get(k) != v for k, v in required.items()):
        raise ValueError("INVALID_API_STATUS")
    email, queue = body.get("email"), body.get("queue")
    if (
        not isinstance(email, dict)
        or email.get("state") not in STATES
        or not isinstance(queue, dict)
    ):
        raise ValueError("INVALID_API_STATUS")
    values = [queue.get(k) for k in ("pending", "critical_pending", "test_pending")]
    if not all(type(v) is int and v >= 0 for v in values):
        raise ValueError("INVALID_API_COUNTS")
    if values[0] != values[1] + values[2]:
        raise ValueError("INCONSISTENT_API_COUNTS")
    return {
        "api_email_state": email["state"],
        "critical_pending": values[1],
        "test_pending": values[2],
    }


def read_api() -> dict[str, object]:
    # Fixed Docker-internal API, ignore proxy environment, forbid redirects.
    from urllib.request import HTTPRedirectHandler

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(
        "http://api:8000/api/v1/demo/alerts/status",
        headers={"Cache-Control": "no-store"},
        method="GET",
    )
    with opener.open(request, timeout=8) as response:
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            raise ValueError("API_UNAVAILABLE")
    return public_view(json.loads(raw))


def observe(
    *,
    path: Path = HEARTBEAT,
    clock=time.monotonic,
    pause=time.sleep,
    email_status=None,
    api_status=read_api,
) -> dict[str, object]:
    if (
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
        or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
    ):
        raise ValueError("DEMO_ONLY")
    if email_status is None:
        from scripts.demo_alert_email_session import status

        email_status = status
    first, first_age = heartbeat(path, clock())
    before = email_status()
    pause(3)
    second, second_age = heartbeat(path, clock())
    after = email_status()
    if before not in STATES or after not in STATES:
        raise ValueError("INVALID_EMAIL_STATE")
    view = api_status()
    stable = before == after == view["api_email_state"]
    advancing = second > first
    result = (
        "HEARTBEAT_ADVANCING_CONFIG_VISIBLE"
        if advancing and stable and after == "CONFIGURED_PRIVATE_RAM"
        else "REVIEW_REQUIRED_KEEP_PAUSED"
    )
    return {
        "worker_observation": result,
        "observation_seconds": 3,
        "heartbeat_advancing": advancing,
        "first_heartbeat_age_ms": first_age,
        "last_heartbeat_age_ms": second_age,
        "worker_email_state": after,
        "worker_api_config_consistent": stable,
        **view,
        "delivery_readiness_verified": False,
        "smtp_authentication_checked": False,
        "inbox_checked": False,
        "new_mail_created": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def main() -> None:
    code = 2
    try:
        report = observe()
        code = 0 if report["worker_observation"] == "HEARTBEAT_ADVANCING_CONFIG_VISIBLE" else 2
    except Exception:  # noqa: BLE001 -- redact private settings and transport failures
        report = {
            "worker_observation": "UNAVAILABLE_KEEP_PAUSED",
            "details_redacted": True,
            "new_mail_created": False,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
