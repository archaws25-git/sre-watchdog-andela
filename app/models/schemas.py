"""Pydantic request/response schemas and enums for the SRE Watchdog API.

This module defines all data transfer objects used across the API endpoints.
Enums enforce valid values for log levels, anomaly statuses, alert dispatch
states, severity labels, Bedrock health states, and analyze job statuses.
Schemas provide request validation and response serialization for every
endpoint in the system.

Typical usage::

    from app.models.schemas import LogEntryCreate, IngestRequest, LogLevel

    entry = LogEntryCreate(
        timestamp=datetime.utcnow(),
        service="api-gateway",
        level=LogLevel.ERROR,
        message="Connection timeout",
    )
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LogLevel(str, Enum):
    """Valid log severity levels for ingested log entries.

    Values correspond to standard Python logging levels.
    """

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AnomalyStatus(str, Enum):
    """Lifecycle status of an anomaly window record.

    Tracks the full disposition from initial detection through alert
    dispatch or suppression.
    """

    PENDING_ANALYSIS = "pending_analysis"
    CONFIRMED = "confirmed"
    BELOW_SCORE_THRESHOLD = "below_score_threshold"
    ANALYSIS_FAILED = "analysis_failed"
    ALERTED = "alerted"
    SUPPRESSED = "suppressed"


class AlertDispatchStatus(str, Enum):
    """Outcome of an alert dispatch attempt.

    Attributes:
        SENT: Webhook POST succeeded.
        FAILED: Webhook POST failed after all retries.
        SUPPRESSED: Alert was suppressed due to active cooldown.
    """

    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class SeverityLabel(str, Enum):
    """Severity classification derived from the numeric anomaly score.

    Bands:
        LOW: 0.00–0.39
        MEDIUM: 0.40–0.69
        HIGH: 0.70–0.89
        CRITICAL: 0.90–1.00
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class BedrockHealthStatus(str, Enum):
    """Cached health status of the AWS Bedrock integration.

    Attributes:
        OK: Last inference call succeeded.
        DEGRADED: Last inference call failed or credentials missing.
        UNKNOWN: No inference call has been made yet.
    """

    OK = "ok"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class AnalyzeJobStatus(str, Enum):
    """Status of an asynchronous analysis job.

    Transitions: pending → running → completed | failed.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Log Ingestion Schemas
# ---------------------------------------------------------------------------


class LogEntryCreate(BaseModel):
    """Schema for creating a single log entry via POST /logs/ingest.

    Attributes:
        timestamp: ISO 8601 datetime of the log event.
        service: Name of the originating service (one of the 5 named services).
        level: Log severity level.
        message: Human-readable log message text.
    """

    timestamp: datetime
    service: str
    level: LogLevel
    message: str


class LogEntryResponse(LogEntryCreate):
    """Schema for a persisted log entry returned by the API.

    Extends LogEntryCreate with server-assigned fields.

    Attributes:
        id: Auto-incremented primary key.
        ingested_at: Server timestamp when the entry was persisted.
    """

    id: int
    ingested_at: datetime

    model_config = {"from_attributes": True}


class IngestRequest(BaseModel):
    """Request body for POST /logs/ingest.

    Attributes:
        entries: List of log entries to ingest. Maximum batch size is
            controlled by MAX_INGEST_BATCH_SIZE.
    """

    entries: List[LogEntryCreate]


class IngestResponse(BaseModel):
    """Response body for POST /logs/ingest.

    Attributes:
        accepted: Number of entries successfully persisted.
        rejected: Number of entries that failed validation.
        errors: List of human-readable error descriptions for rejected entries.
    """

    accepted: int
    rejected: int
    errors: List[str]


class PaginatedLogsResponse(BaseModel):
    """Paginated response envelope for GET /logs.

    Attributes:
        total_count: Total number of entries matching the applied filters.
        page: Current page number (1-indexed).
        page_size: Number of entries per page.
        has_more: Whether additional pages exist beyond the current one.
        data: List of log entries for the current page.
    """

    total_count: int
    page: int
    page_size: int
    has_more: bool
    data: List[LogEntryResponse]


# ---------------------------------------------------------------------------
# Anomaly Window Schemas
# ---------------------------------------------------------------------------


class AnomalyWindowResponse(BaseModel):
    """Response schema for a single anomaly window record.

    Contains the full lifecycle disposition including detection metadata,
    Bedrock analysis outcome, alert decision, and suppression reason.

    Attributes:
        id: Auto-incremented primary key.
        service: Affected service name.
        window_start: Start of the anomaly time window (ISO 8601).
        window_end: End of the anomaly time window (ISO 8601).
        error_rate: Computed error rate within the window (0.0–1.0).
        anomaly_score: Bedrock-assigned score (None until Gate 2 completes).
        status: Current lifecycle status.
        suppression_reason: Reason for suppression (e.g. cooldown_active).
        ai_summary: Plain-text summary from Bedrock analysis.
        failure_reason: Description of failure (populated on analysis_failed).
        alert_id: Foreign key to the linked alert record (if dispatched).
        created_at: Record creation timestamp.
        updated_at: Last modification timestamp.
    """

    id: int
    service: str
    window_start: datetime
    window_end: datetime
    error_rate: float
    anomaly_score: Optional[float]
    status: AnomalyStatus
    suppression_reason: Optional[str]
    ai_summary: Optional[str]
    failure_reason: Optional[str]
    alert_id: Optional[int]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Analyze Job Schemas
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    """Request body for POST /analyze.

    Attributes:
        service: Optional service name filter. When omitted, analysis runs
            across all 5 services.
        start_time: Start of the analysis time range (ISO 8601).
        end_time: End of the analysis time range (ISO 8601).
    """

    service: Optional[str] = None
    start_time: datetime
    end_time: datetime


class AnalyzeResponse(BaseModel):
    """Response body for POST /analyze (HTTP 202 Accepted).

    Attributes:
        job_id: UUID identifying the created analysis job.
        status: Initial job status (always 'pending' on creation).
    """

    job_id: str
    status: AnalyzeJobStatus = AnalyzeJobStatus.PENDING


class AnalyzeJobResult(BaseModel):
    """Response body for GET /analyze/{job_id}.

    Attributes:
        job_id: UUID identifying the analysis job.
        status: Current job status (pending, running, completed, failed).
        anomalies_found: Number of anomalies detected (populated on completion).
        alerts_dispatched: Number of alerts sent (populated on completion).
        error: Error description (populated on failure).
        completed_at: Timestamp when the job finished (populated on completion).
    """

    job_id: str
    status: AnalyzeJobStatus
    anomalies_found: Optional[int] = None
    alerts_dispatched: Optional[int] = None
    error: Optional[str] = None
    completed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Alert Schemas
# ---------------------------------------------------------------------------


class AlertRecordResponse(BaseModel):
    """Response schema for a single alert dispatch record.

    Attributes:
        id: Auto-incremented primary key.
        anomaly_id: Foreign key to the source anomaly window.
        dispatched_at: Timestamp of the dispatch attempt.
        webhook_url: Target URL for the webhook POST.
        payload: Full webhook payload as a dictionary.
        http_status: HTTP response status code (None if suppressed or failed
            before response).
        dispatch_status: Outcome of the dispatch attempt.
        severity: Severity label derived from the anomaly score.
    """

    id: int
    anomaly_id: int
    dispatched_at: datetime
    webhook_url: str
    payload: dict
    http_status: Optional[int]
    dispatch_status: AlertDispatchStatus
    severity: SeverityLabel

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Health Schemas
# ---------------------------------------------------------------------------


class BedrockHealthDetail(BaseModel):
    """Nested schema for the Bedrock health status within /health response.

    Attributes:
        status: Current cached Bedrock status.
        last_checked_at: Timestamp of the last real inference call (None if
            no call has been made yet).
        message: Human-readable description of the current status.
    """

    status: BedrockHealthStatus
    last_checked_at: Optional[datetime]
    message: str


class HealthResponse(BaseModel):
    """Response body for GET /health.

    Attributes:
        status: Overall system status ('ok' or 'degraded').
        database: Database connectivity status ('ok' or 'unreachable').
        bedrock: Detailed Bedrock integration health.
    """

    status: str
    database: str
    bedrock: BedrockHealthDetail


# ---------------------------------------------------------------------------
# Metrics Schemas
# ---------------------------------------------------------------------------


class MetricsResponse(BaseModel):
    """Response body for GET /metrics.

    Contains operational counters derived from database queries.

    Attributes:
        total_logs_ingested: Total number of log entries in the database.
        total_anomalies_detected: Total anomaly window records created.
        total_alerts_dispatched: Total alerts with dispatch_status='sent'.
        total_failed_alerts: Total alerts with dispatch_status='failed'.
        total_analysis_failed: Total anomalies with status='analysis_failed'.
        total_cooldown_suppressed: Total anomalies with status='suppressed'.
    """

    total_logs_ingested: int
    total_anomalies_detected: int
    total_alerts_dispatched: int
    total_failed_alerts: int
    total_analysis_failed: int
    total_cooldown_suppressed: int


# ---------------------------------------------------------------------------
# Webhook Echo Schemas
# ---------------------------------------------------------------------------


class WebhookEchoResponse(BaseModel):
    """Response body for POST /webhooks/echo.

    Attributes:
        received_at: Server timestamp when the payload was received.
        payload: The echoed-back JSON payload.
    """

    received_at: datetime
    payload: dict
