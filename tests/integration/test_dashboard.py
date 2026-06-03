"""Integration tests for the dashboard endpoint."""

import pytest


@pytest.mark.integration
class TestDashboardEndpoint:
    """Tests for GET /dashboard."""

    def test_dashboard_returns_200_html(self, test_client):
        """GET /dashboard returns HTTP 200 with HTML content."""
        response = test_client.get("/dashboard")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_dashboard_contains_chart_js(self, test_client):
        """Dashboard HTML includes Chart.js script tag."""
        response = test_client.get("/dashboard")
        assert "chart.js" in response.text.lower() or "Chart" in response.text

    def test_dashboard_contains_metrics(self, test_client):
        """Dashboard HTML includes metrics section."""
        response = test_client.get("/dashboard")
        assert "Logs Ingested" in response.text or "metric-logs" in response.text
