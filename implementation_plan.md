# Implementation Plan

This document outlines the phased implementation approach for the SRE Watchdog platform. The build follows a layered strategy where each phase builds on the previous one, ensuring testability and incremental validation at every step.

---

## Overview

The SRE Watchdog is implemented as a Python 3.11+ FastAPI application with SQLite persistence, AWS Bedrock AI integration, and a Jinja2/Chart.js dashboard. The implementation follows a bottom-up approach: foundational layers first, then services, then API routers, and finally the dashboard and test suite.

**Total estimated phases:** 6  
**Core technology stack:** Python 3.11+, FastAPI, SQLAlchemy, Pydantic, APScheduler, boto3, httpx, Chart.js, Jinja2

---

## Phase 1: Project Scaffold and Configuration

**Goal:** Establish the project structure, dependency management, and environment-based configuration.

**Deliverables:**
- `requirements.txt` with all pinned dependencies
- `.env.example` with documented environment variables
- `app/config.py` — Pydantic Settings class with validation
- `app/__init__.py` — package marker
- `app/main.py` — FastAPI app factory with lifespan stub
- `pytest.ini` and `.flake8` configuration files
- `README.md` with setup instructions

**Validation:** Application starts with `uvicorn app.main:app` and responds to a basic health probe.

---

## Phase 2: Data Layer

**Goal:** Establish the persistence layer with SQLAlchemy ORM models and database initialization.

**Deliverables:**
- `app/database.py` — SQLite engine creation, WAL mode pragma, session factory
- `app/models/db_models.py` — ORM models: `LogEntry`, `AnomalyWindow`, `AlertRecord`, `WebhookEchoLog`
- `app/models/schemas.py` — Pydantic request/response schemas for all endpoints
- Database migration/initialization on startup via `Base.metadata.create_all()`

**Validation:** Tables are created on startup. Schema validation passes for all model types.

---

## Phase 3: Core Services

**Goal:** Implement the business logic layer — log ingestion, anomaly detection, Bedrock integration, and alert dispatch.

**Deliverables:**
- `app/services/log_ingestion_service.py` — Batch validation, persistence, structured logging
- `app/services/anomaly_detector.py` — Gate 1 statistical pre-filter, sliding window error rate computation
- `app/services/bedrock_client.py` — AWS Bedrock Converse API wrapper, prompt construction, response parsing, retry logic
- `app/services/alert_service.py` — Severity mapping, webhook dispatch, retry, cooldown check
- `app/services/dashboard_service.py` — Aggregation queries for dashboard data
- `app/scheduler.py` — APScheduler setup and job registration
- `app/middleware.py` — Structured JSON request logging

**Validation:** Unit tests pass for all service functions. Bedrock client works with mocked responses.

---

## Phase 4: API Routers

**Goal:** Expose all REST API endpoints, wiring routers to the service layer.

**Deliverables:**
- `app/routers/logs.py` — `POST /logs/ingest`, `GET /logs`
- `app/routers/anomalies.py` — `GET /anomalies`, `GET /anomalies/{id}`
- `app/routers/analyze.py` — `POST /analyze`, `GET /analyze/{job_id}`
- `app/routers/alerts.py` — `GET /alerts`
- `app/routers/webhooks.py` — `POST /webhooks/echo`
- `app/routers/health.py` — `GET /health`
- `app/routers/metrics.py` — `GET /metrics`
- `app/routers/dashboard.py` — `GET /dashboard`
- Router registration in `app/main.py`

**Validation:** All endpoints respond with correct status codes. Integration tests pass.

---

## Phase 5: Dashboard and Synthetic Log Generator

**Goal:** Implement the Jinja2/Chart.js dashboard and the standalone log generation utility.

**Deliverables:**
- `app/templates/dashboard.html` — Jinja2 template with Chart.js, auto-refresh, Run Analysis button
- `generate_logs.py` — CLI script generating ~10,000 synthetic logs across 5 services with 3 seeded anomaly windows

**Validation:** Dashboard renders correctly with sample data. Generator successfully ingests 10,000 entries in 20 batches.

---

## Phase 6: Testing and Quality Assurance

**Goal:** Achieve coverage targets and ensure code quality standards.

**Deliverables:**
- `tests/conftest.py` — Shared fixtures (test DB, test client, mock Bedrock, mock webhook)
- `tests/unit/` — Unit tests for all services (95% critical paths, 70% supporting)
- `tests/integration/` — Integration tests for all routers (85% coverage)
- Property-based test for Log_Entry round-trip (Hypothesis)
- `flake8` passes with zero errors
- Overall coverage ≥ 80% with branch coverage enabled

**Validation:** `pytest --cov-branch --cov-fail-under=80` passes. `flake8` reports no errors.

---

## Dependencies Between Phases

```
Phase 1 (Scaffold)
    └── Phase 2 (Data Layer)
            └── Phase 3 (Services)
                    ├── Phase 4 (Routers)
                    │       └── Phase 5 (Dashboard + Generator)
                    └── Phase 6 (Testing) — runs in parallel with Phases 4–5
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Bedrock API unavailability during development | Mock Bedrock responses in tests; credential check at startup with graceful degradation |
| SQLite concurrency limitations | WAL mode; single-instance deployment for MVP; PostgreSQL upgrade path documented |
| APScheduler thread safety with FastAPI async | Thread-safe queue for BackgroundTask submission; `max_instances=1` prevents overlapping ticks |
| Token cost overruns during testing | `BEDROCK_MAX_LOG_SAMPLE=50` caps prompt size; all tests use mocked Bedrock responses |
