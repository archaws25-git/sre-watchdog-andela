"""SQLAlchemy ORM models for the SRE Watchdog application.

Defines the four core database tables:
- LogEntry: Structured log records ingested from monitored services.
- AnomalyWindow: Detected anomaly windows with full lifecycle disposition.
- AlertRecord: Webhook alert dispatch records linked to anomaly windows.
- WebhookEchoLog: Raw payloads received at the /webhooks/echo endpoint.

All models inherit from the shared ``Base`` defined in ``app.database``.
Table creation is triggered by importing this module (which calls
``Base.metadata.create_all(engine)`` at module load time).
"""

from sqlalchemy import (
    Column,
    Float,
    Index,
    Integer,
    Text,
    ForeignKey,
    text,
)
from sqlalchemy.orm import relationship

from app.database import Base, engine


# ---------------------------------------------------------------------------
# LogEntry
# ---------------------------------------------------------------------------


class LogEntry(Base):
    """A single structured log record ingested via POST /logs/ingest.

    Attributes:
        id: Auto-incrementing primary key.
        timestamp: ISO 8601 timestamp of the original log event.
        service: Name of the originating service (one of the 5 monitored services).
        level: Log severity level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        message: Free-text log message body.
        ingested_at: Server-side timestamp recording when the entry was persisted.
    """

    __tablename__ = "log_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(Text, nullable=False)
    service = Column(Text, nullable=False)
    level = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    ingested_at = Column(
        Text,
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"),
    )

    __table_args__ = (
        Index("idx_log_entries_service_timestamp", "service", "timestamp"),
        Index("idx_log_entries_level", "level"),
        Index("idx_log_entries_timestamp", "timestamp"),
    )

    def __repr__(self) -> str:
        return (
            f"<LogEntry(id={self.id}, service={self.service!r}, "
            f"level={self.level!r}, timestamp={self.timestamp!r})>"
        )


# ---------------------------------------------------------------------------
# AnomalyWindow
# ---------------------------------------------------------------------------


class AnomalyWindow(Base):
    """A detected anomaly window carrying full lifecycle disposition.

    Tracks the complete journey from Gate 1 statistical detection through
    Gate 2 Bedrock analysis to alert dispatch or suppression.

    Attributes:
        id: Auto-incrementing primary key.
        service: The affected service name.
        window_start: ISO 8601 start of the anomaly time window.
        window_end: ISO 8601 end of the anomaly time window.
        error_rate: Computed error rate (0.0–1.0) during the window.
        anomaly_score: Bedrock-assigned severity score (NULL until Gate 2 completes).
        status: Current lifecycle status (pending_analysis, confirmed,
            below_score_threshold, analysis_failed, alerted, suppressed).
        suppression_reason: Reason for suppression (cooldown_active,
            orphaned_on_restart, or NULL).
        ai_summary: Plain-text summary from Bedrock (NULL until Gate 2).
        failure_reason: Populated when status is analysis_failed.
        alert_id: Foreign key to the linked alert_records row (if dispatched).
        created_at: Server-side creation timestamp.
        updated_at: Server-side last-update timestamp.
    """

    __tablename__ = "anomaly_windows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service = Column(Text, nullable=False)
    window_start = Column(Text, nullable=False)
    window_end = Column(Text, nullable=False)
    error_rate = Column(Float, nullable=False)
    anomaly_score = Column(Float, nullable=True)
    status = Column(Text, nullable=False)
    suppression_reason = Column(Text, nullable=True)
    ai_summary = Column(Text, nullable=True)
    failure_reason = Column(Text, nullable=True)
    alert_id = Column(Integer, ForeignKey("alert_records.id"), nullable=True)
    created_at = Column(
        Text,
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"),
    )
    updated_at = Column(
        Text,
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"),
    )

    alert = relationship("AlertRecord", foreign_keys=[alert_id], backref="anomaly_window")

    __table_args__ = (
        Index("idx_anomaly_windows_service", "service"),
        Index("idx_anomaly_windows_status", "status"),
        Index("idx_anomaly_windows_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AnomalyWindow(id={self.id}, service={self.service!r}, "
            f"status={self.status!r}, score={self.anomaly_score})>"
        )


# ---------------------------------------------------------------------------
# AlertRecord
# ---------------------------------------------------------------------------


class AlertRecord(Base):
    """A webhook alert dispatch record linked to an anomaly window.

    Created for every confirmed anomaly that reaches the Alert_Service,
    including suppressed dispatches (for full audit trail).

    Attributes:
        id: Auto-incrementing primary key.
        anomaly_id: Foreign key to the source anomaly_windows row.
        dispatched_at: Timestamp of the dispatch attempt.
        webhook_url: Target URL for the webhook POST.
        payload: JSON blob of the full webhook payload.
        http_status: HTTP response status (NULL if suppressed or failed before response).
        dispatch_status: Outcome (sent, failed, suppressed).
        severity: Assigned severity label (LOW, MEDIUM, HIGH, CRITICAL).
    """

    __tablename__ = "alert_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    anomaly_id = Column(
        Integer,
        ForeignKey("anomaly_windows.id"),
        nullable=False,
    )
    dispatched_at = Column(
        Text,
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"),
    )
    webhook_url = Column(Text, nullable=False)
    payload = Column(Text, nullable=False)
    http_status = Column(Integer, nullable=True)
    dispatch_status = Column(Text, nullable=False)
    severity = Column(Text, nullable=False)

    __table_args__ = (
        Index("idx_alert_records_anomaly_id", "anomaly_id"),
        Index("idx_alert_records_dispatched_at", "dispatched_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AlertRecord(id={self.id}, anomaly_id={self.anomaly_id}, "
            f"dispatch_status={self.dispatch_status!r}, severity={self.severity!r})>"
        )


# ---------------------------------------------------------------------------
# WebhookEchoLog
# ---------------------------------------------------------------------------


class WebhookEchoLog(Base):
    """Raw JSON payloads received at the /webhooks/echo endpoint.

    Used for testing and verifying webhook dispatch without an external target.

    Attributes:
        id: Auto-incrementing primary key.
        received_at: Server-side timestamp of when the payload was received.
        payload: Raw JSON body as a text blob.
    """

    __tablename__ = "webhook_echo_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    received_at = Column(
        Text,
        nullable=False,
        server_default=text("(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"),
    )
    payload = Column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<WebhookEchoLog(id={self.id}, received_at={self.received_at!r})>"


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

Base.metadata.create_all(bind=engine)
