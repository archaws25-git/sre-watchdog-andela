"""Integration tests for the health endpoint."""

import pytest

from app.main import app


@pytest.mark.integration
class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200_with_database_ok(self, test_client):
        """GET /health returns HTTP 200 with database=ok on fresh start."""
        # Reset bedrock_health to the initial state set by conftest
        # (lifespan may override it if no AWS creds are found)
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
        # Reset bedrock_health to the initial state
        app.state.bedrock_health = {
            "status": "unknown",
            "last_checked_at": None,
            "message": "No inference calls made yet",
        }

        response = test_client.get("/health")

        assert response.status_code == 200
        body = response.json()

        # Top-level fields
        assert "status" in body
        assert "database" in body
        assert "bedrock" in body

        # Bedrock nested fields
        bedrock = body["bedrock"]
        assert "status" in bedrock
        assert "last_checked_at" in bedrock
        assert "message" in bedrock

        # Verify values on fresh start
        assert body["status"] == "ok"
        assert bedrock["status"] == "unknown"
        assert bedrock["last_checked_at"] is None
        assert bedrock["message"] == "No inference calls made yet"
