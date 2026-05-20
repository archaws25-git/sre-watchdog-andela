"""Anomaly detection service implementing the two-gate detection pipeline.

Gate 1 (statistical pre-filter) runs synchronously on each APScheduler tick,
computing per-service error rates over a sliding window. When a threshold
breach is detected, an AnomalyWindow record is created and a FastAPI
BackgroundTask is enqueued for Gate 2 (AI analysis via AWS Bedrock).

Gate 2 runs asynchronously within a BackgroundTask, invoking the Bedrock
client for deeper analysis. Based on the returned anomaly score, the record
is updated to confirmed/suppressed/below_score_threshold/analysis_failed,
and alerts are dispatched when appropriate.

A startup cleanup function marks stale pending_analysis records as failed
to prevent orphaned records from accumulating across application restarts.

Typical usage::

    from app.services.anomaly_detector import (
        evaluate_all_services,
        run_gate2,
        cleanup_stale_pending,
    )

    # Called by APScheduler tick:
    evaluate_all_services(db, settings, background_tasks)

    # Called during lifespan startup:
    cleanup_stale_pending(db)
"""

import logging
from datetime import datetime, timedelta
from typing import List

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import SessionLocal
from app.models.db_models import AnomalyWindow, LogEntry
from app.services.alert_service import dispatch as alert_dispatch
from app.services.alert_service import is_in_cooldown
from app.services.bedrock_client import BedrockClient, BedrockParseError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MONITORED_SERVICES: List[str] = [
    "api-gateway",
    "auth-service",
    "payment-service",
    "notification-service",
    "database-proxy",
]
"""The 5 named services monitored by the SRE Watchdog."""

ERROR_LEVELS: set = {"ERROR", "CRITICAL"}
"""Log levels that count toward the error rate computation."""

STALE_PENDING_MINUTES: int = 10
"""Records pending longer than this are marked as orphaned on restart."""


# ---------------------------------------------------------------------------
# Gate 1 — Statistical Pre-filter
# ---------------------------------------------------------------------------


def evaluate_all_services(
    db: Session,
    settings: Settings,
    background_tasks: BackgroundTasks,
    bedrock_client: BedrockClient,
) -> None:
    """Evaluate all monitored services for anomaly threshold breaches (Gate 1).

    For each of the 5 monitored services, queries log entries within the
    configured sliding window, computes the error rate, and if the rate
    exceeds ERROR_RATE_THRESHOLD, creates an AnomalyWindow record with
    status=pending_analysis and enqueues a BackgroundTask for Gate 2.

    This function runs synchronously on the APScheduler tick and never
    blocks on Bedrock latency.

    Args:
        db: An active SQLAlchemy session for database queries and inserts.
        settings: Application settings containing thresholds and window size.
        background_tasks: FastAPI BackgroundTasks instance for enqueuing
            Gate 2 analysis tasks.
        bedrock_client: The Bedrock client instance for Gate 2 analysis.
    """
    now = datetime.utcnow()
    window_start = now - timedelta(minutes=settings.SLIDING_WINDOW_MINUTES)
    window_start_iso = window_start.strftime("%Y-%m-%dT%H:%M:%S")
    window_end_iso = now.strftime("%Y-%m-%dT%H:%M:%S")

    for service in MONITORED_SERVICES:
        # Query log entries within the sliding window for this service
        entries = (
            db.query(LogEntry)
            .filter(
                LogEntry.service == service,
                LogEntry.timestamp >= window_start_iso,
            )
            .all()
        )

        total_count = len(entries)
        if total_count == 0:
            continue

        # Compute error rate: (ERROR + CRITICAL) / total
        error_count = sum(
            1 for entry in entries if entry.level in ERROR_LEVELS
        )
        error_rate = error_count / total_count

        # Check if error rate exceeds threshold
        if error_rate > settings.ERROR_RATE_THRESHOLD:
            # Create AnomalyWindow record with pending_analysis status
            anomaly_window = AnomalyWindow(
                service=service,
                window_start=window_start_iso,
                window_end=window_end_iso,
                error_rate=error_rate,
                status="pending_analysis",
            )
            db.add(anomaly_window)
            db.commit()
            db.refresh(anomaly_window)

            logger.info(
                '{"event": "gate1_threshold_breach", '
                f'"service": "{service}", '
                f'"error_rate": {error_rate:.4f}, '
                f'"threshold": {settings.ERROR_RATE_THRESHOLD}, '
                f'"total_entries": {total_count}, '
                f'"error_entries": {error_count}, '
                f'"anomaly_id": {anomaly_window.id}}}'
            )

            # Enqueue BackgroundTask for Gate 2
            background_tasks.add_task(
                run_gate2,
                anomaly_id=anomaly_window.id,
                settings=settings,
                bedrock_client=bedrock_client,
            )


# ---------------------------------------------------------------------------
# Gate 2 — AI Analysis (BackgroundTask)
# ---------------------------------------------------------------------------


