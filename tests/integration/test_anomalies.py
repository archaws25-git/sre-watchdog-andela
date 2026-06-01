"""Integration tests for the anomalies endpoints."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models.db_models import AnomalyWindow


@pytest.mark.integration
class TestAnomaliesEndpoint:
    """Tests for GET /anomalies and GET /anomalies/{id}."""

    def _create_anomaly(self, test_client, test_db):
        """Helper to create an anomaly window directly in the DB."""
        now = datetime.now(timezone.utc)
        window = AnomalyWindow(
            service="api-gateway",
            window_start=(now - timedelta(minutes=5)).isoformat(),
            window_end=now.isoformat(),
            error_rate=0.5,
            anomaly_score=0.85,
            status="confirmed",
            ai_summary="Test anomaly",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        test_db.add(window)
        test_db.commit()
        test_db.refresh(window)
        return window

    def test_list_anomalies_returns_200(self, test_client, test_db):
        """GET /anomalies returns HTTP 200 with a list."""
        self._create_anomaly(test_client, test_db)
        response = test_client.get("/anomalies")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) >= 1

    def test_list_anomalies_with_service_filter(self, test_client, test_db):
        """GET /anomalies?service=api-gateway filters correctly."""
        self._create_anomaly(test_client, test_db)
        response = test_client.get("/anomalies", params={"service": "api-gateway"})
        assert response.status_code == 200
        body = response.json()
        assert all(a["service"] == "api-gateway" for a in body)

    def test_get_anomaly_by_id_returns_200(self, test_client, test_db):
        """GET /anomalies/{id} returns the anomaly record."""
        window = self._create_anomaly(test_client, test_db)
        response = test_client.get(f"/anomalies/{window.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == window.id
        assert body["service"] == "api-gateway"

    def test_get_anomaly_not_found_returns_404(self, test_client):
        """GET /anomalies/{id} returns 404 for non-existent ID."""
        response = test_client.get("/anomalies/99999")
        assert response.status_code == 404
