# SRE Watchdog — Development Workflow

inclusion: auto

## Running the Application

```bash
# Activate virtual environment
.venv\Scripts\Activate.ps1  # Windows PowerShell

# Set AWS credentials (required for Bedrock)
$Env:AWS_ACCESS_KEY_ID="..."
$Env:AWS_SECRET_ACCESS_KEY="..."
$Env:AWS_SESSION_TOKEN="..."  # if using temporary credentials

# Start the server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Access dashboard
# http://127.0.0.1:8000/dashboard
```

## Generating Test Data

```bash
python generate_logs.py
# Generates 10,000 logs across 5 services with 3 seeded anomaly windows
# Submits in 20 batches of 500 to POST /logs/ingest
```

## Running Tests

```bash
pytest                    # Full suite with coverage
pytest -m unit            # Unit tests only
pytest -m integration     # Integration tests only
pytest -v                 # Verbose output
```

## Linting

```bash
flake8 app/ tests/ generate_logs.py
```

## Database Reset

To start fresh, delete `watchdog.db` and restart the server — tables are recreated automatically on startup.

## Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| BEDROCK_MODEL_ID | us.anthropic.claude-sonnet-4-6 | Bedrock model for analysis |
| ERROR_RATE_THRESHOLD | 0.1 | Gate 1 trigger (10% error rate) |
| ANOMALY_SCORE_THRESHOLD | 0.5 | Gate 2 trigger for alerts |
| DETECTION_INTERVAL_SECONDS | 60 | APScheduler tick frequency |
| ALERT_COOLDOWN_MINUTES | 15 | Suppress duplicate alerts |
| SLIDING_WINDOW_MINUTES | 5 | Error rate computation window |
| WEBHOOK_URL | http://localhost:8000/webhooks/echo | Alert target |

## Triggering On-Demand Analysis

Via API:
```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"start_time": "2026-05-19T00:00:00Z", "end_time": "2026-05-20T00:00:00Z"}'
```

Or click "Run Analysis" on the dashboard (analyzes the past hour across all services).
