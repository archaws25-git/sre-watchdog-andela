"""Alert service for the SRE Watchdog application.

Handles severity mapping, cooldown checking, webhook dispatch with retry,
and alert record persistence. Every confirmed anomaly that reaches this
service results in an ``AlertRecord`` being created — including suppressed
dispatches — for a full audit trail.

Typical usage::

    from app.services.alert_service import dispatch, map_severity, is_in_cooldown
    from app.database import get_db
    from app.config import get_settings

    severity = map_severity(anomaly_window.anomaly_score)
    if not is_in_cooldown(anomaly_window.service, db, settings):
        dispatch(anomaly_window, db, settings)
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.db_models import AlertRecord, AnomalyWindow
from app.models.schemas import AlertDispatchStatus, SeverityLabel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_DISPATCH_RETRIES: int = 3
DISPATCH_TIMEOUT_SECONDS: int = 10


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------


def map_severity(score: float) -> SeverityLabel:
    """Map a numeric anomaly score to a severity label.

    Uses four bands to classify the score:
        - 0.00–0.39 → LOW
        - 0.40–0.69 → MEDIUM
        - 0.70–0.89 → HIGH
        - 0.90–1.00 → CRITICAL

    Args:
        score: Anomaly score between 0.0 and 1.0 inclusive.

    Returns:
        The corresponding ``SeverityLabel`` enum value.
    """
    if score <= 0.39:
        return SeverityLabel.LOW
    elif score <= 0.69:
        return SeverityLabel.MEDIUM
    elif score <= 0.89:
        return SeverityLabel.HIGH
    return SeverityLabel.CRITICAL


# ---------------------------------------------------------------------------
# Cooldown check
# ---------------------------------------------------------------------------


def is_in_cooldown(service: str, db: Session, settings: Settings) -> bool:
    """Check if the service has an alerted anomaly within the cooldown window.

    Queries the ``anomaly_windows`` table for records with ``status == 'alerted'``
    and ``updated_at >= now - ALERT_COOLDOWN_MINUTES``.

    Args:
        service: Name of the service to check cooldown for.
        db: An active SQLAlchemy session for database queries.
        settings: Application settings containing ``ALERT_COOLDOWN_MINUTES``.

    Returns:
        ``True`` if the service is within the cooldown window (an alert was
        recently dispatched), ``False`` otherwise.
    """
    cutoff = (
        datetime.utcnow() - timedelta(minutes=settings.ALERT_COOLDOWN_MINUTES)
    ).isoformat()

    result = (
        db.query(AnomalyWindow)
        .filter(
            AnomalyWindow.service == service,
            AnomalyWindow.status == "alerted",
            AnomalyWindow.updated_at >= cutoff,
        )
        .first()
    )

    return result is not None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch(
    anomaly_window: AnomalyWindow,
    db: Session,
    settings: Settings,
) -> Optional[AlertRecord]:
    """Dispatch a webhook alert for a confirmed anomaly window.

    Checks cooldown status first. If cooldown is active, creates a suppressed
    ``AlertRecord`` without making an HTTP POST. If no cooldown, builds the
    webhook payload, POSTs to ``WEBHOOK_URL`` with retry (up to 3 attempts,
    10s timeout each), and persists an ``AlertRecord`` with the outcome.

    On successful dispatch, updates the ``AnomalyWindow`` status to ``alerted``
    and links the alert record.

    Args:
        anomaly_window: The ``AnomalyWindow`` ORM instance to dispatch an
            alert for. Must have ``anomaly_score`` and ``ai_summary`` populated.
        db: An active SQLAlchemy session for database operations.
        settings: Application settings containing ``WEBHOOK_URL`` and
            ``ALERT_COOLDOWN_MINUTES``.

    Returns:
        The created ``AlertRecord`` instance, or ``None`` if an unexpected
        error prevented record creation.
    """
    severity = map_severity(anomaly_window.anomaly_score)
    payload = _build_payload(anomaly_window, severity)
    payload_json = json.dumps(payload)

    # Check cooldown — suppress if active
    if is_in_cooldown(anomaly_window.service, db, settings):
        alert_record = AlertRecord(
            anomaly_id=anomaly_window.id,
            webhook_url=settings.WEBHOOK_URL,
            payload=payload_json,
            http_status=None,
            dispatch_status=AlertDispatchStatus.SUPPRESSED.value,
            severity=severity.value,
        )
        db.add(alert_record)

        # Update anomaly window status to suppressed
        anomaly_window.status = "suppressed"
        anomaly_window.suppression_reason = "cooldown_active"
        anomaly_window.updated_at = datetime.utcnow().isoformat()

        db.commit()
        db.refresh(alert_record)

        logger.info(
            json.dumps({
                "event": "alert_dispatch_suppressed",
                "anomaly_id": anomaly_window.id,
                "service": anomaly_window.service,
                "severity": severity.value,
                "reason": "cooldown_active",
            })
        )

        return alert_record

    # No cooldown — attempt webhook dispatch with retry
    http_status: Optional[int] = None
    dispatch_status = AlertDispatchStatus.FAILED

    for attempt in range(MAX_DISPATCH_RETRIES):
        try:
            response = httpx.post(
                url=settings.WEBHOOK_URL,
                json=payload,
                timeout=DISPATCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            http_status = response.status_code
            dispatch_status = AlertDispatchStatus.SENT

            logger.info(
                json.dumps({
                    "event": "alert_dispatch_success",
                    "anomaly_id": anomaly_window.id,
                    "service": anomaly_window.service,
                    "severity": severity.value,
                    "http_status": http_status,
                    "attempt": attempt + 1,
                })
            )
            break

        except (httpx.HTTPStatusError, httpx.HTTPError) as exc:
            logger.warning(
                json.dumps({
                    "event": "alert_dispatch_retry",
                    "anomaly_id": anomaly_window.id,
                    "service": anomaly_window.service,
                    "attempt": attempt + 1,
                    "max_retries": MAX_DISPATCH_RETRIES,
                    "error": str(exc),
                })
            )

            if attempt == MAX_DISPATCH_RETRIES - 1:
                logger.error(
                    json.dumps({
                        "event": "alert_dispatch_failed",
                        "anomaly_id": anomaly_window.id,
                        "service": anomaly_window.service,
                        "severity": severity.value,
                        "total_attempts": MAX_DISPATCH_RETRIES,
                        "error": str(exc),
                    })
                )
                dispatch_status = AlertDispatchStatus.FAILED

    # Persist alert record
    alert_record = AlertRecord(
        anomaly_id=anomaly_window.id,
        webhook_url=settings.WEBHOOK_URL,
        payload=payload_json,
        http_status=http_status,
        dispatch_status=dispatch_status.value,
        severity=severity.value,
    )
    db.add(alert_record)

    # Update anomaly window on successful dispatch
    if dispatch_status == AlertDispatchStatus.SENT:
        anomaly_window.status = "alerted"
        anomaly_window.updated_at = datetime.utcnow().isoformat()

    db.commit()
    db.refresh(alert_record)

    # Link alert record to anomaly window
    if dispatch_status == AlertDispatchStatus.SENT:
        anomaly_window.alert_id = alert_record.id
        db.commit()

    return alert_record


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_payload(
    anomaly_window: AnomalyWindow,
    severity: SeverityLabel,
) -> dict:
    """Build the webhook alert payload from an anomaly window.

    Constructs the JSON-serializable dictionary matching the webhook payload
    schema defined in the design document (Section 7.2).

    Args:
        anomaly_window: The ``AnomalyWindow`` ORM instance.
        severity: The computed severity label for this anomaly.

    Returns:
        A dictionary representing the webhook payload.
    """
    return {
        "alert_timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "anomaly_id": anomaly_window.id,
        "service": anomaly_window.service,
        "window_start": anomaly_window.window_start,
        "window_end": anomaly_window.window_end,
        "error_rate": anomaly_window.error_rate,
        "anomaly_score": anomaly_window.anomaly_score,
        "severity": severity.value,
        "ai_summary": anomaly_window.ai_summary,
    }
