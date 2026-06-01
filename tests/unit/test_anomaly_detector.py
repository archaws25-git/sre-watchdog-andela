"""Unit tests for the anomaly detector service."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from app.config import Settings
from app.models.db_models import AnomalyWindow, LogEntry
from app.services.anomaly_detector import (
    cleanup_stale_pending,
    evaluate_all_services,
    run_gate2,
)
from app.services.bedrock_client import BedrockAnalysisResult, BedrockParseError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    """Create a Settings instance with test defaults."""
    defaults = {
        "DATABASE_URL": "sqlite:///:memory:",
        "WEBHOOK_URL": "http://test-webhook.example.com/alerts",
        "ERROR_RATE_THRESHOLD": 0.1,
        "ANOMALY_SCORE_THRESHOLD": 0.5,
        "SLIDING_WINDOW_MINUTES": 5,
        "ALERT_COOLDOWN_MINUTES": 15,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _create_log_entries(test_db, service: str, total: int, error_count: int, timestamp: str | None = None):
    """Create log entries with a specified number of errors."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    for i in range(total):
        level = "ERROR" if i < error_count else "INFO"
        entry = LogEntry(
            timestamp=ts,
            service=service,
            level=level,
            message=f"Test message {i}",
        )
        test_db.add(entry)
    test_db.commit()


# ---------------------------------------------------------------------------
# Tests: Gate 1 — evaluate_all_services
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGate1:
    """Tests for Gate 1 statistical pre-filter."""

    @freeze_time("2026-05-20T12:00:00")
    def test_zero_log_entries_no_anomaly_created(self, test_db):
        """No log entries in window → no AnomalyWindow record created."""
        settings = _make_settings()
        background_tasks = MagicMock()
        bedrock_client = MagicMock()

        evaluate_all_services(test_db, settings, background_tasks, bedrock_client)

        anomalies = test_db.query(AnomalyWindow).all()
        assert len(anomalies) == 0
        background_tasks.add_task.assert_not_called()

    @freeze_time("2026-05-20T12:00:00")
    def test_error_rate_below_threshold_no_anomaly(self, test_db):
        """Error rate below threshold → no anomaly created."""
        settings = _make_settings(ERROR_RATE_THRESHOLD=0.5)
        background_tasks = MagicMock()
        bedrock_client = MagicMock()

        # Create 10 entries with 2 errors (20% error rate, below 50% threshold)
        _create_log_entries(
            test_db,
            service="api-gateway",
            total=10,
            error_count=2,
            timestamp="2026-05-20T11:58:00",
        )

        evaluate_all_services(test_db, settings, background_tasks, bedrock_client)

        anomalies = test_db.query(AnomalyWindow).all()
        assert len(anomalies) == 0

    @freeze_time("2026-05-20T12:00:00")
    def test_single_spike_creates_pending_analysis(self, test_db):
        """Error rate above threshold → pending_analysis record created."""
        settings = _make_settings(ERROR_RATE_THRESHOLD=0.1)
        background_tasks = MagicMock()
        bedrock_client = MagicMock()

        # Create 10 entries with 5 errors (50% error rate, above 10% threshold)
        _create_log_entries(
            test_db,
            service="api-gateway",
            total=10,
            error_count=5,
            timestamp="2026-05-20T11:58:00",
        )

        evaluate_all_services(test_db, settings, background_tasks, bedrock_client)

        anomalies = test_db.query(AnomalyWindow).all()
        assert len(anomalies) == 1
        assert anomalies[0].status == "pending_analysis"
        assert anomalies[0].service == "api-gateway"
        background_tasks.add_task.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: cleanup_stale_pending
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupStalePending:
    """Tests for stale pending record cleanup."""

    @freeze_time("2026-05-20T12:00:00")
    def test_stale_record_marked_as_failed(self, test_db):
        """Record older than 10 min is marked analysis_failed with orphaned_on_restart."""
        # Create a stale pending record (15 minutes old)
        stale_time = (datetime(2026, 5, 20, 11, 45, 0)).isoformat()
        window = AnomalyWindow(
            service="api-gateway",
            window_start=stale_time,
            window_end=stale_time,
            error_rate=0.5,
            status="pending_analysis",
            created_at=stale_time,
            updated_at=stale_time,
        )
        test_db.add(window)
        test_db.commit()

        count = cleanup_stale_pending(test_db)

        assert count == 1
        test_db.refresh(window)
        assert window.status == "analysis_failed"
        assert window.suppression_reason == "orphaned_on_restart"

    @freeze_time("2026-05-20T12:00:00")
    def test_fresh_pending_record_not_cleaned(self, test_db):
        """Record less than 10 min old is NOT cleaned up."""
        # Create a fresh pending record (2 minutes old)
        fresh_time = (datetime(2026, 5, 20, 11, 58, 0)).isoformat()
        window = AnomalyWindow(
            service="api-gateway",
            window_start=fresh_time,
            window_end=fresh_time,
            error_rate=0.5,
            status="pending_analysis",
            created_at=fresh_time,
            updated_at=fresh_time,
        )
        test_db.add(window)
        test_db.commit()

        count = cleanup_stale_pending(test_db)

        assert count == 0
        test_db.refresh(window)
        assert window.status == "pending_analysis"


