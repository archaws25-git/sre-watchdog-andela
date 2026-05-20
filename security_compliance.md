# Security and Compliance

This document describes the security posture of the SRE Watchdog platform, covering authentication, data handling, credential management, and input validation.

---

## 1. Authentication

### MVP: No Authentication

The MVP dashboard and API endpoints are accessible without authentication. This is appropriate for local development and internal network deployment only.

**Documented risk:** Any user with network access to the Watchdog can:
- View all ingested logs and anomaly data
- Trigger analysis jobs
- Access the dashboard

### Production: OAuth2/OIDC

For production deployment, the following authentication strategy is recommended:

| Component | Approach |
|-----------|----------|
| Identity Provider | AWS Cognito, Auth0, or Okta |
| Token Format | JWT Bearer tokens |
| FastAPI Integration | `OAuth2AuthorizationCodeBearer` dependency |
| Token Validation | Verify signature, expiry, and audience claims |
| Session Management | Stateless (JWT) — no server-side session store |

**Role-Based Access Control (RBAC):**

| Role | Permissions |
|------|------------|
| `viewer` | Read-only access to dashboard, logs, anomalies, alerts, metrics |
| `operator` | All viewer permissions + trigger analysis, manage configuration |
| `admin` | All operator permissions + user management, system configuration |

---

## 2. Data Handling

### No PII in Logs

The SRE Watchdog is designed to ingest **application-level operational logs only**. These logs should contain:
- Service identifiers
- Error messages and stack traces
- Request metadata (paths, status codes, latency)
- Infrastructure health indicators

**Logs should NOT contain:**
- Personally identifiable information (PII)
- Authentication tokens or credentials
- Payment card data (PCI DSS scope)
- Health information (HIPAA scope)
- User email addresses or phone numbers

**Responsibility:** Log sanitization is the responsibility of the upstream services before submission to `POST /logs/ingest`. The Watchdog does not perform PII detection or redaction.

### Data Retention

The MVP does not implement automatic data retention policies. For production:
- Implement TTL-based log purging (e.g., 30-day retention for `log_entries`)
- Archive anomaly records to cold storage after 90 days
- Alert records retained indefinitely for audit compliance

### Data at Rest

- SQLite database stored on local filesystem (MVP)
- No encryption at rest in MVP configuration
- Production: Use RDS PostgreSQL with encryption at rest enabled (AWS KMS)

### Data in Transit

- Local development: HTTP (localhost only)
- Production: HTTPS via TLS 1.2+ (terminated at load balancer or App Runner)
- Bedrock API calls: HTTPS (enforced by AWS SDK)
- Webhook dispatch: HTTPS recommended for `WEBHOOK_URL`

---

## 3. AWS Credential Management

### MVP: Local Credentials

AWS credentials for Bedrock access are resolved via the standard boto3 credential chain:

1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
2. Shared credentials file (`~/.aws/credentials`)
3. AWS config file (`~/.aws/config`)
4. IAM instance profile (EC2/ECS task role)

**Startup validation:** The application checks for credential availability at startup via `boto3.Session().get_credentials()`. If absent, a warning is logged and Bedrock health is set to `degraded`. Startup is **not** halted.

### Production: IAM Task Roles

For ECS/Fargate deployment:
- Attach an IAM task execution role with `bedrock:InvokeModel` permission
- Scope the role to the specific model ARN used by the Watchdog
- No long-lived credentials stored in environment variables or files
- Use AWS Secrets Manager for any additional secrets (webhook signing keys, etc.)

**Minimum IAM policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:us-east-1::foundation-model/us.anthropic.claude-sonnet-4-5-20251101-v1:0"
    }
  ]
}
```

### Credential Rotation

- IAM task roles: Automatic rotation managed by AWS
- Long-lived access keys (development only): Rotate every 90 days
- Webhook signing secrets: Rotate quarterly

---

## 4. Input Validation

### API Input Validation

All API inputs are validated via Pydantic schemas before processing:

| Endpoint | Validation |
|----------|-----------|
| `POST /logs/ingest` | Schema validation per entry; batch size limit (500) |
| `GET /logs` | Query parameter types and ranges (`page` ≥ 1, `page_size` 1–500) |
| `POST /analyze` | ISO 8601 timestamps; optional service name from allowed list |
| `POST /webhooks/echo` | Any valid JSON body accepted (echo endpoint) |

**Validation failures:** Return HTTP 422 with structured error body identifying invalid fields.

### SQL Injection Prevention

- All database queries use SQLAlchemy ORM with parameterized queries
- No raw SQL string interpolation anywhere in the codebase
- SQLite WAL mode configured via PRAGMA (not user-controllable)

### Request Size Limits

| Limit | Value | Enforcement |
|-------|-------|-------------|
| Batch size | 500 entries | Application-level check → HTTP 413 |
| Individual message length | Unbounded (MVP) | Consider 10KB limit for production |
| Request body size | Framework default | Configure via ASGI server for production |

---

## 5. Dependency Security

### Pinned Dependencies

All dependencies are pinned to exact versions in `requirements.txt` to prevent supply chain attacks via version drift.

### Vulnerability Scanning

**Recommended tools for production:**
- `pip-audit` — Check installed packages against known vulnerabilities
- `safety` — Python dependency vulnerability scanner
- Dependabot / Renovate — Automated dependency update PRs

### Key Dependencies and Their Security Posture

| Package | Purpose | Security Notes |
|---------|---------|---------------|
| `fastapi` | Web framework | Active maintenance, regular security patches |
| `pydantic` | Data validation | Type-safe input handling |
| `sqlalchemy` | ORM | Parameterized queries by default |
| `boto3` | AWS SDK | AWS-maintained, follows shared responsibility model |
| `httpx` | HTTP client | Supports TLS verification by default |

---

## 6. Logging Security

### What Is Logged

- HTTP request metadata (method, path, status code, latency)
- Bedrock inference metadata (token counts, latency, anomaly scores)
- Alert dispatch outcomes (target URL, severity, HTTP status)
- Application errors and warnings

### What Is NOT Logged

- Request bodies (may contain sensitive log content)
- AWS credentials or tokens
- Full webhook payloads (only metadata)
- Database connection strings with credentials

### Log Output

- All logs emitted as structured JSON to stdout
- No log files written to disk in MVP (stdout captured by container runtime in production)
- Log level configurable via `LOG_LEVEL` environment variable

---

## 7. Network Security (Production)

| Layer | Control |
|-------|---------|
| Ingress | ALB with security groups; restrict to VPC or known CIDR ranges |
| Egress | Security group allows HTTPS to Bedrock endpoint and webhook targets only |
| Internal | VPC private subnets for ECS tasks; no public IP |
| DNS | Private hosted zone for internal service discovery |
| TLS | TLS 1.2+ enforced at ALB; HSTS headers on responses |
