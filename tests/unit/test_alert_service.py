"""Unit tests for the alert service module."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import httpx
import pytest

from app.config import Settings
from app.models.db_models import AnomalyWindow
from app.models.schemas import SeverityLabel
from app.services.alert_service import dispatch, is_in_cooldown, map_severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    """Create a Settings instance with test defaults."""
    defaults = {
        "DATABASE_URL": "sqlite:///:memory:",
        "WEBHOOK_URL": "http://test-webhook.example.com/alerts",
        "ALERT_COOLDOWN_MINUTES": 15,
        "ERROR_RATE_THRESHOLD": 0.1,
        "ANOMALY_SCORE_THRESHOLD": 0.5,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_anomaly_window(
    test_db,
    service: str = "api-gateway",
    status: str = "confirmed",
    anomaly_score: float = 0.85,
    ai_summary: str = "Test anomaly detected",
    created_at: str | None = None,
    updated_at: str | None = None,
) -> AnomalyWindow:
    """Create and persist an AnomalyWindow record."""
    now = datetime.now(timezone.utc).isoformat()
    window = AnomalyWindow(
        service=service,
        window_start=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        window_end=now,
        error_rate=0.5,
        anomaly_score=anomaly_score,
        status=status,
        ai_summary=ai_summary,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )
    test_db.add(window)
    test_db.commit()
    test_db.refresh(window)
    return window


# ---------------------------------------------------------------------------
# Tests: map_severity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMapSeverity:
    """Tests for severity mapping across all four bands."""

    def test_score_zero_is_low(self):
        assert map_severity(0.0) == SeverityLabel.LOW

    def test_score_0_39_is_low(self):
        assert map_severity(0.39) == SeverityLabel.LOW

    def test_score_0_40_is_medium(self):
        assert map_severity(0.40) == SeverityLabel.MEDIUM

    def test_score_0_69_is_medium(self):
        assert map_severity(0.69) == SeverityLabel.MEDIUM

    def test_score_0_70_is_high(self):
        assert map_severity(0.70) == SeverityLabel.HIGH

    def test_score_0_89_is_high(self):
        assert map_severity(0.89) == SeverityLabel.HIGH

    def test_score_0_90_is_critical(self):
        assert map_severity(0.90) == SeverityLabel.CRITICAL

    def test_score_1_0_is_critical(self):
        assert map_severity(1.0) == SeverityLabel.CRITICAL


# ---------------------------------------------------------------------------
# Tests: dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDispatch:
    """Tests for alert dispatch behavior."""

    @patch("app.services.alert_service.httpx.post")
    def test_successful_dispatch_returns_sent(self, mock_post, test_db):
        """Successful webhook POST results in dispatch_status=sent."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        settings = _make_settings()
        anomaly = _make_anomaly_window(test_db)

        alert_record = dispatch(anomaly, test_db, settings)

        assert alert_record is not None
        assert alert_record.dispatch_status == "sent"
        assert alert_record.http_status == 200
        mock_post.assert_called_once()

    @patch("app.services.alert_service.httpx.post")
    def test_dispatch_fails_after_retries(self, mock_post, test_db):
        """HTTPError on all 3 retries results in dispatch_status=failed."""
        mock_post.side_effect = httpx.HTTPError("Connection refused")

        settings = _make_settings()
        anomaly = _make_anomaly_window(test_db)

        alert_record = dispatch(anomaly, test_db, settings)

        assert alert_record is not None
        assert alert_record.dispatch_status == "failed"
        assert mock_post.call_count == 3

    @patch("app.services.alert_service.httpx.post")
    def test_cooldown_suppression(self, mock_post, test_db):
        """Alert within cooldown window is suppressed without HTTP POST."""
        settings = _make_settings()

        # Create an existing alerted anomaly within cooldown window
        _make_anomaly_window(
            test_db,
            service="api-gateway",
            status="alerted",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

        # Create a new confirmed anomaly for the same service
        new_anomaly = _make_anomaly_window(
            test_db,
            service="api-gateway",
            status="confirmed",
        )

        alert_record = dispatch(new_anomaly, test_db, settings)

        assert alert_record is not None
        assert alert_record.dispatch_status == "suppressed"
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: is_in_cooldown
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsInCooldown:
    """Tests for cooldown checking logic."""

    def test_no_recent_alerts_returns_false(self, test_db):
        """No alerted records means no cooldown."""
        settings = _make_settings()
        result = is_in_cooldown("api-gateway", test_db, settings)
        assert result is False

    def test_recent_alert_within_window_returns_true(self, test_db):
        """An alerted record within cooldown window returns True."""
        settings = _make_settings()
        _make_anomaly_window(
            test_db,
            service="api-gateway",
            status="alerted",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

        result = is_in_cooldown("api-gateway", test_db, settings)
        assert result is True

    def test_old_alert_outside_window_returns_false(self, test_db):
        """An alerted record older than cooldown window returns False."""
        settings = _make_settings()
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        _make_anomaly_window(
            test_db,
            service="api-gateway",
            status="alerted",
            updated_at=old_time,
        )

        result = is_in_cooldown("api-gateway", test_db, settings)
        assert result is False
