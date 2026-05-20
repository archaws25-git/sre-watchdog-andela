# Observability Evaluation

This document evaluates the observability capabilities of the SRE Watchdog platform, covering structured logging, health endpoints, metrics, and Bedrock health monitoring.

---

## 1. Structured JSON Logging

All application logs are emitted as structured JSON to stdout, enabling easy parsing by log aggregation tools (CloudWatch Logs, ELK, Datadog, etc.).

### Log Format

Every log entry follows a consistent schema:

```json
{
  "timestamp": "2025-01-15T14:32:00.123Z",
  "level": "INFO",
  "logger": "app.services.log_ingestion_service",
  "message": "Batch ingested successfully",
  "extra_field_1": "value",
  "extra_field_2": 42
}
```

### Log Categories

| Category | Logger Name | Key Fields |
|----------|-------------|-----------|
| HTTP Requests | `app.middleware` | method, path, status_code, latency_ms, request_id |
| Log Ingestion | `app.services.log_ingestion_service` | accepted, rejected |
| Anomaly Detection | `app.services.anomaly_detector` | service, error_rate, anomaly_id, status |
| Bedrock Inference | `app.services.bedrock_client` | service, anomaly_id, anomaly_score, input_tokens, output_tokens, latency_ms |
| Alert Dispatch | `app.services.alert_service` | anomaly_id, service, severity, webhook_url, http_status |
| Scheduler | `app.scheduler` | job_id, next_run_time |

### Log Levels Usage

| Level | Usage |
|-------|-------|
| DEBUG | Detailed diagnostic information (disabled in production) |
| INFO | Normal operational events (ingestion, detection, dispatch) |
| WARNING | Degraded conditions (missing credentials, Bedrock timeout, retry attempts) |
| ERROR | Failures requiring attention (dispatch failure after retries, parse errors) |
| CRITICAL | System-level failures (database unreachable, startup failures) |

### Configuration

The log level is configurable via the `LOG_LEVEL` environment variable (default: `INFO`).

---

## 2. Health Endpoint Design

### Endpoint: `GET /health`

The health endpoint provides a comprehensive view of the Watchdog's operational status.

**Response (HTTP 200 — healthy):**
```json
{
  "status": "ok",
  "database": "ok",
  "bedrock": {
    "status": "ok",
    "last_checked_at": "2025-01-15T14:30:00Z",
    "message": "Last inference completed successfully"
  }
}
```

**Response (HTTP 503 — degraded):**
```json
{
  "status": "degraded",
  "database": "unreachable",
  "bedrock": {
    "status": "unknown",
    "last_checked_at": null,
    "message": "No inference calls made yet"
  }
}
```

### Health Check Logic

| Component | Check Method | Failure Response |
|-----------|-------------|-----------------|
| Database | `SELECT 1` against SQLite | HTTP 503, `database: "unreachable"` |
| Bedrock | Cached status from last real inference | Status field reflects last known state |
| Overall | Degraded if any component is unhealthy | HTTP 503 if database is down |

### Integration with Load Balancers

The `/health` endpoint is designed for use as:
- **ALB health check target** — Returns 200 for healthy, 503 for unhealthy
- **Container orchestrator liveness probe** — Lightweight, fast response
- **Monitoring system target** — Structured JSON for automated parsing

---

## 3. Metrics Endpoint

### Endpoint: `GET /metrics`

The metrics endpoint returns operational counters computed from live database queries.

**Response:**
```json
{
  "total_logs_ingested": 10000,
  "total_anomalies_detected": 15,
  "total_alerts_dispatched": 8,
  "total_failed_alerts": 1,
  "total_analysis_failed": 3,
  "total_cooldown_suppressed": 3
}
```

### Counter Definitions

