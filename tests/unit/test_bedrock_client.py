"""Unit tests for the Bedrock client module."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.config import Settings
from app.services.bedrock_client import (
    BedrockAnalysisResult,
    BedrockClient,
    BedrockParseError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    """Create a Settings instance with test defaults."""
    defaults = {
        "DATABASE_URL": "sqlite:///:memory:",
        "AWS_REGION": "us-east-1",
        "BEDROCK_MODEL_ID": "test-model",
        "BEDROCK_MAX_LOG_SAMPLE": 50,
        "ERROR_RATE_THRESHOLD": 0.1,
        "ANOMALY_SCORE_THRESHOLD": 0.5,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_bedrock_response(score: float = 0.75, summary: str = "Test anomaly") -> dict:
    """Create a valid Bedrock Converse API response."""
    return {
        "output": {
            "message": {
                "content": [
                    {"text": json.dumps({"anomaly_score": score, "summary": summary})}
                ]
            }
        },
        "usage": {"inputTokens": 100, "outputTokens": 50},
    }


def _make_markdown_response(score: float = 0.75, summary: str = "Test anomaly") -> dict:
    """Create a Bedrock response wrapped in markdown code fences."""
    text = f'```json\n{{"anomaly_score": {score}, "summary": "{summary}"}}\n```'
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 100, "outputTokens": 50},
    }


def _make_client_error(error_code: str) -> ClientError:
    """Create a botocore ClientError with the given error code."""
    return ClientError(
        {"Error": {"Code": error_code, "Message": f"Simulated {error_code}"}},
        "Converse",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResponseParsing:
    """Tests for Bedrock response parsing."""

    def test_successful_response_parsing(self):
        """Valid JSON response returns correct anomaly_score and summary."""
        mock_boto = MagicMock()
        mock_boto.converse.return_value = _make_bedrock_response(0.85, "High error rate")

        with patch("app.services.bedrock_client.boto3.client", return_value=mock_boto):
            client = BedrockClient(settings=_make_settings())
            result = client.analyze(
                service="api-gateway",
                window_start=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                window_end=__import__("datetime").datetime(2026, 1, 1, 12, 5),
                error_rate=0.5,
                log_messages=["Error 1", "Error 2"],
            )

        assert isinstance(result, BedrockAnalysisResult)
        assert result.anomaly_score == 0.85
        assert result.summary == "High error rate"

    def test_markdown_fenced_response_parsed_correctly(self):
        """Response wrapped in ```json fences is correctly parsed."""
        mock_boto = MagicMock()
        mock_boto.converse.return_value = _make_markdown_response(0.65, "Markdown response")

        with patch("app.services.bedrock_client.boto3.client", return_value=mock_boto):
            client = BedrockClient(settings=_make_settings())
            result = client.analyze(
                service="auth-service",
                window_start=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                window_end=__import__("datetime").datetime(2026, 1, 1, 12, 5),
                error_rate=0.3,
                log_messages=["Timeout"],
            )

        assert result.anomaly_score == 0.65
        assert result.summary == "Markdown response"

    def test_malformed_response_missing_key_raises_parse_error(self):
        """Response missing 'summary' key raises BedrockParseError."""
        response = {
            "output": {
                "message": {
                    "content": [{"text": json.dumps({"anomaly_score": 0.5})}]
                }
            },
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }
        mock_boto = MagicMock()
        mock_boto.converse.return_value = response

        with patch("app.services.bedrock_client.boto3.client", return_value=mock_boto):
            client = BedrockClient(settings=_make_settings())
            with pytest.raises(BedrockParseError):
                client.analyze(
                    service="api-gateway",
                    window_start=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                    window_end=__import__("datetime").datetime(2026, 1, 1, 12, 5),
                    error_rate=0.5,
                    log_messages=["Error"],
                )

    def test_non_json_response_raises_parse_error(self):
        """Non-JSON text response raises BedrockParseError."""
        response = {
            "output": {
                "message": {
                    "content": [{"text": "This is not JSON at all"}]
                }
            },
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }
        mock_boto = MagicMock()
        mock_boto.converse.return_value = response

        with patch("app.services.bedrock_client.boto3.client", return_value=mock_boto):
            client = BedrockClient(settings=_make_settings())
            with pytest.raises(BedrockParseError):
                client.analyze(
                    service="api-gateway",
                    window_start=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                    window_end=__import__("datetime").datetime(2026, 1, 1, 12, 5),
                    error_rate=0.5,
                    log_messages=["Error"],
                )

    def test_out_of_range_score_raises_parse_error(self):
        """Anomaly score of 1.5 raises BedrockParseError."""
        mock_boto = MagicMock()
        mock_boto.converse.return_value = _make_bedrock_response(1.5, "Too high")

        with patch("app.services.bedrock_client.boto3.client", return_value=mock_boto):
            client = BedrockClient(settings=_make_settings())
            with pytest.raises(BedrockParseError, match="out of range"):
                client.analyze(
                    service="api-gateway",
                    window_start=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                    window_end=__import__("datetime").datetime(2026, 1, 1, 12, 5),
                    error_rate=0.5,
                    log_messages=["Error"],
                )


@pytest.mark.unit
class TestRetryLogic:
    """Tests for Bedrock client retry behavior."""

    @patch("app.services.bedrock_client.time.sleep", return_value=None)
    def test_retry_on_throttling_then_success(self, mock_sleep):
        """ThrottlingException twice then success results in 3 total calls."""
        mock_boto = MagicMock()
        mock_boto.converse.side_effect = [
            _make_client_error("ThrottlingException"),
            _make_client_error("ThrottlingException"),
            _make_bedrock_response(0.7, "Recovered"),
        ]

        with patch("app.services.bedrock_client.boto3.client", return_value=mock_boto):
            client = BedrockClient(settings=_make_settings())
            result = client.analyze(
                service="api-gateway",
                window_start=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                window_end=__import__("datetime").datetime(2026, 1, 1, 12, 5),
                error_rate=0.5,
                log_messages=["Error"],
            )

        assert mock_boto.converse.call_count == 3
        assert result.anomaly_score == 0.7

    @patch("app.services.bedrock_client.time.sleep", return_value=None)
    def test_non_retryable_error_propagates_immediately(self, mock_sleep):
        """ValidationException propagates without retry."""
        mock_boto = MagicMock()
        mock_boto.converse.side_effect = _make_client_error("ValidationException")

        with patch("app.services.bedrock_client.boto3.client", return_value=mock_boto):
            client = BedrockClient(settings=_make_settings())
            with pytest.raises(ClientError) as exc_info:
                client.analyze(
                    service="api-gateway",
                    window_start=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                    window_end=__import__("datetime").datetime(2026, 1, 1, 12, 5),
                    error_rate=0.5,
                    log_messages=["Error"],
                )

        assert mock_boto.converse.call_count == 1
        assert "ValidationException" in str(exc_info.value)


@pytest.mark.unit
class TestHealthStatus:
    """Tests for health status updates."""

    def test_health_updated_to_ok_on_success(self):
        """Successful analysis sets bedrock_health status to 'ok'."""
        mock_boto = MagicMock()
        mock_boto.converse.return_value = _make_bedrock_response(0.5, "OK")
        app_state = SimpleNamespace(bedrock_health={})

        with patch("app.services.bedrock_client.boto3.client", return_value=mock_boto):
            client = BedrockClient(settings=_make_settings(), app_state=app_state)
            client.analyze(
                service="api-gateway",
                window_start=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                window_end=__import__("datetime").datetime(2026, 1, 1, 12, 5),
                error_rate=0.5,
                log_messages=["Error"],
            )

        assert app_state.bedrock_health["status"] == "ok"

    def test_health_updated_to_degraded_on_failure(self):
        """Failed analysis sets bedrock_health status to 'degraded'."""
        mock_boto = MagicMock()
        mock_boto.converse.return_value = {
            "output": {"message": {"content": [{"text": "not json"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }
        app_state = SimpleNamespace(bedrock_health={})

        with patch("app.services.bedrock_client.boto3.client", return_value=mock_boto):
            client = BedrockClient(settings=_make_settings(), app_state=app_state)
            with pytest.raises(BedrockParseError):
                client.analyze(
                    service="api-gateway",
                    window_start=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                    window_end=__import__("datetime").datetime(2026, 1, 1, 12, 5),
                    error_rate=0.5,
                    log_messages=["Error"],
                )

        assert app_state.bedrock_health["status"] == "degraded"


@pytest.mark.unit
class TestLogMessageCapping:
    """Tests for log message capping at BEDROCK_MAX_LOG_SAMPLE."""

    def test_log_messages_capped_at_max_sample(self):
        """Only the last BEDROCK_MAX_LOG_SAMPLE messages are sent to Bedrock."""
        max_sample = 5
        mock_boto = MagicMock()
        mock_boto.converse.return_value = _make_bedrock_response(0.5, "Capped")

        with patch("app.services.bedrock_client.boto3.client", return_value=mock_boto):
            client = BedrockClient(settings=_make_settings(BEDROCK_MAX_LOG_SAMPLE=max_sample))
            messages = [f"Message {i}" for i in range(20)]
            client.analyze(
                service="api-gateway",
                window_start=__import__("datetime").datetime(2026, 1, 1, 12, 0),
                window_end=__import__("datetime").datetime(2026, 1, 1, 12, 5),
                error_rate=0.5,
                log_messages=messages,
            )

        # Verify the prompt only contains the last 5 messages
        call_args = mock_boto.converse.call_args
        prompt_text = call_args[1]["messages"][0]["content"][0]["text"] if call_args[1] else call_args[0][0]
        # The prompt should contain "Message 15" through "Message 19" but not "Message 0"
        assert "Message 19" in str(call_args)
        assert "Message 0" not in str(call_args)
