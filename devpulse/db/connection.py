# devpulse/db/connection.py
# SQLite connection helper with migrations and sane PRAGMAs.

import os
import sqlite3
import logging
from typing import Optional

from .migrations import run_migrations

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.expanduser("~/.devpulse/devpulse.db")


def _ensure_directory_exists(db_path: str) -> None:
    directory = os.path.dirname(os.path.abspath(db_path))
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
        logger.debug("Created database directory: %s", directory)


def open_connection(db_path: Optional[str] = None,
                    ensure_schema: bool = True) -> sqlite3.Connection:
    """Open a configured SQLite connection for DevPulse."""
    if not db_path:
        db_path = DEFAULT_DB_PATH

    _ensure_directory_exists(db_path)

    if ensure_schema:
        run_migrations(db_path)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def initialize_database(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Create the database and return a configured SQLite connection."""
    return open_connection(db_path=db_path, ensure_schema=True)
