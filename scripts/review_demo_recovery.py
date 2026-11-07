"""Read-only recovery review by default. Explicit apply never sends an exchange write."""

import argparse
import getpass
import json
import os
import warnings
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from integrations.binance_usdm.demo import (
    DemoClient,
    DemoFailure,
    DemoRateLimited,
    DemoSettings,
)
from integrations.binance_usdm.recovery_transport import safe_transport_diagnostics
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore
from scripts.demo_key_session import credentials as session_credentials
from services.execution.demo_recovery import (
    RecoveryBlocked,
    acknowledge_flat_emergency,
    local_period_status,
    review,
)
from services.risk.paper import RiskRejected


def blocked(
    stage: str,
    code: str,
    *,
    periods: dict[str, str] | None = None,
    diagnostics: dict[str, str | int] | None = None,
) -> None:
    print(
        json.dumps(
            {
                "recovery": "BLOCKED_KEEP_LOCK",
                "stage": stage,
                "error_code": code,
                "periods": periods or {},
                "transport_diagnostics": safe_transport_diagnostics(diagnostics),
                "next_action": "SHARE_CODE_DO_NOT_RETRY_OR_UNLOCK",
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        ),
        flush=True,
    )
    raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-periods", action="store_true")
    parser.add_argument("--apply-review")
    parser.add_argument("--operation-id", type=UUID)
    parser.add_argument("--acknowledge-unresolved-history", action="store_true")
    args = parser.parse_args()
    if (
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
        or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
    ):
        parser.error("LIVE context forbidden")
    if (
        bool(args.apply_review) != bool(args.operation_id)
        or bool(args.apply_review) != args.acknowledge_unresolved_history
    ):
        parser.error(
            "Apply requires review token, operation ID and explicit history acknowledgement together"
        )
    if args.local_periods:
        if args.apply_review or args.operation_id or args.acknowledge_unresolved_history:
            parser.error("Local period check cannot be combined with recovery apply")
        local_engine = None
        try:
            local_engine = create_engine(
                database_url().get_secret_value(), connect_args={"connect_timeout": 2}
            )
            local_store = DemoStore(local_engine)
            before = local_store.read("DEMO-WINDOWS-ISOLATED-BTC-V1")
            report = local_period_status(local_store, before)
            if local_store.read("DEMO-WINDOWS-ISOLATED-BTC-V1") != before:
                blocked("LOCAL_PERIOD_CHECK", "DATABASE_CHANGED_DURING_REVIEW")
            print(json.dumps(report))
        except SQLAlchemyError:
            blocked("LOCAL_PERIOD_CHECK", "DATABASE_ACCESS_FAILED")
        except ValueError:
            blocked("LOCAL_PERIOD_CHECK", "INPUT_OR_CONFIGURATION_INVALID")
        finally:
            if local_engine is not None:
                local_engine.dispose()
        return
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
        account = "DEMO-WINDOWS-ISOLATED-BTC-V1"
        if args.apply_review:
            stage = "RECOVERY_APPLY"
            lease = store.acquire(account, uuid4())
            result = acknowledge_flat_emergency(
                store,
                lease,
                client,
                args.operation_id,
                args.apply_review,
                acknowledge_unresolved=args.acknowledge_unresolved_history,
            )
            print(
                json.dumps(
                    {"recovery": result, "orders_enabled": False, "live_trading_enabled": False}
                )
            )
        else:
            before = store.read(account)
            stage = "RECOVERY_REVIEW"
            report = review(store, client, before)
            if store.read(account) != before:
                raise RecoveryBlocked(
                    "DATABASE_CHANGED_DURING_REVIEW", "Database changed during review"
                )
            print(
                json.dumps(
                    {
                        "recovery": "REVIEW_ONLY_NO_DATABASE_CHANGE",
                        **report,
                        "risk_state": before.risk_state.value,
                        "paused": before.paused,
                    }
                )
            )
    except RecoveryBlocked as error:
        blocked(stage, error.code, periods=error.periods, diagnostics=error.transport_diagnostics)
    except getpass.GetPassWarning:
        blocked(stage, "HIDDEN_INPUT_UNAVAILABLE")
    except DemoRateLimited as error:
        blocked(stage, "DEMO_RATE_LIMITED", diagnostics=vars(error).get("transport_diagnostics"))
    except DemoFailure as error:
        blocked(
            stage,
            "CLOCK_CHECK_FAILED" if stage == "CLOCK_CHECK" else "DEMO_QUERY_OR_SNAPSHOT_FAILED",
            diagnostics=vars(error).get("transport_diagnostics"),
        )
    except SQLAlchemyError:
        blocked(stage, "DATABASE_ACCESS_FAILED")
    except RiskRejected:
        blocked(stage, "LEASE_OR_RISK_GATE_REJECTED")
    except (ValueError, EOFError):
        blocked(stage, "INPUT_OR_CONFIGURATION_INVALID")
    finally:
        os.environ.pop("BINANCE_DEMO_API_KEY", None)
        os.environ.pop("BINANCE_DEMO_API_SECRET", None)
        if lease is not None and store is not None:
            store.release(lease)
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
