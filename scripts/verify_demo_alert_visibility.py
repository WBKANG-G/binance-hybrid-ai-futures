"""Deployment-only verification: source hashes and local GET, no sends or orders."""

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

from services.operations.demo_alert_status import AlertStatus

EXPECTED: dict[str, str] = {
    "apps/api/main.py": "aa71326357a0b724ea2bf7c24d4bec36376d1d0cfb061e98f93d73990bd3cd14",
    "scripts/demo_alert_email_session.py": "53e2de539f173a2ebdf2acd1143a2e14224d38f2f1050297a25223f1fbff4911",
    "services/operations/demo_alert_status.py": "a34a1b00daa0d6ad8bbd36a25cd09843fea68a641a7987bded3e5400b52da747",
}


def main() -> None:
    if not EXPECTED or not all(
        hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected
        for name, expected in EXPECTED.items()
    ):
        raise ValueError("ALERT_VISIBILITY_SOURCE_MISMATCH")
    with urlopen("http://127.0.0.1:8000/api/v1/demo/alerts/status", timeout=8) as response:
        status = AlertStatus.model_validate_json(response.read(65536))
    if status.status != "READ_ONLY_ALERT_STATUS" or status.is_stale or status.queue is None:
        raise ValueError("ALERT_VISIBILITY_UNAVAILABLE")
    print(
        json.dumps(
            {
                "alert_visibility": "SOURCE_AND_READ_ONLY_ENDPOINT_VERIFIED",
                "email_state": status.email.state,
                "critical_pending": status.queue.critical_pending,
                "test_pending": status.queue.test_pending,
                "worker_liveness_checked": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        )
    )


if __name__ == "__main__":
    main()
