# devpulse/server/websocket.py
# WebSocket endpoint for real-time session updates.
# Clients connect to /ws and receive JSON messages when:
#   - a session starts or stops
#   - a heartbeat is recorded
#   - the active session timer ticks (every 10s)

from __future__ import annotations

import asyncio
import json
import logging
import weakref
from typing import Any, Dict, Set

from aiohttp import web, WSMsgType

log = logging.getLogger(__name__)

# Global set of active WebSocket connections
_clients: Set[web.WebSocketResponse] = set()
_clients_ref = weakref.WeakSet()


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    _clients.add(ws)
    log.debug("WebSocket client connected. Total: %d", len(_clients))

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await _handle_message(ws, msg.data)
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        _clients.discard(ws)
        log.debug("WebSocket client disconnected. Total: %d", len(_clients))

    return ws


async def _handle_message(ws: web.WebSocketResponse, data: str) -> None:
    try:
        msg = json.loads(data)
        kind = msg.get("type")
        if kind == "ping":
            await ws.send_json({"type": "pong"})
    except (json.JSONDecodeError, Exception) as exc:
        log.debug("WS message error: %s", exc)


async def broadcast(event: str, payload: Dict[str, Any]) -> None:
    """Send an event to all connected WebSocket clients."""
    if not _clients:
        return
    message = json.dumps({"event": event, "data": payload}, default=str)
    dead = set()
    for ws in list(_clients):
        try:
            if not ws.closed:
                await ws.send_str(message)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


async def session_tick_loop(app: web.Application) -> None:
    """
    Background task: broadcast active session progress every 10 seconds.
    Runs as an aiohttp background task.
    """
    while True:
        await asyncio.sleep(10)
        if not _clients:
            continue
        try:
            conn = app.get("conn")
            if conn:
                from ..db.queries import SessionQueries
                active = SessionQueries.get_active(conn)
                if active:
                    await broadcast("session_tick", dict(active))
        except Exception as exc:
            log.debug("session_tick_loop error: %s", exc)