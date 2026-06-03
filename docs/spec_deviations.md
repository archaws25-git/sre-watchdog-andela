# Spec Deviations — Original Plan vs. Actual Implementation

This document records all deviations between the original specification (`.kiro/specs/sre-watchdog/`) and the actual implementation. The original spec files are preserved unchanged as the planning baseline.

---

## 1. Bedrock Model ID

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| Model ID | `us.anthropic.claude-sonnet-4-5-20251101-v1:0` | `us.anthropic.claude-sonnet-4-6` |
| Reason | Original model was end-of-life/unavailable at runtime | Iterated through 5 model IDs to find one that worked |

The spec's `requirements.md` (Requirement 5.1) specified the default model. The actual model was changed during integration testing when the original returned `ResourceNotFoundException`. The final working model is Claude Sonnet 4.6 via the cross-region inference profile.

---

## 2. Bedrock Response Parser Enhancement

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| Parser | Expects raw JSON from Bedrock | Also handles markdown-fenced JSON (`\`\`\`json ... \`\`\``) |
| Reason | Claude Sonnet 4.6 sometimes wraps JSON responses in markdown fences despite the prompt requesting raw JSON |

The design document (Section 6.3) specified a simple `json.loads(content_text)` parser. The actual implementation first strips markdown fences before parsing.

---

## 3. Rate Limiting (Not in Original Spec)

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| Rate limiting | Not specified | `POST /logs/ingest`: 60/min, `POST /analyze`: 10/min |
| Library | N/A | `slowapi==0.1.9` |
| Module | N/A | `app/rate_limit.py` |

Rate limiting was added post-spec as a security hardening measure. Returns HTTP 429 with structured error body on exceed.

---

## 4. Service Name Validation (Not in Original Spec)

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| `LogEntryCreate.service` | `str` (any value accepted) | Pydantic `field_validator` restricting to 5 known services |
| Error on unknown service | No validation | HTTP 422 with field-level error |

The spec's schemas (Section 3.2 of design doc) defined `service: str` without validation. A validator was added to prevent data quality issues.

---

## 5. datetime.utcnow() Replacement

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| Datetime generation | `datetime.utcnow()` used throughout | `datetime.now(timezone.utc)` throughout |
| Reason | `utcnow()` is deprecated in Python 3.12+ | All occurrences replaced for forward compatibility |

The design document and task descriptions used `datetime.utcnow()`. The implementation uses the non-deprecated equivalent.

---

## 6. Circular Foreign Key Fix

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| `anomaly_windows.alert_id` FK | `ForeignKey("alert_records.id")` | `ForeignKey("alert_records.id", use_alter=True)` |
| Reason | SQLAlchemy emitted warnings during test teardown (`Can't sort tables for DROP`) | `use_alter=True` resolves the circular dependency warning |

The design document (Section 3.1) specified the FK without `use_alter`. The fix is a SQLAlchemy-specific annotation that doesn't change the database schema.

---

## 7. Credential Failure Purge on Startup (Not in Original Spec)

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| Startup cleanup | Marks stale `pending_analysis` as `analysis_failed` | Also purges `analysis_failed` records caused by expired/missing credentials |
| Patterns matched | N/A | `%credential%`, `%ExpiredToken%`, `%security token%`, `%Access Denied%` |
| Trigger | N/A | Only purges when valid credentials ARE found at startup |

This was added to prevent stale credential-related failures from cluttering the dashboard after a credential rotation and server restart.

---

## 8. OpenAPI/Swagger Customization (Not in Original Spec)

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| API documentation | Default FastAPI auto-generated | Full OpenAPI customization with tags, descriptions, examples |
| Tag descriptions | Not specified | 8 tag groups with detailed descriptions |
| Response examples | Not specified | Schema examples on 6 Pydantic models; `responses` dict on endpoints |

Available at `/docs` (Swagger UI) and `/redoc` (ReDoc).

---

## 9. Security Scanning (Not in Original Spec)

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| SAST tool | Not specified | `bandit==1.9.4` added for Python security scanning |
| Results | N/A | 0 High, 1 Medium (intentional `0.0.0.0` bind), 5 Low (non-crypto `random` usage) |

---

## 10. Type Checking (Not in Original Spec)

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| Type checker | Not specified | `mypy==1.11.2` with `mypy.ini` config |
| Strictness | N/A | Advisory (non-blocking in CI); `disallow_untyped_defs=False` for MVP |

---

## 11. CI/CD Pipeline (Not in Original Spec)

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| CI | Not specified | GitHub Actions `.github/workflows/ci.yml` |
| Matrix | N/A | Python 3.11, 3.12, 3.13 |
| Steps | N/A | flake8 → mypy (advisory) → pytest (80% coverage gate) → import check |
| Trigger | N/A | On push (all branches) and PRs to main |

---

## 12. Consolidated Tool Configuration (Not in Original Spec)

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| Config files | `pytest.ini`, `.flake8` | Also `pyproject.toml` (canonical), `mypy.ini` |
| `pyproject.toml` | Not specified | Consolidates pytest, mypy, flake8, and coverage settings |

---

## 13. Test Coverage Achieved vs. Specified

| Aspect | Original Spec (Requirement 9.9) | Actual Implementation |
|--------|----------------------------------|----------------------|
| Overall floor | 80% | **95.52%** (127 tests) |
| Critical paths (anomaly_detector, alert_service) | 95% line + 95% branch | alert_service: 100%, anomaly_detector: 85% |
| Routers | 85% | Most at 91–100% |
| Supporting modules | 70% | Most at 81–100% |

---

## 14. ANOMALY_SCORE_THRESHOLD Default

| Aspect | Original Spec | Actual Implementation |
|--------|---------------|----------------------|
| Default | `0.5` (in requirements.md) | `0.5` in `config.py` |
| deployment_instructions.md | Shows `0.70` | Incorrect — actual default is `0.5` |

**Note:** The `deployment_instructions.md` table shows `0.70` as the default for `ANOMALY_SCORE_THRESHOLD`, but the actual code default in `app/config.py` is `0.5`. This is a documentation discrepancy, not a code deviation.

---

## 15. Documentation Structure

| Aspect | Original Spec (Requirement 10.5) | Actual Implementation |
|--------|----------------------------------|----------------------|
| Location | Root directory (implied) | `docs/` subdirectory (except README.md, CONTRIBUTING.md) |
| Additional docs | Not specified | `spec_deviations.md` (this file), `.kiro/steering/` files |

---

## Summary

| Category | Count |
|----------|-------|
| Spec requirements fully met | 9 of 10 requirement groups |
| Post-spec enhancements added | 8 (rate limiting, validation, CI, mypy, bandit, OpenAPI, credential purge, `pyproject.toml`) |
| Spec deviations requiring explanation | 6 (model ID, parser, datetime, FK fix, threshold doc error, docs location) |
| Spec requirements partially met | 1 (testing — exceeds floor but anomaly_detector at 85% not 95%) |


---

## 16. Performance Optimizations (Not in Original Spec)

| Aspect | Original Spec (Design Section 5.1) | Actual Implementation |
|--------|-------------------------------------|----------------------|
| Gate 1 data loading | "Query log_entries WHERE service=S" (implied full scan) | SQL-level `func.count()` — no ORM objects loaded into memory |
| Dashboard queries | Not specified | Single GROUP BY aggregation (was 240 queries) |
| Alert service lookups | Not specified | OUTER JOIN query (was N+1 individual queries) |

These optimizations reduce query count from ~261 to ~3 per dashboard load and eliminate memory pressure during Gate 1 ticks with large datasets.
