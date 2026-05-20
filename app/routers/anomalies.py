"""Anomaly window API router for the SRE Watchdog application.

Provides endpoints for querying detected anomaly windows and their full
lifecycle disposition, including detection metadata, Bedrock analysis
outcome, alert decision, and suppression reason.

Endpoints:
    GET /anomalies      — List anomaly windows with optional filters.
    GET /anomalies/{id} — Retrieve a single anomaly window by ID.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import AnomalyWindow
from app.models.schemas import AnomalyWindowResponse

router = APIRouter()


@router.get("/anomalies", response_model=List[AnomalyWindowResponse])
def list_anomalies(
    service: Optional[str] = Query(default=None, description="Filter by service name."),
    status: Optional[str] = Query(default=None, description="Filter by anomaly lifecycle status."),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
    page_size: int = Query(default=20, ge=1, le=100, description="Number of records per page."),
    db: Session = Depends(get_db),
) -> List[AnomalyWindowResponse]:
    """List anomaly window records with optional filtering and pagination.

    Args:
        service: Optional service name to filter results.
        status: Optional anomaly lifecycle status to filter results.
        page: Page number for pagination (default 1).
        page_size: Number of records per page (default 20, max 100).
        db: Database session injected via FastAPI dependency.

    Returns:
        List of AnomalyWindowResponse objects matching the applied filters.
    """
    query = db.query(AnomalyWindow)

    if service is not None:
        query = query.filter(AnomalyWindow.service == service)

    if status is not None:
        query = query.filter(AnomalyWindow.status == status)

    query = query.order_by(AnomalyWindow.created_at.desc())

    offset = (page - 1) * page_size
    results = query.offset(offset).limit(page_size).all()

    return results


@router.get("/anomalies/{id}", response_model=AnomalyWindowResponse)
def get_anomaly(
    id: int,
    db: Session = Depends(get_db),
) -> AnomalyWindowResponse:
    """Retrieve a single anomaly window record by its ID.

    Args:
        id: The primary key of the anomaly window to retrieve.
        db: Database session injected via FastAPI dependency.

    Returns:
        The AnomalyWindowResponse for the requested anomaly window.

    Raises:
        HTTPException: 404 if no anomaly window exists with the given ID.
    """
    anomaly = db.query(AnomalyWindow).filter(AnomalyWindow.id == id).first()

    if anomaly is None:
        raise HTTPException(status_code=404, detail="Anomaly window not found")

    return anomaly
