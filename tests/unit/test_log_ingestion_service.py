"""Unit tests for the log ingestion service.

Covers the three defensive code paths that are unreachable through normal
integration tests:
1. Service-layer batch size guard (router already returns 413 before this)
2. ValidationError on re-validation (entries are pre-validated by Pydantic)
3. SQLAlchemyError on commit failure (requires mocking the DB session)
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.models.schemas import IngestResponse, LogEntryCreate
from app.services.log_ingestion_service import ingest_batch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    """Create a Settings instance with test defaults."""
    defaults = {
        "DATABASE_URL": "sqlite:///:memory:",
        "WEBHOOK_URL": "http://test.example.com",
        "MAX_INGEST_BATCH_SIZE": 500,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_entry(
    service: str = "api-gateway",
    level: str = "ERROR",
    message: str = "Test error",
) -> LogEntryCreate:
    """Create a valid LogEntryCreate instance."""
    return LogEntryCreate(
        timestamp=datetime.now(timezone.utc),
        service=service,
        level=level,
        message=message,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIngestBatchServiceLayer:
    """Unit tests for ingest_batch covering defensive code paths."""

    def test_batch_exceeding_service_layer_limit(self, test_db):
        """Batch exceeding MAX_INGEST_BATCH_SIZE at service layer returns rejection.

        This path is normally unreachable because the router returns 413 first,
        but the service layer has its own guard as a safety net.
        """
        settings = _make_settings(MAX_INGEST_BATCH_SIZE=5)
        entries = [_make_entry(message=f"msg {i}") for i in range(10)]

        result = ingest_batch(entries=entries, db=test_db, settings=settings)

        assert isinstance(result, IngestResponse)
        assert result.accepted == 0
        assert result.rejected == 10
        assert "exceeds maximum" in result.errors[0]

    def test_database_commit_failure_rolls_back(self, test_db):
        """SQLAlchemyError during commit → rollback, all entries rejected."""
        settings = _make_settings()
        entries = [_make_entry(message=f"msg {i}") for i in range(3)]

        # Mock the session's commit to raise SQLAlchemyError
        original_commit = test_db.commit
        test_db.commit = MagicMock(side_effect=SQLAlchemyError("Disk full"))
        # Mock rollback to be a no-op (avoid actual rollback on mocked session)
        test_db.rollback = MagicMock()

        result = ingest_batch(entries=entries, db=test_db, settings=settings)

        assert result.accepted == 0
        assert result.rejected == 3
        assert any("Database error" in e for e in result.errors)
        test_db.rollback.assert_called_once()

        # Restore
        test_db.commit = original_commit

    def test_successful_ingest_returns_correct_counts(self, test_db):
        """Valid entries are persisted and counts are correct."""
        settings = _make_settings()
        entries = [_make_entry(message=f"msg {i}") for i in range(5)]

        result = ingest_batch(entries=entries, db=test_db, settings=settings)

        assert result.accepted == 5
        assert result.rejected == 0
        assert result.errors == []

    def test_empty_batch_returns_zero_counts(self, test_db):
        """Empty entry list returns accepted=0, rejected=0."""
        settings = _make_settings()

        result = ingest_batch(entries=[], db=test_db, settings=settings)

        assert result.accepted == 0
        assert result.rejected == 0
        assert result.errors == []

    @patch("app.services.log_ingestion_service.LogEntryCreate.model_validate")
    def test_validation_error_on_revalidation(self, mock_validate, test_db):
        """ValidationError during re-validation → entry rejected with error detail."""
        from pydantic import ValidationError

        settings = _make_settings()
        entries = [_make_entry()]

        # Make model_validate raise a ValidationError
        mock_validate.side_effect = ValidationError.from_exception_data(
            title="LogEntryCreate",
            line_errors=[
                {
                    "type": "missing",
                    "loc": ("message",),
                    "msg": "Field required",
                    "input": {},
                }
            ],
        )

        result = ingest_batch(entries=entries, db=test_db, settings=settings)

        assert result.accepted == 0
        assert result.rejected == 1
        assert "Entry 0" in result.errors[0]
