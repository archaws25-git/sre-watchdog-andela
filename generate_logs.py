"""Synthetic log generator for the SRE Watchdog platform.

Generates approximately 10,000 structured log entries distributed across 5
named services over a simulated 24-hour period. Seeds exactly 3 deliberate
anomaly windows with distinct failure profiles to validate the anomaly
detection pipeline.

Anomaly Windows:
    1. payment-service — 6 min sharp spike (payment processor failure)
    2. auth-service — 12 min sustained degradation (token validation issues)
    3. api-gateway — 18 min escalating cascade (upstream dependency failure)

Usage:
    python generate_logs.py
    python generate_logs.py --total-entries 10000 --batch-size 500
    python generate_logs.py --ingest-url http://localhost:8000/logs/ingest
"""

import argparse
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERVICES = [
    "api-gateway",
    "auth-service",
    "payment-service",
    "notification-service",
    "database-proxy",
]

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# Normal distribution: ~70% INFO, 15% WARNING, 10% ERROR, 3% CRITICAL, 2% DEBUG
NORMAL_WEIGHTS = [0.02, 0.70, 0.15, 0.10, 0.03]

# Anomaly distribution: ≥40% ERROR+CRITICAL, reduced INFO
ANOMALY_WEIGHTS = [0.02, 0.28, 0.15, 0.30, 0.25]

