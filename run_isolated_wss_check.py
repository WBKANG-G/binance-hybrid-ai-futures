"""Use the existing key-free Docker image for synthetic WSS admission/replay checks."""

import hashlib
import json
from pathlib import Path

SELECTED = [
    "tests/integration/test_demo.py::test_duplicate_and_old_event_reconcile_cumulative_fill_once",
    "tests/unit/test_demo_event_inbox.py",
    "tests/unit/test_demo_event_clock.py",
    "tests/integration/test_demo_event_inbox_validation.py",
    "tests/fault_injection/test_demo_inbox_worker_failure.py",
]
EXPECTED = 45
FILE_HASHES = {
    "tests/integration/test_demo.py": "26ca4551fe665de2afcb98758cdef56adada25614040aee107797e8f9ce9274b",
    "tests/unit/test_demo_event_inbox.py": "1202def1353fe6993978f6d0f64d2fcbec477c53238d3dc06671af9531eda1f7",
    "tests/unit/test_demo_event_clock.py": "b0c07566bf17834a23261119d1e3bd44e22e3313f42cfcddd318c6f8f45cecbc",
    "tests/integration/test_demo_event_inbox_validation.py": "4aaf5d880b14c12ca4df5559f06013ce6d42a42c5e2016cd04ac4c3f89ba75af",
    "tests/fault_injection/test_demo_inbox_worker_failure.py": "b15e0464bf6dc5514c09dff44f4d81bb557afdb591a81899766605551c682f48",
    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",
    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",
    "services/execution/demo_stream.py": "32eb57dab93b5adf9df2544300faa12b8e1d6ed36eb2b97fc23e188afc5deb93",
    "scripts/run_isolated_phase4_fault_check.py": "e1fc92d88bfbd8fd059bc4700b14b1ca678ed324694e28d33f72359960e8b1a2",
}


def main() -> None:
    try:
        import pytest
        from scripts.run_isolated_phase4_fault_check import DATABASE, Counts, check_scope
        from sqlalchemy import create_engine, text

        check_scope()
        if any(
            hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest
            for name, digest in FILE_HASHES.items()
        ):
            raise ValueError("TEST_IMAGE_SOURCE_MISMATCH")
        engine = create_engine(DATABASE, connect_args={"connect_timeout": 2})
        try:
            with engine.connect() as conn:
                if (
                    conn.execute(text("SELECT current_database()")).scalar_one()
                    != "hybrid_fault_review"
                ):
                    raise ValueError("DEDICATED_TEST_DATABASE_REQUIRED")
        finally:
            engine.dispose()
    except Exception:  # noqa: BLE001 -- safe fixed failure, never print environment/key/URL details
        print(
            json.dumps(
                {
                    "wss_check": "BLOCKED_SCOPE_IMAGE_OR_TEST_DATABASE",
                    "external_details_logged": False,
                    "real_exchange_calls": 0,
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )
        raise SystemExit(2) from None
    counts = Counts()
    result = pytest.main(["-q", "-s", "-p", "no:cacheprovider", *SELECTED], plugins=[counts])
    passed = (
        result == 0
        and counts.collected == counts.passed == EXPECTED
        and counts.failed == counts.skipped == 0
    )
    print(
        json.dumps(
            {
                "wss_check": "ISOLATED_SYNTHETIC_WSS_CHECK_PASSED" if passed else "NOT_ACCEPTED",
                "collected": counts.collected,
                "passed": counts.passed,
                "failed": counts.failed,
                "skipped": counts.skipped,
                "scope": "SYNTHETIC_EVENTS_AND_LOOPBACK_WS_WITH_DEDICATED_PG_ONLY",
                "checked_source_files": len(FILE_HASHES),
                "captured_real_wss_frames_replayed": False,
                "real_exchange_calls": 0,
                "real_demo_account_verified": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        ),
        flush=True,
    )
    raise SystemExit(0 if passed else 2)


if __name__ == "__main__":
    main()
