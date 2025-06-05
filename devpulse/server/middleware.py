# devpulse/server/middleware.py
# Middleware chain for the hand-rolled HTTP server.
# Middleware functions wrap the request/response cycle.
# Each middleware receives (req, res, next) where next() calls the
# next middleware or the final handler.
# Includes: logging, CORS, auth, timing, rate limiting, body size limit.

import time
import hmac
import hashlib
import logging
import threading
from collections import defaultdict, deque
from typing import Callable, Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Type alias
MiddlewareFn = Callable[["Request", "Response", Callable], None]


# ---------------------------------------------------------------------------
# Middleware chain
# ---------------------------------------------------------------------------

class MiddlewareChain:
    """
    Executes a list of middleware functions in order, then the final handler.
    Each middleware calls next() to pass control to the next in chain.
    If a middleware does not call next(), the chain stops there
    (useful for auth rejection, rate limiting, etc).

    Usage:
        chain = MiddlewareChain([
            logging_middleware,
            cors_middleware,
            auth_middleware,
        ])
        chain.run(req, res, final_handler)
    """

    def __init__(self, middlewares: Optional[List[MiddlewareFn]] = None):
        self._middlewares: List[MiddlewareFn] = list(middlewares or [])

    def use(self, fn: MiddlewareFn) -> "MiddlewareChain":
        self._middlewares.append(fn)
        return self

    def run(self, req, res, final_handler: Callable) -> None:
        """Execute the chain and then call final_handler."""
        index = [-1]
        middlewares = self._middlewares

        def next_fn():
            index[0] += 1
            if index[0] < len(middlewares):
                try:
                    middlewares[index[0]](req, res, next_fn)
                except Exception as e:
                    logger.error(
                        f"Middleware error at index {index[0]}: {e}",
                        exc_info=True,
                    )
                    res.json(
                        {"error": "Internal server error",
                         "detail": str(e)},
                        status=500,
                    )
            else:
                try:
                    final_handler()
                except Exception as e:
                    logger.error(
                        f"Handler error: {e}", exc_info=True
                    )
                    if not res.headers_sent:
                        res.json(
                            {"error": "Internal server error",
                             "detail": str(e)},
                            status=500,
                        )

        next_fn()


# ---------------------------------------------------------------------------
# Request timing middleware
# ---------------------------------------------------------------------------

def make_timing_middleware() -> MiddlewareFn:
    """
    Records request start time and adds X-Response-Time header.
    Also logs method, path, status, and duration on completion.
    """
    def timing_middleware(req, res, next_fn: Callable) -> None:
        start = time.perf_counter()
        req.started_at = start

        next_fn()

        elapsed_ms = (time.perf_counter() - start) * 1000
        res.set_header("X-Response-Time", f"{elapsed_ms:.1f}ms")

        status = getattr(res, "_status_code", 200)
        logger.info(
            f"{req.method} {req.path} → {status} "
            f"({elapsed_ms:.1f}ms)"
        )

    return timing_middleware


# ---------------------------------------------------------------------------
# Request ID middleware
# ---------------------------------------------------------------------------

_req_counter = 0
_req_counter_lock = threading.Lock()


def make_request_id_middleware() -> MiddlewareFn:
    """
    Assigns a unique request ID to each request.
    Adds X-Request-ID to both request and response headers.
    """
    def request_id_middleware(req, res, next_fn: Callable) -> None:
        global _req_counter
        with _req_counter_lock:
            _req_counter += 1
            rid = f"req-{_req_counter:06d}"

        req.request_id = rid
        res.set_header("X-Request-ID", rid)
        next_fn()

    return request_id_middleware


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

def make_cors_middleware(allowed_origins: str = "*",
                         allowed_methods: str = "GET,POST,PUT,DELETE,PATCH,OPTIONS",
                         allowed_headers: str = "Content-Type,Authorization,X-Request-ID",
                         max_age: int = 3600) -> MiddlewareFn:
    """
    Adds CORS headers to every response.
    Handles preflight OPTIONS requests automatically.
    allowed_origins: "*" or comma-separated list of origins.
    """
    def cors_middleware(req, res, next_fn: Callable) -> None:
        origin = req.get_header("origin") or "*"

        if allowed_origins == "*":
            res.set_header("Access-Control-Allow-Origin", "*")
        else:
            permitted = [o.strip() for o in allowed_origins.split(",")]
            if origin in permitted:
                res.set_header("Access-Control-Allow-Origin", origin)
                res.set_header("Vary", "Origin")
            else:
                res.set_header("Access-Control-Allow-Origin",
                               permitted[0] if permitted else "*")

        res.set_header("Access-Control-Allow-Methods", allowed_methods)
        res.set_header("Access-Control-Allow-Headers", allowed_headers)
        res.set_header("Access-Control-Max-Age", str(max_age))

        # Preflight — respond immediately without calling handler
        if req.method == "OPTIONS":
            res.send("", status=204)
            return

        next_fn()

    return cors_middleware


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