# Message templates per service for realistic log generation
MESSAGE_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "api-gateway": {
        "DEBUG": [
            "Route resolution completed for /api/v2/users in 2ms",
            "Request headers validated successfully",
            "Load balancer health check passed",
        ],
        "INFO": [
            "Request routed to upstream service successfully",
            "Rate limit check passed for client 10.0.0.42",
            "TLS handshake completed in 15ms",
            "Connection pool stats: active=12, idle=38, total=50",
            "Upstream response received in 45ms",
        ],
        "WARNING": [
            "Upstream response time exceeded 2000ms threshold",
            "Rate limit approaching for client 192.168.1.100 (85% of quota)",
            "Connection pool utilization at 80%",
            "Retry attempt 1/3 for upstream service",
        ],
        "ERROR": [
            "Upstream service returned HTTP 503 Service Unavailable",
            "Connection timeout after 5000ms to auth-service",
            "Circuit breaker OPEN for payment-service",
            "Request dropped: connection pool exhausted",
            "Gateway timeout: upstream did not respond within 10s",
        ],
        "CRITICAL": [
            "All upstream services unreachable — cascade failure detected",
            "Connection pool completely exhausted, rejecting all requests",
            "TLS certificate validation failed for upstream endpoint",
            "Memory usage critical: 95% — OOM kill imminent",
        ],
    },
    "auth-service": {
        "DEBUG": [
            "Token signature verification started",
            "Cache hit for session lookup: user_id=8821",
            "RBAC policy evaluation completed in 1ms",
        ],
        "INFO": [
            "User authentication successful: user_id=4421",
            "Token refreshed successfully for session abc123",
            "OAuth2 callback processed for provider=google",
            "Session created: ttl=3600s",
            "Password hash verification completed",
        ],
        "WARNING": [
            "Token expiration within 60 seconds for session xyz789",
            "Failed login attempt 3/5 for user admin@example.com",
            "Session store latency elevated: 450ms",
            "Deprecated auth header format detected",
        ],
        "ERROR": [
            "Token validation failed: signature mismatch",
            "Session store connection refused on port 6379",
            "OAuth2 provider returned invalid grant response",
            "LDAP bind failed: connection timeout after 3000ms",
            "JWT decode error: malformed payload segment",
        ],
        "CRITICAL": [
            "Authentication service unable to validate any tokens",
            "Session store completely unreachable — all auth failing",
            "Private key file corrupted or missing",
            "Brute force attack detected: 500 failed attempts in 60s",
        ],
    },
    "payment-service": {
        "DEBUG": [
            "Payment intent created: amount=49.99 currency=USD",
            "Idempotency key validated: key=pay_abc123",
            "Fraud score calculation started for transaction tx_001",
        ],
        "INFO": [
            "Payment processed successfully: tx_id=tx_7891 amount=$129.99",
            "Refund initiated: tx_id=tx_4456 amount=$25.00",
            "Payment webhook delivered to merchant endpoint",
            "Currency conversion applied: USD->EUR rate=0.92",
            "Subscription renewal processed for customer cust_112",
        ],
        "WARNING": [
            "Payment processor response time elevated: 3200ms",
            "Retry queued for failed webhook delivery to merchant",
            "Fraud score elevated (0.72) for transaction tx_889",
            "Payment processor rate limit warning: 80% of quota used",
        ],
        "ERROR": [
            "Payment processor returned decline: insufficient_funds",
            "Connection timeout to payment gateway after 5000ms",
            "Webhook delivery failed: merchant endpoint returned 500",
            "Transaction rollback triggered: consistency check failed",
            "Payment processor API returned HTTP 502 Bad Gateway",
        ],
        "CRITICAL": [
            "Payment processor completely unreachable — all payments failing",
            "Double-charge detected: tx_id=tx_991 requires immediate review",
            "Payment database write failed: disk full",
            "PCI compliance violation: unencrypted card data detected in logs",
        ],
    },
    "notification-service": {
        "DEBUG": [
            "Email template rendered: template=welcome_email",
            "Push notification payload constructed: 245 bytes",
            "SMS provider rate limit check passed",
        ],
        "INFO": [
            "Email sent successfully to user@example.com",
            "Push notification delivered to device_id=dev_abc",
            "SMS delivered: +1-555-0123 message_id=sms_789",
            "Notification batch processed: 50 emails, 12 push, 3 SMS",
            "Unsubscribe processed for user_id=2234",
        ],
        "WARNING": [
            "Email delivery delayed: SMTP queue depth at 500",
            "Push notification provider latency elevated: 2100ms",
            "SMS delivery rate approaching provider limit",
            "Bounce rate elevated for domain example.com (12%)",
        ],
        "ERROR": [
            "Email delivery failed: SMTP connection refused",
            "Push notification rejected: invalid device token",
            "SMS provider returned error: insufficient credits",
            "Template rendering failed: missing variable 'user_name'",
            "Notification queue consumer crashed: restarting",
        ],
        "CRITICAL": [
            "All notification channels unavailable",
            "Email provider account suspended — no emails sending",
            "Notification queue overflow: 100,000 messages pending",
            "Critical alert notification failed to deliver to on-call",
        ],
    },
    "database-proxy": {
        "DEBUG": [
            "Query plan optimized: using index idx_users_email",
            "Connection returned to pool: active=5, idle=15",
            "Read replica selected for query: replica-2",
        ],
        "INFO": [
            "Query executed successfully in 12ms: SELECT users",
            "Connection pool initialized: max_size=20",
            "Database migration applied: version 2024.01.15",
            "Slow query logged: 850ms (threshold: 1000ms)",
            "Read replica sync confirmed: lag=50ms",
        ],
        "WARNING": [
            "Connection pool utilization at 85% (17/20)",
            "Query execution time approaching threshold: 920ms",
            "Read replica lag elevated: 2500ms",
            "Lock wait timeout approaching for table orders",
        ],
        "ERROR": [
            "Connection pool exhausted: all 20 connections in use",
            "Query timeout after 30000ms: SELECT * FROM log_entries",
            "Database connection lost: attempting reconnect",
            "Deadlock detected on table transactions",
            "Write to primary failed: disk I/O error",
        ],
        "CRITICAL": [
            "Database completely unreachable — all queries failing",
            "Connection pool corruption detected: emergency restart",
            "Primary database disk full — writes halted",
            "Data corruption detected in table payments: checksum mismatch",
        ],
    },
}

# Anomaly window definitions
ANOMALY_WINDOW_CONFIGS: list[tuple[str, int, str]] = [
    ("payment-service", 6, "Sharp spike — sudden payment processor failure"),
    ("auth-service", 12, "Sustained degradation — token validation issues"),
    ("api-gateway", 18, "Escalating cascade — upstream dependency failure"),
]


