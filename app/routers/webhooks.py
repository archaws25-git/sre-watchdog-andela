"""Webhook echo router for the SRE Watchdog API.

Provides an endpoint that accepts any JSON payload, persists it to the
database for audit purposes, and echoes it back. Used for testing and
verifying webhook dispatch without requiring an external target.

Typical usage::

    from app.routers.webhooks import router
    app.include_router(router)
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import WebhookEchoLog
from app.models.schemas import WebhookEchoResponse

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/echo", response_model=WebhookEchoResponse)
async def webhook_echo(
    request: Request,
    db: Session = Depends(get_db),
) -> WebhookEchoResponse:
    """Receive and persist any JSON payload, echoing it back.

    Accepts any valid JSON body, stores the raw payload in the
    webhook_echo_log table for audit and testing purposes, and returns
    the payload along with the server-side received timestamp.

    Args:
        request: The incoming FastAPI request (used to read raw JSON body).
        db: SQLAlchemy database session (injected).

    Returns:
        WebhookEchoResponse containing the received_at timestamp and
        the echoed payload.
    """
    body = await request.json()
    payload_text = json.dumps(body)

    echo_record = WebhookEchoLog(payload=payload_text)
    db.add(echo_record)
    db.commit()
    db.refresh(echo_record)

    return WebhookEchoResponse(
        received_at=datetime.fromisoformat(echo_record.received_at),
        payload=body,
    )
