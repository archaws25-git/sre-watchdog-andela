"""Metrics router for the SRE Watchdog API.

Provides an endpoint that returns operational counters derived from
database queries. Used for monitoring the Watchdog's own activity
and for populating the dashboard metrics bar.

Typical usage::

    from app.routers.metrics import router
    app.include_router(router)
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import AlertRecord, AnomalyWindow, LogEntry
from app.models.schemas import MetricsResponse

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(
    db: Session = Depends(get_db),
) -> MetricsResponse:
    """Retrieve operational counters for the Watchdog platform.

    Runs six count queries against the database to produce aggregate
    metrics covering log ingestion, anomaly detection, alert dispatch,
    and failure/suppression counts.

    Args:
        db: SQLAlchemy database session (injected).

    Returns:
        MetricsResponse containing all six operational counters.
    """
    total_logs_ingested = db.query(func.count(LogEntry.id)).scalar() or 0

    total_anomalies_detected = db.query(func.count(AnomalyWindow.id)).scalar() or 0

    total_alerts_dispatched = (
        db.query(func.count(AlertRecord.id))
        .filter(AlertRecord.dispatch_status == "sent")
        .scalar()
        or 0
    )

    total_failed_alerts = (
        db.query(func.count(AlertRecord.id))
        .filter(AlertRecord.dispatch_status == "failed")
        .scalar()
        or 0
    )

    total_analysis_failed = (
        db.query(func.count(AnomalyWindow.id))
        .filter(AnomalyWindow.status == "analysis_failed")
        .scalar()
        or 0
    )

    total_cooldown_suppressed = (
        db.query(func.count(AnomalyWindow.id))
        .filter(AnomalyWindow.status == "suppressed")
        .scalar()
        or 0
    )

    return MetricsResponse(
        total_logs_ingested=total_logs_ingested,
        total_anomalies_detected=total_anomalies_detected,
        total_alerts_dispatched=total_alerts_dispatched,
        total_failed_alerts=total_failed_alerts,
        total_analysis_failed=total_analysis_failed,
        total_cooldown_suppressed=total_cooldown_suppressed,
    )
