"""Integration tests for the alerts endpoint."""

import json
from datetime import datetime, timezone, timedelta

import pytest

from app.models.db_models import AlertRecord, AnomalyWindow


@pytest.mark.integration
class TestAlertsEndpoint:
    """Tests for GET /alerts."""

    def test_alerts_returns_200_empty_list(self, test_client):
        """GET /alerts returns HTTP 200 with empty list on fresh start."""
        response = test_client.get("/alerts")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 0

    def test_alerts_returns_records_after_creation(self, test_client, test_db):
        """GET /alerts returns alert records after they are created."""
        now = datetime.now(timezone.utc)
        # Create an anomaly window first
        window = AnomalyWindow(
            service="api-gateway",
            window_start=(now - timedelta(minutes=5)).isoformat(),
            window_end=now.isoformat(),
            error_rate=0.5,
            anomaly_score=0.85,
            status="alerted",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        test_db.add(window)
        test_db.commit()
        test_db.refresh(window)

        # Create an alert record
        alert = AlertRecord(
            anomaly_id=window.id,
            webhook_url="http://test.example.com",
            payload=json.dumps({"test": "payload"}),
            http_status=200,
            dispatch_status="sent",
            severity="HIGH",
        )
        test_db.add(alert)
        test_db.commit()

        response = test_client.get("/alerts")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["dispatch_status"] == "sent"
        assert body[0]["severity"] == "HIGH"
