"""Analyze router for the SRE Watchdog application.

Provides endpoints for triggering asynchronous anomaly analysis jobs and
polling their results. Jobs are stored in-memory via ``app.state.analyze_jobs``
and executed as FastAPI BackgroundTasks that run Gate 2 analysis for the
specified service(s) and time range.

Endpoints:
    POST /analyze — Create an analysis job (HTTP 202 Accepted).
    GET /analyze/{job_id} — Poll the status/results of an analysis job.

Typical usage::

    # Trigger analysis for all services over the past hour:
    POST /analyze {"start_time": "...", "end_time": "..."}

    # Poll for results:
    GET /analyze/{job_id}
"""

import logging
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models.db_models import AnomalyWindow, LogEntry
from app.models.schemas import (
    AnalyzeJobResult,
    AnalyzeJobStatus,
    AnalyzeRequest,
    AnalyzeResponse,
)
from app.services.alert_service import dispatch as alert_dispatch
from app.services.alert_service import is_in_cooldown
from app.services.bedrock_client import BedrockClient, BedrockParseError

logger = logging.getLogger(__name__)

router = APIRouter()

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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/analyze", status_code=202, response_model=AnalyzeResponse)
def create_analyze_job(
    body: AnalyzeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> AnalyzeResponse:
    """Create an asynchronous analysis job for the specified time range.

    Accepts an ``AnalyzeRequest`` body with an optional service filter and
    a required time range. Creates a UUID-identified job entry in the
    in-memory job store, enqueues a BackgroundTask to execute Gate 2
    analysis, and returns HTTP 202 with the job identifier.

    Args:
        body: The analysis request containing optional service name and
            required start_time/end_time.
        request: The FastAPI request object (used to access app state).
        background_tasks: FastAPI BackgroundTasks for enqueuing the analysis.

    Returns:
        An ``AnalyzeResponse`` with the job_id and initial status of 'pending'.
    """
    job_id = str(uuid.uuid4())

    # Create job entry in the in-memory store
    job_result = AnalyzeJobResult(
        job_id=job_id,
        status=AnalyzeJobStatus.PENDING,
    )
    request.app.state.analyze_jobs[job_id] = job_result

    # Enqueue the background analysis task
    background_tasks.add_task(
        _run_analysis_job,
        job_id=job_id,
        request=request,
        service=body.service,
        start_time=body.start_time,
        end_time=body.end_time,
    )

    logger.info(
        '{"event": "analyze_job_created", '
        f'"job_id": "{job_id}", '
        f'"service": {body.service!r}, '
        f'"start_time": "{body.start_time.isoformat()}", '
        f'"end_time": "{body.end_time.isoformat()}"}}'
    )

    return AnalyzeResponse(job_id=job_id, status=AnalyzeJobStatus.PENDING)


@router.get("/analyze/{job_id}", response_model=AnalyzeJobResult)
def get_analyze_job(job_id: str, request: Request) -> AnalyzeJobResult:
    """Retrieve the current status and results of an analysis job.

    Looks up the job by its UUID in the in-memory job store. Returns the
    full ``AnalyzeJobResult`` including status, anomalies found, alerts
    dispatched, and any error message.

    Args:
        job_id: The UUID string identifying the analysis job.
        request: The FastAPI request object (used to access app state).

    Returns:
        The ``AnalyzeJobResult`` for the requested job.

    Raises:
        HTTPException: 404 if the job_id is not found in the job store.
    """
    jobs = request.app.state.analyze_jobs
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return jobs[job_id]


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


def _run_analysis_job(
    job_id: str,
    request: Request,
    service: str | None,
    start_time: datetime,
    end_time: datetime,
) -> None:
    """Execute the analysis job as a BackgroundTask.

    Updates the job status through its lifecycle: pending → running →
    completed | failed. For each target service, queries log entries in
    the specified time range, computes the error rate, and if above
    threshold, creates an AnomalyWindow and runs Gate 2 analysis.

    Args:
        job_id: The UUID identifying this job in the in-memory store.
        request: The FastAPI request object (used to access app state).
        service: Optional service name filter. When None, analyzes all
            5 monitored services.
        start_time: Start of the analysis time range.
        end_time: End of the analysis time range.
    """
    jobs = request.app.state.analyze_jobs
    settings = get_settings()

    # Update status to running
    jobs[job_id] = AnalyzeJobResult(
        job_id=job_id,
        status=AnalyzeJobStatus.RUNNING,
    )

    db: Session = SessionLocal()
    try:
        # Determine which services to analyze
        services_to_analyze = [service] if service else MONITORED_SERVICES

        anomalies_found = 0
        alerts_dispatched = 0

        start_time_iso = start_time.strftime("%Y-%m-%dT%H:%M:%S")
        end_time_iso = end_time.strftime("%Y-%m-%dT%H:%M:%S")

        for svc in services_to_analyze:
            # Query log entries in the time range for this service
            entries = (
                db.query(LogEntry)
                .filter(
                    LogEntry.service == svc,
                    LogEntry.timestamp >= start_time_iso,
                    LogEntry.timestamp <= end_time_iso,
                )
                .all()
            )

            total_count = len(entries)
            if total_count == 0:
                continue

            # Compute error rate
            error_count = sum(
                1 for entry in entries if entry.level in ERROR_LEVELS
            )
            error_rate = error_count / total_count

            # Check if error rate exceeds threshold
            if error_rate > settings.ERROR_RATE_THRESHOLD:
                # Create AnomalyWindow record
                anomaly_window = AnomalyWindow(
                    service=svc,
                    window_start=start_time_iso,
                    window_end=end_time_iso,
                    error_rate=error_rate,
                    status="pending_analysis",
                )
                db.add(anomaly_window)
                db.commit()
                db.refresh(anomaly_window)

                anomalies_found += 1

                # Run Gate 2 analysis
                alert_sent = _run_gate2_for_job(
                    anomaly_window=anomaly_window,
                    log_entries=entries,
                    settings=settings,
                    db=db,
                )
                if alert_sent:
                    alerts_dispatched += 1

        # Mark job as completed
        jobs[job_id] = AnalyzeJobResult(
            job_id=job_id,
            status=AnalyzeJobStatus.COMPLETED,
            anomalies_found=anomalies_found,
            alerts_dispatched=alerts_dispatched,
            completed_at=datetime.utcnow(),
        )

        logger.info(
            '{"event": "analyze_job_completed", '
            f'"job_id": "{job_id}", '
            f'"anomalies_found": {anomalies_found}, '
            f'"alerts_dispatched": {alerts_dispatched}}}'
        )

    except Exception as exc:
        # Mark job as failed
        jobs[job_id] = AnalyzeJobResult(
            job_id=job_id,
            status=AnalyzeJobStatus.FAILED,
            error=str(exc),
            completed_at=datetime.utcnow(),
        )

        logger.error(
            '{"event": "analyze_job_failed", '
            f'"job_id": "{job_id}", '
            f'"error": "{str(exc)}"}}'
        )
    finally:
        db.close()


def _run_gate2_for_job(
    anomaly_window: AnomalyWindow,
    log_entries: list,
    settings,
    db: Session,
) -> bool:
    """Run Gate 2 AI analysis for a single anomaly window within a job.

    Invokes the Bedrock client to analyze the anomaly, updates the window
    status based on the result, and dispatches an alert if appropriate.

    Args:
        anomaly_window: The AnomalyWindow ORM instance to analyze.
        log_entries: List of LogEntry ORM instances for the window.
        settings: Application settings containing thresholds and config.
        db: An active SQLAlchemy session for database operations.

    Returns:
        True if an alert was successfully dispatched, False otherwise.
    """
    log_messages = [entry.message for entry in log_entries]

    # Parse window timestamps for Bedrock client
    window_start_dt = datetime.fromisoformat(anomaly_window.window_start)
    window_end_dt = datetime.fromisoformat(anomaly_window.window_end)

    bedrock_client = BedrockClient(settings=settings)

    try:
        result = bedrock_client.analyze(
            service=anomaly_window.service,
            window_start=window_start_dt,
            window_end=window_end_dt,
            error_rate=anomaly_window.error_rate,
            log_messages=log_messages,
        )
    except BedrockParseError as exc:
        anomaly_window.status = "analysis_failed"
        anomaly_window.failure_reason = f"BedrockParseError: {exc.message}"
        anomaly_window.updated_at = datetime.utcnow().isoformat()
        db.commit()
        return False
    except Exception as exc:
        anomaly_window.status = "analysis_failed"
        anomaly_window.failure_reason = str(exc)
        anomaly_window.updated_at = datetime.utcnow().isoformat()
        db.commit()
        return False

    # Update anomaly window with Bedrock results
    anomaly_window.anomaly_score = result.anomaly_score
    anomaly_window.ai_summary = result.summary

    if result.anomaly_score >= settings.ANOMALY_SCORE_THRESHOLD:
        # Score meets threshold — check cooldown
        if is_in_cooldown(anomaly_window.service, db, settings):
            anomaly_window.status = "suppressed"
            anomaly_window.suppression_reason = "cooldown_active"
            anomaly_window.updated_at = datetime.utcnow().isoformat()
            db.commit()
            # Create suppressed alert record
            alert_dispatch(anomaly_window, db, settings)
            return False
        else:
            anomaly_window.status = "confirmed"
            anomaly_window.updated_at = datetime.utcnow().isoformat()
            db.commit()
            # Dispatch alert
            alert_record = alert_dispatch(anomaly_window, db, settings)
            is_sent = (
                alert_record is not None
                and alert_record.dispatch_status == "sent"
            )
            return is_sent
    else:
        anomaly_window.status = "below_score_threshold"
        anomaly_window.updated_at = datetime.utcnow().isoformat()
        db.commit()
        return False
