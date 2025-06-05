# devpulse/server/app.py
# Hand-rolled HTTP server. No Flask, no Django, no FastAPI.
# Extends Python's stdlib http.server.BaseHTTPRequestHandler.
# Wires together Router + MiddlewareChain + static file serving.
# Supports graceful shutdown, port conflict detection, gzip responses.

import gzip
import json
import logging
import os
import re
import signal
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple
from aiohttp import web

from .router import Router
from .middleware import (
    MiddlewareChain,
    make_timing_middleware,
    make_request_id_middleware,
    make_cors_middleware,
    make_auth_middleware,
    make_rate_limit_middleware,
    make_body_size_middleware,
    make_security_headers_middleware,
)

logger = logging.getLogger(__name__)

# MIME type map — hand-written, no mimetypes module dependency
MIME_TYPES: Dict[str, str] = {
    ".html":  "text/html; charset=utf-8",
    ".htm":   "text/html; charset=utf-8",
    ".css":   "text/css; charset=utf-8",
    ".js":    "application/javascript; charset=utf-8",
    ".mjs":   "application/javascript; charset=utf-8",
    ".json":  "application/json; charset=utf-8",
    ".svg":   "image/svg+xml",
    ".png":   "image/png",
    ".jpg":   "image/jpeg",
    ".jpeg":  "image/jpeg",
    ".gif":   "image/gif",
    ".ico":   "image/x-icon",
    ".woff":  "font/woff",
    ".woff2": "font/woff2",
    ".ttf":   "font/ttf",
    ".txt":   "text/plain; charset=utf-8",
    ".md":    "text/markdown; charset=utf-8",
    ".csv":   "text/csv; charset=utf-8",
    ".xml":   "application/xml",
    ".pdf":   "application/pdf",
    ".zip":   "application/zip",
    ".map":   "application/json",
}

COMPRESSIBLE_TYPES = {
    "text/html", "text/css", "application/javascript",
    "application/json", "text/plain", "text/markdown",
    "image/svg+xml", "application/xml",
}


# ---------------------------------------------------------------------------
# Request object — wraps raw handler data
# ---------------------------------------------------------------------------

class Request:
    """
    Wraps a BaseHTTPRequestHandler instance to provide a clean API
    for reading method, path, headers, query params, and body.
    """

    def __init__(self, handler: BaseHTTPRequestHandler):
        self._handler = handler
        self.method: str = handler.command
        raw_path = handler.path or "/"
        parsed = urllib.parse.urlparse(raw_path)
        self.path: str = parsed.path
        self.query_string: str = parsed.query
        self._query_params: Optional[Dict[str, str]] = None
        self._body: Optional[bytes] = None
        self._json: Optional[Any] = None
        self.client_ip: str = handler.client_address[0]
        self.request_id: str = ""
        self.authenticated: bool = False
        self.started_at: float = 0.0
        self.path_params: Dict[str, str] = {}

    def get_header(self, name: str) -> Optional[str]:
        return self._handler.headers.get(name)

    @property
    def query_params(self) -> Dict[str, str]:
        if self._query_params is None:
            parsed = urllib.parse.parse_qs(
                self.query_string, keep_blank_values=False
            )
            # parse_qs returns lists — take first value
            self._query_params = {
                k: v[0] for k, v in parsed.items() if v
            }
        return self._query_params

    def query(self, key: str, default: str = "") -> str:
        return self.query_params.get(key, default)

    def query_int(self, key: str, default: int = 0) -> int:
        val = self.query_params.get(key, "")
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def body_bytes(self) -> bytes:
        if self._body is None:
            length = int(self.get_header("content-length") or 0)
            if length > 0:
                try:
                    self._body = self._handler.rfile.read(length)
                except Exception:
                    self._body = b""
            else:
                self._body = b""
        return self._body

    def body_json(self) -> Any:
        if self._json is None:
            raw = self.body_bytes()
            if not raw:
                return None
            try:
                self._json = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json = None
        return self._json

    def body_text(self) -> str:
        raw = self.body_bytes()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    def accepts_gzip(self) -> bool:
        enc = self.get_header("accept-encoding") or ""
        return "gzip" in enc.lower()

    def __repr__(self) -> str:
        return f"Request({self.method} {self.path})"