| Counter | SQL Source | Significance |
|---------|-----------|--------------|
| `total_logs_ingested` | `COUNT(*) FROM log_entries` | Data volume indicator |
| `total_anomalies_detected` | `COUNT(*) FROM anomaly_windows` | Gate 1 breach frequency |
| `total_alerts_dispatched` | `COUNT(*) FROM alert_records WHERE dispatch_status='sent'` | Successful alert delivery |
| `total_failed_alerts` | `COUNT(*) FROM alert_records WHERE dispatch_status='failed'` | Webhook delivery failures |
| `total_analysis_failed` | `COUNT(*) FROM anomaly_windows WHERE status='analysis_failed'` | Bedrock reliability indicator |
| `total_cooldown_suppressed` | `COUNT(*) FROM anomaly_windows WHERE suppression_reason='cooldown_active'` | Suppression frequency |

### Design Decision: DB Queries vs. In-Memory Counters

Counters are computed from live database queries rather than in-memory counters. This ensures:
- **Accuracy across restarts** — No counter reset on application restart
- **Consistency** — Single source of truth (database)
- **Simplicity** — No counter synchronization logic needed

**Trade-off:** Slightly higher latency per `/metrics` call (SQLite queries). Acceptable for the expected query frequency.

---

## 4. Bedrock Health Caching

### Design

The Bedrock health status is cached in `app.state.bedrock_health` and updated after every real inference call. No synthetic health-check calls are made.

### State Machine

```
                    ┌─────────────┐
    Startup ──────► │   unknown   │
                    └──────┬──────┘
                           │ First inference call
                    ┌──────┴──────┐
              ┌─────┤             ├─────┐
              │     └─────────────┘     │
              ▼                         ▼
    ┌─────────────┐           ┌─────────────┐
    │     ok      │ ◄───────► │  degraded   │
    └─────────────┘           └─────────────┘
     (success)                 (failure/timeout)
```

### Cache Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `ok`, `degraded`, or `unknown` |
| `last_checked_at` | datetime | Timestamp of last real inference call |
| `message` | string | Human-readable status description |

### Update Triggers

- **On successful inference:** status → `ok`, message → "Last inference completed successfully"
- **On inference failure:** status → `degraded`, message → error description
- **On startup (no credentials):** status → `degraded`, message → "No AWS credentials found at startup"
- **Never updated by:** synthetic probes, health check requests, or timer-based polling

---

## 5. Request Tracing

### Request ID

Every HTTP request is assigned a unique `request_id` (UUID v4) by the logging middleware. This ID is:
- Included in the structured log entry for the request
- Available for correlation across log entries within the same request lifecycle

### Latency Tracking

Request latency is measured and logged for every HTTP request:
```json
{
  "method": "POST",
  "path": "/logs/ingest",
  "status_code": 200,
  "latency_ms": 45.23,
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

---

## 6. Observability Gaps and Future Improvements

| Gap | Current State | Recommended Improvement |
|-----|---------------|------------------------|
| Distributed tracing | Not implemented | Add OpenTelemetry instrumentation |
| Prometheus metrics | Custom `/metrics` endpoint | Expose Prometheus-compatible `/metrics` format |
| Alerting on Watchdog health | Manual monitoring | Integrate with CloudWatch Alarms or PagerDuty |
| Log retention | Unbounded growth | Implement TTL-based purging or log rotation |
| Dashboard latency metrics | Not tracked | Add P50/P95/P99 latency percentiles to `/metrics` |
| Bedrock cost tracking | Logged per-call | Aggregate daily/weekly cost summaries in `/metrics` |

---

## 7. Evaluation Summary

| Observability Pillar | MVP Coverage | Production Readiness |
|---------------------|--------------|---------------------|
| **Logging** | Structured JSON, all key events | Ready (add log aggregation) |
| **Health Checks** | `/health` with DB + Bedrock status | Ready (add to ALB target group) |
| **Metrics** | Operational counters via `/metrics` | Partial (add Prometheus format) |
| **Tracing** | Request ID per request | Partial (add OpenTelemetry) |
| **Alerting** | Self-monitoring via `/metrics` counters | Needs external alerting integration |
