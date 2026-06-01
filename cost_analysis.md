# Cost Analysis

This document analyses the operational costs of running the SRE Watchdog, with a focus on AWS Bedrock token usage — the primary variable cost driver.

---

## 1. AWS Bedrock Token Costs

The SRE Watchdog uses AWS Bedrock's Converse API with **Claude Sonnet 4.6** (`us.anthropic.claude-sonnet-4-6`) for anomaly analysis. This is Anthropic's mid-tier model launched February 2026, featuring a 1M token context window and 64K max output tokens.

### Pricing (us-east-1 region, cross-region inference profile)

| Token Type | Cost per 1M tokens | Cost per 1K tokens |
|------------|--------------------|--------------------|
| Input tokens | $3.00 | $0.003 |
| Output tokens | $15.00 | $0.015 |

*Source: [Anthropic Claude Sonnet 4.6 pricing](https://www.anthropic.com/claude/sonnet). Pricing is subject to change — refer to the [AWS Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/) for current rates.*

**Note:** Prompts exceeding 200,000 tokens incur approximately 2× input price and 1.5× output price. The SRE Watchdog's prompts are well under this threshold (~1,500 tokens).

### Per-Inference Token Estimate

Based on observed production usage with Claude Sonnet 4.6:

| Component | Estimated Tokens |
|-----------|-----------------|
| System prompt + context | ~200 input tokens |
| Log messages (50 entries × ~25 tokens each) | ~1,250 input tokens |
| Service metadata + formatting | ~50 input tokens |
| **Total input per call** | **~1,500 tokens** |
| Output (JSON: score + summary) | ~100–150 tokens |
| **Total output per call** | **~120 tokens** |

*Observed in production: input_tokens ≈ 300–330, output_tokens ≈ 115–135 (with fewer log messages in the sliding window). Full 50-message prompts reach ~1,500 input tokens.*

### Cost Per Inference Call

```
Input:  1,500 tokens × $3.00/1M  = $0.0045
Output:   120 tokens × $15.00/1M = $0.0018
─────────────────────────────────────────────
Total per call:                    $0.0063
```

**Observed actual cost per call (lighter prompts):**
```
Input:    330 tokens × $3.00/1M  = $0.00099
Output:   120 tokens × $15.00/1M = $0.0018
─────────────────────────────────────────────
Total per call (light):            $0.0028
```

---

## 2. Cost Optimization Strategies

### 2.1 Log Message Capping

The `BEDROCK_MAX_LOG_SAMPLE` environment variable (default: 50) caps the number of log messages included in each Bedrock prompt. This directly controls input token count.

| Sample Size | Est. Input Tokens | Input Cost | Savings vs. Default |
|-------------|-------------------|------------|---------------------|
| 25 entries | ~825 | $0.0025 | 45% reduction |
| 50 entries (default) | ~1,500 | $0.0045 | Baseline |
| 100 entries | ~2,750 | $0.0083 | 83% increase |

**Recommendation:** The default of 50 provides sufficient context for accurate anomaly scoring while keeping costs predictable.

### 2.2 Gate 1 Pre-filtering (Primary Cost Control)

Gate 1's statistical pre-filter ensures Bedrock is only invoked when the error rate exceeds `ERROR_RATE_THRESHOLD` (default: 10%). Under normal operations, **zero Bedrock calls are made**. This is the most effective cost control mechanism.

**Impact:** If 5 services are monitored with a 60-second detection interval:
- Normal operations: 0 Bedrock calls/hour = $0.00/hour
- Single service anomaly: 1 call per tick during breach = ~$0.38/hour max
- All 5 services in anomaly: 5 calls per tick = ~$1.89/hour max

### 2.3 Cooldown Behaviour

The alert cooldown window (`ALERT_COOLDOWN_MINUTES`, default: 15 minutes) suppresses alert dispatch only. Bedrock analysis **continues running** during cooldown — results are persisted for observability.

**Cost implication:** During sustained incidents, Bedrock is invoked every detection interval for the affected service. With a 60-second interval and 15-minute cooldown, this means up to 15 Bedrock calls per incident per service.

**Cost per sustained incident (15 min):**
```
15 calls × $0.0063/call = $0.095 per service per incident
```

### 2.4 Prompt Caching (Future Optimization)

Claude Sonnet 4.6 on Bedrock supports prompt caching with up to 90% cost savings on cached portions. The system prompt and scoring guide (~200 tokens) are identical across all calls and could be cached.

**Potential savings with prompt caching:**
- Cacheable portion: ~200 tokens (system prompt)
- Cache checkpoint minimum: 1,024 tokens (not met by system prompt alone)
- **Verdict:** Not applicable for current prompt sizes. Would become relevant if `BEDROCK_MAX_LOG_SAMPLE` is increased significantly.

---

## 3. Estimated Monthly Costs

### Usage Tiers

| Tier | Description | Anomalies/Day | Bedrock Calls/Day | Monthly Cost (Bedrock) |
|------|-------------|---------------|-------------------|------------------------|
| **Low** | Stable services, rare anomalies | 1–2 | 2–5 | $0.38 – $0.95 |
| **Medium** | Occasional incidents, weekly spikes | 5–10 | 10–25 | $1.89 – $4.73 |
| **High** | Frequent incidents, noisy services | 20–50 | 40–100 | $7.56 – $18.90 |
| **Stress** | Sustained degradation across services | 100+ | 200+ | $37.80+ |

### Calculation Basis

```
Monthly cost = (Bedrock calls/day) × 30 days × $0.0063/call
```

### Additional AWS Costs (Minimal)

| Service | Usage | Estimated Cost |
|---------|-------|---------------|
| AWS credentials (IAM) | Authentication only | Free |
| Data transfer (Bedrock API) | < 1 MB/day typical | Negligible |
| CloudWatch Logs (if enabled) | Optional | ~$0.50/GB ingested |

**Total AWS cost for MVP (local compute):** Bedrock usage only = **$0.38 – $18.90/month** depending on anomaly frequency.

---

## 4. Cost Monitoring

The SRE Watchdog logs token usage for every Bedrock call as structured JSON:

```json
{
  "event": "bedrock_inference_complete",
  "service": "payment-service",
  "model_id": "us.anthropic.claude-sonnet-4-6",
  "anomaly_score": 0.85,
  "input_tokens": 329,
  "output_tokens": 132,
  "latency_ms": 4213.67
}
```

**Monitoring recommendations:**
- Track `total_anomalies_detected` via `GET /metrics` to correlate with Bedrock costs
- Monitor `total_analysis_failed` — failed calls may still incur partial token costs
- Set AWS billing alerts for unexpected Bedrock usage spikes
- Calculate actual monthly spend: `sum(input_tokens) × $3/1M + sum(output_tokens) × $15/1M`

---

## 5. Cost Comparison: MVP vs. Production

| Aspect | MVP (Local) | Production (ECS/Fargate) |
|--------|-------------|--------------------------|
| Compute | Local machine (free) | Fargate: ~$30–50/month (0.25 vCPU, 0.5 GB) |
| Database | SQLite (free) | RDS PostgreSQL: ~$15–30/month (db.t3.micro) |
| Bedrock (Claude Sonnet 4.6) | $0.38–18.90/month | Same pricing, higher volume |
| Load Balancer | N/A | ALB: ~$16/month + data |
| **Total** | **$0.38–18.90/month** | **$61–115/month** |

---

## 6. Cost Reduction Recommendations

1. **Increase `ERROR_RATE_THRESHOLD`** — Fewer Gate 1 breaches = fewer Bedrock calls. Trade-off: may miss subtle anomalies.
2. **Increase `DETECTION_INTERVAL_SECONDS`** — Less frequent checks = fewer potential Bedrock calls. Recommended: 300s (5 min) to match sliding window.
3. **Reduce `BEDROCK_MAX_LOG_SAMPLE`** — Fewer tokens per call. Trade-off: less context for AI analysis.
4. **Implement Bedrock cooldown** — Skip inference for services already in alert cooldown. Trade-off: reduced observability during sustained incidents.
5. **Use Claude Haiku 4.5** — $1/1M input, $5/1M output (3× cheaper). Trade-off: potentially less accurate anomaly scoring and shorter summaries.
6. **Batch analysis** — Use Bedrock's batch processing (50% cost savings) for non-real-time analysis. Trade-off: increased latency.

---

## 7. Model Comparison (Cost vs. Quality)

| Model | Input $/1M | Output $/1M | Cost/Call | Quality |
|-------|-----------|-------------|-----------|---------|
| Claude Haiku 4.5 | $1.00 | $5.00 | $0.0021 | Good for simple patterns |
| **Claude Sonnet 4.6** (current) | **$3.00** | **$15.00** | **$0.0063** | **Excellent for complex anomalies** |
| Claude Opus 4 | $15.00 | $75.00 | $0.0315 | Overkill for this use case |

**Recommendation:** Claude Sonnet 4.6 provides the best cost-to-quality ratio for SRE anomaly analysis. Its detailed summaries and accurate scoring justify the 3× premium over Haiku.