def make_auth_middleware(conn,
                         enabled: bool = True,
                         exempt_paths: Optional[List[str]] = None) -> MiddlewareFn:
    """
    Token-based auth middleware.
    Reads Authorization: Bearer <token> header.
    Validates against api_tokens table via TokenQueries.
    Paths in exempt_paths bypass auth entirely.
    """
    from ..db.queries import TokenQueries

    _exempt = set(exempt_paths or [
        "/api/auth/token",
        "/health",
        "/",
    ])

    def auth_middleware(req, res, next_fn: Callable) -> None:
        if not enabled:
            next_fn()
            return

        # Strip query string for path matching
        path = req.path.split("?")[0]

        if path in _exempt:
            next_fn()
            return

        # Static files never require auth
        if path.startswith("/static/") or not path.startswith("/api/"):
            next_fn()
            return

        auth_header = req.get_header("authorization") or ""
        if not auth_header.lower().startswith("bearer "):
            res.json(
                {"error": "Unauthorized",
                 "detail": "Missing Authorization: Bearer <token> header"},
                status=401,
            )
            return

        token = auth_header[7:].strip()
        if not token:
            res.json({"error": "Unauthorized", "detail": "Empty token"},
                     status=401)
            return

        if not TokenQueries.validate(conn, token):
            res.json(
                {"error": "Unauthorized",
                 "detail": "Invalid or expired token"},
                status=401,
            )
            return

        req.authenticated = True
        next_fn()

    return auth_middleware


# ---------------------------------------------------------------------------
# Rate limiting middleware
# ---------------------------------------------------------------------------

class _RateLimiter:
    """
    Sliding window rate limiter.
    Tracks request timestamps per client IP in a deque.
    Thread-safe via per-IP lock.
    """

    def __init__(self, max_requests: int, window_s: int = 60):
        self.max_requests = max_requests
        self.window_s = window_s
        self._windows: Dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def is_allowed(self, client_ip: str) -> Tuple[bool, int]:
        """
        Returns (allowed, requests_remaining).
        """
        now = time.time()
        cutoff = now - self.window_s

        with self._lock:
            window = self._windows[client_ip]

            # Evict expired timestamps
            while window and window[0] < cutoff:
                window.popleft()

            if len(window) >= self.max_requests:
                return False, 0

            window.append(now)
            remaining = self.max_requests - len(window)
            return True, remaining

    def reset(self, client_ip: str) -> None:
        with self._lock:
            self._windows.pop(client_ip, None)


# Need Tuple for type hint above
from typing import Tuple


def make_rate_limit_middleware(max_per_minute: int = 120,
                               exempt_paths: Optional[List[str]] = None
                               ) -> MiddlewareFn:
    """
    Sliding-window rate limiter: max_per_minute requests per IP per minute.
    Returns 429 when limit exceeded.
    """
    limiter = _RateLimiter(max_per_minute, window_s=60)
    _exempt = set(exempt_paths or ["/health"])

    def rate_limit_middleware(req, res, next_fn: Callable) -> None:
        path = req.path.split("?")[0]
        if path in _exempt:
            next_fn()
            return

        client_ip = getattr(req, "client_ip", "unknown")
        allowed, remaining = limiter.is_allowed(client_ip)

        res.set_header("X-RateLimit-Limit", str(max_per_minute))
        res.set_header("X-RateLimit-Remaining", str(remaining))
        res.set_header("X-RateLimit-Window", "60s")

        if not allowed:
            res.json(
                {"error": "Too many requests",
                 "detail": f"Limit: {max_per_minute} requests/minute"},
                status=429,
            )
            return

        next_fn()

    return rate_limit_middleware


# ---------------------------------------------------------------------------
# Body size limit middleware
# ---------------------------------------------------------------------------

def make_body_size_middleware(max_bytes: int = 1024 * 1024) -> MiddlewareFn:
    """
    Rejects requests with Content-Length exceeding max_bytes.
    Default: 1MB.
    """
    def body_size_middleware(req, res, next_fn: Callable) -> None:
        content_length = req.get_header("content-length")
        if content_length:
            try:
                length = int(content_length)
                if length > max_bytes:
                    res.json(
                        {"error": "Request entity too large",
                         "detail": f"Max: {max_bytes} bytes"},
                        status=413,
                    )
                    return
            except ValueError:
                pass
        next_fn()

    return body_size_middleware


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

def make_security_headers_middleware() -> MiddlewareFn:
    """
    Adds basic security headers to every response.
    """
    def security_headers_middleware(req, res, next_fn: Callable) -> None:
        next_fn()
        res.set_header("X-Content-Type-Options", "nosniff")
        res.set_header("X-Frame-Options", "DENY")
        res.set_header("X-XSS-Protection", "1; mode=block")
        res.set_header(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )

    return security_headers_middleware