# ---------------------------------------------------------------------------
# Response object
# ---------------------------------------------------------------------------

class Response:
    """
    Accumulates headers and body, then flushes to the handler
    when send() or json() is called.
    Supports gzip compression for compressible content types.
    """

    def __init__(self, handler: BaseHTTPRequestHandler,
                 request: Request):
        self._handler = handler
        self._request = request
        self._headers: Dict[str, str] = {}
        self._status_code: int = 200
        self.headers_sent: bool = False

    def set_header(self, name: str, value: str) -> "Response":
        if not self.headers_sent:
            self._headers[name] = value
        return self

    def status(self, code: int) -> "Response":
        self._status_code = code
        return self

    def _write_headers(self, status: int,
                       content_type: str,
                       content_length: int,
                       extra: Optional[Dict] = None) -> None:
        if self.headers_sent:
            return
        self.headers_sent = True
        self._handler.send_response(status)
        self._handler.send_header("Content-Type", content_type)
        self._handler.send_header("Content-Length", str(content_length))
        for k, v in self._headers.items():
            self._handler.send_header(k, v)
        if extra:
            for k, v in extra.items():
                self._handler.send_header(k, v)
        self._handler.end_headers()

    def send(self, body: str = "", status: int = 200,
             content_type: str = "text/plain; charset=utf-8") -> None:
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self._status_code = status

        use_gzip = (
            self._request.accepts_gzip()
            and len(raw) > 256
            and content_type.split(";")[0].strip() in COMPRESSIBLE_TYPES
        )

        if use_gzip:
            compressed = gzip.compress(raw, compresslevel=6)
            self._write_headers(
                status, content_type, len(compressed),
                extra={"Content-Encoding": "gzip",
                       "Vary": "Accept-Encoding"}
            )
            self._handler.wfile.write(compressed)
        else:
            self._write_headers(status, content_type, len(raw))
            self._handler.wfile.write(raw)

    def json(self, data: Any, status: int = 200,
             indent: Optional[int] = None) -> None:
        body = json.dumps(data, default=str, indent=indent)
        self.send(body, status=status,
                  content_type="application/json; charset=utf-8")

    def json_envelope(self, data: Any, status: int = 200,
                      meta: Optional[Dict] = None) -> None:
        """
        Wrap data in a standard envelope:
        {"data": ..., "meta": {"status": 200, "request_id": ...}}
        """
        envelope = {
            "data": data,
            "meta": {
                "status": status,
                "request_id": self._request.request_id,
                **(meta or {}),
            },
        }
        self.json(envelope, status=status)

    def paginated(self, items: List, total: int,
                  limit: int, offset: int,
                  status: int = 200) -> None:
        """Standard paginated response envelope."""
        self.json_envelope(
            data=items,
            status=status,
            meta={
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total,
                "page": (offset // limit) + 1 if limit else 1,
            },
        )

    def send_file(self, file_path: str,
                  content_type: Optional[str] = None) -> None:
        """
        Stream a file from disk. Supports ETag caching and gzip compression.
        """
        if not os.path.isfile(file_path):
            self.json({"error": "File not found"}, status=404)
            return

        ext = os.path.splitext(file_path)[1].lower()
        mime = content_type or MIME_TYPES.get(ext, "application/octet-stream")

        try:
            stat = os.stat(file_path)
        except OSError:
            self.json({"error": "Cannot stat file"}, status=500)
            return

        # ETag based on size + mtime
        etag = f'"{stat.st_size}-{int(stat.st_mtime)}"'
        client_etag = self._request.get_header("if-none-match")

        if client_etag and client_etag == etag:
            self._write_headers(304, mime, 0,
                                extra={"ETag": etag})
            return

        self.set_header("ETag", etag)
        self.set_header("Cache-Control", "public, max-age=3600")

        try:
            with open(file_path, "rb") as f:
                raw = f.read()
        except OSError as e:
            self.json({"error": str(e)}, status=500)
            return

        use_gzip = (
            self._request.accepts_gzip()
            and len(raw) > 512
            and mime.split(";")[0].strip() in COMPRESSIBLE_TYPES
        )

        if use_gzip:
            compressed = gzip.compress(raw, compresslevel=6)
            self._write_headers(
                200, mime, len(compressed),
                extra={"Content-Encoding": "gzip",
                       "ETag": etag,
                       "Vary": "Accept-Encoding"},
            )
            self._handler.wfile.write(compressed)
        else:
            self._write_headers(200, mime, len(raw),
                                extra={"ETag": etag})
            self._handler.wfile.write(raw)

    def redirect(self, location: str, permanent: bool = False) -> None:
        code = 301 if permanent else 302
        self.set_header("Location", location)
        self.send("", status=code)

    def download(self, content: str, filename: str,
                 content_type: str = "text/plain") -> None:
        """Send content as a file download."""
        self.set_header(
            "Content-Disposition", f'attachment; filename="{filename}"'
        )
        self.send(content, content_type=content_type)

class AiohttpRequest:
    def __init__(self, request: web.Request):
        self._request = request
        self.method = request.method
        self.path = request.path
        self._query_params = None
        self._json = None
        self._text = None
        self.request_id = ""
        self.authenticated = False
        self.path_params: Dict[str, str] = {}

    def get_header(self, name: str) -> Optional[str]:
        return self._request.headers.get(name)

    @property
    def query_params(self) -> Dict[str, str]:
        if self._query_params is None:
            self._query_params = {
                k: v for k, v in self._request.rel_url.query.items()
            }
        return self._query_params

    def query(self, key: str, default: str = "") -> str:
        return self.query_params.get(key, default)

    def query_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.query(key, ""))
        except (ValueError, TypeError):
            return default

    def body_json(self) -> Any:
        return self._json

    def body_text(self) -> str:
        return self._text or ""


