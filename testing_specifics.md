# Testing Specifics

This document describes the testing strategy, tools, coverage requirements, and key test patterns used in the SRE Watchdog project.

---

## Testing Strategy

The SRE Watchdog employs a three-tier testing approach:

1. **Unit Tests** — Validate individual service functions and business logic in isolation.
2. **Integration Tests** — Validate API endpoints end-to-end using a test HTTP client and in-memory database.
3. **Property-Based Tests** — Validate universal properties across all valid inputs using Hypothesis.

---

## Tools and Libraries

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner and framework |
| `pytest-cov` | Coverage measurement with branch coverage support |
| `hypothesis` | Property-based testing for schema round-trip validation |
| `freezegun` | Time manipulation for cooldown and sliding window tests |
| `respx` | Mock HTTP transport for outbound webhook dispatch testing |
| `httpx` | Test client for FastAPI integration tests (via `TestClient`) |
| `unittest.mock` | Patching external dependencies (Bedrock client, boto3) |

---

## Coverage Requirements

The project enforces tiered coverage thresholds reflecting the criticality of each component:

| Component | Line Coverage | Branch Coverage | Rationale |
|-----------|--------------|-----------------|-----------|
| Anomaly Detector (Gate 1/2 logic) | 95% | 95% | Missed branches = silent operational failures |
| Alert Service (dispatch + cooldown) | 95% | 95% | Alert suppression bugs directly impact on-call |
| Bedrock Client (parsing + retry) | 95% | 95% | Parse failures must be handled gracefully |
| API Routers (all endpoints) | 85% | — | Standard API coverage |
| Config, utilities, generator | 70% | — | Supporting code with lower risk |
| **Overall project floor** | **80%** | **Enabled** | Enforced via `--cov-fail-under=80` |

**Execution command:**
```bash
pytest --cov=app --cov-branch --cov-report=term-missing --cov-fail-under=80
```

---

## Test Structure

```
tests/
├── __init__.py
├── conftest.py                  # Shared fixtures
├── unit/
│   ├── __init__.py
│   ├── test_anomaly_detector.py # Gate 1/2, cooldown, status transitions
│   ├── test_bedrock_client.py   # Response parsing, retry, errors
│   ├── test_alert_service.py    # Severity mapping, dispatch, suppression
│   ├── test_config.py           # Env var parsing, ConfigurationError
│   └── test_schemas.py          # Round-trip property test (Hypothesis)
└── integration/
    ├── __init__.py
    ├── test_ingest.py           # POST /logs/ingest scenarios
    ├── test_logs.py             # GET /logs pagination and filters
    ├── test_analyze.py          # POST /analyze + GET /analyze/{job_id}
    ├── test_health.py           # GET /health normal + degraded
    └── test_metrics.py          # GET /metrics counter accuracy
```

---

## Key Test Fixtures (`conftest.py`)

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `test_db` | function | In-memory SQLite database with all tables created |
| `test_client` | function | FastAPI `TestClient` wired to the test database |
| `mock_bedrock` | function | Patched `bedrock_client._client.converse` returning fixture JSON |
| `mock_webhook` | function | `respx` mock intercepting outbound webhook POST requests |
| `sample_logs` | function | Pre-generated list of `LogEntryCreate` objects for ingestion tests |
| `frozen_time` | function | `freezegun` context for time-sensitive tests |

---

## Mocking Strategy

| External Dependency | Mock Approach | Rationale |
|--------------------|---------------|-----------|
| AWS Bedrock | `unittest.mock.patch` on `_client.converse` | Avoids real API calls and token costs |
| Webhook target | `respx` library | Intercepts outbound HTTP without network I/O |
| SQLite database | In-memory `sqlite:///:memory:` | Fast, isolated, no file cleanup needed |
| APScheduler | Not started in tests | Gate 1/2 functions called directly for deterministic testing |
| System time | `freezegun` | Deterministic cooldown and sliding window evaluation |

---

## Property-Based Testing

The project uses Hypothesis for property-based testing of the Log_Entry schema round-trip:

**Property:** For all valid `LogEntryCreate` objects, serializing to JSON and deserializing back produces an equivalent object.

**Strategy:**
- `timestamp`: UTC datetimes
- `service`: Sampled from the 5 valid service names
- `level`: Sampled from `LogLevel` enum values
- `message`: Text strings (1–500 characters)

**Validates:** Requirement 9.8 — round-trip property for Log_Entry serialization.

---

## Critical Test Scenarios

### Anomaly Detector
- Zero log entries in window → no anomaly created
- Single spike → full pipeline: Gate 1 breach → Gate 2 → confirmed → alerted
- Sustained high error rate → multiple anomaly windows created
- Cooldown active → Bedrock runs, result persisted, status = `suppressed`
- Bedrock parse error → status = `analysis_failed`, no alert

### Alert Service
- Severity band boundaries: 0.39 → LOW, 0.40 → MEDIUM, 0.69 → MEDIUM, 0.70 → HIGH, 0.89 → HIGH, 0.90 → CRITICAL
- Successful dispatch → `sent` status, HTTP 200 recorded
- Failed dispatch after 3 retries → `failed` status logged
- `analysis_failed` records never reach Alert Service
- Suppressed records create audit row but no HTTP POST

### Bedrock Client
- Valid JSON response → correct score and summary extraction
- Malformed response → `BedrockParseError` raised
- Score out of range (< 0 or > 1) → `BedrockParseError` raised
- Transient error → retry with exponential backoff
- Non-retryable error → immediate failure

---

## Running Tests

```bash
# Run all tests with coverage
pytest

# Run only unit tests
pytest tests/unit/ -m unit

# Run only integration tests
pytest tests/integration/ -m integration

# Run property-based tests
pytest tests/unit/test_schemas.py -m property

# Run with verbose output
pytest -v --tb=short
```