# ---------------------------------------------------------------------------
# Tests: Gate 2 — run_gate2
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGate2:
    """Tests for Gate 2 AI analysis."""

    @patch("app.services.anomaly_detector.alert_dispatch")
    @patch("app.services.anomaly_detector.is_in_cooldown", return_value=False)
    @patch("app.services.anomaly_detector.SessionLocal")
    def test_bedrock_above_threshold_confirms_and_dispatches(
        self, mock_session_local, mock_cooldown, mock_dispatch, test_db
    ):
        """Bedrock score above threshold → status=confirmed, alert dispatched."""
        # Set up the mock session to return our test_db
        # Patch close() to be a no-op so the session stays usable
        mock_session_local.return_value = test_db
        original_close = test_db.close
        test_db.close = lambda: None

        settings = _make_settings(ANOMALY_SCORE_THRESHOLD=0.5)

        # Create log entries and anomaly window
        _create_log_entries(test_db, "api-gateway", 10, 5, "2026-05-20T11:58:00")
        window = AnomalyWindow(
            service="api-gateway",
            window_start="2026-05-20T11:55:00",
            window_end="2026-05-20T12:00:00",
            error_rate=0.5,
            status="pending_analysis",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        test_db.add(window)
        test_db.commit()
        test_db.refresh(window)
        window_id = window.id

        # Mock Bedrock client
        mock_bedrock = MagicMock()
        mock_bedrock.analyze.return_value = BedrockAnalysisResult(
            anomaly_score=0.85,
            summary="High error rate detected",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200.0,
        )

        run_gate2(anomaly_id=window_id, settings=settings, bedrock_client=mock_bedrock)

        # Re-query the window from the same session
        updated_window = test_db.query(AnomalyWindow).filter(AnomalyWindow.id == window_id).first()
        assert updated_window.status == "confirmed"
        assert updated_window.anomaly_score == 0.85
        mock_dispatch.assert_called_once()

        # Restore close
        test_db.close = original_close

    @patch("app.services.anomaly_detector.alert_dispatch")
    @patch("app.services.anomaly_detector.is_in_cooldown", return_value=False)
    @patch("app.services.anomaly_detector.SessionLocal")
    def test_bedrock_below_threshold_sets_below_score(
        self, mock_session_local, mock_cooldown, mock_dispatch, test_db
    ):
        """Bedrock score below threshold → status=below_score_threshold."""
        mock_session_local.return_value = test_db
        test_db.close = lambda: None

        settings = _make_settings(ANOMALY_SCORE_THRESHOLD=0.5)

        _create_log_entries(test_db, "api-gateway", 10, 5, "2026-05-20T11:58:00")
        window = AnomalyWindow(
            service="api-gateway",
            window_start="2026-05-20T11:55:00",
            window_end="2026-05-20T12:00:00",
            error_rate=0.5,
            status="pending_analysis",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        test_db.add(window)
        test_db.commit()
        test_db.refresh(window)
        window_id = window.id

        mock_bedrock = MagicMock()
        mock_bedrock.analyze.return_value = BedrockAnalysisResult(
            anomaly_score=0.3,
            summary="Low severity",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200.0,
        )

        run_gate2(anomaly_id=window_id, settings=settings, bedrock_client=mock_bedrock)

        updated_window = test_db.query(AnomalyWindow).filter(AnomalyWindow.id == window_id).first()
        assert updated_window.status == "below_score_threshold"
        mock_dispatch.assert_not_called()

    @patch("app.services.anomaly_detector.alert_dispatch")
    @patch("app.services.anomaly_detector.is_in_cooldown", return_value=False)
    @patch("app.services.anomaly_detector.SessionLocal")
    def test_bedrock_parse_error_sets_analysis_failed(
        self, mock_session_local, mock_cooldown, mock_dispatch, test_db
    ):
        """BedrockParseError → status=analysis_failed."""
        mock_session_local.return_value = test_db
        test_db.close = lambda: None

        settings = _make_settings()

        _create_log_entries(test_db, "api-gateway", 10, 5, "2026-05-20T11:58:00")
        window = AnomalyWindow(
            service="api-gateway",
            window_start="2026-05-20T11:55:00",
            window_end="2026-05-20T12:00:00",
            error_rate=0.5,
            status="pending_analysis",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        test_db.add(window)
        test_db.commit()
        test_db.refresh(window)
        window_id = window.id

        mock_bedrock = MagicMock()
        mock_bedrock.analyze.side_effect = BedrockParseError("Invalid response")

        run_gate2(anomaly_id=window_id, settings=settings, bedrock_client=mock_bedrock)

        updated_window = test_db.query(AnomalyWindow).filter(AnomalyWindow.id == window_id).first()
        assert updated_window.status == "analysis_failed"
        assert "BedrockParseError" in updated_window.failure_reason
        mock_dispatch.assert_not_called()
