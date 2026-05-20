# Requirements Document

## Introduction

The SRE Watchdog is a Python-based, API-first Intelligent Observability & Event Watchdog platform designed for Site Reliability Engineering teams. The system ingests application and platform logs, detects anomalies and error spikes using AI-powered analysis via AWS Bedrock, triggers webhook alerts when configurable thresholds are breached, and visualizes service health trends on an internal dashboard. The platform follows 12-factor app methodology, is designed for local execution with remote AWS service calls, and is structured for future production deployment on AWS App Runner or ECS/Fargate.

> **Production Note — Dashboard Authentication:** For a production-ready system, dashboard authentication via OAuth2/OIDC would be required.
>
> **Production Note — Deployment:** For production-grade systems, deployment targets AWS App Runner or ECS/Fargate via Docker; a Dockerfile is a concrete option.
>
> **Production Note — S3 Vectors:** Semantic anomaly detection via log embeddings stored in Amazon S3 Vectors is explicitly out of scope for this MVP. It is documented as a planned enhancement for a subsequent production iteration.
>
> **Production Note — Pagination:** Cursor-based pagination is the production replacement for offset pagination on high-volume, continuously-written log stores. Offset pagination is used here for MVP simplicity.

---

## Glossary

- **Watchdog**: The SRE Watchdog platform as a whole.
- **Log_Ingestion_Service**: The component responsible for receiving, parsing, and storing log entries.
- **Anomaly_Detector**: The component that evaluates log data against thresholds and AI analysis to identify anomalies. Implements a two-gate architecture: Gate 1 (synchronous statistical pre-filter on the APScheduler tick) and Gate 2 (asynchronous Bedrock analysis via FastAPI BackgroundTask). The APScheduler tick never blocks on Bedrock latency. During the alert cooldown window, Bedrock continues to run and results are persisted; only alert dispatch is suppressed.
- **Alert_Service**: The component that constructs and dispatches webhook alert payloads.
- **Dashboard_Service**: The FastAPI + Jinja2 + Chart.js component that renders health trend visualizations. Initial page load is server-side rendered via Jinja2; subsequent auto-refresh and user-triggered actions use client-side JavaScript fetch calls to internal API endpoints.
- **Bedrock_Client**: The wrapper around the AWS Bedrock Converse API used for AI-powered log analysis.
- **Config**: The configuration module that reads all settings from environment variables via `.env`.
- **Log_Entry**: A single structured log record containing at minimum a timestamp, service name, log level, and message.
- **Anomaly_Window**: A time window during which the error rate exceeds `ERROR_RATE_THRESHOLD`. Carries full lifecycle disposition: detection metadata, Bedrock analysis outcome, alert decision, and suppression reason.
- **Anomaly_Lifecycle_Status**: The current disposition of an Anomaly_Window record. Valid values:
  - `pending_analysis` — Gate 1 threshold breach confirmed; Bedrock BackgroundTask enqueued but not yet complete.
  - `analysis_failed` — Bedrock call failed or returned unparseable response.
  - `confirmed` — Bedrock returned a score ≥ `ANOMALY_SCORE_THRESHOLD`; alert dispatched (or suppressed by cooldown).
  - `below_score_threshold` — Bedrock returned a score < `ANOMALY_SCORE_THRESHOLD`; no alert dispatched.
  - `alerted` — Alert successfully dispatched to webhook.
  - `suppressed` — Anomaly confirmed by Bedrock but alert dispatch suppressed due to active cooldown window; `suppression_reason: cooldown_active`.
