# devpulse/db/queries.py
# All SQL queries in one place. No ORM. Each class handles one table domain.
# All methods accept a sqlite3.Connection as first argument.

import sqlite3
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row) if row else None


def _rows_to_list(rows) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Repo queries
# ---------------------------------------------------------------------------

class RepoQueries:

    @staticmethod
    def insert(conn: sqlite3.Connection, name: str, path: str,
               remote_url: str = "", language: str = "unknown") -> int:
        cursor = conn.execute(
            """
            INSERT INTO repos (name, path, remote_url, language, added_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, path, remote_url, language, _now()),
        )
        conn.commit()
        return cursor.lastrowid

    @staticmethod
    def get_by_id(conn: sqlite3.Connection, repo_id: int) -> Optional[Dict]:
        row = conn.execute(
            "SELECT * FROM repos WHERE id = ?;", (repo_id,)
        ).fetchone()
        return _row_to_dict(row)

    @staticmethod
    def get_by_path(conn: sqlite3.Connection, path: str) -> Optional[Dict]:
        row = conn.execute(
            "SELECT * FROM repos WHERE path = ?;", (path,)
        ).fetchone()
        return _row_to_dict(row)

    @staticmethod
    def list_active(conn: sqlite3.Connection) -> List[Dict]:
        rows = conn.execute(
            "SELECT * FROM repos WHERE active = 1 ORDER BY added_at DESC;"
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def list_all(conn: sqlite3.Connection) -> List[Dict]:
        rows = conn.execute(
            "SELECT * FROM repos ORDER BY added_at DESC;"
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def update_language(conn: sqlite3.Connection, repo_id: int, language: str) -> None:
        conn.execute(
            "UPDATE repos SET language = ? WHERE id = ?;",
            (language, repo_id),
        )
        conn.commit()

    @staticmethod
    def update_last_synced(conn: sqlite3.Connection, repo_id: int) -> None:
        conn.execute(
            "UPDATE repos SET last_synced = ? WHERE id = ?;",
            (_now(), repo_id),
        )
        conn.commit()

    @staticmethod
    def deactivate(conn: sqlite3.Connection, repo_id: int) -> None:
        conn.execute(
            "UPDATE repos SET active = 0 WHERE id = ?;", (repo_id,)
        )
        conn.commit()

    @staticmethod
    def delete(conn: sqlite3.Connection, repo_id: int) -> None:
        conn.execute("DELETE FROM repos WHERE id = ?;", (repo_id,))
        conn.commit()

    @staticmethod
    def count(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COUNT(*) FROM repos WHERE active = 1;").fetchone()
        return row[0]


# ---------------------------------------------------------------------------
# Session queries
# ---------------------------------------------------------------------------

class SessionQueries:

    @staticmethod
    def insert(conn: sqlite3.Connection, repo_id: Optional[int],
               started_at: Optional[str] = None) -> int:
        started = started_at or _now()
        cursor = conn.execute(
            """
            INSERT INTO sessions (repo_id, started_at, created_at)
            VALUES (?, ?, ?)
            """,
            (repo_id, started, _now()),
        )
        conn.commit()
        return cursor.lastrowid

    @staticmethod
    def close(conn: sqlite3.Connection, session_id: int,
              ended_at: Optional[str] = None,
              duration_s: Optional[int] = None,
              focus_score: float = 0.0,
              notes: str = "") -> None:
        ended = ended_at or _now()
        conn.execute(
            """
            UPDATE sessions
            SET ended_at = ?, duration_s = ?, focus_score = ?, notes = ?
            WHERE id = ?;
            """,
            (ended, duration_s, focus_score, notes, session_id),
        )
        conn.commit()

    # Backwards-compatible alias
    @staticmethod
    def mark_ended(conn: sqlite3.Connection, session_id: int,
                   ended_at: Optional[str] = None,
                   duration_s: Optional[int] = None,
                   focus_score: float = 0.0,
                   notes: str = "") -> None:
        return SessionQueries.close(
            conn, session_id=session_id,
            ended_at=ended_at, duration_s=duration_s,
            focus_score=focus_score, notes=notes,
        )

    @staticmethod
    def get_by_id(conn: sqlite3.Connection, session_id: int) -> Optional[Dict]:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?;", (session_id,)
        ).fetchone()
        return _row_to_dict(row)

    @staticmethod
    def get_active(conn: sqlite3.Connection) -> Optional[Dict]:
        """Return the most recent session with no ended_at."""
        row = conn.execute(
            """
            SELECT * FROM sessions
            WHERE ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1;
            """
        ).fetchone()
        return _row_to_dict(row)

    @staticmethod
    def list_by_date_range(conn: sqlite3.Connection,
                           start: str, end: str,
                           limit: int = 100,
                           offset: int = 0) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT s.*, r.name as repo_name, r.path as repo_path
            FROM sessions s
            LEFT JOIN repos r ON s.repo_id = r.id
            WHERE s.started_at >= ? AND s.started_at <= ?
            ORDER BY s.started_at DESC
            LIMIT ? OFFSET ?;
            """,
            (start, end, limit, offset),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def list_recent(conn: sqlite3.Connection, limit: int = 20,
                    offset: int = 0) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT s.*, r.name as repo_name
            FROM sessions s
            LEFT JOIN repos r ON s.repo_id = r.id
            ORDER BY s.started_at DESC
            LIMIT ? OFFSET ?;
            """,
            (limit, offset),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def total_duration_by_date(conn: sqlite3.Connection,
                               start: str, end: str) -> List[Dict]:
        """Returns daily total coding time in seconds."""
        rows = conn.execute(
            """
            SELECT
                DATE(started_at) as date,
                SUM(duration_s) as total_seconds,
                COUNT(*) as session_count
            FROM sessions
            WHERE started_at >= ? AND started_at <= ?
              AND ended_at IS NOT NULL
            GROUP BY DATE(started_at)
            ORDER BY date ASC;
            """,
            (start, end),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def average_focus_score(conn: sqlite3.Connection,
                            days: int = 30) -> float:
        row = conn.execute(
            """
            SELECT AVG(focus_score) FROM sessions
            WHERE started_at >= datetime('now', ?)
              AND focus_score > 0;
            """,
            (f"-{days} days",),
        ).fetchone()
        return round(row[0] or 0.0, 2)

    @staticmethod
    def count_by_repo(conn: sqlite3.Connection, repo_id: int) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE repo_id = ?;", (repo_id,)
        ).fetchone()
        return row[0]

    @staticmethod
    def total_count(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COUNT(*) FROM sessions;").fetchone()
        return row[0]


# ---------------------------------------------------------------------------
# Commits cache queries
# ---------------------------------------------------------------------------

class CommitQueries:

    @staticmethod
    def insert_or_ignore(conn: sqlite3.Connection, repo_id: int,
                         hash_: str, author: str, author_email: str,
                         message: str, committed_at: str,
                         lines_added: int = 0, lines_removed: int = 0,
                         files_changed: int = 0, is_merge: int = 0) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO commits_cache
            (repo_id, hash, author, author_email, message, committed_at,
             lines_added, lines_removed, files_changed, is_merge)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (repo_id, hash_, author, author_email, message, committed_at,
             lines_added, lines_removed, files_changed, is_merge),
        )
        conn.commit()

    @staticmethod
    def bulk_insert(conn: sqlite3.Connection, rows: List[Dict]) -> int:
        """Insert many commits. Returns count of new inserts."""
        before = conn.execute(
            "SELECT COUNT(*) FROM commits_cache;"
        ).fetchone()[0]
        conn.executemany(
            """
            INSERT OR IGNORE INTO commits_cache
            (repo_id, hash, author, author_email, message, committed_at,
             lines_added, lines_removed, files_changed, is_merge)
            VALUES (:repo_id, :hash, :author, :author_email, :message,
                    :committed_at, :lines_added, :lines_removed,
                    :files_changed, :is_merge);
            """,
            rows,
        )
        conn.commit()

    # Backwards-compatible wrapper expected by tests
    @staticmethod
    def insert(conn: sqlite3.Connection, repo_id: int, *args, **kwargs) -> None:
        """Compatibility wrapper accepting both positional and keyword args.
        Maps `hash` kwarg to internal `hash_` parameter name.
        """
        # Support both 'hash' and 'hash_' keys
        if 'hash' in kwargs and 'hash_' not in kwargs:
            kwargs['hash_'] = kwargs.pop('hash')
        # Repack positional args to match internal signature if provided
        if args:
            # Assume positional order: hash, author, author_email, committed_at, message, lines_added, lines_removed, files_changed, is_merge
            arg_names = ['hash_', 'author', 'author_email', 'committed_at', 'message', 'lines_added', 'lines_removed', 'files_changed', 'is_merge']
            for name, val in zip(arg_names, args):
                if name not in kwargs:
                    kwargs[name] = val

        rid = kwargs.get('repo_id', repo_id)
        return CommitQueries.insert_or_ignore(
            conn,
            rid,
            kwargs.get('hash_'),
            kwargs.get('author', ''),
            kwargs.get('author_email', ''),
            kwargs.get('message', ''),
            kwargs.get('committed_at', ''),
            kwargs.get('lines_added', 0),
            kwargs.get('lines_removed', 0),
            kwargs.get('files_changed', 0),
            kwargs.get('is_merge', 0),
        )
        after = conn.execute(
            "SELECT COUNT(*) FROM commits_cache;"
        ).fetchone()[0]
        return after - before

    @staticmethod
    def list_by_repo(conn: sqlite3.Connection, repo_id: int,
                     limit: int = 100, offset: int = 0,
                     exclude_merges: bool = True) -> List[Dict]:
        merge_filter = "AND is_merge = 0" if exclude_merges else ""
        rows = conn.execute(
            f"""
            SELECT * FROM commits_cache
            WHERE repo_id = ? {merge_filter}
            ORDER BY committed_at DESC
            LIMIT ? OFFSET ?;
            """,
            (repo_id, limit, offset),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def commits_per_day(conn: sqlite3.Connection, repo_id: int,
                        start: str, end: str) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT DATE(committed_at) as date, COUNT(*) as count
            FROM commits_cache
            WHERE repo_id = ? AND committed_at >= ? AND committed_at <= ?
              AND is_merge = 0
            GROUP BY DATE(committed_at)
            ORDER BY date ASC;
            """,
            (repo_id, start, end),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def author_stats(conn: sqlite3.Connection, repo_id: int) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT
                author,
                author_email,
                COUNT(*) as commit_count,
                SUM(lines_added) as total_added,
                SUM(lines_removed) as total_removed,
                MIN(committed_at) as first_commit,
                MAX(committed_at) as last_commit
            FROM commits_cache
            WHERE repo_id = ? AND is_merge = 0
            GROUP BY author, author_email
            ORDER BY commit_count DESC;
            """,
            (repo_id,),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def most_changed_files(conn: sqlite3.Connection, repo_id: int,
                           limit: int = 20) -> List[Dict]:
        """
        Files with highest change frequency — requires file-level data
        which we store separately in metrics_cache with churn_score.
        This query uses commits_cache aggregate as a proxy.
        """
        rows = conn.execute(
            """
            SELECT
                DATE(committed_at) as date,
                SUM(lines_added + lines_removed) as churn,
                COUNT(*) as commits
            FROM commits_cache
            WHERE repo_id = ? AND is_merge = 0
            GROUP BY DATE(committed_at)
            ORDER BY churn DESC
            LIMIT ?;
            """,
            (repo_id, limit),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def hourly_distribution(conn: sqlite3.Connection,
                            repo_id: int) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT
                CAST(strftime('%H', committed_at) AS INTEGER) as hour,
                COUNT(*) as count
            FROM commits_cache
            WHERE repo_id = ? AND is_merge = 0
            GROUP BY hour
            ORDER BY hour ASC;
            """,
            (repo_id,),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def weekday_distribution(conn: sqlite3.Connection,
                             repo_id: int) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT
                CAST(strftime('%w', committed_at) AS INTEGER) as weekday,
                COUNT(*) as count
            FROM commits_cache
            WHERE repo_id = ? AND is_merge = 0
            GROUP BY weekday
            ORDER BY weekday ASC;
            """,
            (repo_id,),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def exists(conn: sqlite3.Connection, repo_id: int, hash_: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM commits_cache WHERE repo_id = ? AND hash = ?;",
            (repo_id, hash_),
        ).fetchone()
        return row is not None

    @staticmethod
    def count_by_repo(conn: sqlite3.Connection, repo_id: int) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM commits_cache WHERE repo_id = ?;", (repo_id,)
        ).fetchone()
        return row[0]


# ---------------------------------------------------------------------------
# Metrics cache queries
# ---------------------------------------------------------------------------

class MetricsQueries:

    @staticmethod
    def upsert(conn: sqlite3.Connection, repo_id: int, file_path: str,
               language: str, loc: int, blank_lines: int,
               comment_lines: int, function_count: int,
               class_count: int, complexity: float,
               churn_score: float = 0.0) -> None:
        conn.execute(
            """
            INSERT INTO metrics_cache
            (repo_id, file_path, language, loc, blank_lines, comment_lines,
             function_count, class_count, complexity, churn_score, last_computed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo_id, file_path) DO UPDATE SET
                language       = excluded.language,
                loc            = excluded.loc,
                blank_lines    = excluded.blank_lines,
                comment_lines  = excluded.comment_lines,
                function_count = excluded.function_count,
                class_count    = excluded.class_count,
                complexity     = excluded.complexity,
                churn_score    = excluded.churn_score,
                last_computed  = excluded.last_computed;
            """,
            (repo_id, file_path, language, loc, blank_lines, comment_lines,
             function_count, class_count, complexity, churn_score, _now()),
        )

    @staticmethod
    def get_by_file(conn: sqlite3.Connection, repo_id: int,
                    file_path: str) -> Optional[Dict]:
        row = conn.execute(
            "SELECT * FROM metrics_cache WHERE repo_id = ? AND file_path = ?;",
            (repo_id, file_path),
        ).fetchone()
        return _row_to_dict(row)

    @staticmethod
    def list_by_repo(conn: sqlite3.Connection, repo_id: int,
                     order_by: str = "churn_score",
                     limit: int = 50) -> List[Dict]:
        safe_cols = {"churn_score", "loc", "complexity", "function_count"}
        col = order_by if order_by in safe_cols else "churn_score"
        rows = conn.execute(
            f"""
            SELECT * FROM metrics_cache
            WHERE repo_id = ?
            ORDER BY {col} DESC
            LIMIT ?;
            """,
            (repo_id, limit),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def repo_summary(conn: sqlite3.Connection, repo_id: int) -> Dict:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as file_count,
                SUM(loc) as total_loc,
                SUM(blank_lines) as total_blank,
                SUM(comment_lines) as total_comments,
                SUM(function_count) as total_functions,
                SUM(class_count) as total_classes,
                AVG(complexity) as avg_complexity,
                MAX(churn_score) as max_churn
            FROM metrics_cache
            WHERE repo_id = ?;
            """,
            (repo_id,),
        ).fetchone()
        return _row_to_dict(row)

    @staticmethod
    def language_breakdown(conn: sqlite3.Connection,
                           repo_id: int) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT language, COUNT(*) as file_count, SUM(loc) as total_loc
            FROM metrics_cache
            WHERE repo_id = ?
            GROUP BY language
            ORDER BY total_loc DESC;
            """,
            (repo_id,),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def stale_files(conn: sqlite3.Connection, repo_id: int,
                    older_than_hours: int = 24) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT file_path FROM metrics_cache
            WHERE repo_id = ?
              AND last_computed < datetime('now', ?)
            ORDER BY last_computed ASC;
            """,
            (repo_id, f"-{older_than_hours} hours"),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def delete_by_repo(conn: sqlite3.Connection, repo_id: int) -> None:
        conn.execute(
            "DELETE FROM metrics_cache WHERE repo_id = ?;", (repo_id,)
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Heartbeat queries
# ---------------------------------------------------------------------------

class HeartbeatQueries:

    @staticmethod
    def insert(conn: sqlite3.Connection, session_id: int,
               file_path: str = "", event_type: str = "save") -> None:
        conn.execute(
            """
            INSERT INTO heartbeats (session_id, recorded_at, file_path, event_type)
            VALUES (?, ?, ?, ?);
            """,
            (session_id, _now(), file_path, event_type),
        )
        conn.commit()

    @staticmethod
    def last_for_session(conn: sqlite3.Connection,
                         session_id: int) -> Optional[Dict]:
        row = conn.execute(
            """
            SELECT * FROM heartbeats
            WHERE session_id = ?
            ORDER BY recorded_at DESC
            LIMIT 1;
            """,
            (session_id,),
        ).fetchone()
        return _row_to_dict(row)

    @staticmethod
    def count_for_session(conn: sqlite3.Connection,
                          session_id: int) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM heartbeats WHERE session_id = ?;",
            (session_id,),
        ).fetchone()
        return row[0]

    @staticmethod
    def cleanup_old(conn: sqlite3.Connection, days: int = 7) -> int:
        cursor = conn.execute(
            """
            DELETE FROM heartbeats
            WHERE recorded_at < datetime('now', ?);
            """,
            (f"-{days} days",),
        )
        conn.commit()
        return cursor.rowcount

    @staticmethod
    def intervals_for_session(conn: sqlite3.Connection,
                               session_id: int) -> List[str]:
        """Return all recorded_at timestamps for a session, sorted."""
        rows = conn.execute(
            """
            SELECT recorded_at FROM heartbeats
            WHERE session_id = ?
            ORDER BY recorded_at ASC;
            """,
            (session_id,),
        ).fetchall()
        return [r["recorded_at"] for r in rows]


