# devpulse/server/middleware.py
# aiohttp middleware: request logging, error handling, CORS preflight.

from __future__ import annotations

import json
import logging
import time
from typing import Callable

from aiohttp import web

log = logging.getLogger(__name__)


@web.middleware
async def request_logger(request: web.Request, handler: Callable) -> web.Response:
    start = time.monotonic()
    try:
        response = await handler(request)
        elapsed  = (time.monotonic() - start) * 1000
        log.info(
            "%s %s → %d  (%.1fms)",
            request.method, request.path, response.status, elapsed,
        )
        return response
    except web.HTTPException as exc:
        elapsed = (time.monotonic() - start) * 1000
        log.warning(
            "%s %s → %d  (%.1fms)",
            request.method, request.path, exc.status, elapsed,
        )
        raise
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        log.error(
            "%s %s → 500  (%.1fms)  %s",
            request.method, request.path, elapsed, exc, exc_info=True,
        )
        return web.Response(
            status=500,
            content_type="application/json",
            text=json.dumps({"error": "Internal server error"}),
        )


@web.middleware
async def error_handler(request: web.Request, handler: Callable) -> web.Response:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except ValueError as exc:
        return web.Response(
            status=400,
            content_type="application/json",
            text=json.dumps({"error": str(exc)}),
        )
    except PermissionError as exc:
        return web.Response(
            status=403,
            content_type="application/json",
            text=json.dumps({"error": str(exc)}),
        )
    except FileNotFoundError as exc:
        return web.Response(
            status=404,
            content_type="application/json",
            text=json.dumps({"error": str(exc)}),
        )
    except Exception as exc:
        log.exception("Unhandled exception in %s %s", request.method, request.path)
        return web.Response(
            status=500,
            content_type="application/json",
            text=json.dumps({"error": "Internal server error"}),
        )


def json_response(data=None, status: int = 200, meta: dict = None) -> web.Response:
    body: dict = {"data": data}
    if meta:
        body["meta"] = meta
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps(body, default=str),
    )


def error_response(message: str, status: int = 400) -> web.Response:
    return web.Response(
        status=status,
        content_type="application/json",
        text=json.dumps({"error": message}),
    )