- **Webhook_Echo_Endpoint**: The internal FastAPI endpoint at `/webhooks/echo` that receives and records simulated alert payloads.
- **Synthetic_Log_Generator**: The utility that produces ~10,000 synthetic log entries across exactly 5 services over a 24-hour period, seeding exactly 3 deliberate anomaly windows.
- **Health_Endpoint**: The `/health` endpoint that reports the operational status of the Watchdog, including a cached last-known Bedrock status.
- **Error_Rate**: The ratio of ERROR + CRITICAL log entries to total log entries within a given time window.
- **Anomaly_Score**: A numeric value (0.0–1.0) returned by the Bedrock_Client representing the severity of a detected anomaly.
- **Threshold**: A configurable numeric limit stored in `.env` and read via Config; breaching it triggers an alert.
- **Service**: One of the 5 named application or platform services whose logs are monitored: `api-gateway`, `auth-service`, `payment-service`, `notification-service`, `database-proxy`.
- **Analysis_Failed**: A lifecycle status assigned to an Anomaly_Window record when the Bedrock_Client call fails or returns an unparseable response. No alert is dispatched for records in this state.
- **DETECTION_INTERVAL_SECONDS**: The configurable interval (default: 60 seconds) at which the background APScheduler job triggers the Anomaly_Detector's sliding window evaluation.
- **MAX_INGEST_BATCH_SIZE**: The maximum number of Log_Entry records accepted in a single `POST /logs/ingest` request (default and hard cap: 500).
- **Analyze_Job**: An asynchronous analysis task created by `POST /analyze`, identified by a `job_id`, executed as a FastAPI BackgroundTask, and retrievable via `GET /analyze/{job_id}`.

---

## Requirements

### Requirement 1: Project Bootstrap and Configuration

**User Story:** As a developer, I want the project to be bootstrapped with a Python virtual environment, dependency management, and environment-based configuration, so that the system is reproducible, portable, and follows 12-factor app methodology.

#### Acceptance Criteria

1. THE Watchdog SHALL provide a `requirements.txt` listing all pinned dependencies, including `apscheduler` for background scheduling.
2. THE Config SHALL read all configurable values — including `ERROR_RATE_THRESHOLD`, `ANOMALY_SCORE_THRESHOLD`, `SLIDING_WINDOW_MINUTES`, `ALERT_COOLDOWN_MINUTES`, `DETECTION_INTERVAL_SECONDS`, `MAX_INGEST_BATCH_SIZE`, `AWS_REGION`, `BEDROCK_MODEL_ID`, `DATABASE_URL`, and `WEBHOOK_URL` — exclusively from environment variables.
3. THE Watchdog SHALL provide a `.env.example` file documenting every required and optional environment variable with descriptions and safe default values.
4. IF a required environment variable is missing at startup, THEN THE Config SHALL raise a descriptive `ConfigurationError` identifying the missing variable and halt startup.
5. THE Watchdog SHALL never hardcode secrets, credentials, Thresholds, or environment-specific values in source code.
6. THE Watchdog SHALL include a `README.md` with setup instructions covering virtual environment creation, dependency installation, `.env` configuration, and local startup.

---

### Requirement 2: Log Ingestion

**User Story:** As an SRE, I want the system to ingest structured log entries via an API endpoint, so that logs from multiple services can be centrally collected and stored for analysis.

#### Acceptance Criteria