# ---------------------------------------------------------------------------
# Tag queries
# ---------------------------------------------------------------------------

class TagQueries:

    @staticmethod
    def insert(conn: sqlite3.Connection, name: str,
               session_id: Optional[int] = None,
               repo_id: Optional[int] = None) -> int:
        cursor = conn.execute(
            """
            INSERT INTO tags (session_id, repo_id, name, created_at)
            VALUES (?, ?, ?, ?);
            """,
            (session_id, repo_id, name, _now()),
        )
        conn.commit()
        return cursor.lastrowid

    @staticmethod
    def list_for_session(conn: sqlite3.Connection,
                         session_id: int) -> List[str]:
        rows = conn.execute(
            "SELECT name FROM tags WHERE session_id = ? ORDER BY created_at;",
            (session_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    @staticmethod
    def list_for_repo(conn: sqlite3.Connection, repo_id: int) -> List[str]:
        rows = conn.execute(
            """
            SELECT DISTINCT name FROM tags
            WHERE repo_id = ?
            ORDER BY name;
            """,
            (repo_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    @staticmethod
    def all_unique(conn: sqlite3.Connection) -> List[str]:
        rows = conn.execute(
            "SELECT DISTINCT name FROM tags ORDER BY name;"
        ).fetchall()
        return [r["name"] for r in rows]


# ---------------------------------------------------------------------------
# API token queries
# ---------------------------------------------------------------------------

class TokenQueries:

    @staticmethod
    def create(conn: sqlite3.Connection, label: str = "",
               expires_at: Optional[str] = None) -> str:
        token = secrets.token_hex(32)
        conn.execute(
            """
            INSERT INTO api_tokens (token, label, created_at, expires_at)
            VALUES (?, ?, ?, ?);
            """,
            (token, label, _now(), expires_at),
        )
        conn.commit()
        return token

    @staticmethod
    def validate(conn: sqlite3.Connection, token: str) -> bool:
        row = conn.execute(
            """
            SELECT id, expires_at, active FROM api_tokens
            WHERE token = ?;
            """,
            (token,),
        ).fetchone()
        if not row:
            return False
        if not row["active"]:
            return False
        if row["expires_at"]:
            if _now() > row["expires_at"]:
                return False
        # Update last_used
        conn.execute(
            "UPDATE api_tokens SET last_used = ? WHERE token = ?;",
            (_now(), token),
        )
        conn.commit()
        return True

    @staticmethod
    def revoke(conn: sqlite3.Connection, token: str) -> None:
        conn.execute(
            "UPDATE api_tokens SET active = 0 WHERE token = ?;", (token,)
        )
        conn.commit()

    @staticmethod
    def list_active(conn: sqlite3.Connection) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT id, label, created_at, expires_at, last_used
            FROM api_tokens WHERE active = 1
            ORDER BY created_at DESC;
            """
        ).fetchall()
        return _rows_to_list(rows)


# ---------------------------------------------------------------------------
# Goal queries
# ---------------------------------------------------------------------------

class GoalQueries:

    @staticmethod
    def insert(conn: sqlite3.Connection, metric: str, target: float,
               period: str = "daily",
               repo_id: Optional[int] = None) -> int:
        cursor = conn.execute(
            """
            INSERT INTO goals (metric, target, period, repo_id, created_at)
            VALUES (?, ?, ?, ?, ?);
            """,
            (metric, target, period, repo_id, _now()),
        )
        conn.commit()
        return cursor.lastrowid

    @staticmethod
    def list_active(conn: sqlite3.Connection) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT g.*, r.name as repo_name
            FROM goals g
            LEFT JOIN repos r ON g.repo_id = r.id
            WHERE g.active = 1
            ORDER BY g.created_at DESC;
            """
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def record_progress(conn: sqlite3.Connection, goal_id: int,
                        date: str, value: float) -> None:
        target_row = conn.execute(
            "SELECT target FROM goals WHERE id = ?;", (goal_id,)
        ).fetchone()
        met = 1 if target_row and value >= target_row["target"] else 0
        conn.execute(
            """
            INSERT INTO goal_progress (goal_id, date, value, met)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(goal_id, date) DO UPDATE SET
                value = excluded.value,
                met   = excluded.met;
            """,
            (goal_id, date, value, met),
        )
        conn.commit()

    @staticmethod
    def progress_for_goal(conn: sqlite3.Connection, goal_id: int,
                          days: int = 30) -> List[Dict]:
        rows = conn.execute(
            """
            SELECT * FROM goal_progress
            WHERE goal_id = ?
              AND date >= date('now', ?)
            ORDER BY date ASC;
            """,
            (goal_id, f"-{days} days"),
        ).fetchall()
        return _rows_to_list(rows)

    @staticmethod
    def deactivate(conn: sqlite3.Connection, goal_id: int) -> None:
        conn.execute(
            "UPDATE goals SET active = 0 WHERE id = ?;", (goal_id,)
        )
        conn.commit()