"""Unit tests for the scheduler module.

Tests the _BackgroundTasksAdapter, run_detection_tick, and the scheduler
factory/lifecycle functions without actually starting a real APScheduler
background thread.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.scheduler import (
    _BackgroundTasksAdapter,
    create_scheduler,
    run_detection_tick,
    start_scheduler,
    stop_scheduler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    """Create a Settings instance with test defaults."""
    defaults = {
        "DATABASE_URL": "sqlite:///:memory:",
        "WEBHOOK_URL": "http://test.example.com",
        "DETECTION_INTERVAL_SECONDS": 60,
        "ERROR_RATE_THRESHOLD": 0.1,
        "ANOMALY_SCORE_THRESHOLD": 0.5,
        "SLIDING_WINDOW_MINUTES": 5,
        "ALERT_COOLDOWN_MINUTES": 15,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# Tests: _BackgroundTasksAdapter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackgroundTasksAdapter:
    """Tests for the BackgroundTasks adapter used by the scheduler."""

    def test_add_task_stores_callable(self):
        """add_task stores the function and arguments."""
        adapter = _BackgroundTasksAdapter()
        mock_fn = MagicMock()

        adapter.add_task(mock_fn, "arg1", key="val")

        assert len(adapter._tasks) == 1
        assert adapter._tasks[0] == (mock_fn, ("arg1",), {"key": "val"})

    def test_execute_runs_tasks_in_threads(self):
        """execute() runs each task and clears the list."""
        adapter = _BackgroundTasksAdapter()
        results = []

        def task_fn(value):
            results.append(value)

        adapter.add_task(task_fn, "hello")
        adapter.add_task(task_fn, "world")

        # Patch threading.Thread to run synchronously for testing
        with patch("app.scheduler.threading.Thread") as mock_thread:
            mock_instance = MagicMock()
            mock_thread.return_value = mock_instance

            adapter.execute()

            assert mock_thread.call_count == 2
            assert mock_instance.start.call_count == 2

        # Tasks list should be cleared
        assert len(adapter._tasks) == 0

    def test_execute_with_no_tasks_does_nothing(self):
        """execute() with empty task list is a no-op."""
        adapter = _BackgroundTasksAdapter()
        adapter.execute()  # Should not raise
        assert len(adapter._tasks) == 0


# ---------------------------------------------------------------------------
# Tests: run_detection_tick
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunDetectionTick:
    """Tests for the run_detection_tick function."""

    @patch("app.scheduler.evaluate_all_services")
    @patch("app.scheduler.SessionLocal")
    def test_tick_calls_evaluate_all_services(
        self, mock_session_local, mock_evaluate
    ):
        """Detection tick creates a session and calls evaluate_all_services."""
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        mock_bedrock = MagicMock()
        settings = _make_settings()

        run_detection_tick(settings=settings, bedrock_client=mock_bedrock)

        mock_evaluate.assert_called_once()
        call_kwargs = mock_evaluate.call_args[1]
        assert call_kwargs["db"] is mock_db
        assert call_kwargs["settings"] is settings
        assert call_kwargs["bedrock_client"] is mock_bedrock
        mock_db.close.assert_called_once()

    @patch("app.scheduler.evaluate_all_services")
    @patch("app.scheduler.SessionLocal")
    def test_tick_handles_exception_gracefully(
        self, mock_session_local, mock_evaluate
    ):
        """Exception in evaluate_all_services is caught and logged."""
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        mock_evaluate.side_effect = RuntimeError("DB exploded")
        settings = _make_settings()

        # Should not raise
        run_detection_tick(settings=settings, bedrock_client=MagicMock())

        mock_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: create_scheduler, start_scheduler, stop_scheduler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSchedulerLifecycle:
    """Tests for scheduler factory and lifecycle functions."""

    def test_create_scheduler_returns_configured_scheduler(self):
        """create_scheduler returns a BackgroundScheduler with the detection job."""
        settings = _make_settings(DETECTION_INTERVAL_SECONDS=120)
        mock_bedrock = MagicMock()

        scheduler = create_scheduler(settings, mock_bedrock)

        # Verify the scheduler has the detection_tick job registered
        job = scheduler.get_job("detection_tick")
        assert job is not None
        assert job.name == "SRE Watchdog Detection Tick"

    def test_start_and_stop_scheduler(self):
        """start_scheduler starts and stop_scheduler shuts down cleanly."""
        settings = _make_settings()
        mock_bedrock = MagicMock()

        scheduler = create_scheduler(settings, mock_bedrock)

        start_scheduler(scheduler)
        assert scheduler.running is True

        stop_scheduler(scheduler)
        assert scheduler.running is False
