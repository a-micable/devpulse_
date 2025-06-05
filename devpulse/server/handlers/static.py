# devpulse/server/handlers/static.py
# Static file and SPA fallback route registration.
# Serves the web/ directory for the dashboard frontend.
# All file serving logic lives in StaticFileHandler (server/app.py).

import os
import logging
from ..app import StaticFileHandler, Request, Response
from ..router import Router

logger = logging.getLogger(__name__)


def register_static_routes(router: Router, static_dir: str) -> None:
    """
    Register all static file routes onto the router.
    Called once at server startup.
    """
    handler = StaticFileHandler(static_dir)

    @router.get("/")
    def serve_index(req: Request, res: Response, params: dict) -> None:
        index_path = os.path.join(static_dir, "index.html")
        if os.path.isfile(index_path):
            res.send_file(index_path)
        else:
            res.json(
                {"name": "devpulse", "version": "1.0.0",
                 "docs": "/api"},
                status=200,
            )

    @router.get("/health")
    def health_check(req: Request, res: Response, params: dict) -> None:
        res.json({"status": "ok", "service": "devpulse"})

    @router.get("/static/:filepath")
    def serve_static(req: Request, res: Response, params: dict) -> None:
        # :filepath only captures one segment — we need the full tail
        # Reconstruct from raw path
        prefix = "/static/"
        relative = req.path[len(prefix):] if req.path.startswith(prefix) else ""
        handler.serve(req, res, relative)

    # Catch-all for SPA client-side routing — serve index.html
    # for any unknown path that doesn't start with /api or /static
    @router.get("/app/:rest")
    def spa_fallback(req: Request, res: Response, params: dict) -> None:
        index_path = os.path.join(static_dir, "index.html")
        if os.path.isfile(index_path):
            res.send_file(index_path)
        else:
            res.json({"error": "Frontend not found"}, status=404)

    logger.debug(f"Static routes registered. Serving from: {static_dir}")