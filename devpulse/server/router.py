# devpulse/server/router.py
# URL router. No Flask, no FastAPI.
# Matches incoming request paths against registered patterns using regex.
# Supports path parameters: /api/repos/:id  →  {"id": "42"}
# Supports method constraints per route.
# 404 and 405 fallback handlers built in.

import re
import logging
from typing import Callable, Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Type alias for a handler function
Handler = Callable[["Request", "Response", Dict[str, str]], None]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

class Route:
    """
    A single registered route.
    Stores the compiled regex, allowed methods, and handler.

    Path parameter syntax:  /api/repos/:id/commits
    Converted to regex:     /api/repos/(?P<id>[^/]+)/commits
    """

    _PARAM_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")

    def __init__(self, method: str, path_pattern: str,
                 handler: Handler, name: str = ""):
        self.method  = method.upper()
        self.pattern = path_pattern
        self.handler = handler
        self.name    = name or handler.__name__
        self._regex  = self._compile(path_pattern)

    def _compile(self, pattern: str) -> re.Pattern:
        # Escape everything except our :param tokens
        parts = self._PARAM_RE.split(pattern)
        regex = ""
        i = 0
        while i < len(parts):
            if i % 2 == 0:
                # Literal segment — escape for regex
                regex += re.escape(parts[i])
            else:
                # Parameter name
                regex += f"(?P<{parts[i]}>[^/]+)"
            i += 1
        return re.compile(f"^{regex}$")

    def match(self, path: str) -> Optional[Dict[str, str]]:
        """
        Try to match a path. Returns dict of path params if matched,
        empty dict if matched with no params, None if no match.
        """
        m = self._regex.match(path)
        if m is None:
            return None
        return m.groupdict()

    def __repr__(self) -> str:
        return f"Route({self.method} {self.pattern!r} → {self.name})"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class Router:
    """
    URL router. Maintains an ordered list of Route objects.
    resolve() returns the handler and path params for a given
    method + path combination, or raises RoutingError.

    Usage:
        router = Router()

        @router.get("/api/sessions")
        def list_sessions(req, res, params): ...

        @router.get("/api/repos/:id")
        def get_repo(req, res, params):
            repo_id = params["id"]
            ...
    """

    def __init__(self):
        self._routes: List[Route] = []
        self._not_found_handler: Optional[Handler] = None
        self._method_not_allowed_handler: Optional[Handler] = None

    # ------------------------------------------------------------------
    # Registration decorators / methods
    # ------------------------------------------------------------------

    def add_route(self, method: str, path: str,
                  handler: Handler, name: str = "") -> "Router":
        route = Route(method, path, handler, name)
        self._routes.append(route)
        logger.debug(f"Route registered: {route}")
        return self

    def get(self, path: str, name: str = ""):
        def decorator(fn: Handler) -> Handler:
            self.add_route("GET", path, fn, name)
            return fn
        return decorator

    def post(self, path: str, name: str = ""):
        def decorator(fn: Handler) -> Handler:
            self.add_route("POST", path, fn, name)
            return fn
        return decorator

    def put(self, path: str, name: str = ""):
        def decorator(fn: Handler) -> Handler:
            self.add_route("PUT", path, fn, name)
            return fn
        return decorator

    def delete(self, path: str, name: str = ""):
        def decorator(fn: Handler) -> Handler:
            self.add_route("DELETE", path, fn, name)
            return fn
        return decorator

    def patch(self, path: str, name: str = ""):
        def decorator(fn: Handler) -> Handler:
            self.add_route("PATCH", path, fn, name)
            return fn
        return decorator

    def not_found(self, handler: Handler) -> Handler:
        self._not_found_handler = handler
        return handler

    def method_not_allowed(self, handler: Handler) -> Handler:
        self._method_not_allowed_handler = handler
        return handler

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, method: str,
                path: str) -> Tuple[Handler, Dict[str, str], int]:
        """
        Find the handler for a method + path.
        Returns (handler, path_params, status_code).

        status_code is 200 on match, 405 if path matches but wrong method,
        404 if path matches nothing.
        """
        method = method.upper()
        # Strip query string from path
        path = path.split("?")[0].rstrip("/") or "/"

        matched_paths = []  # Track paths that matched (for 405 detection)

        for route in self._routes:
            params = route.match(path)
            if params is None:
                continue
            matched_paths.append(route.method)
            if route.method == method:
                return route.handler, params, 200

        if matched_paths:
            # Path matched but not the method — 405
            handler = (self._method_not_allowed_handler
                       or _default_405_handler)
            return handler, {"allowed": ",".join(set(matched_paths))}, 405

        # Nothing matched — 404
        handler = self._not_found_handler or _default_404_handler
        return handler, {}, 404

    def url_for(self, name: str, **params) -> str:
        """
        Reverse lookup: build a URL from a route name and params.
        e.g. url_for("get_repo", id=42) → "/api/repos/42"
        """
        for route in self._routes:
            if route.name == name:
                path = route.pattern
                for key, val in params.items():
                    path = path.replace(f":{key}", str(val))
                return path
        raise KeyError(f"No route named {name!r}")

    def all_routes(self) -> List[Dict]:
        return [
            {"method": r.method, "pattern": r.pattern, "name": r.name}
            for r in self._routes
        ]


def _default_404_handler(req, res, params):
    res.json({"error": "Not found", "path": req.path}, status=404)


def _default_405_handler(req, res, params):
    allowed = params.get("allowed", "")
    res.set_header("Allow", allowed)
    res.json(
        {"error": "Method not allowed",
         "allowed": allowed.split(",")},
        status=405,
    )