# Data Flow and Integration

This document describes the primary data flows through the SRE Watchdog system, from log ingestion through anomaly detection, alert dispatch, and dashboard visualization.

---

## 1. Log Ingestion Flow

External services or the synthetic log generator submit structured log entries to the Watchdog via the REST API.

```
┌──────────────────┐     POST /logs/ingest      ┌──────────────────────┐
│  Log Source      │ ──────────────────────────► │  FastAPI Router      │
│  (CLI / Service) │     JSON: {entries: [...]}  │  (app/routers/logs)  │
└──────────────────┘                             └──────────┬───────────┘
                                                            │
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │  Log_Ingestion_Svc   │
                                                 │  - Validate schema   │
                                                 │  - Check batch size  │
                                                 │  - Persist to DB     │
                                                 └──────────┬───────────┘
                                                            │
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │  SQLite (WAL mode)   │
                                                 │  log_entries table   │
                                                 └──────────────────────┘
```

**Key details:**
- Maximum batch size: 500 entries (returns HTTP 413 if exceeded)
- Each entry validated against `LogEntryCreate` schema (timestamp, service, level, message)
- Atomic persistence — all valid entries in a batch are committed in a single transaction
- Structured JSON log emitted for every ingest request (accepted/rejected counts)

---

## 2. Detection Pipeline Flow (Gate 1 → Gate 2)

The detection pipeline runs on a fixed interval via APScheduler and processes anomalies through two gates.

```
┌──────────────────┐
│  APScheduler     │  Every DETECTION_INTERVAL_SECONDS (default: 60s)
│  Interval Tick   │
└────────┬─────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  GATE 1 — Statistical Pre-filter (Synchronous)                  │
│                                                                  │
│  For each of 5 services:                                         │
│    1. Query log_entries WHERE timestamp >= now - SLIDING_WINDOW  │
│    2. Compute error_rate = (ERROR + CRITICAL) / total            │
│    3. IF error_rate > ERROR_RATE_THRESHOLD:                      │
│       → INSERT anomaly_windows (status=pending_analysis)         │
│       → Enqueue BackgroundTask for Gate 2                        │
│                                                                  │
│  Tick returns immediately — never awaits Bedrock                 │
└────────┬────────────────────────────────────────────────────────┘
         │ (one BackgroundTask per breaching service)
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  GATE 2 — AI Analysis (Asynchronous BackgroundTask)             │
│                                                                  │
│  1. Fetch anomaly_window record + log messages for window        │
│  2. Construct prompt (service, window, error_rate, messages)     │
│  3. Call Bedrock Converse API (with retry on transient errors)   │
│  4. Parse response → anomaly_score + ai_summary                 │
│                                                                  │
│  Decision tree:                                                  │
│  ├─ Parse failure → status=analysis_failed                       │
│  ├─ Score < ANOMALY_SCORE_THRESHOLD → status=below_score         │
│  └─ Score >= threshold:                                          │
│      ├─ Cooldown active → status=suppressed                      │
│      └─ No cooldown → status=confirmed → dispatch alert          │
└─────────────────────────────────────────────────────────────────┘
```

**Concurrency model:**
- Gate 1 runs synchronously in APScheduler's thread pool (single instance, coalesced)
- Gate 2 runs as independent FastAPI BackgroundTasks — multiple services analyzed concurrently
- SQLite WAL mode supports concurrent reads (Gate 1 queries) and writes (Gate 2 updates)

---

## 3. Alert Dispatch Flow

When Gate 2 confirms an anomaly and no cooldown is active, the Alert Service dispatches a webhook notification.

```
┌──────────────────┐                    ┌──────────────────────┐
│  Gate 2 Result   │  score >= threshold │  Alert_Service       │
│  (confirmed)     │ ──────────────────► │  1. Map severity     │
└──────────────────┘   no cooldown       │  2. Build payload    │
                                         │  3. POST to webhook  │
                                         └──────────┬───────────┘
                                                    │
                              ┌──────────────────────┼──────────────────────┐
                              │                      │                      │
                              ▼                      ▼                      ▼
                    ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
                    │  Success (200)  │   │  Retry (3x)     │   │  Suppressed     │
                    │  status=sent    │   │  status=failed   │   │  (cooldown)     │
                    │  alert_id set   │   │  after 3 fails   │   │  no HTTP POST   │
                    └─────────────────┘   └─────────────────┘   └─────────────────┘
                              │                      │                      │
                              ▼                      ▼                      ▼
                    ┌─────────────────────────────────────────────────────────────┐
                    │  alert_records table (full audit trail for all outcomes)    │
                    └─────────────────────────────────────────────────────────────┘
```

