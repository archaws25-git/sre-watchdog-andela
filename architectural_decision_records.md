# Architectural Decision Records

This document captures the key architectural decisions made during the design and implementation of the SRE Watchdog platform. Each decision follows the ADR format: Context, Decision, and Consequences.

---

## ADR-001: Two-Gate Detection Pipeline

### Context

The SRE Watchdog needs to detect anomalies in ingested logs using both statistical thresholds and AI-powered analysis via AWS Bedrock. Bedrock API calls have variable latency (1–5 seconds), and the detection scheduler must run at a fixed interval without blocking.

### Decision

Implement a two-gate architecture:
- **Gate 1 (Synchronous):** APScheduler tick computes error rates per service over a sliding window. If the rate exceeds `ERROR_RATE_THRESHOLD`, an `anomaly_windows` record is created with status `pending_analysis` and a FastAPI BackgroundTask is enqueued.
- **Gate 2 (Asynchronous):** Each BackgroundTask invokes the Bedrock Converse API independently. Multiple Gate 2 tasks may run concurrently for different services.

### Consequences

- **Positive:** The scheduler tick never blocks on Bedrock latency. Detection remains responsive regardless of AI inference time.
- **Positive:** Multiple services can be analyzed concurrently via separate BackgroundTasks.
- **Negative:** Gate 2 results are eventually consistent — there is a window between Gate 1 detection and Gate 2 confirmation where the anomaly status is `pending_analysis`.
- **Negative:** BackgroundTasks are not durable across restarts; startup cleanup marks stale records as `analysis_failed`.

---

## ADR-002: Bedrock Failure Handling

### Context

AWS Bedrock calls can fail due to throttling, service unavailability, timeouts, or malformed responses. The system must handle these failures gracefully without generating false alerts.

### Decision

When a Bedrock call fails or returns an unparseable response:
1. The anomaly window status is set to `analysis_failed`.
2. The `failure_reason` field records the specific error.
3. No alert is dispatched for `analysis_failed` records.
4. Exponential backoff retry (3 attempts: 1s, 2s, 4s) is applied for transient errors only (`ThrottlingException`, `ServiceUnavailableException`, `ModelTimeoutException`).

### Consequences

- **Positive:** Infrastructure failures never generate noisy alerts to on-call engineers.
- **Positive:** Full audit trail is preserved — failed analyses are visible in the anomaly list.
- **Negative:** Genuine anomalies may go undetected if Bedrock is persistently unavailable. The `analysis_failed` counter in `/metrics` provides visibility into this scenario.

---

## ADR-003: Alert Cooldown Behaviour

### Context

During active incidents, the same service may continuously breach the error rate threshold. Without suppression, the system would dispatch redundant alerts every detection interval.

### Decision

- A configurable cooldown window (`ALERT_COOLDOWN_MINUTES`, default: 15 minutes) suppresses **alert dispatch only**.
- During cooldown, Gate 1 still creates anomaly records, Gate 2 still invokes Bedrock, and all results are persisted.
- Suppressed anomalies receive status `suppressed` with `suppression_reason: cooldown_active`.
- A `suppressed` alert record is created (with `dispatch_status=suppressed`) for full audit trail — but no HTTP POST is made.

### Consequences

- **Positive:** On-call engineers are not flooded with duplicate alerts during sustained incidents.
- **Positive:** Full observability is maintained — all Bedrock analyses are persisted regardless of cooldown state.
- **Positive:** Post-incident review can see the complete timeline of detections during the cooldown period.
- **Negative:** If a new, distinct failure mode emerges during cooldown for the same service, the alert is suppressed. This is an acceptable trade-off for MVP.

---

## ADR-004: Offset Pagination for GET /logs

### Context

The `GET /logs` endpoint needs pagination for potentially large result sets. Two approaches were considered: offset-based (`page`/`page_size`) and cursor-based (`last_id`/`last_timestamp`).

### Decision

Use offset-based pagination with `page` (default: 1) and `page_size` (default: 100, max: 500) query parameters. The response includes a pagination envelope with `total_count`, `page`, `page_size`, `has_more`, and `data`.

### Consequences

- **Positive:** Simple to implement and understand. Standard pattern for MVP-stage APIs.
- **Positive:** Supports random page access (jump to page N).
- **Negative:** Offset pagination suffers from "page drift" under concurrent writes — new entries can shift pages. Documented as a production upgrade path to cursor-based pagination.
- **Negative:** `COUNT(*)` queries for `total_count` become expensive at scale. Acceptable for SQLite with expected data volumes.

---

## ADR-005: In-Memory Job Store for /analyze

### Context

The `POST /analyze` endpoint creates asynchronous analysis jobs that clients poll via `GET /analyze/{job_id}`. Job state needs to be tracked somewhere.

### Decision

Store job state in `app.state.analyze_jobs: dict[str, AnalyzeJobResult]` — an in-memory dictionary. Job state is not durable across application restarts.

### Consequences

- **Positive:** Zero additional infrastructure. Simple dict lookup for polling.
- **Positive:** Fast reads — no DB query needed for job status checks.
- **Negative:** Job state is lost on restart. Documented as a production upgrade path (persist to DB or use Celery + Redis).
- **Negative:** Memory grows unbounded if jobs are never cleaned up. A TTL-based eviction strategy is recommended for production.

---

## ADR-006: SQLite with WAL Mode

### Context

The system needs a persistence layer that supports concurrent reads (API queries) and writes (BackgroundTask updates) without external infrastructure dependencies.

### Decision

Use SQLite in WAL (Write-Ahead Logging) mode. WAL mode allows concurrent readers and a single writer without blocking, which suits the BackgroundTask concurrency model.

### Consequences

- **Positive:** Zero-dependency persistence — no external database server required for local development.
- **Positive:** WAL mode provides adequate concurrency for the expected load (single-instance deployment).
- **Negative:** SQLite does not support true multi-process writes. For production multi-instance deployment, migration to PostgreSQL (via RDS) is required. SQLAlchemy's `DATABASE_URL` abstraction makes this a configuration-only change.

---

## ADR-007: Bedrock Health via Inference Caching

### Context

The `/health` endpoint needs to report Bedrock availability. Options considered: (a) synthetic health-check calls to Bedrock, (b) cached status from real inference calls.

### Decision

Cache the last-known Bedrock status from real inference calls. No synthetic health-check calls are made. The status starts as `unknown` and transitions to `ok` or `degraded` after the first real inference completes.

### Consequences

- **Positive:** Zero additional token cost — no synthetic prompts sent to Bedrock.
- **Positive:** Health status reflects actual operational experience, not synthetic probes.
- **Negative:** Status remains `unknown` until the first real anomaly triggers a Bedrock call. Acceptable for an observability platform where detection runs continuously.

---

## ADR-008: Dashboard SSR + Client-Side Refresh

### Context

The dashboard needs to display real-time data (anomalies, alerts, metrics) while providing a fast initial page load.

### Decision

- Initial page load is server-side rendered via Jinja2 with Chart.js data embedded.
- Subsequent updates use client-side JavaScript `fetch` calls to internal API endpoints (`/anomalies`, `/alerts`, `/metrics`) every 60 seconds.
- The "Run Analysis" button triggers `POST /analyze` and polls `GET /analyze/{job_id}` every 3 seconds.

### Consequences

- **Positive:** Fast first paint — no client-side framework needed.
- **Positive:** Clean separation — Jinja2 for structure, Chart.js for visualization, fetch for live data.
- **Negative:** No WebSocket support — data freshness is limited to the 60-second polling interval. Acceptable for SRE dashboards where sub-second updates are not required.
