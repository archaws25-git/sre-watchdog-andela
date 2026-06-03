"""Integration tests for the health endpoint."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.main import app
from app.routers.health import _parse_datetime


@pytest.mark.integration
class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200_with_database_ok(self, test_client):
        """GET /health returns HTTP 200 with database=ok on fresh start."""
        app.state.bedrock_health = {
            "status": "unknown",
            "last_checked_at": None,
            "message": "No inference calls made yet",
        }

        response = test_client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["database"] == "ok"
        assert body["bedrock"]["status"] == "unknown"

    def test_health_response_includes_all_required_fields(self, test_client):
        """Health response includes all required fields."""
        app.state.bedrock_health = {
            "status": "unknown",
            "last_checked_at": None,
            "message": "No inference calls made yet",
        }

        response = test_client.get("/health")

        assert response.status_code == 200
        body = response.json()

        assert "status" in body
        assert "database" in body
        assert "bedrock" in body

        bedrock = body["bedrock"]
        assert "status" in bedrock
        assert "last_checked_at" in bedrock
        assert "message" in bedrock

        assert body["status"] == "ok"
        assert bedrock["status"] == "unknown"
        assert bedrock["last_checked_at"] is None
        assert bedrock["message"] == "No inference calls made yet"

    def test_health_returns_503_when_db_unreachable(self, test_client):
        """GET /health returns HTTP 503 when database is unreachable."""
        app.state.bedrock_health = {
            "status": "unknown",
            "last_checked_at": None,
            "message": "No inference calls made yet",
        }

        # Patch the db session's execute to raise an exception
        with patch("app.routers.health.get_db") as mock_get_db:
            mock_session = MagicMock()
            mock_session.execute.side_effect = Exception("DB connection lost")
            mock_get_db.return_value = iter([mock_session])

            # Need to override the dependency
            from app.database import get_db as real_get_db

            def _broken_db():
                mock_db = MagicMock()
                mock_db.execute.side_effect = Exception("DB connection lost")
                yield mock_db

            app.dependency_overrides[real_get_db] = _broken_db

            response = test_client.get("/health")

            # The test_client fixture will clean up overrides

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["database"] == "unreachable"

    def test_health_with_bedrock_status_ok(self, test_client):
        """GET /health reflects bedrock status 'ok' with last_checked_at."""
        now = datetime.now(timezone.utc)
        app.state.bedrock_health = {
            "status": "ok",
            "last_checked_at": now,
            "message": "Last inference call succeeded",
        }

        response = test_client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["bedrock"]["status"] == "ok"
        assert body["bedrock"]["last_checked_at"] is not None
        assert body["bedrock"]["message"] == "Last inference call succeeded"


@pytest.mark.unit
class TestParseDatetime:
    """Tests for the _parse_datetime helper function."""

    def test_none_returns_none(self):
        assert _parse_datetime(None) is None

    def test_datetime_object_passes_through(self):
        now = datetime.now(timezone.utc)
        assert _parse_datetime(now) is now

    def test_iso_string_parsed_correctly(self):
        result = _parse_datetime("2026-05-20T12:00:00")
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.month == 5
        assert result.day == 20
