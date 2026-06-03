"""Rate limiting configuration for the SRE Watchdog API.

Provides a shared ``Limiter`` instance used by endpoint decorators to
enforce per-client request rate limits. The limiter uses the client's
remote IP address as the rate-limiting key.

Typical usage in a router::

    from app.rate_limit import limiter

    @router.post("/endpoint")
    @limiter.limit("60/minute")
    def my_endpoint(request: Request, ...):
        ...
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
