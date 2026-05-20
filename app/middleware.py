"""Structured JSON request logging middleware for the SRE Watchdog.

Emits one structured JSON log line per HTTP request containing a unique
request ID, HTTP method, path, response status code, and latency in
milliseconds.  The request ID is also returned as an ``X-Request-ID``
response header for downstream correlation.
"""

import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that logs every HTTP request as a structured JSON line.

    For each incoming request the middleware:
    1. Generates a unique UUID4 ``request_id``.
    2. Records the start time before forwarding the request.
    3. Computes ``latency_ms`` after the response is produced.
    4. Emits a single structured JSON log line with the request metadata.
    5. Attaches the ``X-Request-ID`` header to the outgoing response.

    Attributes:
        app: The wrapped ASGI application.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialise the middleware with the ASGI application.

        Args:
            app: The ASGI application to wrap.
        """
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process a single HTTP request and emit a structured log line.

        Args:
            request: The incoming HTTP request.
            call_next: Callable that forwards the request to the next
                middleware or route handler and returns the response.

        Returns:
            The HTTP response with an ``X-Request-ID`` header attached.
        """
        request_id = str(uuid.uuid4())
        start_time = time.perf_counter()

        response = await call_next(request)

        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)

        log_data = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
        }

        logger.info(json.dumps(log_data))

        response.headers["X-Request-ID"] = request_id

        return response
