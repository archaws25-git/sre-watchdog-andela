"""APScheduler setup, job registration, and FastAPI lifespan integration.

Configures a ``BackgroundScheduler`` that drives the Gate 1 detection tick
at the interval defined by ``DETECTION_INTERVAL_SECONDS``. The scheduler
runs outside the FastAPI request lifecycle, so the tick function creates its
own database session and spawns threads for Gate 2 analysis instead of using
FastAPI ``BackgroundTasks``.

Provides factory and lifecycle functions for clean integration with the
FastAPI ``lifespan`` context manager:

- ``create_scheduler(settings, bedrock_client)`` — builds and configures the
  scheduler with the detection tick job.
- ``start_scheduler(scheduler)`` — starts the scheduler (call in lifespan
  startup).
- ``stop_scheduler(scheduler)`` — gracefully shuts down the scheduler (call
  in lifespan shutdown).

Typical usage::

    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from app.config import get_settings
    from app.scheduler import create_scheduler, start_scheduler, stop_scheduler
    from app.services.bedrock_client import BedrockClient

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        bedrock_client = BedrockClient(settings=settings, app_state=app.state)
        scheduler = create_scheduler(settings, bedrock_client)
        start_scheduler(scheduler)
        yield
        stop_scheduler(scheduler)

    app = FastAPI(lifespan=lifespan)
"""

import logging
import threading
from datetime import timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.database import SessionLocal
from app.services.anomaly_detector import evaluate_all_services
from app.services.bedrock_client import BedrockClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detection Tick — runs on each APScheduler interval
# ---------------------------------------------------------------------------


class _BackgroundTasksAdapter:
    """Adapter that mimics FastAPI BackgroundTasks for use outside requests.

    Since APScheduler runs outside the FastAPI request lifecycle, we cannot
    use the real ``BackgroundTasks`` instance. This adapter collects tasks
    and executes each one in a separate daemon thread, replicating the
    non-blocking behaviour of FastAPI BackgroundTasks.
    """

    def __init__(self) -> None:
        """Initialise with an empty task list."""
        self._tasks: list = []

    def add_task(self, func, *args, **kwargs) -> None:
        """Enqueue a callable for background execution.

        Args:
            func: The callable to execute in a background thread.
            *args: Positional arguments passed to the callable.
            **kwargs: Keyword arguments passed to the callable.
        """
        self._tasks.append((func, args, kwargs))

    def execute(self) -> None:
        """Execute all enqueued tasks in separate daemon threads.

        Each task runs in its own thread so that Gate 2 Bedrock calls
        do not block the scheduler tick or each other.
        """
        for func, args, kwargs in self._tasks:
            thread = threading.Thread(
                target=func,
                args=args,
                kwargs=kwargs,
                daemon=True,
            )
            thread.start()
        self._tasks.clear()


def run_detection_tick(settings: Settings, bedrock_client: BedrockClient) -> None:
    """Execute a single detection tick (Gate 1) for all monitored services.

    Creates a fresh database session, runs ``evaluate_all_services`` to
    perform the statistical pre-filter, and then spawns background threads
    for any Gate 2 analysis tasks that were enqueued.

    This function is registered as the APScheduler interval job and runs
    outside the FastAPI request lifecycle.

    Args:
        settings: Application settings containing thresholds and intervals.
        bedrock_client: The Bedrock client instance for Gate 2 analysis.
    """
    db = SessionLocal()
    try:
        background_tasks = _BackgroundTasksAdapter()

        evaluate_all_services(
            db=db,
            settings=settings,
            background_tasks=background_tasks,
            bedrock_client=bedrock_client,
        )

        # Execute enqueued Gate 2 tasks in background threads
        background_tasks.execute()

        logger.debug('{"event": "detection_tick_complete"}')
    except Exception as exc:
        logger.error(
            '{"event": "detection_tick_error", "error": "%s"}',
            str(exc),
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler Factory and Lifecycle
# ---------------------------------------------------------------------------


def create_scheduler(
    settings: Settings, bedrock_client: BedrockClient
) -> BackgroundScheduler:
    """Create and configure the APScheduler BackgroundScheduler.

    Configures the scheduler with:
    - ``coalesce=True`` — if multiple ticks are missed, only one is fired.
    - ``max_instances=1`` — prevents overlapping tick executions.
    - ``timezone=UTC`` — all scheduling uses UTC timestamps.

    Registers ``run_detection_tick`` as an interval job triggered every
    ``DETECTION_INTERVAL_SECONDS``.

    Args:
        settings: Application settings containing ``DETECTION_INTERVAL_SECONDS``.
        bedrock_client: The Bedrock client instance passed to the tick function.

    Returns:
        A configured but not yet started ``BackgroundScheduler`` instance.
    """
    scheduler = BackgroundScheduler(timezone=timezone.utc)

    scheduler.add_job(
        func=run_detection_tick,
        trigger=IntervalTrigger(seconds=settings.DETECTION_INTERVAL_SECONDS),
        kwargs={"settings": settings, "bedrock_client": bedrock_client},
        id="detection_tick",
        name="SRE Watchdog Detection Tick",
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    logger.info(
        '{"event": "scheduler_created", '
        '"detection_interval_seconds": %d}',
        settings.DETECTION_INTERVAL_SECONDS,
    )

    return scheduler


def start_scheduler(scheduler: BackgroundScheduler) -> None:
    """Start the APScheduler background scheduler.

    Should be called during the FastAPI lifespan startup phase, after
    database initialisation and stale record cleanup.

    Args:
        scheduler: The configured ``BackgroundScheduler`` to start.
    """
    scheduler.start()
    logger.info('{"event": "scheduler_started"}')


def stop_scheduler(scheduler: BackgroundScheduler) -> None:
    """Gracefully shut down the APScheduler background scheduler.

    Waits for any currently executing jobs to finish before shutting down.
    Should be called during the FastAPI lifespan shutdown phase.

    Args:
        scheduler: The running ``BackgroundScheduler`` to stop.
    """
    scheduler.shutdown(wait=True)
    logger.info('{"event": "scheduler_stopped"}')
