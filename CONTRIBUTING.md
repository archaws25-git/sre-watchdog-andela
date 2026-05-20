# Contributing to SRE Watchdog

Thank you for contributing to SRE Watchdog. This document covers the development workflow, coding standards, and testing practices for the project.

---

## Development Workflow

1. **Fork** the repository (or create a feature branch from `main`).
2. **Create a branch** with a descriptive name:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes** following the coding standards below.
4. **Run tests and linting** to verify your changes pass:
   ```bash
   pytest
   flake8 app/ tests/ generate_logs.py
   ```
5. **Commit** with a clear, concise message describing the change.
6. **Push** your branch and open a Pull Request against `main`.
7. Address any review feedback and ensure CI checks pass.

---

## Coding Standards

### Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Variables and functions | `snake_case` | `error_rate`, `compute_error_rate()` |
| Classes | `PascalCase` | `AnomalyDetector`, `BedrockClient` |
| Constants | `SCREAMING_SNAKE_CASE` | `MAX_INGEST_BATCH_SIZE`, `RETRY_BASE_DELAY_SECONDS` |

### Docstrings

All public functions and methods must have **Google-style docstrings**:

```python
def compute_error_rate(total_count: int, error_count: int) -> float:
    """Compute the error rate as a ratio of errors to total entries.

    Args:
        total_count: Total number of log entries in the window.
        error_count: Number of ERROR + CRITICAL entries.

    Returns:
        A float between 0.0 and 1.0 representing the error rate.

    Raises:
        ValueError: If total_count is zero.
    """
```

Every Python source file must include a **module-level docstring** at the top describing the module's purpose:

```python
"""Anomaly detection service implementing the two-gate detection pipeline.

Gate 1 performs synchronous statistical pre-filtering on each APScheduler tick.
Gate 2 dispatches asynchronous Bedrock analysis via FastAPI BackgroundTasks.
"""
```

### General Style

- Maximum line length: **120 characters**
- Use type hints for all function signatures
- Prefer explicit imports over wildcard imports
- Keep functions focused — one responsibility per function
- Use `Optional[T]` for nullable parameters, not `T | None` (for consistency)

---

## Linting

The project uses `flake8` with the configuration in `.flake8`:

```bash
flake8 app/ tests/ generate_logs.py
```

Configuration (`.flake8`):
- `max-line-length = 120`
- `exclude = .venv,__pycache__,.git`
- `ignore = E203,W503`

All code must pass flake8 with **zero errors** before submitting a PR.

---

## Testing

### Running Tests

Run the full test suite:

```bash
pytest
```

Run with explicit coverage reporting:

```bash
pytest --cov=app --cov-branch --cov-report=term-missing
```

Run specific test categories using markers:

```bash
pytest -m unit          # Unit tests only
pytest -m integration   # Integration tests only
pytest -m property      # Property-based tests only
```

### Coverage Requirements

| Scope | Minimum Coverage |
|-------|-----------------|
| Critical paths (anomaly detection, alert dispatch) | 95% line + 95% branch |
| API routers | 85% line |
| Supporting modules (config, utilities, generator) | 70% line |
| **Overall project floor** | **80% line** |

The `pytest.ini` enforces the 80% floor automatically via `--cov-fail-under=80`. Branch coverage is mandatory for detection and alert dispatch logic.

### Test Markers

Decorate tests with the appropriate marker:

```python
import pytest

@pytest.mark.unit
def test_severity_mapping():
    ...

@pytest.mark.integration
def test_ingest_endpoint():
    ...

@pytest.mark.property
def test_log_entry_roundtrip():
    ...
```

### Writing Tests

- Place unit tests in `tests/unit/`
- Place integration tests in `tests/integration/`
- Use the fixtures defined in `tests/conftest.py` for test DB, test client, and mocked services
- Do not mock core logic — mock only external boundaries (Bedrock API, webhook targets)
- Property-based tests use the `hypothesis` library

---

## Project Structure

```
sre-watchdog/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app factory, lifespan, router registration
│   ├── config.py               # pydantic-settings; reads all env vars
│   ├── database.py             # SQLite engine, WAL mode, session factory
│   ├── middleware.py           # Structured JSON request logging
│   ├── scheduler.py            # APScheduler setup and job registration
│   ├── models/
│   │   ├── db_models.py        # SQLAlchemy ORM models
│   │   └── schemas.py          # Pydantic request/response schemas
│   ├── routers/
│   │   ├── logs.py             # POST /logs/ingest, GET /logs
│   │   ├── anomalies.py        # GET /anomalies, GET /anomalies/{id}
│   │   ├── analyze.py          # POST /analyze, GET /analyze/{job_id}
│   │   ├── alerts.py           # GET /alerts
│   │   ├── webhooks.py         # POST /webhooks/echo
│   │   ├── health.py           # GET /health
│   │   ├── metrics.py          # GET /metrics
│   │   └── dashboard.py        # GET /dashboard (Jinja2 SSR)
│   ├── services/
│   │   ├── log_ingestion_service.py
│   │   ├── anomaly_detector.py
│   │   ├── bedrock_client.py
│   │   ├── alert_service.py
│   │   └── dashboard_service.py
│   └── templates/
│       └── dashboard.html      # Jinja2 + Chart.js dashboard template
├── tests/
│   ├── conftest.py             # Shared fixtures
│   ├── unit/                   # Unit tests
│   └── integration/            # Integration tests
├── generate_logs.py            # Synthetic log generator CLI
├── .env.example                # Environment variable reference
├── requirements.txt            # Pinned dependencies
├── pytest.ini                  # pytest configuration
└── .flake8                     # flake8 configuration
```

---

## Questions?

If you're unsure about a design decision or coding pattern, check the spec documents in `.kiro/specs/sre-watchdog/` or open a discussion on the PR.