1. THE Log_Ingestion_Service SHALL expose a `POST /logs/ingest` endpoint that accepts a JSON payload containing one or more Log_Entry records, with a maximum batch size equal to `MAX_INGEST_BATCH_SIZE` (default: 500).
2. IF a `POST /logs/ingest` request contains more entries than `MAX_INGEST_BATCH_SIZE`, THEN THE Log_Ingestion_Service SHALL return HTTP 413 with a structured error body stating the limit and the received count.
3. WHEN a valid Log_Entry is received, THE Log_Ingestion_Service SHALL persist it to the SQLite database within 500ms.
4. WHEN a Log_Entry payload is received, THE Log_Ingestion_Service SHALL validate that each entry contains a timestamp (ISO 8601), a service name, a log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`), and a message string.
5. IF a Log_Entry fails validation, THEN THE Log_Ingestion_Service SHALL return an HTTP 422 response with a structured error body identifying the invalid fields.
6. THE Log_Ingestion_Service SHALL expose a `GET /logs` endpoint that returns stored Log_Entry records with offset-based pagination. The endpoint SHALL accept `page` (default: 1) and `page_size` (default: 100, max: 500) query parameters, plus optional filters for `service`, `level`, `start_time`, and `end_time`.
7. THE `GET /logs` response SHALL include a pagination envelope with the fields: `total_count`, `page`, `page_size`, `has_more`, and a `data` array of Log_Entry records.
8. THE Log_Ingestion_Service SHALL record structured log output (JSON format) for every ingest request, including the count of accepted and rejected entries.

---

### Requirement 3: Synthetic Log Generation

**User Story:** As a developer, I want a synthetic log generator utility, so that I can populate the system with realistic test data including deliberate anomaly windows for validating detector behavior.

#### Acceptance Criteria

1. THE Synthetic_Log_Generator SHALL produce approximately 10,000 Log_Entry records distributed across exactly 5 named Services (`api-gateway`, `auth-service`, `payment-service`, `notification-service`, `database-proxy`) over a simulated 24-hour period.
2. THE Synthetic_Log_Generator SHALL seed exactly 3 deliberate Anomaly_Windows with the following profiles:
   - **Window 1 — Sharp Spike:** `payment-service`, duration 6 minutes, simulating a sudden payment processor failure.
   - **Window 2 — Sustained Degradation:** `auth-service`, duration 12 minutes, simulating a token validation service degradation.
   - **Window 3 — Escalating Cascade:** `api-gateway`, duration 18 minutes, simulating an upstream dependency cascade failure.
3. WHEN executed, THE Synthetic_Log_Generator SHALL submit all generated Log_Entry records to the `POST /logs/ingest` endpoint in batches of exactly 500 entries per request (20 total calls for 10,000 entries).
4. THE Synthetic_Log_Generator SHALL be executable as a standalone CLI script (`python generate_logs.py`) with configurable parameters for entry count, service count, and anomaly window count via command-line arguments.
5. THE Synthetic_Log_Generator SHALL produce log entries with realistic distributions: approximately 70% INFO, 15% WARNING, 10% ERROR, and 5% CRITICAL/DEBUG outside of Anomaly_Windows.
6. WITHIN a seeded Anomaly_Window, THE Synthetic_Log_Generator SHALL produce a minimum of 40% ERROR or CRITICAL log entries for the affected Service.

---

### Requirement 4: Anomaly Detection

**User Story:** As an SRE, I want the system to automatically detect anomalies and error spikes in ingested logs, so that I am alerted to service degradation before it becomes a critical incident.

#### Acceptance Criteria

1. THE Anomaly_Detector SHALL evaluate ingested logs using a configurable sliding time window (default: 5 minutes, configurable via `SLIDING_WINDOW_MINUTES`) to compute the Error_Rate per Service.
2. THE Anomaly_Detector SHALL implement a two-gate detection pipeline:
   - **Gate 1 — Statistical Pre-filter (synchronous, APScheduler tick):** For each Service, compute Error_Rate over the sliding window. IF Error_Rate exceeds `ERROR_RATE_THRESHOLD`, THEN create an Anomaly_Window record with status `pending_analysis` and enqueue a FastAPI BackgroundTask for Gate 2 analysis. The APScheduler tick SHALL complete Gate 1 for all Services and return without blocking on Bedrock latency.
   - **Gate 2 — AI Analysis (asynchronous, FastAPI BackgroundTask):** Each BackgroundTask invokes the Bedrock_Client for its assigned Anomaly_Window. Gate 2 runs synchronously within the BackgroundTask (i.e., the BackgroundTask awaits the Bedrock response before updating the record). Multiple Gate 2 tasks MAY run concurrently for different Services.
3. WHEN the Bedrock_Client returns an Anomaly_Score ≥ `ANOMALY_SCORE_THRESHOLD`, THE BackgroundTask SHALL update the Anomaly_Window status to `confirmed` and trigger the Alert_Service — UNLESS the Service is within the active cooldown window, in which case the status SHALL be set to `suppressed` with `suppression_reason: cooldown_active` and no alert is dispatched.
4. WHEN the Bedrock_Client returns an Anomaly_Score < `ANOMALY_SCORE_THRESHOLD`, THE BackgroundTask SHALL update the Anomaly_Window status to `below_score_threshold`. No alert is dispatched.
5. IF the Bedrock_Client call fails, times out, or returns an unparseable response, THEN THE BackgroundTask SHALL log a warning, update the Anomaly_Window status to `analysis_failed`, record the failure reason, and NOT dispatch an alert.
6. THE alert cooldown window (configurable via `ALERT_COOLDOWN_MINUTES`, default: 15 minutes) suppresses alert dispatch only. Bedrock analysis continues to run during the cooldown period and all results are persisted to the database for audit and observability purposes.
7. WHEN a Service is within the active cooldown window and Gate 1 detects a new threshold breach, THE Anomaly_Detector SHALL still create an Anomaly_Window record, enqueue a Gate 2 BackgroundTask, and persist the Bedrock result. The final status SHALL be `suppressed` with `suppression_reason: cooldown_active` if the score meets the threshold, or `below_score_threshold` if it does not.
8. THE Anomaly_Detector SHALL expose a `POST /analyze` endpoint that creates an Analyze_Job and returns HTTP 202 Accepted with a `job_id`. The analysis SHALL execute as a FastAPI BackgroundTask. The `service` parameter SHALL be optional; when omitted, analysis SHALL run across all 5 Services for the specified time range.
9. THE Anomaly_Detector SHALL expose a `GET /analyze/{job_id}` endpoint that returns the current status and results of an Analyze_Job (`pending`, `running`, `completed`, `failed`).
10. THE Anomaly_Detector SHALL expose a `GET /anomalies` endpoint that returns all Anomaly_Window records with their full lifecycle disposition.
11. THE Anomaly_Detector SHALL expose a `GET /anomalies/{id}` endpoint that returns the complete lifecycle record for a single Anomaly_Window, including: Service, time range, Error_Rate, Anomaly_Score, Anomaly_Lifecycle_Status, suppression reason, AI-generated summary, and linked alert ID (if dispatched).
12. THE Anomaly_Detector background scheduler job SHALL be driven by APScheduler, running at the interval defined by `DETECTION_INTERVAL_SECONDS` (default: 60 seconds).

---

### Requirement 5: AWS Bedrock AI Integration

**User Story:** As an SRE, I want the system to use AWS Bedrock's LLM capabilities to analyze anomalous log patterns, so that I receive intelligent, context-aware summaries rather than raw threshold alerts.

#### Acceptance Criteria

1. THE Bedrock_Client SHALL use the AWS Bedrock Converse API to send log analysis requests to the model specified by the `BEDROCK_MODEL_ID` environment variable (default: `us.anthropic.claude-sonnet-4-5-20251101-v1:0`).
2. THE Bedrock_Client SHALL operate in the AWS region specified by the `AWS_REGION` environment variable (default: `us-east-1`).
3. WHEN invoking the Bedrock Converse API, THE Bedrock_Client SHALL construct a prompt containing the anomalous log messages, the affected Service name, the time window, and the computed Error_Rate.
4. THE Bedrock_Client SHALL parse the model response and extract a structured result containing a plain-text summary and a numeric Anomaly_Score between 0.0 and 1.0.
5. IF the model response does not contain a parseable Anomaly_Score, THEN THE Bedrock_Client SHALL raise a `BedrockParseError` exception to be caught by the Anomaly_Detector, which will mark the record as `analysis_failed` and suppress alert dispatch.
6. THE Bedrock_Client SHALL implement exponential backoff with a maximum of 3 retries for transient AWS API errors (throttling, service unavailable).
7. THE Bedrock_Client SHALL record the token usage and latency of each Bedrock API call to structured logs for cost and performance monitoring.
8. AT application startup, THE Watchdog SHALL validate AWS credential availability once via `boto3.Session().get_credentials()` and log a warning if credentials are absent. This does not halt startup but sets the initial cached Bedrock status to `degraded`.
9. AT runtime, THE `/health` endpoint SHALL report a cached `last_bedrock_status` field that is updated after each real Bedrock inference call (success or failure). The cache SHALL record the timestamp of the last status update.

---

### Requirement 6: Webhook Alert Dispatch

**User Story:** As an SRE, I want the system to dispatch structured webhook alerts when anomalies are confirmed, so that downstream systems or on-call tools can receive and act on incident notifications.

#### Acceptance Criteria

1. THE Alert_Service SHALL dispatch a `POST` request to the URL specified by the `WEBHOOK_URL` environment variable when triggered by the Anomaly_Detector.
2. THE Alert_Service SHALL construct the webhook payload as a JSON object containing: alert timestamp, affected Service name, Anomaly_Window time range, Error_Rate, Anomaly_Score, AI-generated summary, severity label (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`), and `anomaly_id`.
3. WHEN the Anomaly_Score is between 0.0 and 0.39 (inclusive), THE Alert_Service SHALL assign severity `LOW`.
4. WHEN the Anomaly_Score is between 0.40 and 0.69 (inclusive), THE Alert_Service SHALL assign severity `MEDIUM`.
5. WHEN the Anomaly_Score is between 0.70 and 0.89 (inclusive), THE Alert_Service SHALL assign severity `HIGH`.
6. WHEN the Anomaly_Score is between 0.90 and 1.0 (inclusive), THE Alert_Service SHALL assign severity `CRITICAL`.
7. THE Webhook_Echo_Endpoint SHALL accept `POST` requests at `/webhooks/echo`, persist the received payload to the SQLite database, and return an HTTP 200 response with the received payload echoed back.
8. THE Alert_Service SHALL expose a `GET /alerts` endpoint returning all dispatched alert records as a webhook dispatch log, each record linked to its source Anomaly_Window via `anomaly_id`, including payload, HTTP response status, and dispatch timestamp.
9. IF the webhook dispatch fails after 3 retry attempts, THEN THE Alert_Service SHALL log the failure with the full payload and mark the alert record as `FAILED` in the database.
10. THE Alert_Service SHALL record structured log output for every dispatched alert, including the target URL, severity, and response status code.
11. Anomaly_Window records with status `analysis_failed` or `below_score_threshold` SHALL NOT be passed to the Alert_Service. Records with status `suppressed` (cooldown_active) SHALL have an alert record created with dispatch status `suppressed` — the webhook POST SHALL NOT be made.

