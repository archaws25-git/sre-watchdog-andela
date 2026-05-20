"""Health check router for the SRE Watchdog API.

Provides a lightweight health endpoint that verifies database connectivity
and reports the cached Bedrock integration status. Used by infrastructure
health checks and monitoring systems.

Typical usage::

    from app.routers.health import router
    app.include_router(router)
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.schemas import BedrockHealthDetail, BedrockHealthStatus, HealthResponse

router = APIRouter(tags=["health"])

DEFAULT_BEDROCK_HEALTH = {
    "status": "unknown",
    "last_checked_at": None,
    "message": "No inference calls made yet",
}


@router.get("/health", response_model=HealthResponse)
def get_health(
    request: Request,
    db: Session = Depends(get_db),
) -> HealthResponse | JSONResponse:
    """Check system health including database and Bedrock status.

    Performs a lightweight SELECT 1 query against the database to verify
    connectivity. Returns HTTP 200 if the database is reachable, or
    HTTP 503 if the check fails. Includes the cached Bedrock health
    status from app.state.

    Args:
        request: The incoming FastAPI request (used to access app.state).
        db: SQLAlchemy database session (injected).

    Returns:
        HealthResponse with HTTP 200 if healthy, or a 503 JSONResponse
        if the database is unreachable.
    """
    bedrock_health_raw = getattr(
        request.app.state, "bedrock_health", DEFAULT_BEDROCK_HEALTH
    )

    bedrock_detail = BedrockHealthDetail(
        status=BedrockHealthStatus(bedrock_health_raw.get("status", "unknown")),
        last_checked_at=_parse_datetime(bedrock_health_raw.get("last_checked_at")),
        message=bedrock_health_raw.get("message", "No inference calls made yet"),
    )

    # Check database connectivity
    try:
        db.execute(text("SELECT 1"))
        database_status = "ok"
    except Exception:
        database_status = "unreachable"

    if database_status == "unreachable":
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "database": "unreachable",
                "bedrock": bedrock_detail.model_dump(mode="json"),
            },
        )

    return HealthResponse(
        status="ok",
        database="ok",
        bedrock=bedrock_detail,
    )


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    """Parse a datetime value from string or pass through datetime objects.

    Args:
        value: An ISO 8601 string, a datetime object, or None.

    Returns:
        A datetime object, or None if the input is None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
