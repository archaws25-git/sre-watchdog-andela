"""Integration tests for the logs retrieval endpoint."""

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ingest_entries(test_client, entries: list) -> None:
    """Helper to ingest a batch of entries."""
    response = test_client.post("/logs/ingest", json={"entries": entries})
    assert response.status_code == 200


def _make_entry(
    service: str = "api-gateway",
    level: str = "ERROR",
    message: str = "Test error",
    timestamp: str | None = None,
) -> dict:
    """Create a valid log entry payload."""
    return {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "service": service,
        "level": level,
        "message": message,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLogsEndpoint:
    """Tests for GET /logs."""

    def test_pagination_envelope_fields_present(self, test_client):
        """Response contains all pagination envelope fields."""
        # Ingest some entries first
        entries = [_make_entry(message=f"Log {i}") for i in range(5)]
        _ingest_entries(test_client, entries)

        response = test_client.get("/logs")
        assert response.status_code == 200

        body = response.json()
        assert "total_count" in body
        assert "page" in body
        assert "page_size" in body
        assert "has_more" in body
        assert "data" in body
        assert body["total_count"] == 5
        assert body["page"] == 1

    def test_custom_page_and_page_size(self, test_client):
        """Custom page and page_size parameters return correct slice."""
        entries = [_make_entry(message=f"Log {i}") for i in range(15)]
        _ingest_entries(test_client, entries)

        response = test_client.get("/logs", params={"page": 2, "page_size": 5})
        assert response.status_code == 200

        body = response.json()
        assert body["page"] == 2
        assert body["page_size"] == 5
        assert len(body["data"]) == 5
        assert body["has_more"] is True  # 15 total, page 2 of 5 = still more

    def test_service_filter(self, test_client):
        """Service filter returns only matching entries."""
        entries = [
            _make_entry(service="api-gateway", message="Gateway log"),
            _make_entry(service="auth-service", message="Auth log"),
            _make_entry(service="api-gateway", message="Gateway log 2"),
        ]
        _ingest_entries(test_client, entries)

        response = test_client.get("/logs", params={"service": "auth-service"})
        assert response.status_code == 200

        body = response.json()
        assert body["total_count"] == 1
        assert all(entry["service"] == "auth-service" for entry in body["data"])

    def test_level_filter(self, test_client):
        """Level filter returns only matching entries."""
        entries = [
            _make_entry(level="ERROR", message="Error log"),
            _make_entry(level="INFO", message="Info log"),
            _make_entry(level="ERROR", message="Error log 2"),
        ]
        _ingest_entries(test_client, entries)

        response = test_client.get("/logs", params={"level": "INFO"})
        assert response.status_code == 200

        body = response.json()
        assert body["total_count"] == 1
        assert all(entry["level"] == "INFO" for entry in body["data"])
