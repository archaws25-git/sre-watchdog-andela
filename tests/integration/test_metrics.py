"""Integration tests for the metrics endpoint."""

from datetime import datetime, timezone

import pytest


@pytest.mark.integration
class TestMetricsEndpoint:
    """Tests for GET /metrics."""

    def test_metrics_returns_200_with_all_counters_at_zero(self, test_client):
        """GET /metrics returns HTTP 200 with all six counters at 0 on fresh start."""
        response = test_client.get("/metrics")

        assert response.status_code == 200
        body = response.json()

        assert body["total_logs_ingested"] == 0
        assert body["total_anomalies_detected"] == 0
        assert body["total_alerts_dispatched"] == 0
        assert body["total_failed_alerts"] == 0
        assert body["total_analysis_failed"] == 0
        assert body["total_cooldown_suppressed"] == 0

    def test_counters_increment_after_ingesting_logs(self, test_client):
        """total_logs_ingested increases after ingesting log entries."""
        # Ingest some entries
        entries = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": "api-gateway",
                "level": "ERROR",
                "message": f"Test error {i}",
            }
            for i in range(5)
        ]
        ingest_response = test_client.post("/logs/ingest", json={"entries": entries})
        assert ingest_response.status_code == 200

        # Check metrics
        response = test_client.get("/metrics")
        assert response.status_code == 200
        body = response.json()

        assert body["total_logs_ingested"] == 5
