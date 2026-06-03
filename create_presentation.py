"""Generate the SRE Watchdog MVP presentation as a PowerPoint file."""

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE


def add_title_slide(prs, title, subtitle):
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    slide.placeholders[1].text = subtitle


def add_content_slide(prs, title, bullets):
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = title
    body = slide.placeholders[1]
    tf = body.text_frame
    tf.clear()
    for i, bullet in enumerate(bullets):
        if i == 0:
            tf.paragraphs[0].text = bullet
        else:
            p = tf.add_paragraph()
            p.text = bullet
        tf.paragraphs[i].level = 0
        tf.paragraphs[i].font.size = Pt(18)


def add_screenshot_slide(prs, title, caption):
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Blank layout
    # Title
    txBox = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(9), Inches(0.8))
    tf = txBox.text_frame
    tf.text = title
    tf.paragraphs[0].font.size = Pt(28)
    tf.paragraphs[0].font.bold = True
    # Placeholder box for screenshot
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1), Inches(1.3), Inches(8), Inches(4.5)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(0x16, 0x21, 0x3E)
    shape.line.color.rgb = RGBColor(0x36, 0xA2, 0xEB)
    # Text inside placeholder
    tf2 = shape.text_frame
    tf2.word_wrap = True
    tf2.paragraphs[0].alignment = PP_ALIGN.CENTER
    tf2.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf2.paragraphs[0].text = "[INSERT SCREENSHOT HERE]"
    tf2.paragraphs[0].font.size = Pt(20)
    tf2.paragraphs[0].font.color.rgb = RGBColor(0x36, 0xA2, 0xEB)
    # Caption
    txBox2 = slide.shapes.add_textbox(Inches(1), Inches(6.0), Inches(8), Inches(0.5))
    tf3 = txBox2.text_frame
    tf3.text = caption
    tf3.paragraphs[0].font.size = Pt(14)
    tf3.paragraphs[0].font.italic = True
    tf3.paragraphs[0].font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
    tf3.paragraphs[0].alignment = PP_ALIGN.CENTER


