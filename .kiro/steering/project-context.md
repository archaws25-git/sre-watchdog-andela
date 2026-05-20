# SRE Watchdog — Project Context

inclusion: always

## Overview

This is the SRE Watchdog project — a Python 3.11+ FastAPI application for AI-powered log observability. It ingests structured logs, detects anomalies via a two-gate pipeline (statistical pre-filter + AWS Bedrock AI analysis), dispatches webhook alerts, and visualizes service health on a Jinja2/Chart.js dashboard.

## Tech Stack

- **Framework:** FastAPI with Uvicorn
- **Database:** SQLite with WAL mode via SQLAlchemy ORM
- **AI:** AWS Bedrock Converse API (Claude Sonnet 4.6 via `us.anthropic.claude-sonnet-4-6`)
- **Scheduling:** APScheduler (BackgroundScheduler)
- **Configuration:** pydantic-settings (reads from .env)
- **HTTP Client:** httpx (webhook dispatch, log generator)
- **Dashboard:** Jinja2 templates + Chart.js
- **Testing:** pytest, hypothesis, freezegun, respx

## Architecture

- **Gate 1 (synchronous):** APScheduler tick computes per-service error rate over a sliding window. If threshold breached, creates AnomalyWindow with `pending_analysis` status.
- **Gate 2 (asynchronous):** FastAPI BackgroundTask invokes Bedrock for AI analysis. Updates status to confirmed/suppressed/below_score_threshold/analysis_failed.
- **Alert dispatch:** Webhook POST with retry (3 attempts). Cooldown prevents duplicate alerts for the same service.

## Key Conventions

- snake_case for variables/functions
- PascalCase for classes
- SCREAMING_SNAKE_CASE for constants
- Google-style docstrings on all public functions
- Module-level docstrings in every Python file
- Structured JSON logging throughout
- All configuration via environment variables (never hardcoded)

## 5 Monitored Services

`api-gateway`, `auth-service`, `payment-service`, `notification-service`, `database-proxy`

## Important Files

- `app/main.py` — FastAPI app factory, lifespan, router registration
- `app/config.py` — Settings class with all env vars
- `app/services/anomaly_detector.py` — Gate 1 + Gate 2 logic
- `app/services/bedrock_client.py` — Bedrock Converse API wrapper
- `app/services/alert_service.py` — Severity mapping, cooldown, webhook dispatch
- `generate_logs.py` — Synthetic log generator CLI