def run_gate2(
    anomaly_id: int,
    settings: Settings,
    bedrock_client: BedrockClient,
) -> None:
    """Execute Gate 2 AI analysis for a single anomaly window (BackgroundTask).

    Creates its own database session (since BackgroundTasks run outside the
    request lifecycle), fetches the anomaly window and associated log messages,
    invokes the Bedrock client for analysis, and updates the record status
    based on the result.

    If the anomaly score meets the threshold and the service is not in
    cooldown, dispatches an alert. If cooldown is active, marks the record
    as suppressed.

    Args:
        anomaly_id: Primary key of the AnomalyWindow record to analyze.
        settings: Application settings containing thresholds and configuration.
        bedrock_client: The Bedrock client instance for AI analysis.
    """
    db = SessionLocal()
    try:
        # Fetch the anomaly window record
        anomaly_window = (
            db.query(AnomalyWindow)
            .filter(AnomalyWindow.id == anomaly_id)
            .first()
        )

        if anomaly_window is None:
            logger.error(
                f'{{"event": "gate2_anomaly_not_found", "anomaly_id": {anomaly_id}}}'
            )
            return

        # Fetch log messages for the window
        log_entries = (
            db.query(LogEntry)
            .filter(
                LogEntry.service == anomaly_window.service,
                LogEntry.timestamp >= anomaly_window.window_start,
                LogEntry.timestamp <= anomaly_window.window_end,
            )
            .order_by(LogEntry.timestamp.asc())
            .all()
        )

        log_messages = [entry.message for entry in log_entries]

        # Parse window timestamps for Bedrock client
        window_start_dt = datetime.fromisoformat(anomaly_window.window_start)
        window_end_dt = datetime.fromisoformat(anomaly_window.window_end)

        # Invoke Bedrock analysis
        try:
            result = bedrock_client.analyze(
                service=anomaly_window.service,
                window_start=window_start_dt,
                window_end=window_end_dt,
                error_rate=anomaly_window.error_rate,
                log_messages=log_messages,
            )
        except BedrockParseError as exc:
            # Bedrock returned unparseable response
            anomaly_window.status = "analysis_failed"
            anomaly_window.failure_reason = f"BedrockParseError: {exc.message}"
            anomaly_window.updated_at = datetime.utcnow().isoformat()
            db.commit()

            logger.warning(
                f'{{"event": "gate2_parse_error", '
                f'"anomaly_id": {anomaly_id}, '
                f'"service": "{anomaly_window.service}", '
                f'"error": "{exc.message}"}}'
            )
            return
        except Exception as exc:
            # Any other Bedrock failure (ClientError, timeout, etc.)
            anomaly_window.status = "analysis_failed"
            anomaly_window.failure_reason = str(exc)
            anomaly_window.updated_at = datetime.utcnow().isoformat()
            db.commit()

            logger.warning(
                f'{{"event": "gate2_analysis_failed", '
                f'"anomaly_id": {anomaly_id}, '
                f'"service": "{anomaly_window.service}", '
                f'"error": "{str(exc)}"}}'
            )
            return

        # Update anomaly window with Bedrock results
        anomaly_window.anomaly_score = result.anomaly_score
        anomaly_window.ai_summary = result.summary

        if result.anomaly_score >= settings.ANOMALY_SCORE_THRESHOLD:
            # Score meets threshold — check cooldown before dispatching
            if is_in_cooldown(anomaly_window.service, db, settings):
                # Cooldown active — suppress alert dispatch
                anomaly_window.status = "suppressed"
                anomaly_window.suppression_reason = "cooldown_active"
                anomaly_window.updated_at = datetime.utcnow().isoformat()
                db.commit()

                # Still create a suppressed alert record via alert_service
                alert_dispatch(anomaly_window, db, settings)

                logger.info(
                    f'{{"event": "gate2_suppressed_cooldown", '
                    f'"anomaly_id": {anomaly_id}, '
                    f'"service": "{anomaly_window.service}", '
                    f'"anomaly_score": {result.anomaly_score}}}'
                )
            else:
                # No cooldown — confirm and dispatch alert
                anomaly_window.status = "confirmed"
                anomaly_window.updated_at = datetime.utcnow().isoformat()
                db.commit()

                # Dispatch alert (this will update status to 'alerted' on success)
                alert_dispatch(anomaly_window, db, settings)

                logger.info(
                    f'{{"event": "gate2_confirmed", '
                    f'"anomaly_id": {anomaly_id}, '
                    f'"service": "{anomaly_window.service}", '
                    f'"anomaly_score": {result.anomaly_score}}}'
                )
        else:
            # Score below threshold — no alert
            anomaly_window.status = "below_score_threshold"
            anomaly_window.updated_at = datetime.utcnow().isoformat()
            db.commit()

            logger.info(
                f'{{"event": "gate2_below_threshold", '
                f'"anomaly_id": {anomaly_id}, '
                f'"service": "{anomaly_window.service}", '
                f'"anomaly_score": {result.anomaly_score}, '
                f'"threshold": {settings.ANOMALY_SCORE_THRESHOLD}}}'
            )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Startup Cleanup
# ---------------------------------------------------------------------------


def cleanup_stale_pending(db: Session) -> int:
    """Mark stale pending_analysis records as analysis_failed on startup.

    Records that have been in pending_analysis status for longer than
    STALE_PENDING_MINUTES (10 minutes) are considered orphaned from a
    previous application instance that shut down before Gate 2 completed.

    Args:
        db: An active SQLAlchemy session for the cleanup operation.

    Returns:
        The number of records marked as analysis_failed.
    """
    cutoff = (
        datetime.utcnow() - timedelta(minutes=STALE_PENDING_MINUTES)
    ).isoformat()

    stale_count = (
        db.query(AnomalyWindow)
        .filter(
            AnomalyWindow.status == "pending_analysis",
            AnomalyWindow.created_at < cutoff,
        )
        .update(
            {
                "status": "analysis_failed",
                "suppression_reason": "orphaned_on_restart",
                "failure_reason": "Application restarted before Gate 2 completed",
                "updated_at": datetime.utcnow().isoformat(),
            }
        )
    )

    db.commit()

    if stale_count > 0:
        logger.info(
            f'{{"event": "startup_cleanup", '
            f'"stale_records_cleaned": {stale_count}}}'
        )

    return stale_count
