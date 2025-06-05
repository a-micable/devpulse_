# devpulse/server/__main__.py
# Entry point for running the DevPulse HTTP server as a module.

import os
import sys

from devpulse.db.connection import open_connection
from devpulse.utils.config import Config
from devpulse.server.app import DevPulseServer
from devpulse.server.handlers.api import register_api_routes
from devpulse.server.handlers.static import register_static_routes
from devpulse.server.router import Router


def main(argv=None) -> int:
    config = Config()
    config.ensure_data_dir()

    db_path = config.db_path
    conn = open_connection(db_path)

    router = Router()
    register_api_routes(router, conn, config=config)

    static_dir = config.get_str("server", "static_dir")
    if not static_dir:
        package_root = os.path.dirname(__file__)
        static_dir = os.path.join(package_root, "web")

    if os.path.isdir(static_dir):
        register_static_routes(router, static_dir)

    server = DevPulseServer(
        router=router,
        conn=conn,
        config=config,
        static_dir=static_dir,
    )
    server.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
