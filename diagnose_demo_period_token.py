"""Read-only diagnosis for the installed period preview/apply code. No apply path."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml
from integrations.binance_usdm.demo import DemoClient, DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore, accounts
from scripts.demo_key_session import credentials
from services.execution.demo_models import DemoLedger
from services.execution.demo_paused_period import review_paused_periods
from sqlalchemy import Engine, event, select

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
INITIAL_COMMAND_TOKEN = "bcf38c1998320344e9c03411b83a9369b152bfcaa76f50fb23438fc89cdf7a1c"
CORRECTED_COMMAND_TOKEN = "bfcf38c1998320344e9c03411b83a9369b152bfca76f50fb23438fc89cdf7a1c"
EXCLUDED = {"reconciled", "stream_ready"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def normalized(ledger: DemoLedger) -> dict[str, Any]:
    return ledger.model_dump(mode="json", exclude=EXCLUDED)


def token_from(ledger: DemoLedger, report: dict[str, object], account: str) -> str:
    return digest(
        {
            "account": account,
            "source": report["source"],
            "ledger": normalized(ledger),
            "day": report["proposed_day_kst"],
            "week": report["proposed_week_kst"],
            "evidence": report["signed_evidence_hash"],
        }
    )


def read_account(engine: Engine, account: str) -> dict[str, Any]:
    with engine.connect() as conn:
        return dict(
            conn.execute(select(accounts).where(accounts.c.account_id == account)).mappings().one()
        )


def diagnose(store: DemoStore, client: DemoClient, account: str = ACCOUNT) -> dict[str, object]:
    def selects_only(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        if statement != "SET TRANSACTION READ ONLY" and not statement.lstrip().upper().startswith(
            "SELECT"
        ):
            raise ValueError("READ_ONLY_DIAGNOSTIC_SQL_GUARD")

    def begin_readonly(conn: Any) -> None:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")

    event.listen(store.engine, "before_cursor_execute", selects_only)
    event.listen(store.engine, "begin", begin_readonly)
    try:
        raw_before = read_account(store.engine, account)
        ledger = DemoLedger.model_validate(raw_before["state"])
        model_before = normalized(ledger)
        report = review_paused_periods(
            store, client, ledger, account_id=account, attest_no_activity=True
        )
        observed = str(report["review_token"])
        simulated = ledger.model_copy(deep=True)
        simulated.reconciled = simulated.stream_ready = False
        reloaded = DemoLedger.model_validate(
            json.loads(json.dumps(simulated.model_dump(mode="json")))
        )
        raw_after = read_account(store.engine, account)
        if digest(raw_before) != digest(raw_after) or model_before != normalized(ledger):
            raise ValueError("ACCOUNT_CHANGED_DURING_DIAGNOSTIC")
        reproduced = token_from(ledger, report, account)
        simulated_token = token_from(simulated, report, account)
        roundtrip_token = token_from(reloaded, report, account)
        return {
            "diagnostic": "READ_ONLY_PERIOD_TOKEN_COMPARISON",
            "observed_review_token": observed,
            "observed_token_groups_of_8": [observed[i : i + 8] for i in range(0, len(observed), 8)],
            "initial_command_token_matches": observed == INITIAL_COMMAND_TOKEN,
            "corrected_command_token_matches": observed == CORRECTED_COMMAND_TOKEN,
            "token_formula_reproduces_preview": observed == reproduced,
            "simulated_acquire_flags_token_matches": observed == simulated_token,
            "simulated_json_roundtrip_token_matches": observed == roundtrip_token,
            "normalized_ledger_hash": digest(model_before),
            "normalized_simulated_ledger_hash": digest(normalized(simulated)),
            "normalized_roundtrip_ledger_hash": digest(normalized(reloaded)),
            "roundtrip_changed_top_level_fields": sorted(
                k for k in model_before if model_before[k] != normalized(reloaded)[k]
            ),
            "signed_evidence_hash": report["signed_evidence_hash"],
            "exchange_fingerprint": report["exchange_fingerprint"],
            "proposed_day_kst": report["proposed_day_kst"],
            "proposed_week_kst": report["proposed_week_kst"],
            "proposed_daily_base_usdt": report["proposed_daily_base_usdt"],
            "proposed_weekly_base_usdt": report["proposed_weekly_base_usdt"],
            "old_day_kst": report["old_day_kst"],
            "old_week_kst": report["old_week_kst"],
            "risk_state": ledger.risk_state.value,
            "paused": ledger.paused,
            "read_diagnostic": report["read_diagnostic"],
            "account_owner_fence_lease_state_unchanged": True,
            "database_writes_allowed": False,
            "lease_acquired": False,
            "apply_attempted": False,
            "orders_enabled": False,
            "live_trading_enabled": False,
            "next_action": "SHARE_DIAGNOSTIC_DO_NOT_APPLY",
        }
    finally:
        event.remove(store.engine, "begin", begin_readonly)
        event.remove(store.engine, "before_cursor_execute", selects_only)


def main() -> None:
    from sqlalchemy import create_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attest-no-activity-since-anchor", action="store_true")
    args = parser.parse_args()
    engine = None
    stage = "CONTEXT"
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
            or not args.attest_no_activity_since_anchor
        ):
            raise ValueError("READ_ONLY_CONTEXT_AND_ATTESTATION_REQUIRED")
        stage = "CACHED_DEMO_KEY"
        keys = credentials(prompt=False)
        stage = "CONFIGURATION"
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(),
                Loader=UniqueKeyLoader,
            )
        ).model_copy(update={"demo_trading_enabled": False})
        client = DemoClient(cfg, keys)
        stage = "READ_ONLY_DIAGNOSTIC"
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        report = diagnose(DemoStore(engine), client)
    except Exception:  # noqa: BLE001 -- never expose credentials/DB/transport exception text
        report = {
            "diagnostic": "BLOCKED_NO_APPLY",
            "stage": stage,
            "failure": "READ_ONLY_DIAGNOSTIC_CONTEXT_OR_QUERY_FAILED",
            "apply_attempted": False,
            "orders_enabled": False,
            "live_trading_enabled": False,
            "external_details_logged": False,
        }
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