class AiohttpResponse:
    def __init__(self):
        self._status = 200
        self._headers: Dict[str, str] = {}
        self._body: Any = None

    def set_header(self, name: str, value: str) -> "AiohttpResponse":
        self._headers[name] = value
        return self

    def status(self, code: int) -> "AiohttpResponse":
        self._status = code
        return self

    def json(self, data: Any, status: int = 200,
             indent: Optional[int] = None) -> web.Response:
        self._status = status
        return web.json_response(
            data,
            status=status,
            dumps=lambda obj: json.dumps(obj, default=str, indent=indent),
            headers=self._headers,
        )

    def json_envelope(self, data: Any, status: int = 200,
                      meta: Optional[Dict] = None) -> web.Response:
        envelope = {
            "data": data,
            "meta": {
                "status": status,
                **(meta or {}),
            },
        }
        return self.json(envelope, status=status)


def create_app(conn, config=None):
    from .handlers.api import register_api_routes
    from .router import Router

    router = Router()
    register_api_routes(router, conn, config=config)

    app = web.Application()
    app["conn"] = conn
    app["config"] = config

    async def dispatch(request: web.Request) -> web.Response:
        if request.path == "/health":
            return web.json_response({"status": "ok"}, status=200)

        req = AiohttpRequest(request)
        req._json = await request.json() if request.method in ("POST", "PUT", "PATCH") else None
        req._text = await request.text()
        res = AiohttpResponse()
        handler, params, status = router.resolve(request.method, request.path)
        req.path_params = params

        if status != 200:
            if status == 405:
                return web.json_response(
                    {"error": "Method not allowed", "allowed": params.get("allowed", "")},
                    status=405,
                    headers={"Allow": params.get("allowed", "")},
                )
            return web.json_response({"error": "Not found", "path": request.path}, status=404)

        return handler(req, res, params)

    app.router.add_route("*", "/{tail:.*}", dispatch)
    return app

# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

class _DevPulseHandler(BaseHTTPRequestHandler):
    """
    The actual HTTP handler. One instance per request (stdlib behavior).
    Delegates to the Router + MiddlewareChain stored on the server.
    Overrides log_message to use our logger instead of stderr.
    """

    # Injected by DevPulseServer before serving
    router: Router = None
    middleware: MiddlewareChain = None

    def log_message(self, fmt: str, *args) -> None:
        # Silence default stderr logging — our middleware handles it
        pass

    def log_error(self, fmt: str, *args) -> None:
        logger.error(fmt % args)

    def _dispatch(self) -> None:
        req = Request(self)
        res = Response(self, req)

        handler_fn, path_params, status = self.router.resolve(
            req.method, req.path
        )
        req.path_params = path_params

        if status in (404, 405):
            # Route not found — skip middleware, call error handler directly
            try:
                handler_fn(req, res, path_params)
            except Exception as e:
                logger.error(f"Error handler raised: {e}", exc_info=True)
                if not res.headers_sent:
                    res.json({"error": "Internal server error"}, status=500)
            return

        def final():
            handler_fn(req, res, path_params)

        self.middleware.run(req, res, final)

    def do_GET(self):     self._dispatch()
    def do_POST(self):    self._dispatch()
    def do_PUT(self):     self._dispatch()
    def do_DELETE(self):  self._dispatch()
    def do_PATCH(self):   self._dispatch()
    def do_OPTIONS(self): self._dispatch()
    def do_HEAD(self):    self._dispatch()


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class DevPulseServer:
    """
    The HTTP server. Wraps stdlib HTTPServer with:
    - Router wiring
    - Middleware chain construction from config
    - Port conflict detection
    - Graceful shutdown
    - Startup banner
    - Background thread option

    Usage:
        server = DevPulseServer(
            router=my_router,
            conn=db_conn,
            config=my_config,
        )
        server.start()   # blocks
        # or
        server.start_background()  # returns thread
    """

    def __init__(self, router: Router,
                 conn,
                 config=None,
                 static_dir: str = ""):
        self.router = router
        self.conn = conn
        self.config = config
        self.static_dir = static_dir
        self._http_server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

        host = config.server_host if config else "127.0.0.1"
        port = config.server_port if config else 7820
        self.host = host
        self.port = port

        self.middleware = self._build_middleware()

    def _build_middleware(self) -> MiddlewareChain:
        config = self.config
        auth_enabled = config.auth_enabled if config else False
        rate_limit = (
            config.get_int("server", "rate_limit_per_minute", 120)
            if config else 120
        )
        cors_origins = (
            config.get_str("server", "cors_origins", "*")
            if config else "*"
        )

        chain = MiddlewareChain()
        chain.use(make_request_id_middleware())
        chain.use(make_timing_middleware())
        chain.use(make_security_headers_middleware())
        chain.use(make_cors_middleware(allowed_origins=cors_origins))
        chain.use(make_body_size_middleware(max_bytes=2 * 1024 * 1024))
        chain.use(make_rate_limit_middleware(max_per_minute=rate_limit))

        if auth_enabled:
            chain.use(make_auth_middleware(
                self.conn, enabled=True
            ))

        return chain

    def _check_port(self) -> bool:
        """Return True if the port is available."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self.host, self.port))
            return True
        except OSError:
            return False

    def _print_banner(self) -> None:
        from ..utils.colors import Color
        print()
        print(Color.cyan("  ╔══════════════════════════════════╗"))
        print(Color.cyan("  ║ ") + Color.bold("devpulse") +
              Color.muted("  server") +
              Color.cyan("               ║"))
        print(Color.cyan("  ║ ") +
              Color.muted(f"http://{self.host}:{self.port}") +
              " " * max(0, 22 - len(f"{self.host}:{self.port}")) +
              Color.cyan("  ║"))
        print(Color.cyan("  ╚══════════════════════════════════╝"))
        print()
        route_count = len(self.router.all_routes())
        print(Color.muted(f"  {route_count} routes registered"))
        auth_status = "enabled" if (
            self.config and self.config.auth_enabled
        ) else "disabled"
        print(Color.muted(f"  auth: {auth_status}"))
        print(Color.muted("  press Ctrl+C to stop"))
        print()

    def _make_handler_class(self):
        """
        Create a handler class with router and middleware bound.
        We do this so each request handler instance has access
        to our router/middleware without globals.
        """
        router = self.router
        middleware = self.middleware
        static_dir = self.static_dir

        class BoundHandler(_DevPulseHandler):
            pass

        BoundHandler.router = router
        BoundHandler.middleware = middleware
        return BoundHandler

    def start(self, print_banner: bool = True) -> None:
        """Start the server — blocks until interrupted."""
        if not self._check_port():
            print(
                f"Error: Port {self.port} is already in use on {self.host}.",
                file=sys.stderr,
            )
            sys.exit(1)

        handler_class = self._make_handler_class()

        self._http_server = HTTPServer(
            (self.host, self.port), handler_class
        )
        self._http_server.socket.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )

        if print_banner:
            self._print_banner()

        # Register signal handlers for graceful shutdown
        original_sigint  = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)

        def _shutdown(signum, frame):
            logger.info(f"Signal {signum} received — shutting down server.")
            threading.Thread(
                target=self._http_server.shutdown, daemon=True
            ).start()
            signal.signal(signal.SIGINT,  original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)

        signal.signal(signal.SIGINT,  _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        logger.info(
            f"DevPulse server listening on http://{self.host}:{self.port}"
        )

        try:
            self._http_server.serve_forever()
        finally:
            logger.info("Server stopped.")

    def start_background(self) -> threading.Thread:
        """Start the server in a daemon thread. Returns the thread."""
        if not self._check_port():
            raise OSError(
                f"Port {self.port} is already in use on {self.host}."
            )

        handler_class = self._make_handler_class()
        self._http_server = HTTPServer(
            (self.host, self.port), handler_class
        )

        self._thread = threading.Thread(
            target=self._http_server.serve_forever,
            daemon=True,
            name="devpulse-server",
        )
        self._thread.start()
        logger.info(
            f"Server started in background on "
            f"http://{self.host}:{self.port}"
        )
        return self._thread

    def stop(self) -> None:
        if self._http_server:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None
            logger.info("Server stopped.")

    def is_running(self) -> bool:
        return self._http_server is not None

    def __enter__(self) -> "DevPulseServer":
        self.start_background()
        return self

    def __exit__(self, *_) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Static file handler helper (used by API handlers)
# ---------------------------------------------------------------------------

class StaticFileHandler:
    """
    Serves files from a static directory.
    Called by the /static/* route handler.
    Handles ETag caching, MIME detection, and gzip.
    """

    def __init__(self, static_dir: str):
        self.static_dir = os.path.abspath(static_dir)

    def serve(self, req: Request, res: Response,
              relative_path: str) -> None:
        # Security: prevent directory traversal
        safe_path = os.path.normpath(
            os.path.join(self.static_dir, relative_path.lstrip("/"))
        )
        if not safe_path.startswith(self.static_dir):
            res.json({"error": "Forbidden"}, status=403)
            return

        if os.path.isdir(safe_path):
            # Try index.html
            index = os.path.join(safe_path, "index.html")
            if os.path.isfile(index):
                res.send_file(index)
                return
            res.json({"error": "Not found"}, status=404)
            return

        if not os.path.isfile(safe_path):
            res.json({"error": "Not found"}, status=404)
            return

        res.send_file(safe_path)