---

### Requirement 7: Health and Observability Endpoints

**User Story:** As an SRE, I want the platform to expose health and observability endpoints, so that I can monitor the Watchdog's own operational status and integrate it with infrastructure health checks.

#### Acceptance Criteria

1. THE Watchdog SHALL expose a `GET /health` endpoint that returns an HTTP 200 response with a JSON body indicating the status of the API, database connectivity, and a cached last-known Bedrock status.
2. WHEN the SQLite database is unreachable, THE Health_Endpoint SHALL return an HTTP 503 response with a JSON body identifying the database as the failing component.
3. THE `/health` response SHALL include a `bedrock` field with sub-fields: `status` (`ok`, `degraded`, `unknown`), `last_checked_at` (ISO 8601 timestamp of the last real inference call), and `message`. The status SHALL be `unknown` until the first inference call completes.
4. THE Watchdog SHALL emit structured JSON logs for every HTTP request, including method, path, status code, and response time in milliseconds.
5. THE Watchdog SHALL expose a `GET /metrics` endpoint returning operational counters: `total_logs_ingested`, `total_anomalies_detected`, `total_alerts_dispatched`, `total_failed_alerts`, `total_analysis_failed`, and `total_cooldown_suppressed`.

---

### Requirement 8: Dashboard Visualization

