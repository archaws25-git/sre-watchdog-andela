"""FastAPI application factory and lifespan management for the SRE Watchdog.

Creates the FastAPI application instance, registers all routers and middleware,
and manages the application lifecycle via an async context manager. On startup,
the lifespan handler initialises shared state, validates AWS credentials,
cleans up stale anomaly records, and starts the APScheduler detection tick.
On shutdown, the scheduler is gracefully stopped.

Typical usage::

    uvicorn app.main:app --reload
"""

import logging
import sys
from contextlib import asynccontextmanager

import boto3
from fastapi import FastAPI

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.middleware import RequestLoggingMiddleware
from app.routers import alerts, analyze, anomalies, dashboard, health, logs, metrics, webhooks
from app.scheduler import create_scheduler, start_scheduler, stop_scheduler
from app.services.anomaly_detector import cleanup_stale_pending
from app.services.bedrock_client import BedrockClient

logger = logging.getLogger(__name__)


def _configure_logging(log_level: str) -> None:
    """Configure Python root logger with structured JSON format.

    Sets the root logger to the specified level and applies a JSON-style
    formatter so that all application log output is machine-parseable.

    Args:
        log_level: Python logging level string (DEBUG, INFO, WARNING, etc.).
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)

    formatter = logging.Formatter(
        '{"timestamp": "%(asctime)s", "level": "%(levelname)s", '
        '"logger": "%(name)s", "message": %(message)s}',
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle.

    Startup:
        1. Load and validate application settings.
        2. Configure structured JSON logging.
        3. Create database tables if they do not exist.
        4. Initialise shared application state (bedrock_health, analyze_jobs).
        5. Validate AWS credentials and set bedrock_health to degraded if absent.
        6. Clean up stale pending_analysis records from prior restarts.
        7. Create the BedrockClient with app state for health caching.
        8. Create and start the APScheduler detection tick.

    Shutdown:
        1. Gracefully stop the APScheduler.

    Args:
        app: The FastAPI application instance.

    Yields:
        Control to the running application between startup and shutdown.
    """
    # --- Settings and logging ---
    settings = get_settings()
    _configure_logging(settings.LOG_LEVEL)

    # --- Database table creation ---
    Base.metadata.create_all(bind=engine)

    # --- Shared application state ---
    app.state.bedrock_health = {
        "status": "unknown",
        "last_checked_at": None,
        "message": "No inference calls made yet",
    }
    app.state.analyze_jobs = {}

    # --- AWS credential check ---
    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        logger.warning(
            '"AWS credentials not found. Bedrock calls will fail."'
        )
        app.state.bedrock_health["status"] = "degraded"
        app.state.bedrock_health["message"] = "No AWS credentials found at startup"

    # --- Startup cleanup: mark stale pending_analysis records ---
    db = SessionLocal()
    try:
        stale_count = cleanup_stale_pending(db)
        if stale_count > 0:
            logger.info(
                f'"Startup cleanup: marked {stale_count} stale records as analysis_failed"'
            )
    finally:
        db.close()

    # --- Bedrock client and scheduler ---
    bedrock_client = BedrockClient(settings=settings, app_state=app.state)
    scheduler = create_scheduler(settings, bedrock_client)
    start_scheduler(scheduler)

    logger.info('"SRE Watchdog application started successfully"')

    yield

    # --- Shutdown ---
    stop_scheduler(scheduler)
    logger.info('"SRE Watchdog application shut down"')


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(title="SRE Watchdog", lifespan=lifespan)

# --- Middleware ---
app.add_middleware(RequestLoggingMiddleware)

# --- Routers ---
app.include_router(logs.router)
app.include_router(anomalies.router)
app.include_router(analyze.router)
app.include_router(alerts.router)
app.include_router(webhooks.router)
app.include_router(health.router)
app.include_router(metrics.router)
app.include_router(dashboard.router)
