"""Preview an operator-attested day/week baselines; explicit source acceptance is required to apply."""

import argparse
import getpass
import json
import os
import re
import warnings
from decimal import DecimalException
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from integrations.binance_usdm.demo import DemoClient, DemoFailure, DemoSettings
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials as session_credentials
from scripts.review_demo_recovery import blocked
from services.execution.demo_day_review import DayReviewBlocked
from services.execution.demo_history_review import HistoryBlocked
from services.execution.demo_period_review import (
    PeriodReviewBlocked,
    apply_periods,
    review_periods,
)
from services.risk.paper import RiskRejected

ACCOUNT_ID = "DEMO-WINDOWS-ISOLATED-BTC-V1"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attest-no-activity-since-anchor", action="store_true")
    parser.add_argument("--accept-operator-evidence", action="store_true")
    parser.add_argument("--apply-review")
    parser.add_argument("--operation-id", type=UUID)
    args = parser.parse_args()
    if (
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
        or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
    ):
        blocked("PERIOD_CONTEXT", "LIVE_CONTEXT_FORBIDDEN")
    if not args.attest_no_activity_since_anchor:
        blocked("PERIOD_ATTESTATION", "EXPLICIT_OPERATOR_ATTESTATION_REQUIRED")
    if (
        bool(args.apply_review) != args.accept_operator_evidence
        or bool(args.apply_review) != bool(args.operation_id)
        or (args.apply_review and not re.fullmatch(r"[0-9a-f]{64}", args.apply_review))
    ):
        blocked("PERIOD_ARGUMENTS", "REVIEW_TOKEN_AND_OPERATION_ID_REQUIRED")
    engine = None
    lease = None
    store = None
    stage = "CREDENTIAL_INPUT"
    try:
        warnings.simplefilter("error", getpass.GetPassWarning)
        credentials = session_credentials(prompt=True)
        stage = "CONFIGURATION"
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        client = DemoClient(cfg, credentials)
        stage = "CLOCK_CHECK"
        client.sync_clock()
        stage = "DATABASE_READ"
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        account = ACCOUNT_ID
        if args.apply_review:
            stage = "PERIOD_APPLY"
            lease = store.acquire(account, uuid4())
            result = apply_periods(
                store,
                lease,
                client,
                args.operation_id,
                args.apply_review,
                attest_no_activity=args.attest_no_activity_since_anchor,
                accept_operator_evidence=args.accept_operator_evidence,
            )
            after = store.read(account)
            print(
                json.dumps(
                    {
                        "period_baselines": result,
                        "source": "OPERATOR_ATTESTED_FLAT_PERIODS_WITH_SIGNED_USDT_EVIDENCE",
                        "account_wide_exchange_export_verified": False,
                        "operation_id": str(args.operation_id),
                        "risk_state": after.risk_state.value,
                        "paused": after.paused,
                        "unresolved_requests_acknowledged": False,
                        "period_baselines_changed": result != "ALREADY_RECORDED_NO_ACTION",
                        "weekly_baseline_changed": result != "ALREADY_RECORDED_NO_ACTION",
                        "high_water_preserved": True,
                        "recovery_eligible": False,
                        "orders_enabled": False,
                        "live_trading_enabled": False,
                    }
                )
            )
        else:
            before = store.read(account)
            stage = "PERIOD_PREVIEW"
            report = review_periods(
                store,
                client,
                before,
                account_id=account,
                attest_no_activity=args.attest_no_activity_since_anchor,
            )
            if store.read(account) != before:
                raise PeriodReviewBlocked("DATABASE_CHANGED_DURING_REVIEW")
            print(json.dumps(report))
    except (
        PeriodReviewBlocked,
        DayReviewBlocked,
        HistoryBlocked,
    ) as error:
        blocked(stage, str(error))
    except getpass.GetPassWarning:
        blocked(stage, "HIDDEN_INPUT_UNAVAILABLE")
    except DemoFailure:
        blocked(stage, "DEMO_QUERY_OR_SNAPSHOT_FAILED")
    except SQLAlchemyError:
        blocked(stage, "DATABASE_ACCESS_FAILED")
    except RiskRejected:
        blocked(stage, "LEASE_OR_RISK_GATE_REJECTED")
    except (ValueError, EOFError, DecimalException, KeyError, TypeError):
        blocked(stage, "INPUT_OR_CONFIGURATION_INVALID")
    finally:
        os.environ.pop("BINANCE_DEMO_API_KEY", None)
        os.environ.pop("BINANCE_DEMO_API_SECRET", None)
        try:
            if lease is not None and store is not None:
                store.release(lease)
        except (RiskRejected, SQLAlchemyError):
            blocked("LEASE_RELEASE", "LEASE_RELEASE_NOT_CONFIRMED_KEEP_LOCK")
        finally:
            if engine is not None:
                engine.dispose()


if __name__ == "__main__":
    main()
