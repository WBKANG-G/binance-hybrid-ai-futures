"""Single, capped operational DEMO validation; explicitly not a strategy signal."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy.exc import SQLAlchemyError

from integrations.binance_usdm.demo import DemoClient, DemoFailure, DemoRateLimited
from integrations.binance_usdm.filters import number
from packages.domain.contracts import RiskState, Signal, TradingMode
from services.execution.demo import DemoCoordinator
from services.execution.demo_models import DemoLedger
from services.execution.demo_preview import PreviewBlocked, preview
from services.execution.demo_stream import session as session
from services.risk.paper import GateSnapshot, RiskRejected

MAX_TEST_NOTIONAL = Decimal("100")  # DEMO-only hard ceiling, never LIVE configuration.
FLAT_CONFIRMATION_ATTEMPTS = 4
FLAT_CONFIRMATION_INTERVAL_SECONDS = 1


def database_failure(error: SQLAlchemyError) -> str:
    state = getattr(getattr(error, "orig", None), "sqlstate", None)
    if state == "55P03":
        return "DATABASE_LOCK_TIMEOUT"
    if state == "40P01":
        return "DATABASE_DEADLOCK"
    if state in {"08000", "08001", "08003", "08004", "08006", "08007", "08P01"}:
        return "DATABASE_CONNECTION_FAILED"
    return "OTHER_DATABASE_FAILURE"


def safe_failure(error: Exception) -> str:
    """Allowlisted categories only; never return raw exception text or private URLs."""
    if isinstance(error, DemoRateLimited):
        return "DEMO_RATE_LIMITED"
    if isinstance(error, PreviewBlocked) and str(error) == (
        "Preview quotes expired during final account verification"
    ):
        return "QUOTE_EXPIRED_DURING_ACCOUNT_CHECK"
    if isinstance(error, DemoFailure) and str(error) == "Clock stale or cooldown active":
        return "CLOCK_STALE_OR_COOLDOWN"
    if isinstance(error, RiskRejected):
        return "LEASE_OR_RISK_GATE_REJECTED"
    if isinstance(error, SQLAlchemyError):
        return database_failure(error)
    if isinstance(error, DemoFailure):
        return "DEMO_SESSION_OR_QUERY_FAILED"
    return "OTHER_VALIDATION_FAILURE"


def pause_finalization_locally(coordinator: DemoCoordinator) -> None:
    """Pause must persist without clock/REST availability; never performs an exit."""
    limited = (
        coordinator.session_diagnostic.code == "RATE_LIMITED"
        or coordinator.client.clock_ms() < coordinator.client.cooldown_until
    )

    def pause(ledger: DemoLedger) -> None:
        ledger.paused = True
        ledger.stream_ready = ledger.reconciled = False
        if (
            limited
            and ledger.risk_state
            not in {
                RiskState.EMERGENCY,
                RiskState.DAILY_LOCK,
                RiskState.WEEKLY_LOCK,
                RiskState.DRAWDOWN_LOCK,
            }
            and (
                any(ledger.positions.values())
                or any(o.open_quantity or o.remaining for o in ledger.orders.values())
            )
        ):
            # Exposure cannot be verified/closed while rate limited; never report
            # a NORMAL unprotected position because the first clock query failed.
            ledger.risk_state = RiskState.EMERGENCY

    coordinator.store.change(coordinator.lease, "DEMO_FINALIZATION_LOCAL_PAUSE", pause)


async def confirm_exit(coordinator: DemoCoordinator) -> str:
    """Bounded observation after one flatten intent. No command/POST retries."""
    budgeted = getattr(coordinator, "confirm_exit_budgeted", None)
    if budgeted is not None:
        budgeted_result = await budgeted()
        if budgeted_result not in {"UNKNOWN", "VERIFIED_FLAT"}:
            raise DemoFailure("Invalid budgeted confirmation result")
        return str(budgeted_result)
    for attempt in range(FLAT_CONFIRMATION_ATTEMPTS):
        if attempt:
            await asyncio.sleep(FLAT_CONFIRMATION_INTERVAL_SECONDS)
        await asyncio.to_thread(coordinator.store.renew, coordinator.lease)
        await asyncio.to_thread(coordinator.client.sync_clock)
        result = await asyncio.to_thread(coordinator.confirm_flat)
        if result == "VERIFIED_FLAT":
            return result
    return "UNKNOWN"


def prepare(
    coordinator: DemoCoordinator,
    operation_id: UUID,
    direction: Literal["LONG", "SHORT"],
    *,
    entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "PASSIVE",
) -> tuple[Signal, GateSnapshot, Decimal]:
    if direction not in {"LONG", "SHORT"}:
        raise DemoFailure("Validation direction must be explicit")
    client = coordinator.client
    client.sync_clock()
    ledger = coordinator.store.read(coordinator.lease.account_id)
    if (
        not ledger.stream_ready
        or not ledger.reconciled
        or ledger.last_reconciled is None
        or ledger.risk_state.value != "NORMAL"
    ):
        raise DemoFailure("Validation requires fresh private stream and NORMAL reconciliation")
    observer = DemoClient(
        client.settings.model_copy(update={"demo_trading_enabled": False}),
        client.credentials,
        send=client.send,
        clock_ms=client.clock_ms,
    )
    observer.sync_clock()
    report = preview(observer, ledger, entry_style=entry_style)
    item = next(row for row in report["illustrations"] if row["direction"] == direction)
    if Decimal(item["notional_usdt"]) > MAX_TEST_NOTIONAL:
        raise DemoFailure("Minimum DEMO validation order exceeds 100 USDT test ceiling")
    now_ms = client.clock_ms() + client.offset
    rows = client.public("/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": "2"})
    if not isinstance(rows, list):
        raise DemoFailure("Validation closed-candle evidence unavailable")
    closed = []
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) < 7
            or type(row[0]) is not int
            or type(row[6]) is not int
        ):
            raise DemoFailure("Invalid validation candle response")
        if row[0] % 60000 or row[6] != row[0] + 59999:
            raise DemoFailure("Invalid validation candle interval")
        o, h, low, c = (number(row[i]) for i in range(1, 5))
        if low > min(o, c) or h < max(o, c) or low > h:
            raise DemoFailure("Invalid validation candle prices")
        if 0 < now_ms - row[6] <= 90000:
            closed.append(row[6])
    if not closed or datetime.fromisoformat(report["expires_at"]) <= datetime.fromtimestamp(
        now_ms / 1000, UTC
    ):
        raise DemoFailure("Validation candle/quote evidence expired")
    now = datetime.fromtimestamp(now_ms / 1000, UTC)
    signal = Signal(
        signal_id=operation_id,
        environment=TradingMode.DEMO,
        strategy=None,
        purpose="DEMO_VALIDATION",
        symbol="BTCUSDT",
        direction=direction,
        signal_closed_candle_at=datetime.fromtimestamp(max(closed) / 1000, UTC),
        expires_at=now + timedelta(seconds=10),
        suggested_limit_price=item["limit_price"],
        initial_stop_price=item["assumed_stop_price"],
        target_price=item["assumed_target_price"],
        risk_profile_version=ledger.policy.risk.version,
    )
    # Single-symbol operational check; no AI or claimed trend/breakout decision.
    gate = GateSnapshot(
        market_at=datetime.fromisoformat(report["as_of"]),
        account_at=ledger.last_reconciled,
        correlation_at=ledger.last_reconciled,
        bid=report["bid"],
        ask=report["ask"],
        mark=report["mark"],
        correlation_group="SINGLE_BTC_DEMO_VALIDATION",
        evaluation="RULES_ONLY",
    )
    return signal, gate, Decimal(item["execution_quantity_cap"])


async def validate_once(
    coordinator: DemoCoordinator,
    operation_id: UUID,
    direction: Literal["LONG", "SHORT"],
    *,
    observation_seconds: float = 30,
    entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "PASSIVE",
) -> None:
    """Keep the private supervisor active, then cancel residual entry and verify flat.

    Reusing the operation ID recovers the same durable intent; it never creates a new entry.
    Session cancellation starts only after entry preparation/submission has returned.
    """
    if (
        not coordinator.client.settings.demo_trading_enabled
        or coordinator.client.settings.margin_mode != "ISOLATED"
        or coordinator.client.settings.symbols != ("BTCUSDT",)
        or direction not in {"LONG", "SHORT"}
        or entry_style not in {"PASSIVE", "OPPOSITE_QUOTE"}
    ):
        raise DemoFailure("Validation requires explicit enabled ISOLATED BTCUSDT context")
    store, lease = coordinator.store, coordinator.lease
    key = str(operation_id)
    original = store.read(lease.account_id)
    if any(k != key and (o.remaining or o.open_quantity) for k, o in original.orders.items()):
        raise DemoFailure("Another active DEMO intent prevents validation")
    if key in original.orders and (
        original.orders[key].signal.purpose != "DEMO_VALIDATION"
        or original.orders[key].signal.direction != direction
        or original.validation_entry_styles.get(key, "PASSIVE") != entry_style
    ):
        raise DemoFailure("Validation operation ID conflict")
    if key not in original.orders and (
        not original.reconciled
        or original.risk_state.value != "NORMAL"
        or any(original.positions.values())
    ):
        raise DemoFailure("New validation requires an empty reconciled NORMAL account")
    loop = asyncio.get_running_loop()
    callback_done = asyncio.Event()
    failure = False
    validation_stage = "SESSION_CONNECT"
    session_failure = "NONE"

    def on_ready() -> None:
        nonlocal validation_stage, session_failure
        try:
            current = store.read(lease.account_id)
            if key not in current.orders:
                precheck = getattr(coordinator, "before_entry_prepare", None)
                if precheck is not None:
                    precheck()
                validation_stage = "RESUME_ENTRIES"
                coordinator.resume_entries(uuid5(operation_id, "resume"))
                validation_stage = "PREPARE_ENTRY"
                signal, gate, cap = prepare(
                    coordinator, operation_id, direction, entry_style=entry_style
                )
                validation_stage = "RESERVE_ENTRY"
                coordinator.client.sync_clock()
                order = coordinator.reserve(signal, gate, quantity_cap=cap, entry_style=entry_style)
                print(
                    json.dumps(
                        dict(
                            validation="RESERVED_NOT_YET_SUBMITTED",
                            environment="DEMO",
                            operation_id=key,
                            purpose="DEMO_VALIDATION",
                            entry_style=entry_style,
                            direction=direction,
                            quantity=str(order.quantity),
                            quantity_cap=str(cap),
                            limit_price=str(signal.suggested_limit_price),
                            stop_price=str(signal.initial_stop_price),
                            target_price=str(signal.target_price),
                            live_trading_enabled=False,
                        )
                    ),
                    flush=True,
                )
                validation_stage = "SUBMIT_ENTRY"
                coordinator.submit(key)
            else:
                print(
                    json.dumps(
                        dict(
                            validation="RECOVER_EXISTING_INTENT",
                            operation_id=key,
                            live_trading_enabled=False,
                        )
                    ),
                    flush=True,
                )
            validation_stage = "OBSERVATION"
        except Exception as error:
            session_failure = safe_failure(error)
            raise
        finally:
            loop.call_soon_threadsafe(callback_done.set)

    task = asyncio.create_task(session(coordinator, on_ready=on_ready))
    done = asyncio.create_task(callback_done.wait())
    try:
        await asyncio.wait({task, done}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            await task  # Propagate connection/setup failures before reporting success.
        else:
            finished, _ = await asyncio.wait({task}, timeout=observation_seconds)
            if finished:
                await task
    except Exception as error:
        failure = True  # Never include raw private session errors or URLs in output.
        if session_failure == "NONE":
            session_failure = safe_failure(error)
    finally:
        done.cancel()
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    if coordinator.session_failed or session_failure != "NONE":
        failure = True
        if session_failure == "NONE":
            session_failure = "DEMO_SESSION_OR_QUERY_FAILED"
    # The stream heartbeat has stopped. Renew the same fenced lease before finalization.
    # Only one flatten command; delayed fills are observed without resubmitting exits.
    result = "UNKNOWN"
    completion_stage = "LOCAL_PAUSE_ENTRIES"
    completion_failure = "NONE"
    completion_database_failure = "NONE"
    try:
        begin = getattr(coordinator, "begin_finalization", None)
        if begin is not None:
            await asyncio.to_thread(begin)
        await asyncio.to_thread(pause_finalization_locally, coordinator)
        if coordinator.client.clock_ms() < coordinator.client.cooldown_until:
            raise DemoRateLimited("DEMO cooldown active; manual exchange verification required")
        completion_stage = "PAUSE_ENTRIES"
        await asyncio.to_thread(store.renew, lease)
        await asyncio.to_thread(coordinator.client.sync_clock)
        await asyncio.to_thread(
            coordinator.command, uuid5(operation_id, "finish-pause"), "PAUSE_ENTRIES"
        )
        before_exit = await asyncio.to_thread(store.read, lease.account_id)
        if any(before_exit.positions.values()) or any(
            o.open_quantity for o in before_exit.orders.values()
        ):
            completion_stage = "FLATTEN_REQUEST"
            await asyncio.to_thread(store.renew, lease)
            await asyncio.to_thread(
                coordinator.command, uuid5(operation_id, "finish"), "EMERGENCY_FLATTEN"
            )
        completion_stage = "FLAT_CONFIRMATION"
        result = await confirm_exit(coordinator)
        if result != "VERIFIED_FLAT":
            completion_failure = "FLAT_NOT_CONFIRMED"
    except RiskRejected:
        completion_failure = "LEASE_OR_RISK_GATE_REJECTED"
        result = "UNKNOWN"
    except DemoRateLimited:
        completion_failure = "RATE_LIMITED_VERIFY_EXCHANGE_MANUALLY"
        result = "UNKNOWN"
        try:
            await asyncio.to_thread(pause_finalization_locally, coordinator)
        except SQLAlchemyError as error:
            completion_database_failure = database_failure(error)
        except RiskRejected:
            completion_failure = "LEASE_OR_RISK_GATE_REJECTED"
    except DemoFailure:
        completion_failure = "DEMO_QUERY_OR_CLEANUP_FAILED"
        result = "UNKNOWN"
    except SQLAlchemyError as error:
        completion_failure = "DATABASE_ACCESS_FAILED"
        completion_database_failure = database_failure(error)
        result = "UNKNOWN"
    except Exception:
        completion_failure = "OTHER_COMPLETION_FAILURE"
        result = "UNKNOWN"
    final = store.read(lease.account_id)
    order = final.orders.get(key)
    evidence = final.validation_evidence.get(key, {})
    acceptance = bool(
        order
        and order.filled
        and evidence.get("stop_verified")
        and evidence.get("target_verified")
        and result == "VERIFIED_FLAT"
        and not failure
    )
    print(
        json.dumps(
            dict(
                validation="FINISHED_FLAT"
                if result == "VERIFIED_FLAT"
                else "UNRESOLVED_DO_NOT_REPEAT",
                environment="DEMO",
                operation_id=key,
                entry_filled=bool(order and order.filled),
                filled_quantity=str(order.filled) if order else "0",
                entry_state=order.state.value if order else "NOT_CREATED",
                position_state=order.position_state.value if order else "NOT_CREATED",
                entry_outcome=(
                    "FULL_FILL"
                    if order and order.filled == order.quantity
                    else "PARTIAL_FILL"
                    if order and order.filled
                    else "UNFILLED"
                ),
                stop_verified=final.validation_evidence.get(key, {}).get("stop_verified", False),
                target_verified=final.validation_evidence.get(key, {}).get(
                    "target_verified", False
                ),
                filled_protection_and_flat_verified=acceptance,
                session_error=failure,
                validation_stage=validation_stage,
                session_failure=session_failure,
                private_session_diagnostic=coordinator.session_diagnostic.model_dump(mode="json"),
                completion_stage=completion_stage,
                completion_failure=completion_failure,
                completion_database_failure=completion_database_failure,
                last_reconciliation_mismatch_fields=final.last_reconciliation_mismatch_fields,
                reconciliation_settle_reason=final.reconciliation_settle_reason,
                initial_fill_settled=key in final.initial_fill_settlements,
                live_trading_enabled=False,
            )
        ),
        flush=True,
    )
    if result != "VERIFIED_FLAT" or failure or order is None or (order.filled and not acceptance):
        raise DemoFailure(
            "DEMO validation incomplete; inspect sanitized state and recover with the same operation ID"
        )