**User Story:** As an SRE, I want a web dashboard that visualizes service health trends and detected anomalies, so that I can quickly assess the state of monitored services at a glance.

#### Acceptance Criteria

1. THE Dashboard_Service SHALL serve an HTML dashboard at `GET /dashboard` rendered via Jinja2 templates with an initial server-side render.
2. THE Dashboard_Service SHALL display a time-series chart (Chart.js) showing Error_Rate per Service over the most recent 24 hours.
3. THE Dashboard_Service SHALL display a list of the most recent 20 detected Anomaly_Windows, including Service name, time range, Anomaly_Score, Anomaly_Lifecycle_Status, severity label, and AI-generated summary.
4. THE Dashboard_Service SHALL display a list of the most recent 20 dispatched alerts, including timestamp, Service name, severity, `anomaly_id`, and webhook dispatch status.
5. WHEN the dashboard page is loaded, THE Dashboard_Service SHALL perform the initial render server-side via Jinja2. Subsequent data updates SHALL use client-side JavaScript fetch calls to the internal API endpoints (`/anomalies`, `/alerts`, `/metrics`), not direct database queries.
6. THE Dashboard_Service SHALL include a manual "Run Analysis" button that triggers a `POST /analyze` request for all Services (no `service` parameter) over the past hour. The button SHALL display a loading state while polling `GET /analyze/{job_id}` every 3 seconds, and SHALL refresh the anomaly list upon job completion.
7. THE Dashboard_Service SHALL auto-refresh the displayed data every 60 seconds using client-side JavaScript polling without requiring a full page reload.
8. THE Dashboard_Service SHALL be accessible without authentication for the MVP. Documentation SHALL note that OAuth2/OIDC authentication is required for production use.