# ---------------------------------------------------------------------------
# Core Generation Logic
# ---------------------------------------------------------------------------


@dataclass
class _AnomalyWindowSlot:
    """Internal representation of a placed anomaly window on the timeline."""

    service: str
    start: datetime
    end: datetime
    duration_minutes: int
    description: str


def _pick_level(weights: list[float]) -> str:
    """Select a log level based on the given probability weights.

    Args:
        weights: Probability weights for [DEBUG, INFO, WARNING, ERROR, CRITICAL].

    Returns:
        A log level string.
    """
    return random.choices(LOG_LEVELS, weights=weights, k=1)[0]


def _pick_message(service: str, level: str) -> str:
    """Select a realistic log message for the given service and level.

    Args:
        service: The service name.
        level: The log level.

    Returns:
        A realistic log message string.
    """
    templates = MESSAGE_TEMPLATES[service][level]
    return random.choice(templates)


def _build_timeline(
    total_entries: int,
    service_count: int,
    anomaly_window_count: int,
) -> list[dict[str, Any]]:
    """Build a 24-hour timeline of log entries with seeded anomaly windows.

    Distributes entries across services, places anomaly windows at distinct
    points in the timeline, and applies appropriate log level distributions
    inside and outside anomaly windows.

    Args:
        total_entries: Total number of log entries to generate.
        service_count: Number of services to use (up to 5).
        anomaly_window_count: Number of anomaly windows to seed (up to 3).

    Returns:
        A list of log entry dictionaries sorted by timestamp.
    """
    services = SERVICES[:service_count]
    now = datetime.now(timezone.utc)
    timeline_start = now - timedelta(hours=24)

    # Place anomaly windows at distinct points in the 24-hour timeline
    # Spread them across hours 4, 10, and 18 to avoid overlap
    anomaly_start_hours = [4, 10, 18]
    active_windows: list[_AnomalyWindowSlot] = []
    for i in range(min(anomaly_window_count, len(ANOMALY_WINDOW_CONFIGS))):
        service_name, duration_minutes, description = ANOMALY_WINDOW_CONFIGS[i]
        window_start = timeline_start + timedelta(hours=anomaly_start_hours[i])
        window_end = window_start + timedelta(minutes=duration_minutes)
        active_windows.append(_AnomalyWindowSlot(
            service=service_name,
            start=window_start,
            end=window_end,
            duration_minutes=duration_minutes,
            description=description,
        ))

    # Distribute entries across the timeline
    entries: list[dict[str, Any]] = []
    entries_per_service = total_entries // len(services)
    remainder = total_entries - (entries_per_service * len(services))

    for service_idx, service in enumerate(services):
        # Give remainder entries to the first service
        count = entries_per_service + (remainder if service_idx == 0 else 0)

        for _ in range(count):
            # Generate a random timestamp within the 24-hour window
            offset_seconds = random.uniform(0, 24 * 3600)
            timestamp = timeline_start + timedelta(seconds=offset_seconds)

            # Check if this entry falls within an anomaly window for this service
            is_in_anomaly = False
            for window in active_windows:
                if (
                    window.service == service
                    and window.start <= timestamp <= window.end
                ):
                    is_in_anomaly = True
                    break

            # Select level based on distribution
            if is_in_anomaly:
                level = _pick_level(ANOMALY_WEIGHTS)
            else:
                level = _pick_level(NORMAL_WEIGHTS)

            message = _pick_message(service, level)

            entries.append({
                "timestamp": timestamp.isoformat(),
                "service": service,
                "level": level,
                "message": message,
            })

    # Ensure anomaly windows have sufficient ERROR+CRITICAL density
    # For each anomaly window, check and boost if needed
    for window in active_windows:
        window_entries = [
            e for e in entries
            if e["service"] == window.service
            and window.start <= datetime.fromisoformat(e["timestamp"]) <= window.end
        ]

        if not window_entries:
            # If no entries fell in the window naturally, inject some
            inject_count = max(20, int(total_entries * 0.01))
            for _ in range(inject_count):
                offset = random.uniform(0, window.duration_minutes * 60)
                timestamp = window.start + timedelta(seconds=offset)
                level = _pick_level(ANOMALY_WEIGHTS)
                message = _pick_message(window.service, level)
                entries.append({
                    "timestamp": timestamp.isoformat(),
                    "service": window.service,
                    "level": level,
                    "message": message,
                })
        else:
            # Check ERROR+CRITICAL ratio
            error_critical_count = sum(
                1 for e in window_entries if e["level"] in ("ERROR", "CRITICAL")
            )
            total_window = len(window_entries)
            current_ratio = error_critical_count / total_window if total_window > 0 else 0

            # If below 40%, convert some entries to ERROR/CRITICAL
            if current_ratio < 0.40:
                needed = int(0.45 * total_window) - error_critical_count
                non_error_entries = [
                    e for e in window_entries
                    if e["level"] not in ("ERROR", "CRITICAL")
                ]
                random.shuffle(non_error_entries)
                for entry in non_error_entries[:needed]:
                    new_level = random.choices(
                        ["ERROR", "CRITICAL"], weights=[0.6, 0.4], k=1
                    )[0]
                    entry["level"] = new_level
                    entry["message"] = _pick_message(window.service, new_level)

    # Sort all entries by timestamp
    entries.sort(key=lambda e: e["timestamp"])

    return entries


