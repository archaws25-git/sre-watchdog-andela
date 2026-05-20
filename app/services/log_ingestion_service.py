"""Log ingestion service for the SRE Watchdog application.

Handles batch validation and persistence of log entries to the SQLite
database. Each ingest call is wrapped in a single database transaction
and emits structured JSON logs recording accepted/rejected counts.

Typical usage::

    from app.services.log_ingestion_service import ingest_batch
    from app.database import get_db
    from app.config import get_settings

    response = ingest_batch(entries=request.entries, db=next(get_db()), settings=get_settings())
"""

import json
import logging
from typing import List

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models.db_models import LogEntry
from app.models.schemas import IngestResponse, LogEntryCreate

logger = logging.getLogger(__name__)


def ingest_batch(
    entries: List[LogEntryCreate],
    db: Session,
    settings: Settings,
) -> IngestResponse:
    """Validate and persist a batch of log entries in a single DB transaction.

    Each entry is validated against the ``LogEntryCreate`` schema. Valid
    entries are persisted atomically; invalid entries are counted and their
    error descriptions collected. A structured JSON log line is emitted for
    every call regardless of outcome.

    Args:
        entries: List of ``LogEntryCreate`` objects to ingest. Entries are
            expected to be pre-validated by Pydantic at the router level,
            but this function performs an additional safety check.
        db: An active SQLAlchemy session for database operations.
        settings: Application settings instance (used for future extensibility
            such as batch size enforcement at the service layer).

    Returns:
        An ``IngestResponse`` containing the count of accepted entries,
        rejected entries, and a list of human-readable error descriptions.

    Raises:
        No exceptions are raised to the caller. Database errors during
        commit are caught, logged, and reflected in the response counts.
    """
    accepted = 0
    rejected = 0
    errors: List[str] = []
    db_entries: List[LogEntry] = []

    # Enforce maximum batch size at the service layer as a safety net
    max_batch_size = settings.MAX_INGEST_BATCH_SIZE
    if len(entries) > max_batch_size:
        logger.warning(
            json.dumps({
                "event": "ingest_batch_oversized",
                "limit": max_batch_size,
                "received": len(entries),
            })
        )
        return IngestResponse(
            accepted=0,
            rejected=len(entries),
            errors=[
                f"Batch size {len(entries)} exceeds maximum of {max_batch_size}"
            ],
        )

    for index, entry in enumerate(entries):
        try:
            # Re-validate to catch any edge cases not caught upstream
            validated = LogEntryCreate.model_validate(entry.model_dump())

            db_entry = LogEntry(
                timestamp=validated.timestamp.isoformat(),
                service=validated.service,
                level=validated.level.value,
                message=validated.message,
            )
            db_entries.append(db_entry)
        except ValidationError as exc:
            rejected += 1
            error_details = "; ".join(
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in exc.errors()
            )
            errors.append(f"Entry {index}: {error_details}")

    # Persist all valid entries in a single transaction
    if db_entries:
        try:
            db.add_all(db_entries)
            db.commit()
            accepted = len(db_entries)
        except SQLAlchemyError as exc:
            db.rollback()
            rejected += len(db_entries)
            accepted = 0
            errors.append(f"Database error: {str(exc)}")
            logger.error(
                json.dumps({
                    "event": "ingest_batch_db_error",
                    "error": str(exc),
                    "entries_affected": len(db_entries),
                })
            )

    # Emit structured JSON log for every ingest call
    logger.info(
        json.dumps({
            "event": "ingest_batch_complete",
            "accepted": accepted,
            "rejected": rejected,
            "total_submitted": len(entries),
        })
    )

    return IngestResponse(
        accepted=accepted,
        rejected=rejected,
        errors=errors,
    )
