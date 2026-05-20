# Implementation Plan: SRE Watchdog

## Overview

Implement the SRE Watchdog as a Python 3.11+ FastAPI application. The build proceeds in layers: project scaffold and configuration first, then the data layer, then core services (ingestion, detection, Bedrock, alerting), then API routers, then the dashboard, and finally the synthetic log generator and test suite. Each layer is wired into the application before moving to the next so there is no orphaned code at any point.

## Tasks

- [x] 1. Project scaffold, configuration, and database foundation
  - [x] 1.1 Create project directory structure and `requirements.txt`
    - Create all directories: `app/`, `app/models/`, `app/routers/`, `app/services/`, `app/templates/`, `tests/`, `tests/unit/`, `tests/integration/`
    - Add all `__init__.py` package markers
    - Write `requirements.txt` with pinned versions for: `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `pydantic-settings`, `apscheduler`, `boto3`, `httpx`, `jinja2`, `hypothesis`, `pytest`, `pytest-cov`, `freezegun`, `respx`
    - _Requirements: 1.1_

  - [x] 1.2 Implement `app/config.py` — Settings and ConfigurationError
    - Write `Settings` class using `pydantic-settings` `BaseSettings` reading all env vars: `DATABASE_URL`, `AWS_REGION`, `BEDROCK_MODEL_ID`, `BEDROCK_MAX_LOG_SAMPLE`, `ERROR_RATE_THRESHOLD`, `ANOMALY_SCORE_THRESHOLD`, `SLIDING_WINDOW_MINUTES`, `ALERT_COOLDOWN_MINUTES`, `DETECTION_INTERVAL_SECONDS`, `MAX_INGEST_BATCH_SIZE`, `WEBHOOK_URL`, `LOG_LEVEL`, `APP_HOST`, `APP_PORT`
    - Add `field_validator` for `ERROR_RATE_THRESHOLD` and `ANOMALY_SCORE_THRESHOLD` (must be 0.0–1.0)
    - Implement `ConfigurationError` exception class and `get_settings()` factory that raises it on missing/invalid config
    - Write `.env.example` with descriptions and safe defaults for every variable
    - _Requirements: 1.2, 1.3, 1.4, 1.5_

  - [ ]* 1.3 Write unit tests for `app/config.py`
    - Test successful settings load from env vars
    - Test `ConfigurationError` raised when a required var is missing
    - Test `field_validator` rejects out-of-range threshold values
    - _Requirements: 9.4_

  - [x] 1.4 Implement `app/database.py` — SQLite engine, WAL mode, session factory
    - Create SQLAlchemy engine from `DATABASE_URL` with `connect_args={"check_same_thread": False}`
    - Enable WAL mode via `PRAGMA journal_mode=WAL` on engine connect event
    - Define `Base` declarative base and `SessionLocal` session factory
    - Implement `get_db()` dependency-injection generator
    - _Requirements: 2.3_

- [x] 2. ORM models and Pydantic schemas
  - [x] 2.1 Implement `app/models/db_models.py` — SQLAlchemy ORM models
    - Define `LogEntry` model with columns: `id`, `timestamp`, `service`, `level`, `message`, `ingested_at`; add indexes on `(service, timestamp)`, `level`, `timestamp`
    - Define `AnomalyWindow` model with all columns and status/suppression fields; add indexes on `service`, `status`, `created_at`
    - Define `AlertRecord` model with all columns; add indexes on `anomaly_id`, `dispatched_at`
    - Define `WebhookEchoLog` model with `id`, `received_at`, `payload`
    - Call `Base.metadata.create_all(engine)` in database init
    - _Requirements: 2.3, 4.1, 6.1, 6.7_

  - [x] 2.2 Implement `app/models/schemas.py` — Pydantic schemas and enums
    - Define all enums: `LogLevel`, `AnomalyStatus`, `AlertDispatchStatus`, `SeverityLabel`, `BedrockHealthStatus`, `AnalyzeJobStatus`
    - Define all request/response schemas: `LogEntryCreate`, `LogEntryResponse`, `IngestRequest`, `IngestResponse`, `PaginatedLogsResponse`, `AnomalyWindowResponse`, `AnalyzeRequest`, `AnalyzeResponse`, `AnalyzeJobResult`, `AlertRecordResponse`, `BedrockHealthDetail`, `HealthResponse`, `MetricsResponse`, `WebhookEchoResponse`
    - _Requirements: 2.4, 2.7, 4.10, 4.11, 5.4, 6.2, 7.1, 7.3, 7.5_

  - [ ]* 2.3 Write property test for `LogEntryCreate` round-trip serialization
    - **Property: Round-trip consistency** — FOR ALL valid `LogEntryCreate` objects, `model_validate_json(model_dump_json(entry)) == entry`
    - Use `hypothesis` `@given(st.builds(...))` with `st.datetimes`, `st.sampled_from(VALID_SERVICES)`, `st.sampled_from(list(LogLevel))`, `st.text(min_size=1, max_size=500)`
    - Place in `tests/unit/test_schemas.py`
    - _Requirements: 9.8_

- [x] 3. Core services — log ingestion and middleware
  - [x] 3.1 Implement `app/services/log_ingestion_service.py`
    - Implement `ingest_batch(entries, db, settings)`: validate each `LogEntryCreate`, persist all valid entries in a single DB transaction, return `IngestResponse` with `accepted`/`rejected`/`errors` counts
    - Emit structured JSON log for every ingest call (accepted count, rejected count)
    - _Requirements: 2.3, 2.4, 2.5, 2.8_

  - [x] 3.2 Implement `app/middleware.py` — `RequestLoggingMiddleware`
    - Subclass `BaseHTTPMiddleware`; emit one structured JSON log line per request with `request_id`, `method`, `path`, `status_code`, `latency_ms`
    - _Requirements: 7.4_

- [x] 4. Bedrock client
  - [x] 4.1 Implement `app/services/bedrock_client.py`
    - Define `BedrockAnalysisResult` dataclass with `anomaly_score`, `summary`, `input_tokens`, `output_tokens`, `latency_ms`
    - Define `BedrockParseError` exception
    - Implement `BedrockClient.analyze(service, window_start, window_end, error_rate, log_messages)` using the Converse API with the prompt template from the design
    - Implement `_parse_response()` that raises `BedrockParseError` on malformed/out-of-range responses
    - Implement exponential backoff retry (max 3 attempts, base delay 1s doubling) for `ThrottlingException`, `ServiceUnavailableException`, `ModelTimeoutException`
    - Log token usage and latency as structured JSON after each call
    - Update `app.state.bedrock_health` after every real inference call (success or failure)
    - Cap log messages at `BEDROCK_MAX_LOG_SAMPLE` (most recent entries)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.9_

  - [ ]* 4.2 Write unit tests for `app/services/bedrock_client.py`
    - Test successful response parsing returns correct `anomaly_score` and `summary`
    - Test malformed response (missing key, non-JSON, out-of-range score) raises `BedrockParseError`
    - Test retry logic: mock `converse` to raise `ThrottlingException` twice then succeed; assert 3 total calls
    - Test non-retryable error propagates immediately without retry
    - _Requirements: 9.3_

- [x] 5. Alert service
  - [x] 5.1 Implement `app/services/alert_service.py`
    - Implement `map_severity(score: float) -> SeverityLabel` with the four bands (0–0.39 LOW, 0.40–0.69 MEDIUM, 0.70–0.89 HIGH, 0.90–1.0 CRITICAL)
    - Implement `is_in_cooldown(service, db, settings) -> bool` querying `anomaly_windows` for `alerted` status within `ALERT_COOLDOWN_MINUTES`
    - Implement `dispatch(anomaly_window, db, settings)`: build webhook payload, POST to `WEBHOOK_URL` with `httpx`, retry up to 3 times on failure, persist `AlertRecord` with `sent`/`failed`/`suppressed` status
    - For cooldown-suppressed anomalies: create `AlertRecord` with `dispatch_status=suppressed`, no HTTP POST
    - Emit structured JSON log for every dispatch attempt
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.9, 6.10, 6.11_

  - [ ]* 5.2 Write unit tests for `app/services/alert_service.py`
    - Test `map_severity` for all four bands including boundary values (0.0, 0.39, 0.40, 0.69, 0.70, 0.89, 0.90, 1.0)
    - Test successful dispatch: mock `httpx.post` returns 200; assert `dispatch_status=sent`
    - Test dispatch fails after 3 retries: mock raises `httpx.HTTPError` three times; assert `dispatch_status=failed`
    - Test `analysis_failed` anomaly is never passed to `Alert_Service`
    - Test `suppressed` (cooldown) anomaly creates `AlertRecord` with `dispatch_status=suppressed` and no HTTP POST
    - _Requirements: 9.4_

- [x] 6. Anomaly detector and scheduler
  - [x] 6.1 Implement `app/services/anomaly_detector.py` — Gate 1 statistical pre-filter
    - Implement `evaluate_all_services(db, settings, background_tasks)`: for each of the 5 services, query `log_entries` within the sliding window, compute `error_rate`, and if `error_rate > ERROR_RATE_THRESHOLD` insert an `AnomalyWindow` record with `status=pending_analysis` and enqueue a `BackgroundTask` for Gate 2
    - Implement `run_gate2(anomaly_id, db, settings, bedrock_client, alert_service)`: fetch the anomaly window, fetch log messages, call `bedrock_client.analyze()`, update status to `confirmed`/`below_score_threshold`/`analysis_failed` based on result, call `alert_service.dispatch()` if score ≥ threshold and not in cooldown
    - Implement startup cleanup: mark `pending_analysis` records older than 10 minutes as `analysis_failed` with `suppression_reason=orphaned_on_restart`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [x] 6.2 Implement `app/scheduler.py` — APScheduler setup and lifespan wiring
    - Create `BackgroundScheduler` with `coalesce=True`, `max_instances=1`, `timezone=UTC`
    - Register `run_detection_tick` as an `IntervalTrigger` job using `DETECTION_INTERVAL_SECONDS`
    - Wire scheduler start/stop into the FastAPI `lifespan` context manager
    - _Requirements: 4.12_

  - [ ]* 6.3 Write unit tests for `app/services/anomaly_detector.py`
    - Test zero log entries in window → no `AnomalyWindow` record created, no BackgroundTask enqueued
    - Test single spike: error_rate > threshold → `pending_analysis` record created, Gate 2 runs → `confirmed` + alert dispatched
    - Test sustained high error rate across multiple ticks → multiple `AnomalyWindow` records created
    - Test cooldown suppression: Gate 2 runs, Bedrock result persisted, `status=suppressed`, `suppression_reason=cooldown_active`, no HTTP POST
    - Test `analysis_failed` on `BedrockParseError`: status set to `analysis_failed`, no alert dispatched
    - Test `below_score_threshold`: Bedrock returns score < threshold → status set accordingly, no alert
    - Use `freezegun` for time-sensitive sliding window and cooldown tests
    - _Requirements: 9.2_

- [x] 7. API routers
  - [x] 7.1 Implement `app/routers/logs.py` — `POST /logs/ingest` and `GET /logs`
    - `POST /logs/ingest`: validate batch size against `MAX_INGEST_BATCH_SIZE`, return HTTP 413 with structured error body if exceeded; delegate to `log_ingestion_service.ingest_batch()`; return `IngestResponse`
    - `GET /logs`: accept `page`, `page_size`, `service`, `level`, `start_time`, `end_time` query params; query `log_entries` with filters; return `PaginatedLogsResponse` with `total_count`, `page`, `page_size`, `has_more`, `data`
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7_

  - [x] 7.2 Implement `app/routers/anomalies.py` — `GET /anomalies` and `GET /anomalies/{id}`
    - `GET /anomalies`: accept `service`, `status`, `page`, `page_size` filters; return list of `AnomalyWindowResponse`
    - `GET /anomalies/{id}`: return single `AnomalyWindowResponse` or HTTP 404
    - _Requirements: 4.10, 4.11_

  - [x] 7.3 Implement `app/routers/analyze.py` — `POST /analyze` and `GET /analyze/{job_id}`
    - `POST /analyze`: accept `AnalyzeRequest`, create a UUID job entry in `app.state.analyze_jobs`, enqueue `BackgroundTask` that runs Gate 2 for the specified service(s) and time range, return HTTP 202 with `AnalyzeResponse`
    - `GET /analyze/{job_id}`: look up job in `app.state.analyze_jobs`, return `AnalyzeJobResult` or HTTP 404; status transitions: `pending` → `running` → `completed` | `failed`
    - _Requirements: 4.8, 4.9_

  - [x] 7.4 Implement `app/routers/alerts.py`, `app/routers/webhooks.py`, `app/routers/health.py`, `app/routers/metrics.py`
    - `GET /alerts`: paginated list of `AlertRecordResponse`
    - `POST /webhooks/echo`: persist raw JSON body to `webhook_echo_log`, return `WebhookEchoResponse`
    - `GET /health`: run `SELECT 1` against DB; return `HealthResponse` (HTTP 200 or 503); include `bedrock` field from `app.state.bedrock_health`
    - `GET /metrics`: run the six counter queries from the design and return `MetricsResponse`
    - _Requirements: 6.7, 6.8, 7.1, 7.2, 7.3, 7.5_

- [x] 8. Checkpoint — wire application together
  - Implement `app/main.py`: create `FastAPI` app with `lifespan` context manager; register `RequestLoggingMiddleware`; include all routers; run startup cleanup and Bedrock credential check in lifespan; start/stop APScheduler
  - Verify the app starts with `uvicorn app.main:app --reload` and `GET /health` returns HTTP 200
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 1.6, 5.8_

- [x] 9. Dashboard
  - [x] 9.1 Implement `app/services/dashboard_service.py` — aggregation queries
    - Implement `get_chart_data(db)`: bucket `log_entries` into 1-hour intervals over the past 24 hours, compute `error_rate` per service per bucket, return Chart.js-compatible JSON structure; default empty buckets to `0.0`
    - Implement `get_recent_anomalies(db, limit=20)` and `get_recent_alerts(db, limit=20)` for SSR initial render
    - _Requirements: 8.2, 8.3_

  - [x] 9.2 Implement `app/routers/dashboard.py` and `app/templates/dashboard.html`
    - Route handler: call `dashboard_service` methods, pass data to Jinja2 `TemplateResponse`
    - Template: metrics bar (logs ingested, anomalies, alerts, failed); Chart.js time-series line chart (one line per service, 5 distinct colours); recent anomalies table (service, window, score, status, severity, AI summary); recent alerts table (timestamp, service, severity, anomaly_id, dispatch status)
    - "Run Analysis" button: `POST /analyze` (no `service` param, past hour), poll `GET /analyze/{job_id}` every 3 seconds, show loading state, refresh anomaly list on completion
    - Auto-refresh every 60 seconds via `setInterval` calling `fetch` on `/anomalies`, `/alerts`, `/metrics`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

- [x] 10. Synthetic log generator
  - [x] 10.1 Implement `generate_logs.py` — standalone CLI script
    - Implement `generate_logs(total_entries, service_count, anomaly_window_count, ingest_url, batch_size)` with the algorithm from the design: build timeline, assign log levels from normal/anomaly distributions, generate realistic messages from `MESSAGE_TEMPLATES`, sort by timestamp, chunk into batches of `batch_size`, POST to `/logs/ingest`
    - Seed exactly 3 anomaly windows: `payment-service` 6 min sharp spike, `auth-service` 12 min sustained degradation, `api-gateway` 18 min escalating cascade; each with ≥ 40% ERROR+CRITICAL entries
    - Normal distribution: ~70% INFO, 15% WARNING, 10% ERROR, 3% CRITICAL, 2% DEBUG
    - Add `argparse` CLI with `--total-entries`, `--service-count`, `--anomaly-window-count`, `--ingest-url`, `--batch-size`; all parameters default to MVP spec values
    - Print progress to stdout (batch N/20, HTTP status per batch)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [ ] 11. Integration tests
  - [ ]* 11.1 Write integration tests for `POST /logs/ingest` (`tests/integration/test_ingest.py`)
    - Test valid batch of 100 entries → HTTP 200, `accepted=100`, `rejected=0`
    - Test entries with missing/invalid fields → HTTP 422 with field-level error detail
    - Test empty batch (`entries=[]`) → HTTP 200, `accepted=0`, `rejected=0`
    - Test batch of 501 entries → HTTP 413 with `limit` and `received` in body
    - _Requirements: 9.5_

  - [ ]* 11.2 Write integration tests for `GET /logs` (`tests/integration/test_logs.py`)
    - Test default pagination envelope fields present (`total_count`, `page`, `page_size`, `has_more`, `data`)
    - Test custom `page` and `page_size` parameters return correct slice
    - Test `service`, `level`, `start_time`, `end_time` filter combinations
    - _Requirements: 9.6_

  - [ ]* 11.3 Write integration tests for `POST /analyze` and `GET /analyze/{job_id}` (`tests/integration/test_analyze.py`)
    - Test `POST /analyze` returns HTTP 202 with `job_id` and `status=pending`
    - Test `GET /analyze/{job_id}` returns current status
    - Test job transitions to `completed` with `anomalies_found` and `alerts_dispatched` populated
    - Test `GET /analyze/{unknown_id}` returns HTTP 404
    - _Requirements: 9.7_

  - [ ]* 11.4 Write integration tests for `GET /health` and `GET /metrics` (`tests/integration/test_health.py`, `tests/integration/test_metrics.py`)
    - Test `GET /health` returns HTTP 200 with `database=ok` and `bedrock.status=unknown` on fresh start
    - Test `GET /health` returns HTTP 503 when DB is unreachable
    - Test `GET /metrics` counters increment correctly after ingest, anomaly detection, and alert dispatch operations
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

- [x] 12. Test infrastructure and `conftest.py`
  - [x] 12.1 Implement `tests/conftest.py` — shared pytest fixtures
    - `test_db` fixture: in-memory SQLite (`sqlite:///:memory:`), create all tables, yield session, drop all
    - `test_client` fixture: `TestClient` wrapping the FastAPI app with `test_db` overriding `get_db`
    - `mock_bedrock` fixture: `unittest.mock.patch` on `BedrockClient._client.converse` returning a fixture JSON response
    - `mock_webhook` fixture: `respx` mock intercepting outbound `POST` to `WEBHOOK_URL`
    - Write `pytest.ini` with `testpaths=tests`, `--cov=app --cov-branch --cov-report=term-missing --cov-fail-under=80`, and markers `unit`, `integration`, `property`
    - Write `.flake8` with `max-line-length=120`, `exclude=.venv,__pycache__,.git`, `ignore=E203,W503`
    - _Requirements: 9.1, 9.9, 9.10_

