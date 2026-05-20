"""Shared pytest fixtures for the SRE Watchdog test suite.

Provides reusable fixtures for:
- In-memory SQLite test database with all tables created/dropped per test.
- FastAPI TestClient with dependency overrides for DB and settings.
- Mock Bedrock client returning a fixture JSON response.
- Mock webhook interceptor via respx for outbound POST requests.
"""

from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import Base, get_db
from app.main import app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_WEBHOOK_URL = "http://test-webhook.example.com/alerts"

MOCK_BEDROCK_RESPONSE = {
    "output": {
        "message": {
            "content": [
                {
                    "text": '{"anomaly_score": 0.85, "summary": "Test anomaly detected"}'
                }
            ]
        }
    },
    "usage": {"inputTokens": 100, "outputTokens": 50},
}


# ---------------------------------------------------------------------------
# Test Settings
# ---------------------------------------------------------------------------


def get_test_settings() -> Settings:
    """Create a Settings instance configured for testing.

    Returns:
        A Settings object with test-safe defaults including in-memory
        SQLite and a mock webhook URL.
    """
    return Settings(
        DATABASE_URL="sqlite:///:memory:",
        WEBHOOK_URL=TEST_WEBHOOK_URL,
        AWS_REGION="us-east-1",
        BEDROCK_MODEL_ID="test-model-id",
        LOG_LEVEL="DEBUG",
        ERROR_RATE_THRESHOLD=0.1,
        ANOMALY_SCORE_THRESHOLD=0.5,
        SLIDING_WINDOW_MINUTES=5,
        ALERT_COOLDOWN_MINUTES=15,
        DETECTION_INTERVAL_SECONDS=60,
        MAX_INGEST_BATCH_SIZE=500,
    )


# ---------------------------------------------------------------------------
# Database Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_db() -> Generator[Session, None, None]:
    """Create an in-memory SQLite database session for testing.

    Creates all tables from Base.metadata before yielding the session,
    and drops all tables after the test completes.

    Yields:
        A SQLAlchemy Session bound to an in-memory SQLite engine.
    """
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(test_engine, "connect")
    def _enable_wal_mode(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(bind=test_engine)

    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    session = TestSessionLocal()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


# ---------------------------------------------------------------------------
# TestClient Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_client(test_db: Session) -> Generator[TestClient, None, None]:
    """Create a FastAPI TestClient with dependency overrides.

    Overrides the ``get_db`` dependency to use the in-memory test database
    and initialises required application state (bedrock_health, analyze_jobs).

    Args:
        test_db: The in-memory database session fixture.

    Yields:
        A TestClient instance configured for testing.
    """

    def _override_get_db() -> Generator[Session, None, None]:
        try:
            yield test_db
        finally:
            pass

    from app.config import get_settings

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_settings] = get_test_settings

    # Initialise application state required by routers
    app.state.bedrock_health = {
        "status": "unknown",
        "last_checked_at": None,
        "message": "No inference calls made yet",
    }
    app.state.analyze_jobs = {}

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client

    # Clean up overrides
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mock Bedrock Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_bedrock() -> Generator[MagicMock, None, None]:
    """Patch the Bedrock client's converse method with a mock.

    The mock returns a valid Bedrock Converse API response containing
    an anomaly_score of 0.85 and a test summary.

    Yields:
        The MagicMock object patching the boto3 bedrock-runtime client's
        converse method.
    """
    mock_client = MagicMock()
    mock_client.converse.return_value = MOCK_BEDROCK_RESPONSE

    with patch(
        "app.services.bedrock_client.boto3.client",
        return_value=mock_client,
    ) as _:
        yield mock_client


# ---------------------------------------------------------------------------
# Mock Webhook Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_webhook() -> Generator[respx.MockRouter, None, None]:
    """Mock outbound POST requests to the test webhook URL.

    Uses respx to intercept HTTP POST requests to TEST_WEBHOOK_URL
    and return an HTTP 200 response.

    Yields:
        The respx MockRouter instance for assertion inspection.
    """
    with respx.mock(assert_all_called=False) as router:
        router.post(TEST_WEBHOOK_URL).mock(
            return_value=Response(200, json={"status": "received"})
        )
        yield router
