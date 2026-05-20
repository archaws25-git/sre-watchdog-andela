"""Alert records router for the SRE Watchdog API.

Provides a paginated endpoint for retrieving the webhook dispatch log,
including all sent, failed, and suppressed alert records linked to their
source anomaly windows.

Typical usage::

    from app.routers.alerts import router
    app.include_router(router)
"""

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import AlertRecord
from app.models.schemas import AlertRecordResponse

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertRecordResponse])
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
    records = (
        db.query(AlertRecord)
        .order_by(AlertRecord.dispatched_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return [
        AlertRecordResponse(
            id=record.id,
            anomaly_id=record.anomaly_id,
            dispatched_at=record.dispatched_at,
            webhook_url=record.webhook_url,
            payload=json.loads(record.payload),
            http_status=record.http_status,
            dispatch_status=record.dispatch_status,
            severity=record.severity,
        )
        for record in records
    ]
