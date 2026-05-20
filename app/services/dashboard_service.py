"""Dashboard service for the SRE Watchdog application.

Provides aggregation queries for the Jinja2 server-side rendered dashboard.
Computes Chart.js-compatible time-series data (error rate per service over
24 hours) and retrieves recent anomalies and alerts for the initial page load.

Typical usage::

    from app.services.dashboard_service import (
        get_chart_data,
        get_recent_anomalies,
        get_recent_alerts,
    )
    from app.database import get_db

    chart_data = get_chart_data(db)
    anomalies = get_recent_anomalies(db, limit=20)
    alerts = get_recent_alerts(db, limit=20)
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.db_models import AlertRecord, AnomalyWindow, LogEntry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MONITORED_SERVICES: list[str] = [
    "api-gateway",
    "auth-service",
    "payment-service",
    "notification-service",
    "database-proxy",
]

SERVICE_COLORS: dict[str, str] = {
    "api-gateway": "#FF6384",
    "auth-service": "#36A2EB",
    "payment-service": "#FFCE56",
    "notification-service": "#4BC0C0",
    "database-proxy": "#9966FF",
}


# ---------------------------------------------------------------------------
# Chart data
# ---------------------------------------------------------------------------


def get_chart_data(db: Session) -> dict[str, Any]:
    """Generate Chart.js-compatible error rate time-series data.

    Buckets ``log_entries`` into 1-hour intervals over the past 24 hours and
    computes the error rate (ERROR + CRITICAL / total) per service per bucket.
    Buckets with no log entries default to ``0.0``.

    Args:
        db: An active SQLAlchemy session for database queries.

    Returns:
        A dictionary with the structure::

            {
                "labels": ["2025-01-15T00:00", "2025-01-15T01:00", ...],
                "datasets": [
                    {
                        "label": "api-gateway",
                        "data": [0.0, 0.05, ...],
                        "borderColor": "#FF6384",
                        "tension": 0.3,
                        "fill": false
                    },
                    ...
                ]
            }
    """
    now = datetime.utcnow()
    # Align to the start of the current hour
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    start_time = current_hour - timedelta(hours=23)

    # Generate 24 hourly bucket boundaries
    buckets: list[tuple[datetime, datetime]] = []
    labels: list[str] = []
    for i in range(24):
        bucket_start = start_time + timedelta(hours=i)
        bucket_end = bucket_start + timedelta(hours=1)
        buckets.append((bucket_start, bucket_end))
        labels.append(bucket_start.strftime("%Y-%m-%dT%H:%M"))

    # Build datasets for each service
    datasets: list[dict[str, Any]] = []
    for service in MONITORED_SERVICES:
        data: list[float] = []
        for bucket_start, bucket_end in buckets:
            bucket_start_iso = bucket_start.strftime("%Y-%m-%dT%H:%M:%S")
            bucket_end_iso = bucket_end.strftime("%Y-%m-%dT%H:%M:%S")

            # Count total entries in this bucket for this service
            total_count = (
                db.query(func.count(LogEntry.id))
                .filter(
                    LogEntry.service == service,
                    LogEntry.timestamp >= bucket_start_iso,
                    LogEntry.timestamp < bucket_end_iso,
                )
                .scalar()
            ) or 0

            if total_count == 0:
                data.append(0.0)
            else:
                # Count ERROR + CRITICAL entries
                error_count = (
                    db.query(func.count(LogEntry.id))
                    .filter(
                        LogEntry.service == service,
                        LogEntry.timestamp >= bucket_start_iso,
                        LogEntry.timestamp < bucket_end_iso,
                        LogEntry.level.in_(["ERROR", "CRITICAL"]),
                    )
                    .scalar()
                ) or 0

                error_rate = error_count / total_count
                data.append(round(error_rate, 4))

        datasets.append({
            "label": service,
            "data": data,
            "borderColor": SERVICE_COLORS[service],
            "tension": 0.3,
            "fill": False,
        })

    return {
        "labels": labels,
        "datasets": datasets,
    }


# ---------------------------------------------------------------------------
# Recent anomalies
# ---------------------------------------------------------------------------


def _compute_severity_label(anomaly_score: float | None) -> str:
    """Compute a human-readable severity label from an anomaly score.

    Args:
        anomaly_score: Numeric score between 0.0 and 1.0, or None if
            Gate 2 analysis has not completed.

    Returns:
        A severity string: "LOW", "MEDIUM", "HIGH", "CRITICAL", or
        "UNKNOWN" if the score is None.
    """
    if anomaly_score is None:
        return "UNKNOWN"
    if anomaly_score <= 0.39:
        return "LOW"
    elif anomaly_score <= 0.69:
        return "MEDIUM"
    elif anomaly_score <= 0.89:
        return "HIGH"
    return "CRITICAL"


def get_recent_anomalies(db: Session, limit: int = 20) -> list[dict[str, Any]]:
    """Retrieve the most recent anomaly windows for dashboard display.

    Queries ``anomaly_windows`` ordered by ``created_at`` descending and
    returns a list of dictionaries suitable for Jinja2 template rendering.

    Args:
        db: An active SQLAlchemy session for database queries.
        limit: Maximum number of anomaly records to return. Defaults to 20.

    Returns:
        A list of dictionaries, each containing:
            - service: The affected service name.
            - window_start: ISO 8601 start of the anomaly window.
            - window_end: ISO 8601 end of the anomaly window.
            - anomaly_score: Numeric score (0.0–1.0) or None.
            - status: Current lifecycle status string.
            - ai_summary: Plain-text AI summary or None.
            - severity: Computed severity label string.
    """
    anomalies = (
        db.query(AnomalyWindow)
        .order_by(AnomalyWindow.created_at.desc())
        .limit(limit)
        .all()
    )

    results: list[dict[str, Any]] = []
    for anomaly in anomalies:
        results.append({
            "service": anomaly.service,
            "window_start": anomaly.window_start,
            "window_end": anomaly.window_end,
            "anomaly_score": anomaly.anomaly_score,
            "status": anomaly.status,
            "ai_summary": anomaly.ai_summary,
            "severity": _compute_severity_label(anomaly.anomaly_score),
        })

    return results


# ---------------------------------------------------------------------------
# Recent alerts
# ---------------------------------------------------------------------------


def get_recent_alerts(db: Session, limit: int = 20) -> list[dict[str, Any]]:
    """Retrieve the most recent alert records for dashboard display.

    Queries ``alert_records`` joined with ``anomaly_windows`` to include the
    service name, ordered by ``dispatched_at`` descending.

    Args:
        db: An active SQLAlchemy session for database queries.
        limit: Maximum number of alert records to return. Defaults to 20.

    Returns:
        A list of dictionaries, each containing:
            - dispatched_at: ISO 8601 timestamp of the dispatch attempt.
            - service: The affected service name (from joined anomaly window).
            - severity: Severity label string.
            - anomaly_id: Foreign key to the source anomaly window.
            - dispatch_status: Outcome string (sent, failed, suppressed).
    """
    rows = (
        db.query(AlertRecord, AnomalyWindow.service)
        .join(AnomalyWindow, AlertRecord.anomaly_id == AnomalyWindow.id)
        .order_by(AlertRecord.dispatched_at.desc())
        .limit(limit)
        .all()
    )

    results: list[dict[str, Any]] = []
    for alert, service_name in rows:
        results.append({
            "dispatched_at": alert.dispatched_at,
            "service": service_name or "unknown",
            "severity": alert.severity,
            "anomaly_id": alert.anomaly_id,
            "dispatch_status": alert.dispatch_status,
        })

    return results
