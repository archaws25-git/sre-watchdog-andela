"""Configuration module for the SRE Watchdog application.

Reads all tuneable values exclusively from environment variables (or a .env
file) using pydantic-settings.  No secrets, thresholds, or environment-specific
values are hardcoded here.

Typical usage::

    from app.config import get_settings

    settings = get_settings()
    print(settings.DATABASE_URL)
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import ValidationError


class ConfigurationError(Exception):
    """Raised when the application configuration is missing or invalid.

    Wraps pydantic ``ValidationError`` so callers do not need to import
    pydantic directly to handle configuration failures.

    Attributes:
        message: Human-readable description of the configuration problem.
    """

    def __init__(self, message: str) -> None:
        """Initialise ConfigurationError with a descriptive message.

        Args:
            message: Description of the configuration problem.
        """
        super().__init__(message)
        self.message = message


class Settings(BaseSettings):
    """Application settings loaded from environment variables or a .env file.

    All fields have safe defaults so the application can start without a
    .env file during development.  In production, override the defaults via
    real environment variables or a populated .env file.

    Attributes:
        DATABASE_URL: SQLAlchemy-compatible database connection string.
        AWS_REGION: AWS region used for Bedrock API calls.
        BEDROCK_MODEL_ID: Bedrock model identifier for anomaly analysis.
        BEDROCK_MAX_LOG_SAMPLE: Maximum number of log messages sent to Bedrock
            per analysis request.
        ERROR_RATE_THRESHOLD: Gate 1 threshold; error rate above this value
            triggers an anomaly window (0.0–1.0).
        ANOMALY_SCORE_THRESHOLD: Gate 2 threshold; Bedrock scores at or above
            this value trigger alert dispatch (0.0–1.0).
        SLIDING_WINDOW_MINUTES: Width of the sliding window used to compute
            the per-service error rate.
        ALERT_COOLDOWN_MINUTES: Minimum minutes between alerts for the same
            service.
        DETECTION_INTERVAL_SECONDS: APScheduler tick interval in seconds.
        MAX_INGEST_BATCH_SIZE: Hard cap on entries accepted per ingest request.
        WEBHOOK_URL: Target URL for outbound alert webhook POST requests.
        LOG_LEVEL: Python logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        APP_HOST: Host address the Uvicorn server binds to.
        APP_PORT: TCP port the Uvicorn server listens on.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    DATABASE_URL: str = "sqlite:///./watchdog.db"

    # --- AWS / Bedrock ---
    AWS_REGION: str = "us-east-1"
    BEDROCK_MODEL_ID: str = "us.anthropic.claude-sonnet-4-6"
    BEDROCK_MAX_LOG_SAMPLE: int = 50

    # --- Detection thresholds ---
    ERROR_RATE_THRESHOLD: float = 0.1
    ANOMALY_SCORE_THRESHOLD: float = 0.5

    # --- Timing ---
    SLIDING_WINDOW_MINUTES: int = 5
    ALERT_COOLDOWN_MINUTES: int = 15
    DETECTION_INTERVAL_SECONDS: int = 60

    # --- Ingestion ---
    MAX_INGEST_BATCH_SIZE: int = 500

    # --- Alerting ---
    WEBHOOK_URL: str = "http://localhost:8000/webhooks/echo"

    # --- Application ---
    LOG_LEVEL: str = "INFO"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("ERROR_RATE_THRESHOLD")
    @classmethod
    def validate_error_rate_threshold(cls, value: float) -> float:
        """Validate that ERROR_RATE_THRESHOLD is within [0.0, 1.0].

        Args:
            value: The candidate threshold value.

        Returns:
            The validated value, unchanged.

        Raises:
            ValueError: If ``value`` is outside the range [0.0, 1.0].
        """
        if not (0.0 <= value <= 1.0):
            raise ValueError(
                f"ERROR_RATE_THRESHOLD must be between 0.0 and 1.0, got {value}"
            )
        return value

    @field_validator("ANOMALY_SCORE_THRESHOLD")
    @classmethod
    def validate_anomaly_score_threshold(cls, value: float) -> float:
        """Validate that ANOMALY_SCORE_THRESHOLD is within [0.0, 1.0].

        Args:
            value: The candidate threshold value.

        Returns:
            The validated value, unchanged.

        Raises:
            ValueError: If ``value`` is outside the range [0.0, 1.0].
        """
        if not (0.0 <= value <= 1.0):
            raise ValueError(
                f"ANOMALY_SCORE_THRESHOLD must be between 0.0 and 1.0, got {value}"
            )
        return value


def get_settings() -> Settings:
    """Create and return a validated ``Settings`` instance.

    Reads configuration from environment variables and the .env file.
    Wraps pydantic ``ValidationError`` in a ``ConfigurationError`` so
    callers receive a single, descriptive exception type on misconfiguration.

    Returns:
        A fully validated ``Settings`` object.

    Raises:
        ConfigurationError: If any required environment variable is missing
            or any value fails validation (e.g. a threshold outside [0.0, 1.0]).

    Example::

        from app.config import get_settings, ConfigurationError

        try:
            settings = get_settings()
        except ConfigurationError as exc:
            print(f"Bad config: {exc.message}")
            raise SystemExit(1)
    """
    try:
        return Settings()
    except ValidationError as exc:
        # Build a concise, human-readable summary of every failing field.
        errors = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigurationError(
            f"Application configuration is invalid — {errors}"
        ) from exc