def main():
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)

    # Slide 1: Title
    add_title_slide(
        prs,
        "SRE Watchdog",
        "AI-Powered Intelligent Observability & Event Watchdog\n"
        "MVP Presentation - June 2026"
    )

    # Slide 2: Problem Statement
    add_content_slide(prs, "The Problem", [
        "SRE teams drown in log noise - thousands of entries per minute",
        "Manual threshold monitoring misses subtle degradation patterns",
        "Alert fatigue from duplicate/noisy notifications",
        "No intelligent context in alerts - just raw numbers",
        "Need: AI-powered anomaly detection with actionable summaries",
    ])

    # Slide 3: Solution Overview
    add_content_slide(prs, "The Solution: SRE Watchdog", [
        "Python 3.13 FastAPI application with AI-powered log analysis",
        "Two-gate detection: statistical pre-filter + AWS Bedrock AI",
        "Automatic webhook alerts with severity classification",
        "Real-time dashboard with Chart.js visualizations",
        "Rate limiting, input validation, and security scanning",
        "95.5% test coverage with CI/CD pipeline",
        "Dockerized for portable deployment",
    ])

    # Slide 4: Architecture
    add_content_slide(prs, "Architecture Overview", [
        "Gate 1: APScheduler tick - SQL-level COUNT for error rate",
        "Gate 2: FastAPI BackgroundTask - Bedrock Converse API (Claude Sonnet 4.6)",
        "Alert Service: Severity mapping + webhook dispatch with retry",
        "Cooldown: Prevents alert fatigue (15-min suppression window)",
        "Rate Limiting: 60/min ingest, 10/min analyze (slowapi)",
        "Dashboard: Jinja2 SSR + Chart.js + 60s auto-refresh",
        "Storage: SQLite WAL mode for concurrent read/write",
    ])

    # Slide 5: Tech Stack
    add_content_slide(prs, "Tech Stack", [
        "Language: Python 3.13 (Docker) / 3.11+ (local)",
        "Framework: FastAPI + Uvicorn + slowapi (rate limiting)",
        "AI: AWS Bedrock (Claude Sonnet 4.6 via Converse API)",
        "Database: SQLite with WAL mode (SQLAlchemy ORM)",
        "Scheduling: APScheduler (BackgroundScheduler)",
        "Dashboard: Jinja2 + Chart.js (single aggregation query)",
        "Quality: pytest (95.5%), flake8, mypy, bandit, GitHub Actions CI",
    ])

    # Slide 6: Development Timeline
   # add_content_slide(prs, "Development Timeline (~14.5 hours total)", [
   #     "Session 1 (8.5h): Requirements - Design - Implementation - Debugging",
   #     "  Phase 1: Spec-driven requirements & design (Turns 1-12)",
   #     "  Phase 2: Full implementation via task execution (37 min)",
   #     "  Phase 3: Bedrock integration & model troubleshooting (5h)",
   #     "Session 2 (6h): Quality - Security - Performance - Docker",
   #     "  Phase 4: Full test suite (127 tests, 95.5% coverage)",
   #     "  Phase 5: Rate limiting, validation, CI/CD, Docker, perf fixes",
   # ])

    # Slide 7: Key Features
    add_content_slide(prs, "Key Features Delivered", [
        "10,000 synthetic logs across 5 services (24-hour simulation)",
        "3 seeded anomaly windows: sharp spike, sustained degradation, cascade",
        "AI-generated anomaly summaries with severity scoring (0.0-1.0)",
        "Webhook alerts with 4-band severity (LOW/MEDIUM/HIGH/CRITICAL)",
        "Cooldown suppression + credential failure auto-purge on restart",
        "On-demand analysis via POST /analyze + async job polling",
        "11 API endpoints with OpenAPI/Swagger documentation",
    ])

    # Slide 8: Dashboard Screenshot - Overview
    add_screenshot_slide(
        prs,
        "Dashboard: Metrics & Error Rate Chart",
        "Metrics bar showing logs ingested, anomalies, alerts, and failures. "
        "Chart.js line chart with error rate per service over 24 hours."
    )

    # Slide 9: Dashboard Screenshot - Anomalies
    add_screenshot_slide(
        prs,
        "Dashboard: Anomaly Detection Results",
        "Recent anomalies table showing service, time window, AI score, "
        "lifecycle status, severity badge, and AI-generated summary."
    )

    # Slide 10: Dashboard Screenshot - Alerts
    add_screenshot_slide(
        prs,
        "Dashboard: Alert Dispatch Log",
        "Recent alerts table showing timestamp, service, severity, "
        "anomaly ID, and dispatch status (sent/suppressed/failed)."
    )

    # Slide 11: Dashboard Screenshot - Swagger UI
    add_screenshot_slide(
        prs,
        "OpenAPI/Swagger Documentation",
        "Interactive API docs at /docs with tag groups, request examples, "
        "response schemas, and error code documentation."
    )

    # Slide 12: Detection Pipeline
    add_content_slide(prs, "Detection Pipeline in Action", [
        "1. APScheduler tick fires every 60 seconds",
        "2. Gate 1: SQL COUNT per service (no ORM objects loaded)",
        "3. If error_rate > 10%: create AnomalyWindow (pending_analysis)",
        "4. Gate 2: BackgroundTask invokes Bedrock with log context",
        "5. Bedrock returns anomaly_score (0.0-1.0) + AI summary",
        "6. If score >= 0.5 and no cooldown: dispatch webhook alert",
        "7. Full lifecycle persisted for audit trail",
    ])

    # Slide 13: AI Integration
    add_content_slide(prs, "AWS Bedrock AI Integration", [
        "Model: Claude Sonnet 4.6 (us.anthropic.claude-sonnet-4-6)",
        "Prompt: Service context + error rate + log messages (capped at 50)",
        "Response: JSON with anomaly_score + plain-text summary",
        "Parser: Handles both raw JSON and markdown-fenced responses",
        "Retry: Exponential backoff (1s, 2s, 4s) for transient errors",
        "Health caching: /health reports last-known Bedrock status",
        "Cost: ~$0.006 per inference call ($1-18/month typical)",
    ])

    # Slide 14: Testing & Quality
    add_content_slide(prs, "Testing & Quality Assurance", [
        "127 tests (71 unit + 54 integration + 1 property-based + 1 live Bedrock)",
        "95.5% code coverage with branch coverage enabled",
        "16 modules at 100% coverage (all critical paths)",
        "Property-based test: Hypothesis round-trip validation",
        "Security: bandit SAST scanning (0 High issues)",
        "Linting: flake8 (zero errors) + mypy (type checking)",
        "CI/CD: GitHub Actions matrix (Python 3.11/3.12/3.13)",
    ])

    # Slide 15: Security & Performance
    add_content_slide(prs, "Security & Performance", [
        "Rate limiting: 60/min ingest, 10/min analyze (HTTP 429)",
        "Input validation: service name restricted to 5 known services",
        "SQL injection: parameterized queries via SQLAlchemy ORM",
        "Performance: Single GROUP BY query for dashboard (was 240)",
        "Performance: SQL COUNT for Gate 1 (no ORM objects in memory)",
        "Performance: OUTER JOIN for alerts (was N+1 queries)",
        "Startup cleanup: auto-purge credential-failure records",
    ])

    # Slide 16: Challenges & Solutions
    add_content_slide(prs, "Challenges & Solutions", [
        "Model ID iteration: 5 attempts to find working Bedrock model",
        "Response parsing: Claude wraps JSON in markdown fences",
        "Circular FK: use_alter=True resolves SQLAlchemy DROP warnings",
        "BackgroundTask testing: Option 1 (direct) + Option 2 (TestClient exit)",
        "Credential expiry: auto-purge + structured error in dashboard",
        "Dashboard N+1: OUTER JOIN for service names in alert list",
    ])

    # Slide 17: Docker & Deployment
    add_content_slide(prs, "Docker & Deployment", [
        "Dockerfile: python:3.13-slim with health check",
        "Single command: docker run -p 8000:8000 sre-watchdog:latest",
        "AWS credentials via environment variables",
        "Dashboard at http://localhost:8000/dashboard",
        "Swagger UI at http://localhost:8000/docs",
        "Production path: App Runner or ECS/Fargate + RDS PostgreSQL",
    ])

    # Slide 18: What's Next
    add_content_slide(prs, "Production Roadmap", [
        "Authentication: OAuth2/OIDC for dashboard access",
        "Deployment: AWS App Runner or ECS/Fargate",
        "Database: Migrate to RDS PostgreSQL (config-only change)",
        "Semantic analysis: S3 Vectors for log embeddings",
        "Cursor-based pagination for high-volume log stores",
        "OpenTelemetry: Distributed tracing integration",
        "Prometheus: /metrics in standard exposition format",
    ])

    # Slide 19: Project Statistics
    add_content_slide(prs, "Project Statistics", [
        "Total time: ~14.5 hours (within 16-hour window)",
        "Code generation (all tasks): 37 minutes",
        "Source files: 25 app modules + 16 test files",
        "Test coverage: 95.5% (127 tests passing)",
        "Documentation: 10 markdown files + spec deviations",
        "API: 11 endpoints with OpenAPI examples",
        "Security: 0 High bandit findings, rate-limited",
    ])

    # Slide 20: Demo
    add_content_slide(prs, "Live Demo", [
        "1. docker run -p 8000:8000 sre-watchdog:latest",
        "2. python generate_logs.py (10K logs, 3 anomaly windows)",
        "3. Open http://localhost:8000/dashboard",
        "4. Click 'Run Analysis' - watch Bedrock AI in action",
        "5. Observe: anomaly scores, AI summaries, alert dispatch",
        "6. Check /docs (Swagger), /health, /metrics",
    ])

    # Slide 21: Thank You
    add_title_slide(
        prs,
        "Thank You",
        "SRE Watchdog - AI-Powered Observability\n"
        "GitHub: github.com/archaws25-git/sre-watchdog-andela\n"
        "Questions?"
    )

    output_path = "SRE_Watchdog_MVP_Presentation.pptx"
    prs.save(output_path)
    print(f"Presentation saved to: {output_path}")


if __name__ == "__main__":
    main()
