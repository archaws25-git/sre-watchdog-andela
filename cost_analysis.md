# Cost Analysis

This document analyses the operational costs of running the SRE Watchdog, with a focus on AWS Bedrock token usage — the primary variable cost driver.

---

## 1. AWS Bedrock Token Costs

The SRE Watchdog uses AWS Bedrock's Converse API with Claude Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-20251101-v1:0`) for anomaly analysis.

### Pricing (us-east-1 region)

| Token Type | Cost per 1K tokens |
|------------|-------------------|
| Input tokens | $0.003 |
| Output tokens | $0.015 |

*Note: Pricing is subject to change. Refer to the [AWS Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/) for current rates.*

### Per-Inference Token Estimate

| Component | Estimated Tokens |
|-----------|-----------------|
| System prompt + context | ~200 input tokens |
| Log messages (50 entries × ~25 tokens each) | ~1,250 input tokens |
| Service metadata + formatting | ~50 input tokens |
| **Total input per call** | **~1,500 tokens** |
| Output (JSON: score + summary) | ~80–120 tokens |
| **Total output per call** | **~100 tokens** |

### Cost Per Inference Call

```
Input:  1,500 tokens × $0.003/1K = $0.0045
Output:   100 tokens × $0.015/1K = $0.0015
─────────────────────────────────────────────
Total per call:                    $0.006
```

---

## 2. Cost Optimization Strategies

### 2.1 Log Message Capping

The `BEDROCK_MAX_LOG_SAMPLE` environment variable (default: 50) caps the number of log messages included in each Bedrock prompt. This directly controls input token count.

| Sample Size | Est. Input Tokens | Input Cost | Savings vs. Uncapped |
|-------------|-------------------|------------|---------------------|
| 25 entries | ~825 | $0.0025 | 45% |
| 50 entries (default) | ~1,500 | $0.0045 | Baseline |
| 100 entries | ~2,750 | $0.0083 | -83% increase |

**Recommendation:** The default of 50 provides sufficient context for accurate anomaly scoring while keeping costs predictable.

### 2.2 Cooldown Suppression

The alert cooldown window (`ALERT_COOLDOWN_MINUTES`, default: 15 minutes) prevents redundant alerts but does **not** suppress Bedrock calls. This is a deliberate design choice — Bedrock results are persisted for observability even during cooldown.

**Cost implication:** During sustained incidents, Bedrock is invoked every detection interval for the affected service. With a 60-second interval and 15-minute cooldown, this means up to 15 additional Bedrock calls per incident per service.

**Mitigation:** If cost is a concern during sustained incidents, consider increasing `DETECTION_INTERVAL_SECONDS` or implementing a separate "Bedrock cooldown" that skips inference for services already in cooldown.

### 2.3 Gate 1 Pre-filtering

Gate 1's statistical pre-filter ensures Bedrock is only invoked when the error rate exceeds the threshold. Under normal operations (error rate < 10%), zero Bedrock calls are made. This is the primary cost control mechanism.

---

## 3. Estimated Monthly Costs

### Usage Tiers

| Tier | Description | Anomalies/Day | Bedrock Calls/Day | Monthly Cost |
|------|-------------|---------------|-------------------|--------------|
| **Low** | Stable services, rare anomalies | 1–2 | 2–5 | $0.90 – $2.70 |
| **Medium** | Occasional incidents, weekly spikes | 5–10 | 10–25 | $1.80 – $4.50 |
| **High** | Frequent incidents, noisy services | 20–50 | 40–100 | $7.20 – $18.00 |
| **Stress** | Sustained degradation across services | 100+ | 200+ | $36.00+ |

### Calculation Basis

```
Monthly cost = (Bedrock calls/day) × 30 days × $0.006/call
```

### Additional AWS Costs (Minimal)

| Service | Usage | Estimated Cost |
|---------|-------|---------------|
| AWS credentials (IAM) | Authentication only | Free |
| Data transfer (Bedrock API) | < 1 MB/day typical | Negligible |
| CloudWatch Logs (if enabled) | Optional | ~$0.50/GB ingested |

---

## 4. Cost Monitoring

The SRE Watchdog logs token usage for every Bedrock call:

```json
{
  "level": "INFO",
  "logger": "app.services.bedrock_client",
  "message": "Bedrock inference completed",
  "input_tokens": 1240,
  "output_tokens": 87,
  "latency_ms": 1823.4
}
```

**Monitoring recommendations:**
- Track `total_anomalies_detected` via `GET /metrics` to correlate with Bedrock costs
- Monitor `total_analysis_failed` — failed calls still incur partial token costs
- Set AWS billing alerts for unexpected Bedrock usage spikes

---

## 5. Cost Comparison: MVP vs. Production

| Aspect | MVP (Local) | Production (ECS/Fargate) |
|--------|-------------|--------------------------|
| Compute | Local machine (free) | Fargate: ~$30–50/month (0.25 vCPU, 0.5 GB) |
| Database | SQLite (free) | RDS PostgreSQL: ~$15–30/month (db.t3.micro) |
| Bedrock | $1–18/month (usage-based) | Same pricing, higher volume |
| Load Balancer | N/A | ALB: ~$16/month + data |
| **Total** | **$1–18/month** | **$62–114/month** |

---

## 6. Cost Reduction Recommendations

1. **Increase `ERROR_RATE_THRESHOLD`** — Fewer Gate 1 breaches = fewer Bedrock calls. Trade-off: may miss subtle anomalies.
2. **Increase `DETECTION_INTERVAL_SECONDS`** — Less frequent checks = fewer potential Bedrock calls. Trade-off: slower detection.
3. **Reduce `BEDROCK_MAX_LOG_SAMPLE`** — Fewer tokens per call. Trade-off: less context for AI analysis.
4. **Implement Bedrock cooldown** — Skip inference for services already in alert cooldown. Trade-off: reduced observability during sustained incidents.
5. **Use a smaller model** — Switch to Claude Haiku for lower per-token costs. Trade-off: potentially less accurate anomaly scoring.
