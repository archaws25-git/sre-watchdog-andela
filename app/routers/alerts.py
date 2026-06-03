"""Alert records router for the SRE Watchdog API.

Provides a paginated endpoint for retrieving the webhook dispatch log,
including all sent, failed, and suppressed alert records linked to their
source anomaly windows.

Typical usage::

    from app.routers.alerts import router
    app.include_router(router)
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import AlertRecord, AnomalyWindow
from app.models.schemas import AlertDispatchStatus, AlertRecordResponse, SeverityLabel

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertRecordResponse], summary="List alert dispatch records")
def get_alerts(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(
        default=20, ge=1, le=100, description="Number of records per page"
    ),
    db: Session = Depends(get_db),
) -> list[AlertRecordResponse]:
    """Retrieve a paginated list of alert dispatch records.

    Returns alert records ordered by dispatched_at descending (most recent
    first). Each record includes the full webhook payload, HTTP response
    status, dispatch outcome, and severity label.

    Args:
        page: Page number (1-indexed, default 1).
        page_size: Number of records per page (default 20, max 100).
        db: SQLAlchemy database session (injected).

    Returns:
        List of AlertRecordResponse objects for the requested page.
    """
    offset = (page - 1) * page_size
    rows = (
        db.query(AlertRecord, AnomalyWindow.service)
        .outerjoin(AnomalyWindow, AlertRecord.anomaly_id == AnomalyWindow.id)
        .order_by(AlertRecord.dispatched_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return [
        AlertRecordResponse(
            id=int(record.id),  # type: ignore[arg-type]
            anomaly_id=int(record.anomaly_id),  # type: ignore[arg-type]
            service=service_name or "unknown",
            dispatched_at=datetime.fromisoformat(str(record.dispatched_at)),
            webhook_url=str(record.webhook_url),
            payload=json.loads(str(record.payload)),
            http_status=int(record.http_status) if record.http_status else None,
            dispatch_status=AlertDispatchStatus(str(record.dispatch_status)),
            severity=SeverityLabel(str(record.severity)),
        )
        for record, service_name in rows
    ]
