"""API authentication middleware and request rate limiting."""

from functools import wraps
import hmac
import threading
import time
from typing import Any, Callable, Optional

from flask import Flask, jsonify, request

from sentinel_analysis.bootstrap.config import Settings


class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding window rate limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, list[float]] = {}

    def check(
        self,
        client_id: str,
        limit: int,
        window_seconds: int = 60,
    ) -> tuple[bool, int, int]:
        """Check if request is allowed. Returns (allowed, remaining, retry_after)."""
        now = time.time()
        window_start = now - window_seconds

        with self._lock:
            timestamps = self._requests.get(client_id, [])
            # Evict timestamps outside current sliding window
            timestamps = [ts for ts in timestamps if ts > window_start]

            if len(timestamps) >= limit:
                oldest = timestamps[0]
                retry_after = max(1, int(oldest + window_seconds - now + 0.999))
                self._requests[client_id] = timestamps
                return False, 0, retry_after

            timestamps.append(now)
            self._requests[client_id] = timestamps
            remaining = limit - len(timestamps)
            return True, remaining, 0

    def reset(self, client_id: Optional[str] = None) -> None:
        """Reset rate limit counts (used in tests)."""
        with self._lock:
            if client_id is None:
                self._requests.clear()
            else:
                self._requests.pop(client_id, None)


rate_limiter = SlidingWindowRateLimiter()


def get_client_ip(trusted_proxies: tuple[str, ...] = ()) -> str:
    """Safely determine the client IP address.

    X-Forwarded-For is only consulted if the immediate peer (request.remote_addr)
    is in the configured trusted_proxies list. Otherwise, request.remote_addr
    is returned to prevent IP spoofing and rate-limiting bypass.
    """
    remote_addr = (request.remote_addr or "127.0.0.1").strip()
    if trusted_proxies and remote_addr in trusted_proxies:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded and forwarded.strip():
            client_ip = forwarded.split(",")[0].strip()
            if client_ip:
                return client_ip
    return remote_addr


def get_api_key_from_request() -> Optional[str]:
    """Extract API key from X-API-Key or Authorization header."""
    # 1. Check X-API-Key header
    key = request.headers.get("X-API-Key")
    if key and key.strip():
        return key.strip()

    # 2. Check Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.strip():
        parts = auth_header.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()

    return None


def validate_api_key(provided_key: Optional[str], expected_key: Optional[str]) -> bool:
    """Timing-attack safe comparison of API keys."""
    if not expected_key:
        return True
    if not provided_key:
        return False
    return hmac.compare_digest(provided_key.encode("utf-8"), expected_key.encode("utf-8"))


def public_endpoint(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to mark an API endpoint exempt from API key and global rate limiting."""
    fn._sentinel_public = True  # type: ignore[attr-defined]
    return fn


def require_api_key(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Explicit route decorator enforcing API key authentication."""
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        from sentinel_analysis.interfaces.web.dependencies import container
        settings: Settings = container().settings
        if settings.api_key:
            key = get_api_key_from_request()
            if not validate_api_key(key, settings.api_key):
                return jsonify({
                    "status": "error",
                    "error": "Unauthorized",
                    "message": "Invalid or missing API key. Provide via X-API-Key header or Authorization: Bearer <token>.",
                }), 401
        return fn(*args, **kwargs)
    return wrapper


def setup_security(app: Flask, settings: Settings) -> None:
    """Attach rate limiting and API key authentication interceptors to Flask app."""

    @app.before_request
    def check_security() -> Any:
        # Preflight requests are exempt
        if request.method == "OPTIONS":
            return None

        path = request.path

        # Unrestricted infrastructure endpoints
        if path in {"/healthz", "/readyz", "/livez"} or path.startswith("/static"):
            return None

        # Check view-level exemption
        endpoint = request.endpoint
        if endpoint and endpoint in app.view_functions:
            view_fn = app.view_functions[endpoint]
            if getattr(view_fn, "_sentinel_public", False):
                return None

        # Only protect API endpoints (/api/*)
        if not path.startswith("/api/"):
            return None

        trusted_proxies = getattr(settings, "trusted_proxies", ())
        client_id = get_client_ip(trusted_proxies)

        # 1. Global rate limiting for API
        if settings.rate_limiting_enabled:
            allowed, remaining, retry_after = rate_limiter.check(
                client_id=client_id,
                limit=settings.rate_limit_per_minute,
                window_seconds=60,
            )
            if not allowed:
                response = jsonify({
                    "status": "error",
                    "error": "Too Many Requests",
                    "message": f"Rate limit exceeded. Try again in {retry_after} seconds.",
                    "retry_after": retry_after,
                })
                response.status_code = 429
                response.headers["Retry-After"] = str(retry_after)
                response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
                response.headers["X-RateLimit-Remaining"] = "0"
                response.headers["X-RateLimit-Reset"] = str(int(time.time() + retry_after))
                return response

        # 2. API key authentication
        if settings.api_key:
            key = get_api_key_from_request()
            if not validate_api_key(key, settings.api_key):
                return jsonify({
                    "status": "error",
                    "error": "Unauthorized",
                    "message": "Invalid or missing API key. Provide via X-API-Key header or Authorization: Bearer <token>.",
                }), 401

        return None