**Webhook payload structure:**
```json
{
  "alert_timestamp": "ISO 8601",
  "anomaly_id": 42,
  "service": "payment-service",
  "window_start": "ISO 8601",
  "window_end": "ISO 8601",
  "error_rate": 0.67,
  "anomaly_score": 0.85,
  "severity": "HIGH",
  "ai_summary": "Plain-text explanation from Bedrock"
}
```

**Severity mapping:**
| Score Range | Severity |
|-------------|----------|
| 0.00 – 0.39 | LOW |
| 0.40 – 0.69 | MEDIUM |
| 0.70 – 0.89 | HIGH |
| 0.90 – 1.00 | CRITICAL |

---

## 4. Dashboard Data Flow

The dashboard combines server-side rendering for initial load with client-side polling for live updates.

```
┌──────────────────┐     GET /dashboard          ┌──────────────────────┐
│  SRE Browser     │ ──────────────────────────► │  Dashboard Router    │
└──────────────────┘                             └──────────┬───────────┘
                                                            │
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │  Dashboard_Service   │
                                                 │  - Chart data (24h)  │
                                                 │  - Recent anomalies  │
                                                 │  - Recent alerts     │
                                                 │  - Metrics counters  │
                                                 └──────────┬───────────┘
                                                            │
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │  Jinja2 SSR          │
                                                 │  (HTML + Chart.js)   │
                                                 └──────────┬───────────┘
                                                            │
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │  Browser renders     │
                                                 │  Initial page load   │
                                                 └──────────┬───────────┘
                                                            │
                                              Every 60 seconds (auto-refresh)
                                                            │
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │  Client-side fetch   │
                                                 │  GET /anomalies      │
                                                 │  GET /alerts         │
                                                 │  GET /metrics        │
                                                 └──────────────────────┘
```

**Chart data computation:**
- Log entries bucketed into 1-hour intervals over the past 24 hours
- Error rate computed per service per bucket
- Empty buckets default to 0.0 error rate
- One line per service (5 lines) with distinct colours

**Run Analysis flow:**
1. User clicks "Run Analysis" button
2. Client sends `POST /analyze` (all services, past hour)
3. Server returns HTTP 202 with `job_id`
4. Client polls `GET /analyze/{job_id}` every 3 seconds
5. On completion, client refreshes anomaly and alert lists

---

## 5. On-Demand Analysis Flow (POST /analyze)

```
┌──────────────────┐     POST /analyze           ┌──────────────────────┐
│  Client          │ ──────────────────────────► │  Analyze Router      │
│  (Dashboard/API) │     {service?, start, end}  │  Returns 202 + UUID  │
└──────────────────┘                             └──────────┬───────────┘
                                                            │
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │  In-Memory Job Store │
                                                 │  status: pending     │
                                                 └──────────┬───────────┘
                                                            │
                                                            ▼
                                                 ┌──────────────────────┐
                                                 │  BackgroundTask      │
                                                 │  - Run Gate 1 + 2   │
                                                 │  - Update job status │
                                                 │  - Record results    │
                                                 └──────────┬───────────┘
                                                            │
                              ┌──────────────────────────────┼──────────────────┐
                              ▼                              ▼                  ▼
                    ┌─────────────────┐           ┌─────────────────┐  ┌──────────────┐
                    │  status: running│           │ status: completed│  │status: failed│
                    └─────────────────┘           └─────────────────┘  └──────────────┘
```

**Polling endpoint:** `GET /analyze/{job_id}` returns current status, anomalies found, alerts dispatched, and any error message.

---

## 6. Health Check Flow

```
GET /health
    │
    ├─ SELECT 1 against SQLite → database: "ok" | "unreachable"
    │
    ├─ Read cached bedrock_health from app.state:
    │   - status: "ok" | "degraded" | "unknown"
    │   - last_checked_at: timestamp of last real inference
    │   - message: human-readable status description
    │
    └─ Return:
        - HTTP 200 if database is reachable
        - HTTP 503 if database is unreachable
```

The Bedrock health status is never probed synthetically — it reflects the outcome of the most recent real inference call.