- [x] 13. Documentation files
  - [x] 13.1 Write `README.md` and `CONTRIBUTING.md`
    - `README.md`: virtual environment setup, `pip install -r requirements.txt`, `.env` configuration from `.env.example`, local startup command (`uvicorn app.main:app --reload`), how to run the synthetic log generator, how to run tests
    - `CONTRIBUTING.md`: development workflow, coding standards (snake_case, PascalCase, SCREAMING_SNAKE_CASE, Google-style docstrings, flake8), how to run tests with coverage
    - _Requirements: 1.6, 10.6_

  - [x] 13.2 Write remaining markdown documentation files
    - Create stubs with appropriate content for: `architectural_decision_records.md`, `implementation_plan.md`, `testing_specifics.md`, `data_flow_integration.md`, `cost_analysis.md`, `security_compliance.md`, `observability_evaluation.md`, `deployment_instructions.md`
    - _Requirements: 10.5_

- [x] 14. Final checkpoint — full test suite and linting
  - Run `pytest --cov=app --cov-branch --cov-fail-under=80` and confirm all tests pass
  - Run `flake8 app/ tests/ generate_logs.py` and confirm zero errors
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 9.1, 9.9, 9.10, 10.4_

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP build
- Each task references specific requirements for traceability
- Checkpoints (tasks 8 and 14) validate incremental progress before moving to the next layer
- The round-trip property test in task 2.3 uses Hypothesis and is the only property-based test; all other tests are unit or integration tests
- APScheduler is not started in tests; Gate 1/2 functions are called directly via fixtures
- `freezegun` is used for all time-sensitive tests (sliding window, cooldown)
- `respx` is used to intercept outbound `httpx` webhook calls in tests

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4"] },
    { "id": 2, "tasks": ["2.1", "2.2"] },
    { "id": 3, "tasks": ["2.3", "3.1", "3.2"] },
    { "id": 4, "tasks": ["4.1"] },
    { "id": 5, "tasks": ["4.2", "5.1"] },
    { "id": 6, "tasks": ["5.2", "6.1"] },
    { "id": 7, "tasks": ["6.2", "6.3"] },
    { "id": 8, "tasks": ["7.1", "7.2", "7.3", "7.4"] },
    { "id": 9, "tasks": ["9.1"] },
    { "id": 10, "tasks": ["9.2", "10.1"] },
    { "id": 11, "tasks": ["12.1"] },
    { "id": 12, "tasks": ["11.1", "11.2", "11.3", "11.4"] },
    { "id": 13, "tasks": ["13.1", "13.2"] }
  ]
}
```
