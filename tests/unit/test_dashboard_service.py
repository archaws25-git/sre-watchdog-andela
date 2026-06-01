"""Unit tests for the dashboard service module.

Covers the data-dependent code paths that require log entries, anomaly
windows, and alert records to exist in the database.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models.db_models import AlertRecord, AnomalyWindow, LogEntry
from app.services.dashboard_service import (
    _compute_severity_label,
    get_chart_data,
    get_recent_alerts,
    get_recent_anomalies,
)


# ---------------------------------------------------------------------------
# Tests: _compute_severity_label
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestComputeSeverityLabel:
    """Tests for the severity label computation helper."""

    def test_none_returns_unknown(self):
        assert _compute_severity_label(None) == "UNKNOWN"

    def test_zero_returns_low(self):
        assert _compute_severity_label(0.0) == "LOW"

    def test_0_39_returns_low(self):
        assert _compute_severity_label(0.39) == "LOW"

    def test_0_40_returns_medium(self):
        assert _compute_severity_label(0.40) == "MEDIUM"

    def test_0_69_returns_medium(self):
        assert _compute_severity_label(0.69) == "MEDIUM"

    def test_0_70_returns_high(self):
        assert _compute_severity_label(0.70) == "HIGH"

    def test_0_89_returns_high(self):
        assert _compute_severity_label(0.89) == "HIGH"

    def test_0_90_returns_critical(self):
        assert _compute_severity_label(0.90) == "CRITICAL"

    def test_1_0_returns_critical(self):
        assert _compute_severity_label(1.0) == "CRITICAL"


# ---------------------------------------------------------------------------
# Tests: get_chart_data
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetChartData:
    """Tests for the chart data aggregation function."""

    def test_empty_db_returns_all_zeros(self, test_db):
        """Empty database returns chart data with all 0.0 values."""
        result = get_chart_data(test_db)

        assert "labels" in result
        assert "datasets" in result
        assert len(result["labels"]) == 24
        assert len(result["datasets"]) == 5
        for dataset in result["datasets"]:
            assert all(v == 0.0 for v in dataset["data"])

    def test_with_log_entries_computes_error_rate(self, test_db):
        """Log entries in a bucket produce a non-zero error rate."""
        # Insert entries in the current hour bucket
        now = datetime.now(timezone.utc)
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        # 4 ERROR + 6 INFO = 40% error rate
        for i in range(10):
            level = "ERROR" if i < 4 else "INFO"
            ts = current_hour + timedelta(minutes=i * 5)
            test_db.add(LogEntry(
                timestamp=ts.strftime("%Y-%m-%dT%H:%M:%S"),
                service="api-gateway",
                level=level,
                message=f"msg {i}",
            ))
        test_db.commit()

        result = get_chart_data(test_db)

        # Find the api-gateway dataset
        gw_dataset = next(d for d in result["datasets"] if d["label"] == "api-gateway")

        # The last bucket (current hour) should have a non-zero error rate
        assert any(v > 0.0 for v in gw_dataset["data"])

    def test_chart_data_has_correct_structure(self, test_db):
        """Chart data has the expected Chart.js-compatible structure."""
        result = get_chart_data(test_db)

        for dataset in result["datasets"]:
            assert "label" in dataset
            assert "data" in dataset
            assert "borderColor" in dataset
            assert "tension" in dataset
            assert "fill" in dataset
            assert len(dataset["data"]) == 24


# ---------------------------------------------------------------------------
# Tests: get_recent_anomalies
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRecentAnomalies:
    """Tests for the recent anomalies retrieval function."""

    def test_empty_db_returns_empty_list(self, test_db):
        """No anomaly records → empty list."""
        result = get_recent_anomalies(test_db)
        assert result == []

    def test_returns_anomalies_with_correct_fields(self, test_db):
        """Anomaly records are returned with all expected fields."""
        now = datetime.now(timezone.utc)
        window = AnomalyWindow(
            service="payment-service",
            window_start=(now - timedelta(minutes=5)).isoformat(),
            window_end=now.isoformat(),
            error_rate=0.45,
            anomaly_score=0.85,
            status="confirmed",
            ai_summary="Payment processor failure detected",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        test_db.add(window)
        test_db.commit()

        result = get_recent_anomalies(test_db)

        assert len(result) == 1
        assert result[0]["service"] == "payment-service"
        assert result[0]["anomaly_score"] == 0.85
        assert result[0]["status"] == "confirmed"
        assert result[0]["ai_summary"] == "Payment processor failure detected"
        assert result[0]["severity"] == "HIGH"

    def test_respects_limit_parameter(self, test_db):
        """Only returns up to `limit` records."""
        now = datetime.now(timezone.utc)
        for i in range(5):
            test_db.add(AnomalyWindow(
                service="api-gateway",
                window_start=(now - timedelta(minutes=5)).isoformat(),
                window_end=now.isoformat(),
                error_rate=0.3,
                status="confirmed",
                created_at=(now - timedelta(minutes=i)).isoformat(),
                updated_at=now.isoformat(),
            ))
        test_db.commit()

        result = get_recent_anomalies(test_db, limit=3)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Tests: get_recent_alerts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRecentAlerts:
    """Tests for the recent alerts retrieval function."""

    def test_empty_db_returns_empty_list(self, test_db):
        """No alert records → empty list."""
        result = get_recent_alerts(test_db)
        assert result == []

    def test_returns_alerts_with_service_from_joined_anomaly(self, test_db):
        """Alert records include the service name from the joined anomaly window."""
        now = datetime.now(timezone.utc)

        # Create anomaly window first
        window = AnomalyWindow(
            service="auth-service",
            window_start=(now - timedelta(minutes=5)).isoformat(),
            window_end=now.isoformat(),
            error_rate=0.5,
            anomaly_score=0.75,
            status="alerted",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        test_db.add(window)
        test_db.commit()
        test_db.refresh(window)

        # Create alert record linked to the anomaly
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

        result = get_recent_alerts(test_db)

        assert len(result) == 1
        assert result[0]["service"] == "auth-service"
        assert result[0]["severity"] == "HIGH"
        assert result[0]["dispatch_status"] == "sent"
        assert result[0]["anomaly_id"] == window.id
