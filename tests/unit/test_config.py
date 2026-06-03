"""Unit tests for the application configuration module."""

import pytest
from pydantic import ValidationError

from app.config import ConfigurationError, Settings, get_settings


@pytest.mark.unit
class TestSettingsLoad:
    """Tests for successful settings loading from environment variables."""

    def test_settings_load_with_defaults(self):
        """Settings can be instantiated with all defaults."""
        settings = Settings()
        assert settings.DATABASE_URL == "sqlite:///./watchdog.db"
        assert settings.AWS_REGION == "us-east-1"
        assert settings.ERROR_RATE_THRESHOLD == 0.1
        assert settings.ANOMALY_SCORE_THRESHOLD == 0.5
        assert settings.SLIDING_WINDOW_MINUTES == 5
        assert settings.ALERT_COOLDOWN_MINUTES == 15
        assert settings.MAX_INGEST_BATCH_SIZE == 500

    def test_settings_load_from_env_vars(self, monkeypatch):
        """Settings reads values from environment variables."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.db")
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        monkeypatch.setenv("ERROR_RATE_THRESHOLD", "0.25")

        settings = Settings()
        assert settings.DATABASE_URL == "sqlite:///./test.db"
        assert settings.AWS_REGION == "eu-west-1"
        assert settings.ERROR_RATE_THRESHOLD == 0.25


@pytest.mark.unit
class TestConfigurationError:
    """Tests for ConfigurationError raised on invalid configuration."""

    def test_configuration_error_on_invalid_threshold(self, monkeypatch):
        """get_settings raises ConfigurationError for invalid threshold values."""
        monkeypatch.setenv("ERROR_RATE_THRESHOLD", "2.0")
        with pytest.raises(ConfigurationError) as exc_info:
            get_settings()
        assert "ERROR_RATE_THRESHOLD" in exc_info.value.message

    def test_configuration_error_on_invalid_type(self, monkeypatch):
        """get_settings raises ConfigurationError for non-numeric threshold."""
        monkeypatch.setenv("ERROR_RATE_THRESHOLD", "not_a_number")
        with pytest.raises(ConfigurationError):
            get_settings()


@pytest.mark.unit
class TestFieldValidators:
    """Tests for field_validator on threshold values."""

    def test_error_rate_threshold_rejects_above_one(self):
        """ERROR_RATE_THRESHOLD > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            Settings(ERROR_RATE_THRESHOLD=1.5)

    def test_error_rate_threshold_rejects_below_zero(self):
        """ERROR_RATE_THRESHOLD < 0.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            Settings(ERROR_RATE_THRESHOLD=-0.1)

    def test_anomaly_score_threshold_rejects_above_one(self):
        """ANOMALY_SCORE_THRESHOLD > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            Settings(ANOMALY_SCORE_THRESHOLD=1.1)

    def test_anomaly_score_threshold_rejects_below_zero(self):
        """ANOMALY_SCORE_THRESHOLD < 0.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            Settings(ANOMALY_SCORE_THRESHOLD=-0.5)

    def test_error_rate_threshold_accepts_zero(self):
        """ERROR_RATE_THRESHOLD accepts boundary value 0.0."""
        settings = Settings(ERROR_RATE_THRESHOLD=0.0)
        assert settings.ERROR_RATE_THRESHOLD == 0.0

    def test_error_rate_threshold_accepts_one(self):
        """ERROR_RATE_THRESHOLD accepts boundary value 1.0."""
        settings = Settings(ERROR_RATE_THRESHOLD=1.0)
        assert settings.ERROR_RATE_THRESHOLD == 1.0

    def test_anomaly_score_threshold_accepts_zero(self):
        """ANOMALY_SCORE_THRESHOLD accepts boundary value 0.0."""
        settings = Settings(ANOMALY_SCORE_THRESHOLD=0.0)
        assert settings.ANOMALY_SCORE_THRESHOLD == 0.0

    def test_anomaly_score_threshold_accepts_one(self):
        """ANOMALY_SCORE_THRESHOLD accepts boundary value 1.0."""
        settings = Settings(ANOMALY_SCORE_THRESHOLD=1.0)
        assert settings.ANOMALY_SCORE_THRESHOLD == 1.0
