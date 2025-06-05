# devpulse/db/schema.py
# Raw SQL table definitions — no ORM, no abstraction layer.
# Each migration version adds to MIGRATIONS list in migrations.py.

SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id     INTEGER REFERENCES repos(id) ON DELETE SET NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    duration_s  INTEGER,
    focus_score REAL DEFAULT 0.0,
    notes       TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

SESSIONS_INDEX_STARTED = """
CREATE INDEX IF NOT EXISTS idx_sessions_started_at
ON sessions(started_at);
"""

SESSIONS_INDEX_REPO = """
CREATE INDEX IF NOT EXISTS idx_sessions_repo_id
ON sessions(repo_id);
"""

REPOS_TABLE = """
CREATE TABLE IF NOT EXISTS repos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL UNIQUE,
    remote_url  TEXT DEFAULT '',
    language    TEXT DEFAULT 'unknown',
    active      INTEGER DEFAULT 1,
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced TEXT
);
"""

COMMITS_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS commits_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    hash        TEXT NOT NULL,
    author      TEXT NOT NULL,
    author_email TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    lines_added  INTEGER DEFAULT 0,
    lines_removed INTEGER DEFAULT 0,
    files_changed INTEGER DEFAULT 0,
    is_merge    INTEGER DEFAULT 0,
    UNIQUE(repo_id, hash)
);
"""

COMMITS_INDEX_REPO = """
CREATE INDEX IF NOT EXISTS idx_commits_repo_id
ON commits_cache(repo_id);
"""

COMMITS_INDEX_DATE = """
CREATE INDEX IF NOT EXISTS idx_commits_committed_at
ON commits_cache(committed_at);
"""

METRICS_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS metrics_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,
    language    TEXT DEFAULT 'unknown',
    loc         INTEGER DEFAULT 0,
    blank_lines INTEGER DEFAULT 0,
    comment_lines INTEGER DEFAULT 0,
    function_count INTEGER DEFAULT 0,
    class_count INTEGER DEFAULT 0,
    complexity  REAL DEFAULT 0.0,
    churn_score REAL DEFAULT 0.0,
    last_computed TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(repo_id, file_path)
);
"""

HEARTBEATS_TABLE = """
CREATE TABLE IF NOT EXISTS heartbeats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    file_path   TEXT DEFAULT '',
    event_type  TEXT DEFAULT 'save'
);
"""

HEARTBEATS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_heartbeats_session_id
ON heartbeats(session_id);
"""

TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    repo_id     INTEGER REFERENCES repos(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

API_TOKENS_TABLE = """
CREATE TABLE IF NOT EXISTS api_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT NOT NULL UNIQUE,
    label       TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT,
    last_used   TEXT,
    active      INTEGER DEFAULT 1
);
"""

GOALS_TABLE = """
CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    metric      TEXT NOT NULL,
    target      REAL NOT NULL,
    period      TEXT NOT NULL DEFAULT 'daily',
    repo_id     INTEGER REFERENCES repos(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    active      INTEGER DEFAULT 1
);
"""

GOAL_PROGRESS_TABLE = """
CREATE TABLE IF NOT EXISTS goal_progress (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id     INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    value       REAL NOT NULL DEFAULT 0.0,
    met         INTEGER DEFAULT 0,
    UNIQUE(goal_id, date)
);
"""

SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT NOT NULL DEFAULT ''
);
"""


def create_tables(conn):
    """Create the default DevPulse schema in the provided SQLite connection."""
    statements = [
        REPOS_TABLE,
        SESSIONS_TABLE,
        SESSIONS_INDEX_STARTED,
        SESSIONS_INDEX_REPO,
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
        SCHEMA_VERSION_TABLE,
    ]
    with conn:
        for sql in statements:
            conn.execute(sql)
