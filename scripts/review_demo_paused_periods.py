"""NORMAL/paused day+week preview; explicit token/source acceptance for adoption."""

import argparse
import getpass
import json
import os
import re
import warnings
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

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
from services.execution.demo_paused_period import (
    PeriodPreviewBudgetBlocked,
    apply_paused_periods,
    recorded_period_apply,
    review_paused_periods,
)
from services.execution.demo_period_review import SOURCE, PeriodReviewBlocked
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
        or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
    ):
        blocked("PERIOD_CONTEXT", "LIVE_CONTEXT_FORBIDDEN")
    if not args.attest_no_activity_since_anchor:
        blocked("PERIOD_ATTESTATION", "EXPLICIT_OPERATOR_ATTESTATION_REQUIRED")
    if (
        bool(args.apply_review) != args.accept_operator_evidence
        or bool(args.apply_review) != bool(args.operation_id)
        or (args.apply_review and not re.fullmatch(r"[0-9a-f]{64}", args.apply_review))
        or (args.operation_id and args.operation_id.version != 4)
    ):
        blocked("PERIOD_ARGUMENTS", "REVIEW_TOKEN_AND_OPERATION_ID_REQUIRED")
    engine = None
    store = None
    lease = None
    report: dict[str, object] = {}
    stage = "DATABASE_READ"
    try:
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        store = DemoStore(engine)
        before = store.read(ACCOUNT_ID)
        already = bool(
            args.apply_review
            and recorded_period_apply(before, args.operation_id, args.apply_review)
        )
        if already:
            result = "ALREADY_RECORDED_NO_ACTION"
        else:
            stage = "CREDENTIAL_INPUT"
            warnings.simplefilter("error", getpass.GetPassWarning)
            credentials = session_credentials(prompt=True)
            stage = "CONFIGURATION"
            cfg = DemoSettings.model_validate(
                yaml.load(
                    Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
                )
            ).model_copy(update={"demo_trading_enabled": False})
            client = DemoClient(cfg, credentials)
            if args.apply_review:
                stage = "PAUSED_PERIOD_APPLY"
                lease = store.acquire(ACCOUNT_ID, uuid4())
                result = apply_paused_periods(
                    store,
                    lease,
                    client,
                    args.operation_id,
                    args.apply_review,
                    attest_no_activity=args.attest_no_activity_since_anchor,
                    accept_operator_evidence=args.accept_operator_evidence,
                )
            else:
                stage = "PAUSED_PERIOD_PREVIEW"
                report = review_paused_periods(
                    store,
                    client,
                    before,
                    account_id=ACCOUNT_ID,
                    attest_no_activity=args.attest_no_activity_since_anchor,
                )
                if store.read(ACCOUNT_ID) != before:
                    raise PeriodReviewBlocked("DATABASE_CHANGED_DURING_REVIEW")
        if args.apply_review:
            after = store.read(ACCOUNT_ID)
            receipt = after.attested_period_baselines[str(args.operation_id)]
            report = {
                "period_baselines": result,
                "source": SOURCE,
                "operation_id": str(args.operation_id),
                "review_token": receipt.review_token,
                "adopted_day_kst": datetime_key(receipt.day_boundary_utc),
                "adopted_week_kst": datetime_key(receipt.week_boundary_utc),
                "adopted_baseline_usdt": str(receipt.baseline_usdt),
                "current_day_kst": after.daily_key,
                "current_week_kst": after.weekly_key,
                "current_daily_base_usdt": str(after.daily_base),
                "current_weekly_base_usdt": str(after.weekly_base),
                "risk_state": after.risk_state.value,
                "paused": after.paused,
                "period_baselines_changed": result != "ALREADY_RECORDED_NO_ACTION",
                "account_wide_exchange_export_verified": False,
                "unresolved_requests_acknowledged": False,
                "high_water_preserved": True,
                "old_valuation_timestamp_preserved": True,
                "recovery_eligible": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
                "already_recorded_read_only": already,
            }
    except (PeriodReviewBlocked, DayReviewBlocked, HistoryBlocked) as error:
        blocked(stage, str(error))
    except PeriodPreviewBudgetBlocked as error:
        blocked(stage, error.code)
    except getpass.GetPassWarning:
        blocked(stage, "HIDDEN_INPUT_UNAVAILABLE")
    except DemoFailure:
        blocked(stage, "DEMO_QUERY_OR_SNAPSHOT_FAILED")
    except RiskRejected:
        blocked(stage, "LEASE_OR_RISK_GATE_REJECTED")
    except SQLAlchemyError:
        blocked(stage, "DATABASE_ACCESS_FAILED")
    except (ValueError, EOFError, TypeError, KeyError, ArithmeticError):
        blocked(stage, "INPUT_OR_CONFIGURATION_INVALID")
    finally:
        os.environ.pop("BINANCE_DEMO_API_KEY", None)
        os.environ.pop("BINANCE_DEMO_API_SECRET", None)
        try:
            if lease is not None and store is not None:
                store.release(lease)
                report["leader_released"] = True
        except (RiskRejected, SQLAlchemyError):
            blocked("LEASE_RELEASE", "LEASE_RELEASE_NOT_CONFIRMED_KEEP_LOCK")
        finally:
            if engine is not None:
                engine.dispose()
    print(json.dumps(report), flush=True)


def datetime_key(boundary: datetime) -> str:
    return boundary.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()


if __name__ == "__main__":
    main()
