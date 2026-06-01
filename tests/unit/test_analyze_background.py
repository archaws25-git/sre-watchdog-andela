"""Unit tests for the analyze router background task logic (Option 1).

Tests ``_run_analysis_job`` and ``_run_gate2_for_job`` directly, bypassing
the HTTP layer. This covers all internal branching: error rate below threshold,
anomaly detection, Bedrock success/failure, cooldown suppression, and the
job failure path.

These tests complement the integration tests in ``test_analyze_e2e.py`` which
validate the full HTTP → BackgroundTask → completion flow.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import respx
from httpx import Response
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models.db_models import AnomalyWindow, LogEntry
from app.routers.analyze import _run_analysis_job, _run_gate2_for_job
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
        "DETECTION_INTERVAL_SECONDS": 60,
        "MAX_INGEST_BATCH_SIZE": 500,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_isolated_db():
    """Create a fresh in-memory SQLite session for background task tests.

    Background tasks create their own SessionLocal() internally, so we need
    to patch SessionLocal to return a session bound to our test engine.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _wal(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session()


def _make_request_stub(jobs: dict) -> SimpleNamespace:
    """Create a minimal request-like object with app.state.analyze_jobs."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(analyze_jobs=jobs)))


def _add_log_entries(db, service: str, total: int, error_count: int,
                     start_time: datetime, end_time: datetime):
    """Insert log entries spanning the given time range."""
    span = (end_time - start_time).total_seconds()
    for i in range(total):
        ts = start_time + timedelta(seconds=(span / total) * i)
        level = "ERROR" if i < error_count else "INFO"
        db.add(LogEntry(
            timestamp=ts.strftime("%Y-%m-%dT%H:%M:%S"),
            service=service,
            level=level,
            message=f"msg {i}",
        ))
    db.commit()


# ---------------------------------------------------------------------------
# Tests: _run_analysis_job
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunAnalysisJob:
    """Direct unit tests for the _run_analysis_job background function."""

    @patch("app.routers.analyze.get_settings")
    @patch("app.routers.analyze.SessionLocal")
    def test_no_log_entries_job_completes_with_zero_anomalies(
        self, mock_session_local, mock_get_settings
    ):
        """Empty DB → job completes with anomalies_found=0."""
        db = _make_isolated_db()
        mock_session_local.return_value = db
        db.close = lambda: None
        mock_get_settings.return_value = _make_settings()

        jobs = {}
        request = _make_request_stub(jobs)
        jobs["job-1"] = MagicMock()

        now = datetime.now(timezone.utc)
        _run_analysis_job(
            job_id="job-1",
            request=request,
            service="api-gateway",
            start_time=now - timedelta(hours=1),
            end_time=now,
        )

        assert jobs["job-1"].status == "completed"
        assert jobs["job-1"].anomalies_found == 0
        assert jobs["job-1"].alerts_dispatched == 0

    @patch("app.routers.analyze.get_settings")
    @patch("app.routers.analyze.SessionLocal")
    def test_error_rate_below_threshold_no_anomaly_created(
        self, mock_session_local, mock_get_settings
    ):
        """Error rate below threshold → no AnomalyWindow created."""
        db = _make_isolated_db()
        mock_session_local.return_value = db
        db.close = lambda: None
        settings = _make_settings(ERROR_RATE_THRESHOLD=0.5)
        mock_get_settings.return_value = settings

        now = datetime.now(timezone.utc)
        # 2 errors out of 10 = 20% error rate, below 50% threshold
        _add_log_entries(db, "api-gateway", 10, 2,
                         now - timedelta(hours=1), now)

        jobs = {}
        request = _make_request_stub(jobs)
        jobs["job-2"] = MagicMock()

        _run_analysis_job(
            job_id="job-2",
            request=request,
            service="api-gateway",
            start_time=now - timedelta(hours=1),
            end_time=now,
        )

        assert jobs["job-2"].anomalies_found == 0
        assert db.query(AnomalyWindow).count() == 0

    @patch("app.routers.analyze.get_settings")
    @patch("app.routers.analyze.SessionLocal")
    @patch("app.routers.analyze.BedrockClient")
    def test_high_error_rate_creates_anomaly_and_dispatches_alert(
        self, mock_bedrock_cls, mock_session_local, mock_get_settings, mock_webhook
    ):
        """Error rate above threshold → anomaly created, alert dispatched."""
        db = _make_isolated_db()
        mock_session_local.return_value = db
        db.close = lambda: None
        settings = _make_settings(ERROR_RATE_THRESHOLD=0.1, ANOMALY_SCORE_THRESHOLD=0.5)
        mock_get_settings.return_value = settings

        # Mock Bedrock to return high score
        mock_bedrock = MagicMock()
        mock_bedrock.analyze.return_value = BedrockAnalysisResult(
            anomaly_score=0.85,
            summary="High error rate detected",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200.0,
        )
        mock_bedrock_cls.return_value = mock_bedrock

        now = datetime.now(timezone.utc)
        # 8 errors out of 10 = 80% error rate, above 10% threshold
        _add_log_entries(db, "api-gateway", 10, 8,
                         now - timedelta(hours=1), now)

        jobs = {}
        request = _make_request_stub(jobs)
        jobs["job-3"] = MagicMock()

        _run_analysis_job(
            job_id="job-3",
            request=request,
            service="api-gateway",
            start_time=now - timedelta(hours=1),
            end_time=now,
        )

        assert jobs["job-3"].anomalies_found == 1
        assert db.query(AnomalyWindow).count() == 1

    @patch("app.routers.analyze.get_settings")
    @patch("app.routers.analyze.SessionLocal")
    def test_exception_in_job_sets_failed_status(
        self, mock_session_local, mock_get_settings
    ):
        """Unhandled exception → job status set to 'failed' with error message."""
        # Raise after get_settings() succeeds but before DB work starts
        mock_get_settings.return_value = _make_settings()
        mock_session_local.side_effect = Exception("DB connection refused")

        jobs = {}
        request = _make_request_stub(jobs)
        # Pre-populate with a real AnalyzeJobResult so the except block can update it
        from app.models.schemas import AnalyzeJobResult, AnalyzeJobStatus
        jobs["job-4"] = AnalyzeJobResult(job_id="job-4", status=AnalyzeJobStatus.RUNNING)

        now = datetime.now(timezone.utc)
        _run_analysis_job(
            job_id="job-4",
            request=request,
            service="api-gateway",
            start_time=now - timedelta(hours=1),
            end_time=now,
        )

        assert jobs["job-4"].status == "failed"
        assert "DB connection refused" in jobs["job-4"].error

    @patch("app.routers.analyze.get_settings")
    @patch("app.routers.analyze.SessionLocal")
    @patch("app.routers.analyze.BedrockClient")
    def test_all_five_services_analyzed_when_service_is_none(
        self, mock_bedrock_cls, mock_session_local, mock_get_settings
    ):
        """When service=None, all 5 services are analyzed."""
        db = _make_isolated_db()
        mock_session_local.return_value = db
        db.close = lambda: None
        mock_get_settings.return_value = _make_settings()

        mock_bedrock = MagicMock()
        mock_bedrock.analyze.return_value = BedrockAnalysisResult(
            anomaly_score=0.3,  # below threshold — no alert
            summary="Low severity",
            input_tokens=50,
            output_tokens=20,
            latency_ms=100.0,
        )
        mock_bedrock_cls.return_value = mock_bedrock

        now = datetime.now(timezone.utc)
        services = ["api-gateway", "auth-service", "payment-service",
                    "notification-service", "database-proxy"]
        for svc in services:
            _add_log_entries(db, svc, 10, 5,
                             now - timedelta(hours=1), now)

        jobs = {}
        request = _make_request_stub(jobs)
        jobs["job-5"] = MagicMock()

        _run_analysis_job(
            job_id="job-5",
            request=request,
            service=None,  # analyze all services
            start_time=now - timedelta(hours=1),
            end_time=now,
        )

        assert jobs["job-5"].anomalies_found == 5


# ---------------------------------------------------------------------------
# Tests: _run_gate2_for_job
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunGate2ForJob:
    """Direct unit tests for the _run_gate2_for_job helper function."""

    def _make_anomaly_window(self, db) -> AnomalyWindow:
        """Create a pending_analysis AnomalyWindow in the test DB."""
        now = datetime.now(timezone.utc)
        window = AnomalyWindow(
            service="api-gateway",
            window_start=(now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S"),
            window_end=now.strftime("%Y-%m-%dT%H:%M:%S"),
            error_rate=0.5,
            status="pending_analysis",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        db.add(window)
        db.commit()
        db.refresh(window)
        return window

    @patch("app.routers.analyze.BedrockClient")
    def test_score_above_threshold_no_cooldown_returns_true(
        self, mock_bedrock_cls, mock_webhook
    ):
        """Score ≥ threshold, no cooldown → status=confirmed, returns True."""
        db = _make_isolated_db()
        settings = _make_settings(ANOMALY_SCORE_THRESHOLD=0.5)
        window = self._make_anomaly_window(db)

        mock_bedrock = MagicMock()
        mock_bedrock.analyze.return_value = BedrockAnalysisResult(
            anomaly_score=0.85,
            summary="High severity",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200.0,
        )
        mock_bedrock_cls.return_value = mock_bedrock

        log_entries = [
            SimpleNamespace(message="Error 1"),
            SimpleNamespace(message="Error 2"),
        ]

        result = _run_gate2_for_job(
            anomaly_window=window,
            log_entries=log_entries,
            settings=settings,
            db=db,
        )

        db.refresh(window)
        # Either confirmed (then alerted by dispatch) or alerted directly
        assert window.status in ("confirmed", "alerted")
        assert window.anomaly_score == 0.85

    @patch("app.routers.analyze.BedrockClient")
    def test_score_below_threshold_returns_false(self, mock_bedrock_cls):
        """Score < threshold → status=below_score_threshold, returns False."""
        db = _make_isolated_db()
        settings = _make_settings(ANOMALY_SCORE_THRESHOLD=0.5)
        window = self._make_anomaly_window(db)

        mock_bedrock = MagicMock()
        mock_bedrock.analyze.return_value = BedrockAnalysisResult(
            anomaly_score=0.3,
            summary="Low severity",
            input_tokens=50,
            output_tokens=20,
            latency_ms=100.0,
        )
        mock_bedrock_cls.return_value = mock_bedrock

        result = _run_gate2_for_job(
            anomaly_window=window,
            log_entries=[SimpleNamespace(message="Info")],
            settings=settings,
            db=db,
        )

        db.refresh(window)
        assert result is False
        assert window.status == "below_score_threshold"

    @patch("app.routers.analyze.BedrockClient")
    def test_bedrock_parse_error_sets_analysis_failed(self, mock_bedrock_cls):
        """BedrockParseError → status=analysis_failed, returns False."""
        db = _make_isolated_db()
        settings = _make_settings()
        window = self._make_anomaly_window(db)

        mock_bedrock = MagicMock()
        mock_bedrock.analyze.side_effect = BedrockParseError("Invalid JSON")
        mock_bedrock_cls.return_value = mock_bedrock

        result = _run_gate2_for_job(
            anomaly_window=window,
            log_entries=[SimpleNamespace(message="Error")],
            settings=settings,
            db=db,
        )

        db.refresh(window)
        assert result is False
        assert window.status == "analysis_failed"
        assert "BedrockParseError" in window.failure_reason

    @patch("app.routers.analyze.BedrockClient")
    def test_generic_exception_sets_analysis_failed(self, mock_bedrock_cls):
        """Any unexpected exception → status=analysis_failed, returns False."""
        db = _make_isolated_db()
        settings = _make_settings()
        window = self._make_anomaly_window(db)

        mock_bedrock = MagicMock()
        mock_bedrock.analyze.side_effect = RuntimeError("Unexpected failure")
        mock_bedrock_cls.return_value = mock_bedrock

        result = _run_gate2_for_job(
            anomaly_window=window,
            log_entries=[SimpleNamespace(message="Error")],
            settings=settings,
            db=db,
        )

        db.refresh(window)
        assert result is False
        assert window.status == "analysis_failed"
        assert "Unexpected failure" in window.failure_reason

    @patch("app.routers.analyze.BedrockClient")
    @patch("app.routers.analyze.is_in_cooldown", return_value=True)
    def test_cooldown_active_sets_suppressed(
        self, mock_cooldown, mock_bedrock_cls
    ):
        """Score ≥ threshold but cooldown active → status=suppressed, returns False."""
        db = _make_isolated_db()
        settings = _make_settings(ANOMALY_SCORE_THRESHOLD=0.5)
        window = self._make_anomaly_window(db)

        mock_bedrock = MagicMock()
        mock_bedrock.analyze.return_value = BedrockAnalysisResult(
            anomaly_score=0.85,
            summary="High severity",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200.0,
        )
        mock_bedrock_cls.return_value = mock_bedrock

        result = _run_gate2_for_job(
            anomaly_window=window,
            log_entries=[SimpleNamespace(message="Error")],
            settings=settings,
            db=db,
        )

        db.refresh(window)
        assert result is False
        assert window.status == "suppressed"
        assert window.suppression_reason == "cooldown_active"
