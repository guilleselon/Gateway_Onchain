"""
In-memory rate limiter keyed by API key ID.

Tracks the timestamps of recent requests per key. No external dependencies,
no database writes. Resets when the process restarts.

Usage:
    from app.core.rate_limit import check_rate_limit

    if not check_rate_limit(api_key_id=merchant["id"]):
        return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
"""

import threading
import time
from collections import deque

# Configuration: 60 requests per 60 seconds per API key.
DEFAULT_MAX_REQUESTS = 60
DEFAULT_WINDOW_SECONDS = 60

_lock = threading.Lock()
_requests: dict[int, deque] = {}


def check_rate_limit(
    api_key_id: int,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> bool:
    """
    Return True if the request is allowed, False if the limit is exceeded.

    Uses a sliding window: keeps only timestamps within the last
    `window_seconds` and checks if the count exceeds `max_requests`.
    """
    now = time.time()
    cutoff = now - window_seconds

    with _lock:
        # Get or create the deque for this key
        bucket = _requests.setdefault(api_key_id, deque())

        # Drop timestamps older than the window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        # Check limit
        if len(bucket) >= max_requests:
            return False

        # Record this request
        bucket.append(now)
        return True
