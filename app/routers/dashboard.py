"""Dashboard router for the SRE Watchdog application.

Serves the Jinja2 server-side rendered dashboard at ``GET /dashboard``.
The initial page load queries the database via ``dashboard_service`` methods
and passes the resulting data to the template for Chart.js rendering and
table population. Subsequent updates are handled client-side via JavaScript
fetch calls to the internal API endpoints.

Typical usage::

    from app.routers.dashboard import router
    app.include_router(router)
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import AlertRecord, AnomalyWindow, LogEntry
from app.services.dashboard_service import (
    get_chart_data,
    get_recent_alerts,
    get_recent_anomalies,
)

# ---------------------------------------------------------------------------
# Router and template setup
# ---------------------------------------------------------------------------

router = APIRouter(tags=["dashboard"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@router.get("/dashboard")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    """Render the SRE Watchdog dashboard page.

    Queries the database for chart data, recent anomalies, recent alerts,
    and aggregate metrics, then passes all data to the Jinja2 template
    for server-side rendering.

    Args:
        request: The incoming FastAPI request (required by Jinja2Templates).
        db: SQLAlchemy database session (injected).

    Returns:
        A Jinja2 TemplateResponse rendering ``dashboard.html`` with the
        full dashboard context.
    """
    # Chart data for the time-series line chart
    chart_data = get_chart_data(db)

    # Recent anomalies and alerts for the tables
    recent_anomalies = get_recent_anomalies(db, limit=20)
    recent_alerts = get_recent_alerts(db, limit=20)

    # Metrics bar counters
    total_logs_ingested = db.query(func.count(LogEntry.id)).scalar() or 0
    total_anomalies = db.query(func.count(AnomalyWindow.id)).scalar() or 0
    total_alerts = (
        db.query(func.count(AlertRecord.id))
        .filter(AlertRecord.dispatch_status == "sent")
        .scalar()
        or 0
    )
    total_failed = (
        db.query(func.count(AlertRecord.id))
        .filter(AlertRecord.dispatch_status == "failed")
        .scalar()
        or 0
    )

    context = {
        "request": request,
        "chart_data": chart_data,
        "recent_anomalies": recent_anomalies,
        "recent_alerts": recent_alerts,
        "total_logs_ingested": total_logs_ingested,
        "total_anomalies": total_anomalies,
        "total_alerts": total_alerts,
        "total_failed": total_failed,
    }

    return templates.TemplateResponse("dashboard.html", context)