def generate_logs(
    total_entries: int = 10000,
    service_count: int = 5,
    anomaly_window_count: int = 3,
    ingest_url: str = "http://localhost:8000/logs/ingest",
    batch_size: int = 500,
) -> None:
    """Generate synthetic log entries and POST them to the ingest endpoint.

    Builds a 24-hour timeline with seeded anomaly windows, chunks entries
    into batches, and sends each batch to the specified ingest URL.

    Args:
        total_entries: Total number of log entries to generate.
        service_count: Number of services to distribute entries across.
        anomaly_window_count: Number of anomaly windows to seed.
        ingest_url: URL of the POST /logs/ingest endpoint.
        batch_size: Number of entries per HTTP request batch.
    """
    print(f"Generating {total_entries} log entries across {service_count} services...")
    print(f"Seeding {anomaly_window_count} anomaly windows")
    print(f"Target: {ingest_url}")
    print(f"Batch size: {batch_size}")
    print("-" * 60)

    # Build the timeline
    entries = _build_timeline(total_entries, service_count, anomaly_window_count)
    print(f"Generated {len(entries)} entries, sending in batches...")
    print("-" * 60)

    # Chunk into batches and POST
    total_batches = (len(entries) + batch_size - 1) // batch_size

    with httpx.Client(timeout=30.0) as client:
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(entries))
            batch = entries[start_idx:end_idx]

            payload = {"entries": batch}

            try:
                response = client.post(ingest_url, json=payload)
                print(
                    f"Batch {batch_num + 1}/{total_batches}: "
                    f"HTTP {response.status_code} "
                    f"({len(batch)} entries)"
                )
            except httpx.HTTPError as exc:
                print(
                    f"Batch {batch_num + 1}/{total_batches}: "
                    f"FAILED — {exc}"
                )

    print("-" * 60)
    print("Log generation complete.")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and run the synthetic log generator."""
    parser = argparse.ArgumentParser(
        description="SRE Watchdog — Synthetic Log Generator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--total-entries",
        type=int,
        default=10000,
        help="Total number of log entries to generate",
    )
    parser.add_argument(
        "--service-count",
        type=int,
        default=5,
        help="Number of services to distribute entries across",
    )
    parser.add_argument(
        "--anomaly-window-count",
        type=int,
        default=3,
        help="Number of anomaly windows to seed",
    )
    parser.add_argument(
        "--ingest-url",
        type=str,
        default="http://localhost:8000/logs/ingest",
        help="URL of the POST /logs/ingest endpoint",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of entries per HTTP request batch",
    )

    args = parser.parse_args()

    generate_logs(
        total_entries=args.total_entries,
        service_count=args.service_count,
        anomaly_window_count=args.anomaly_window_count,
        ingest_url=args.ingest_url,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
