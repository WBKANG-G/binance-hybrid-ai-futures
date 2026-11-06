"""Read-only development status API. No credential fields or order routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import yaml
from fastapi import FastAPI, Query, Response
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from packages.domain.config import AppConfig, database_url, load_config, validate_startup
from packages.persistence.demo import DemoStore
from packages.persistence.health import probe_database
from packages.persistence.market import MarketRepository
from packages.persistence.paper import PaperStore
from scripts.demo_alert_email_session import public_status as public_email_status
from services.execution.demo_status import demo_status
from services.execution.status import paper_status
from services.operations.demo_alert_status import ACCOUNT, AlertStatus, EmailMetadata, read_queue


class HealthResponse(BaseModel):
    status: Literal["development_only"] = "development_only"
    phase: Literal[4] = 4
    environment: Literal["BACKTEST", "PAPER", "DEMO", "LIVE"]
    live_trading_enabled: Literal[False] = False
    orders_enabled: Literal[False] = False
    demo_trading_enabled: Literal[False] = False
    database: Literal["not_checked", "ready", "unavailable"] = "not_checked"
    as_of: datetime
    is_stale: Literal[False] = False


class InstanceResponse(BaseModel):
    instance_id: UUID
    environment: Literal["BACKTEST", "PAPER", "DEMO", "LIVE"]
    orders_enabled: Literal[False] = False
    live_trading_enabled: Literal[False] = False


def read_market_status(symbol: str, interval: str, now: datetime) -> dict[str, object]:
    engine = create_engine(database_url().get_secret_value(), connect_args={"connect_timeout": 2})
    try:
        return MarketRepository(engine).status(symbol, interval, now=now)
    finally:
        engine.dispose()


def read_alert_status() -> AlertStatus:
    engine = create_engine(database_url().get_secret_value(), connect_args={"connect_timeout": 2})
    try:
        observed, queue, latest = read_queue(engine, ACCOUNT)
        return AlertStatus(
            status="READ_ONLY_ALERT_STATUS",
            as_of=observed,
            is_stale=False,
            email=EmailMetadata.model_validate(public_email_status()),
            queue=queue,
            latest_critical=latest,
        )
    finally:
        engine.dispose()


def create_app(config: AppConfig | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            selected = config or load_config()
        except (ValueError, OSError, yaml.YAMLError):
            raise ValueError(
                "Invalid development configuration; check the config schema and environment variable names"
            ) from None
        validate_startup(selected)
        application.state.config = selected
        application.state.instance_id = uuid4()
        yield

    application = FastAPI(
        title="Binance Hybrid AI Futures — Phase 4", version="0.3.0", lifespan=lifespan
    )

    @application.get("/api/v1/system/instance", response_model=InstanceResponse)
    def instance() -> InstanceResponse:
        return InstanceResponse(
            instance_id=application.state.instance_id,
            environment=application.state.config.mode,
        )

    @application.get("/api/v1/system/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(environment=application.state.config.mode, as_of=datetime.now(UTC))

    @application.get("/api/v1/system/ready", response_model=HealthResponse)
    def ready(response: Response) -> HealthResponse:
        available = probe_database()
        if not available:
            response.status_code = 503
        return HealthResponse(
            environment=application.state.config.mode,
            database="ready" if available else "unavailable",
            as_of=datetime.now(UTC),
        )

    @application.get("/api/v1/market-data/status")
    def market_status(
        response: Response,
        symbol: str = Query("BTCUSDT", pattern=r"^[A-Z0-9]{5,30}$"),
        interval: str = Query("1m", pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)$"),
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        try:
            return read_market_status(symbol, interval, now)
        except (SQLAlchemyError, ValueError):
            response.status_code = 503
            return dict(
                symbol=symbol,
                interval=interval,
                as_of=None,
                observed_at=now,
                is_stale=True,
                reconciliation_required=True,
                reason="STORAGE_UNAVAILABLE",
                orders_enabled=False,
            )

    @application.get("/api/v1/paper/status")
    def paper_snapshot(
        response: Response,
        account_id: str | None = Query(None, pattern=r"^PAPER-[A-Za-z0-9_-]{1,74}$"),
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        if account_id is None:
            return paper_status(None, now)
        engine = None
        try:
            engine = create_engine(
                database_url().get_secret_value(), connect_args={"connect_timeout": 2}
            )
            return paper_status(PaperStore(engine).read(account_id), now)
        except (SQLAlchemyError, ValueError):
            response.status_code = 503
            status = paper_status(None, now)
            status["reason"] = "STORAGE_UNAVAILABLE_OR_ACCOUNT_MISSING"
            return status
        finally:
            if engine is not None:
                engine.dispose()

    @application.get("/api/v1/demo/alerts/status", response_model=AlertStatus)
    def alert_snapshot(response: Response) -> AlertStatus:
        response.headers["Cache-Control"] = "no-store"
        try:
            return read_alert_status()
        except (SQLAlchemyError, ValueError, OSError):
            response.status_code = 503
            return AlertStatus(
                status="UNAVAILABLE",
                as_of=datetime.now(UTC),
                is_stale=True,
                email=EmailMetadata(state="UNKNOWN"),
            )

    @application.get("/api/v1/demo/status")
    def demo_snapshot(
        response: Response,
        account_id: str | None = Query(None, pattern=r"^DEMO-[A-Za-z0-9_-]{1,75}$"),
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        if account_id is None:
            return demo_status(None, now)
        engine = None
        try:
            engine = create_engine(
                database_url().get_secret_value(), connect_args={"connect_timeout": 2}
            )
            return demo_status(DemoStore(engine).read(account_id), now)
        except (SQLAlchemyError, ValueError):
            response.status_code = 503
            result = demo_status(None, now)
            result["reason"] = "STORAGE_UNAVAILABLE_OR_ACCOUNT_MISSING"
            return result
        finally:
            if engine is not None:
                engine.dispose()

    return application


app = create_app()
