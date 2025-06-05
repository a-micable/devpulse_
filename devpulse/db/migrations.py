# devpulse/db/migrations.py
# Manual migration runner. No Alembic, no third-party tools.
# Each migration is a tuple: (version, description, list_of_sql_statements)
# run_migrations() is idempotent — safe to call every startup.

import sqlite3
import logging
from typing import List, Tuple

from .schema import (
    SCHEMA_VERSION_TABLE,
    SESSIONS_TABLE,
    SESSIONS_INDEX_STARTED,
    SESSIONS_INDEX_REPO,
    REPOS_TABLE,
    COMMITS_CACHE_TABLE,
    COMMITS_INDEX_REPO,
    COMMITS_INDEX_DATE,
    METRICS_CACHE_TABLE,
    HEARTBEATS_TABLE,
    HEARTBEATS_INDEX,
    TAGS_TABLE,
    API_TOKENS_TABLE,
    GOALS_TABLE,
    GOAL_PROGRESS_TABLE,
)

logger = logging.getLogger(__name__)

# Each entry: (version_int, "description", [sql, sql, ...])
MIGRATIONS: List[Tuple[int, str, List[str]]] = [
    (
        1,
        "initial tables: repos, sessions, commits_cache",
        [
            REPOS_TABLE,
            SESSIONS_TABLE,
            SESSIONS_INDEX_STARTED,
            SESSIONS_INDEX_REPO,
            COMMITS_CACHE_TABLE,
            COMMITS_INDEX_REPO,
            COMMITS_INDEX_DATE,
        ],
    ),
    (
        2,
        "add metrics_cache table",
        [
            METRICS_CACHE_TABLE,
        ],
    ),
    (
        3,
        "add heartbeats table for session tracking",
        [
            HEARTBEATS_TABLE,
            HEARTBEATS_INDEX,
        ],
    ),
    (
        4,
        "add tags table for session and repo tagging",
        [
            TAGS_TABLE,
        ],
    ),
    (
        5,
        "add api_tokens table for server auth",
        [
            API_TOKENS_TABLE,
        ],
    ),
    (
        6,
        "add goals and goal_progress tables",
        [
            GOALS_TABLE,
            GOAL_PROGRESS_TABLE,
        ],
    ),
    (
        7,
        "add notes column to sessions if missing",
        [
            # Safe ALTER — SQLite ignores if column exists via try/except in runner
            "ALTER TABLE sessions ADD COLUMN notes TEXT DEFAULT '';",
        ],
    ),
]


def _get_current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    try:
        cursor = conn.execute(
            "SELECT MAX(version) FROM schema_version;"
        )
        result = cursor.fetchone()[0]
        return result if result is not None else 0
    except sqlite3.OperationalError:
        # schema_version table doesn't exist yet
        return 0


def _apply_migration(
    conn: sqlite3.Connection,
    version: int,
    description: str,
    statements: List[str],
) -> None:
    """Apply a single migration inside a transaction."""
    logger.info(f"Applying migration {version}: {description}")
    try:
        with conn:
            for sql in statements:
                sql = sql.strip()
                if not sql:
                    continue
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError as e:
                    # Allow "duplicate column" errors for ALTER TABLE migrations
                    if "duplicate column" in str(e).lower():
                        logger.debug(f"  Skipping duplicate column: {e}")
                        continue
                    raise
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?);",
                (version, description),
            )
        logger.info(f"  Migration {version} applied successfully.")
    except sqlite3.Error as e:
        logger.error(f"  Migration {version} failed: {e}")
        raise


def run_migrations(db_path: str) -> None:
    """
    Open the database and apply any pending migrations in order.
    Creates the schema_version table first if it doesn't exist.
    Safe to call on every application startup.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    # Bootstrap: create version tracking table first
    conn.execute(SCHEMA_VERSION_TABLE)
    conn.commit()

    current = _get_current_version(conn)
    logger.info(f"Database at migration version {current}")

    pending = [m for m in MIGRATIONS if m[0] > current]

    if not pending:
        logger.info("Database is up to date.")
        conn.close()
        return

    for version, description, statements in sorted(pending, key=lambda x: x[0]):
        _apply_migration(conn, version, description, statements)

    logger.info(f"Applied {len(pending)} migration(s). Now at version {pending[-1][0]}.")
    conn.close()


def get_connection(db_path: str) -> sqlite3.Connection:
    """
    Return a configured SQLite connection with WAL mode and foreign keys on.
    Caller is responsible for closing.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn