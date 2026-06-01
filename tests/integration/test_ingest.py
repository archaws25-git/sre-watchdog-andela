"""Integration tests for the log ingestion endpoint."""

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
class TestIngestEndpoint:
    """Tests for POST /logs/ingest."""

    def test_valid_batch_of_10_entries(self, test_client):
        """Valid batch of 10 entries returns HTTP 200 with accepted=10, rejected=0."""
        entries = [_make_entry(message=f"Error {i}") for i in range(10)]
        response = test_client.post("/logs/ingest", json={"entries": entries})

        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] == 10
        assert body["rejected"] == 0

    def test_entries_with_missing_fields_returns_422(self, test_client):
        """Entries with missing required fields return HTTP 422."""
        entries = [{"service": "api-gateway", "level": "ERROR"}]  # missing timestamp and message
        response = test_client.post("/logs/ingest", json={"entries": entries})

        assert response.status_code == 422

    def test_empty_batch_returns_200_with_zero_counts(self, test_client):
        """Empty batch returns HTTP 200 with accepted=0, rejected=0."""
        response = test_client.post("/logs/ingest", json={"entries": []})

        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] == 0
        assert body["rejected"] == 0

    def test_batch_exceeding_limit_returns_413(self, test_client):
        """Batch of 501 entries returns HTTP 413 with limit and received."""
        entries = [_make_entry(message=f"Error {i}") for i in range(501)]
        response = test_client.post("/logs/ingest", json={"entries": entries})

        assert response.status_code == 413
        body = response.json()
        assert "limit" in body
        assert "received" in body
        assert body["limit"] == 500
        assert body["received"] == 501