---

### Requirement 9: Testing

**User Story:** As a developer, I want a comprehensive test suite covering core logic and edge cases, so that I can confidently refactor and extend the system without introducing regressions.

#### Acceptance Criteria

1. THE Watchdog SHALL include a test suite executable via `pytest` from the project root.
2. THE Watchdog test suite SHALL include unit tests for the Anomaly_Detector covering: zero log entries, a single error spike (Gate 1 breach → Gate 2 Bedrock call → `confirmed` + alert), a sustained high Error_Rate over multiple windows, cooldown suppression (Gate 2 runs, Bedrock result persisted, alert suppressed with `cooldown_active`), and `analysis_failed` status assignment.
3. THE Watchdog test suite SHALL include unit tests for the Bedrock_Client covering: successful response parsing, malformed response handling (triggering `BedrockParseError`), and retry behavior on transient errors.
4. THE Watchdog test suite SHALL include unit tests for the Alert_Service covering: correct severity label assignment for all four severity bands, successful dispatch, failed dispatch after retries, and verification that `analysis_failed`, `suppressed`, and `cooldown_skipped` anomaly records are never passed to the Alert_Service.
5. THE Watchdog test suite SHALL include integration tests for the `POST /logs/ingest` endpoint covering: valid batch ingestion, invalid payload rejection, empty batch handling, and HTTP 413 response when batch exceeds `MAX_INGEST_BATCH_SIZE`.
6. THE Watchdog test suite SHALL include integration tests for `GET /logs` covering: default pagination response envelope, custom `page` and `page_size` parameters, and filter combinations.
7. THE Watchdog test suite SHALL include integration tests for `POST /analyze` covering: HTTP 202 response with `job_id`, `GET /analyze/{job_id}` status polling, and job completion with results.
8. THE Watchdog test suite SHALL include a round-trip property test: FOR ALL valid Log_Entry objects serialized to JSON and deserialized back, THE resulting object SHALL be equivalent to the original (round-trip property).
9. THE Watchdog test suite SHALL enforce tiered coverage thresholds:
   - **Critical paths** (Anomaly_Detector logic, Alert_Service dispatch): 95% line coverage AND 95% branch coverage.
   - **All API routers**: 85% line coverage minimum.
   - **Supporting modules** (Config, utilities, Synthetic_Log_Generator): 70% line coverage minimum.
   - **Overall project floor**: 80% line coverage minimum.
