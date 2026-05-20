# SRE Watchdog

AI-powered observability platform for Site Reliability Engineering teams. SRE Watchdog ingests application logs, detects anomalies using a two-gate statistical + AI pipeline (AWS Bedrock), dispatches webhook alerts when thresholds are breached, and visualizes service health on an internal dashboard.

---

## Prerequisites

- **Python 3.11+**
- **AWS credentials** configured for Bedrock access (`~/.aws/credentials` or environment variables)
- **pip** (bundled with Python)

---

## Setup

### 1. Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

- **Windows (PowerShell):**
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
- **Windows (CMD):**
  ```cmd
  .\.venv\Scripts\activate.bat
  ```
- **macOS / Linux:**
  ```bash
  source .venv/bin/activate
  ```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the example file and edit it with your settings:

```bash
cp .env.example .env
```

Key variables to configure:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLAlchemy connection string | `sqlite:///./watchdog.db` |
| `AWS_REGION` | AWS region for Bedrock | `us-east-1` |
| `BEDROCK_MODEL_ID` | Bedrock model identifier | `us.anthropic.claude-sonnet-4-5-20251101-v1:0` |
| `ERROR_RATE_THRESHOLD` | Gate 1 error rate trigger | `0.1` |
| `ANOMALY_SCORE_THRESHOLD` | Gate 2 AI score trigger | `0.5` |
| `WEBHOOK_URL` | Alert dispatch target | `http://localhost:8000/webhooks/echo` |

See `.env.example` for the full list of configurable variables with descriptions.

### 4. Start the application

```bash
uvicorn app.main:app --reload
```

The API server starts at `http://localhost:8000`. Access the dashboard at:

**http://localhost:8000/dashboard**

---

## Synthetic Log Generator

Populate the system with ~10,000 realistic log entries across 5 services, including 3 deliberate anomaly windows:

```bash
python generate_logs.py
```

The generator submits logs in batches of 500 to the running application's `POST /logs/ingest` endpoint. Make sure the server is running before executing the generator.

Optional CLI arguments allow configuring entry count, service count, and anomaly window count. Run `python generate_logs.py --help` for details.

---

## Running Tests

Run the full test suite with coverage:

```bash
pytest
```

This executes with the settings defined in `pytest.ini`:
- Coverage for the `app/` package with branch coverage enabled
- Minimum 80% coverage threshold enforced
- Test markers: `unit`, `integration`, `property`

Run specific test categories:

```bash
pytest -m unit
pytest -m integration
pytest -m property
```

Run with verbose output:

```bash
pytest -v
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/logs/ingest` | Ingest a batch of log entries (max 500) |
| `GET` | `/logs` | Query stored logs with pagination and filters |
| `POST` | `/analyze` | Trigger on-demand anomaly analysis (returns job ID) |
| `GET` | `/analyze/{job_id}` | Poll analysis job status and results |
| `GET` | `/anomalies` | List detected anomaly windows |
| `GET` | `/anomalies/{id}` | Get full lifecycle detail for an anomaly |
| `GET` | `/alerts` | List dispatched alert records |
| `POST` | `/webhooks/echo` | Built-in webhook echo endpoint for testing |
| `GET` | `/health` | Platform health check (API, DB, Bedrock status) |
| `GET` | `/metrics` | Operational counters |
| `GET` | `/dashboard` | HTML dashboard with charts and anomaly list |

---

## Architecture Overview

```
┌──────────────┐     ┌──────────────────────────────────────────────┐
│  Log Sources │────▶│  FastAPI Application                         │
│  (CLI / API) │     │                                              │
└──────────────┘     │  ┌─────────────┐   ┌──────────────────────┐ │
                     │  │ Log Ingest  │   │ APScheduler (Gate 1) │ │
                     │  │ Service     │   │ Statistical Filter   │ │
                     │  └─────────────┘   └──────────┬───────────┘ │
                     │                               │              │
                     │                    ┌──────────▼───────────┐  │
                     │                    │ BackgroundTask        │  │
                     │                    │ (Gate 2 — Bedrock AI) │  │
                     │                    └──────────┬───────────┘  │
                     │                               │              │
                     │                    ┌──────────▼───────────┐  │
                     │                    │ Alert Service         │  │
                     │                    │ (Webhook Dispatch)    │  │
                     │                    └──────────────────────┘  │
                     │                                              │
                     │  ┌─────────────┐   ┌──────────────────────┐ │
                     │  │ Dashboard   │   │ SQLite (WAL mode)    │ │
                     │  │ (Jinja2 +   │   │ log_entries          │ │
                     │  │  Chart.js)  │   │ anomaly_windows      │ │
                     │  └─────────────┘   │ alert_records        │ │
                     │                    └──────────────────────┘  │
                     └──────────────────────────────────────────────┘
                                              │
                                    ┌─────────▼─────────┐
                                    │  AWS Bedrock       │
                                    │  (Claude Sonnet)   │
                                    └───────────────────┘
```

**Detection pipeline:**
1. **Gate 1** (synchronous, APScheduler tick): Computes per-service error rate over a sliding window. Creates an anomaly record if the threshold is breached.
2. **Gate 2** (asynchronous, BackgroundTask): Sends log context to AWS Bedrock for AI analysis. Updates the anomaly lifecycle status and dispatches alerts when confirmed.

---

## License

This project is licensed under the MIT License — see the [LICENSE.txt](LICENSE.txt) file for details.
