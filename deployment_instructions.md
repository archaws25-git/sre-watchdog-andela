# Deployment Instructions

This document covers local development setup, containerization, cloud deployment options, and environment variable configuration for the SRE Watchdog platform.

---

## 1. Local Development Setup

### Prerequisites

- Python 3.11 or higher
- pip (Python package manager)
- AWS credentials configured (for Bedrock integration)
- Git

### Step-by-Step Setup

```bash
# 1. Clone the repository
git clone <repository-url>
cd sre-watchdog

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate the virtual environment
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Configure environment variables
cp .env.example .env
# Edit .env with your AWS credentials and desired configuration

# 6. Start the application
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 7. (Optional) Generate synthetic test data
python generate_logs.py
```

### Verify Installation

```bash
# Health check
curl http://localhost:8000/health

# Expected response:
# {"status": "ok", "database": "ok", "bedrock": {"status": "unknown", ...}}

# Dashboard
# Open http://localhost:8000/dashboard in a browser
```

### Running Tests

```bash
# Run all tests with coverage
pytest

# Run with verbose output
pytest -v --tb=short

# Run specific test categories
pytest tests/unit/ -m unit
pytest tests/integration/ -m integration
```

### AWS Credentials Setup

The Watchdog requires AWS credentials for Bedrock API access. Configure via any standard boto3 method:

```bash
# Option 1: Environment variables
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_REGION=us-east-1

# Option 2: AWS CLI configuration
aws configure

# Option 3: Shared credentials file (~/.aws/credentials)
[default]
aws_access_key_id = your-access-key
aws_secret_access_key = your-secret-key
```

---

## 2. Docker Containerization (Future)

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose the application port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Start the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Docker Compose (Development)

```yaml
version: '3.8'

services:
  watchdog:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./data:/app/data  # Persist SQLite database
    restart: unless-stopped
```

### Build and Run

```bash
# Build the image
docker build -t sre-watchdog:latest .

# Run the container
docker run -d \
  --name sre-watchdog \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  sre-watchdog:latest
```

---

## 3. AWS App Runner Deployment (Future)

AWS App Runner provides the simplest path to production deployment with automatic scaling and TLS.

### Steps

1. **Push image to ECR:**
   ```bash
   aws ecr create-repository --repository-name sre-watchdog
   docker tag sre-watchdog:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/sre-watchdog:latest
   docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/sre-watchdog:latest
   ```

2. **Create App Runner service:**
   - Source: ECR image
   - Port: 8000
   - Health check path: `/health`
   - Environment variables: Configure via App Runner console or CLI
   - Instance role: Attach IAM role with `bedrock:InvokeModel` permission

3. **Configure auto-scaling:**
   - Min instances: 1
   - Max instances: 5
   - Concurrency: 100 requests per instance

### Considerations

- SQLite is not suitable for App Runner (ephemeral filesystem). Migrate to RDS PostgreSQL.
- Use AWS Secrets Manager for sensitive environment variables.
- App Runner provides automatic TLS termination.

---

## 4. AWS ECS/Fargate Deployment (Future)

ECS/Fargate provides full control over networking, scaling, and resource allocation.

### Architecture

```
Internet → ALB → ECS Service (Fargate) → RDS PostgreSQL
                                       → AWS Bedrock
                                       → Webhook targets
```

### Task Definition (Key Fields)

```json
{
  "family": "sre-watchdog",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "containerDefinitions": [
    {
      "name": "watchdog",
      "image": "<account-id>.dkr.ecr.us-east-1.amazonaws.com/sre-watchdog:latest",
      "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 10
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/sre-watchdog",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "secrets": [
        {"name": "DATABASE_URL", "valueFrom": "arn:aws:secretsmanager:..."},
        {"name": "WEBHOOK_URL", "valueFrom": "arn:aws:secretsmanager:..."}
      ]
    }
  ],
  "taskRoleArn": "arn:aws:iam::<account-id>:role/sre-watchdog-task-role"
}
```

### Database Migration

For production ECS deployment, replace SQLite with Amazon RDS PostgreSQL:

```bash
# Update DATABASE_URL in environment
DATABASE_URL=postgresql://user:password@rds-endpoint:5432/sre_watchdog
```

SQLAlchemy's database URL abstraction means only the `DATABASE_URL` environment variable needs to change — no code modifications required.

---

## 5. Environment Variable Configuration

### Required Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite/PostgreSQL connection string | `sqlite:///./sre_watchdog.db` |
| `AWS_REGION` | AWS region for Bedrock API | `us-east-1` |
| `BEDROCK_MODEL_ID` | Bedrock model identifier | `us.anthropic.claude-sonnet-4-5-20251101-v1:0` |
| `WEBHOOK_URL` | Alert dispatch target URL | `http://localhost:8000/webhooks/echo` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ERROR_RATE_THRESHOLD` | Gate 1 error rate threshold (0.0–1.0) | `0.10` |
| `ANOMALY_SCORE_THRESHOLD` | Gate 2 score threshold (0.0–1.0) | `0.70` |
| `SLIDING_WINDOW_MINUTES` | Detection window duration | `5` |
| `ALERT_COOLDOWN_MINUTES` | Alert suppression window | `15` |
| `DETECTION_INTERVAL_SECONDS` | Scheduler tick interval | `60` |
| `MAX_INGEST_BATCH_SIZE` | Max entries per ingest request | `500` |
| `BEDROCK_MAX_LOG_SAMPLE` | Max log messages per Bedrock prompt | `50` |
| `LOG_LEVEL` | Application log level | `INFO` |
| `APP_HOST` | Server bind host | `0.0.0.0` |
| `APP_PORT` | Server bind port | `8000` |

### Environment-Specific Overrides

| Environment | Key Differences |
|-------------|----------------|
| **Development** | SQLite, echo webhook, DEBUG logging, short intervals |
| **Staging** | PostgreSQL, test webhook endpoint, INFO logging |
| **Production** | PostgreSQL (RDS), real webhook (PagerDuty/Slack), INFO logging, longer cooldowns |

---

## 6. Troubleshooting

### Common Issues

| Issue | Cause | Resolution |
|-------|-------|-----------|
| `ConfigurationError` at startup | Missing required env var | Check `.env` file exists and contains all required variables |
| Bedrock status "degraded" | No AWS credentials found | Configure AWS credentials via any boto3-supported method |
| HTTP 413 on ingest | Batch exceeds 500 entries | Split into smaller batches (generator uses 500 per batch) |
| Database locked errors | Concurrent write contention | Ensure WAL mode is enabled; check for long-running transactions |
| Webhook dispatch failures | Target URL unreachable | Verify `WEBHOOK_URL` is accessible; check network connectivity |

### Diagnostic Commands

```bash
# Check application health
curl -s http://localhost:8000/health | python -m json.tool

# View operational metrics
curl -s http://localhost:8000/metrics | python -m json.tool

# Check recent anomalies
curl -s "http://localhost:8000/anomalies?page_size=5" | python -m json.tool

# Verify database exists
ls -la sre_watchdog.db

# Check Python version
python --version  # Should be 3.11+
```
