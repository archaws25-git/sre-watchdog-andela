"""Integration tests for the analyze endpoint."""

import os
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_analyze_request(
    service: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    """Create a valid analyze request payload."""
    now = datetime.now(timezone.utc)
    return {
        "service": service,
        "start_time": start_time or (now - timedelta(hours=1)).isoformat(),
        "end_time": end_time or now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAnalyzeEndpoint:
    """Tests for POST /analyze and GET /analyze/{job_id}."""

    def test_post_analyze_returns_202_with_job_id(self, test_client, mock_bedrock):
        """POST /analyze returns HTTP 202 with job_id and status=pending."""
        payload = _make_analyze_request(service="api-gateway")
        response = test_client.post("/analyze", json=payload)

        assert response.status_code == 202
        body = response.json()
        assert "job_id" in body
        assert body["status"] == "pending"

    def test_get_analyze_job_returns_status(self, test_client, mock_bedrock):
        """GET /analyze/{job_id} returns current job status."""
        payload = _make_analyze_request(service="api-gateway")
        create_response = test_client.post("/analyze", json=payload)
        job_id = create_response.json()["job_id"]

        response = test_client.get(f"/analyze/{job_id}")
        assert response.status_code == 200

        body = response.json()
        assert body["job_id"] == job_id
        assert body["status"] in ["pending", "running", "completed", "failed"]

    def test_get_analyze_unknown_id_returns_404(self, test_client):
        """GET /analyze/{unknown_id} returns HTTP 404."""
        response = test_client.get("/analyze/nonexistent-job-id")
        assert response.status_code == 404


@pytest.mark.live_bedrock
class TestAnalyzeLiveBedrock:
    """Tests that use real AWS Bedrock credentials (slow, costs money)."""

    @pytest.fixture(autouse=True)
    def _skip_without_aws_creds(self):
        """Skip if AWS credentials are not available."""
        import boto3

        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials is None:
            pytest.skip("AWS credentials not available")

    def test_live_analyze_completes(self, test_client):
        """Live Bedrock analysis completes without error."""
        # Ingest some log entries first
        entries = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": "api-gateway",
                "level": "ERROR",
                "message": f"Connection timeout error {i}",
            }
            for i in range(10)
        ]
        test_client.post("/logs/ingest", json={"entries": entries})

        now = datetime.now(timezone.utc)
        payload = {
            "service": "api-gateway",
            "start_time": (now - timedelta(hours=1)).isoformat(),
            "end_time": now.isoformat(),
        }
        response = test_client.post("/analyze", json=payload)
        assert response.status_code == 202