10. THE test suite SHALL be executed with `pytest --cov-branch --cov-fail-under=80`. Branch coverage is mandatory for detection and alert dispatch logic where missed branches represent silent operational failures.

---

### Requirement 10: Code Quality and Documentation

**User Story:** As a developer, I want all code to be well-documented and follow consistent style standards, so that the codebase is maintainable and onboarding new contributors is straightforward.

#### Acceptance Criteria

1. THE Watchdog SHALL include module-level docstrings in every Python source file describing the module's purpose and responsibilities.
2. THE Watchdog SHALL include function-level docstrings for all public functions and methods, following Google-style docstring format.
3. THE Watchdog SHALL use `snake_case` for all variable and function names, `PascalCase` for all class names, and `SCREAMING_SNAKE_CASE` for all constants.
4. THE Watchdog SHALL pass `flake8` linting with no errors using the project's configured rule set.
5. THE Watchdog SHALL include the following markdown documentation files: `design.md`, `architectural_decision_records.md`, `implementation_plan.md`, `testing_specifics.md`, `data_flow_integration.md`, `cost_analysis.md`, `security_compliance.md`, `observability_evaluation.md`, and `deployment_instructions.md`.
6. THE Watchdog SHALL include a `CONTRIBUTING.md` file describing the development workflow, coding standards, and how to run tests.

---

## Architectural Decisions Captured in Requirements

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Two-gate detection: Gate 1 synchronous statistical pre-filter (APScheduler tick), Gate 2 async Bedrock analysis (BackgroundTask) | APScheduler tick never blocks on Bedrock latency; multiple services analyzed concurrently |
| 2 | Bedrock failure → `analysis_failed`, suppress alert | Prevents infrastructure failures from generating noisy alerts |
| 3 | `/analyze` `service` param is optional (omit = all services) | Resolves R4 vs R8 conflict; dashboard "Run Analysis" omits service param |
| 4 | Cooldown suppresses alert dispatch only; Bedrock continues running | Full observability during cooldown; audit trail preserved; no data loss |
| 5 | Dashboard: Jinja2 SSR initial load + client-side JS fetch for refresh | Clean separation; Jinja2 for first paint, Chart.js + fetch for live updates |
| 6 | S3 Vectors out of scope for MVP | Time-constrained MVP; documented as a planned production enhancement |
| 7 | 5 services: `api-gateway`, `auth-service`, `payment-service`, `notification-service`, `database-proxy` | Realistic SRE service topology |
| 8 | 3 seeded anomaly windows with distinct profiles (6/12/18 min) | Tests sharp spike, sustained degradation, and cascade — three distinct SRE failure modes |
| 9 | Bedrock skipped entirely during cooldown window | Avoids unnecessary API cost; cooldown_skipped status preserves full audit trail |
| 10 | `MAX_INGEST_BATCH_SIZE=500`, HTTP 413 if exceeded | Guards memory pressure and 500ms SLA; generator uses 20×500 batches |
| 11 | Offset pagination on `GET /logs` (page/page_size); cursor-based noted for production | MVP simplicity; production note documents the upgrade path |
| 12 | Anomaly_Window carries full lifecycle disposition; `GET /anomalies/{id}` is single queryable entity | Full audit trail for SRE post-incident review |
| 13 | `POST /analyze` → HTTP 202 + job_id; BackgroundTask; `GET /analyze/{job_id}` for polling | Avoids Bedrock latency blocking HTTP response; dashboard polls every 3s |
| 14 | Bedrock health: startup credential check + cached last-known status from real inference calls | No synthetic health-check calls; zero extra token cost |
| 15 | Tiered test coverage: 95% critical paths, 85% routers, 70% supporting, 80% floor | Proportional investment; branch coverage mandatory for silent-failure paths |
