"""Log ingestion and retrieval router for the SRE Watchdog API.

Provides two endpoints:
- POST /logs/ingest: Accepts batches of structured log entries for persistence.
- GET /logs: Returns stored log entries with offset-based pagination and filters.

Typical usage::

    from app.routers.logs import router
    app.include_router(router)
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models.db_models import LogEntry
from app.models.schemas import (
    IngestRequest,
    IngestResponse,
    LogEntryResponse,
    PaginatedLogsResponse,
)
from app.services import log_ingestion_service

router = APIRouter(prefix="/logs", tags=["logs"])


@router.post("/ingest", response_model=IngestResponse)
def ingest_logs(
    request: IngestRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> IngestResponse | JSONResponse:
    """Ingest a batch of structured log entries.

    Validates that the batch size does not exceed MAX_INGEST_BATCH_SIZE.
    If the limit is exceeded, returns HTTP 413 with a structured error body.
    Otherwise, delegates to the log ingestion service for validation and
    persistence.

    Args:
        request: The ingest request containing a list of log entries.
        db: SQLAlchemy database session (injected).
        settings: Application settings (injected).

    Returns:
        IngestResponse on success, or a 413 JSONResponse if the batch is
        too large.
    """
    if len(request.entries) > settings.MAX_INGEST_BATCH_SIZE:
        return JSONResponse(
            status_code=413,
            content={
                "error": "Batch too large",
                "limit": settings.MAX_INGEST_BATCH_SIZE,
                "received": len(request.entries),
            },
        )

    return log_ingestion_service.ingest_batch(
        entries=request.entries,
        db=db,
        settings=settings,
    )


@router.get("", response_model=PaginatedLogsResponse)
def get_logs(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(
        default=100, ge=1, le=500, description="Number of entries per page (max 500)"
    ),
    service: Optional[str] = Query(default=None, description="Filter by service name"),
    level: Optional[str] = Query(default=None, description="Filter by log level"),
    start_time: Optional[datetime] = Query(
        default=None, description="Filter entries at or after this ISO 8601 timestamp"
    ),
    end_time: Optional[datetime] = Query(
        default=None, description="Filter entries at or before this ISO 8601 timestamp"
    ),
    db: Session = Depends(get_db),
) -> PaginatedLogsResponse:
    """Retrieve stored log entries with pagination and optional filters.

    Supports filtering by service name, log level, and time range. Returns
    a paginated response envelope with total count, current page metadata,
    and the data slice.

    Args:
        page: Page number (1-indexed, default 1).
        page_size: Number of entries per page (default 100, max 500).
        service: Optional service name filter.
        level: Optional log level filter (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        start_time: Optional start of time range filter (inclusive).
        end_time: Optional end of time range filter (inclusive).
        db: SQLAlchemy database session (injected).

    Returns:
        PaginatedLogsResponse containing total_count, page, page_size,
        has_more flag, and the data array of log entries.
    """
    query = db.query(LogEntry)

    if service is not None:
        query = query.filter(LogEntry.service == service)
    if level is not None:
        query = query.filter(LogEntry.level == level)
    if start_time is not None:
        query = query.filter(LogEntry.timestamp >= start_time.isoformat())
    if end_time is not None:
        query = query.filter(LogEntry.timestamp <= end_time.isoformat())

    total_count = query.count()

    offset = (page - 1) * page_size
    entries = query.order_by(LogEntry.id).offset(offset).limit(page_size).all()

    has_more = page * page_size < total_count

    data = [
        LogEntryResponse(
            id=entry.id,
            timestamp=datetime.fromisoformat(entry.timestamp),
            service=entry.service,
            level=entry.level,
            message=entry.message,
            ingested_at=datetime.fromisoformat(entry.ingested_at),
        )
        for entry in entries
    ]

    return PaginatedLogsResponse(
        total_count=total_count,
        page=page,
        page_size=page_size,
        has_more=has_more,
        data=data,
    )
