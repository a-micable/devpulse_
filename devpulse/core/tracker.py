# devpulse/core/tracker.py
# Session tracking daemon.
# Manages start/stop/pause/resume of coding sessions.
# Persists state via SQLite and a PID file for cross-process coordination.
# Integrates with CodeWatcher for auto-pause/resume on idle.
# No external deps — pure stdlib.

import os
import tempfile
import sys
import time
import json
import signal
import logging
import threading
import sqlite3
from typing import Optional, Dict, List, Any

from .watcher import CodeWatcher, FileEvent
from ..db.queries import (
    SessionQueries,
    HeartbeatQueries,
    TagQueries,
    RepoQueries,
)
from ..utils.dates import (
    now_iso,
    format_duration,
    format_relative,
    parse_iso,
    today_str,
)

logger = logging.getLogger(__name__)

# Default locations
DEFAULT_DATA_DIR  = os.path.join(tempfile.gettempdir(), "devpulse")
DEFAULT_PID_FILE  = os.path.join(DEFAULT_DATA_DIR, "tracker.pid")
DEFAULT_STATE_FILE = os.path.join(DEFAULT_DATA_DIR, "tracker.state")


# ---------------------------------------------------------------------------
# PID file management
# ---------------------------------------------------------------------------

class PidFile:
    """
    Manages a PID file for single-instance enforcement.
    Writes the current process PID on acquire, removes on release.
    Detects and cleans up orphaned PID files from crashed processes.
    """

    def __init__(self, path: str = DEFAULT_PID_FILE):
        self.path = path

    def acquire(self) -> bool:
        """
        Write our PID to the file.
        Returns False if another live process already holds it.
        """
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        existing = self.read()
        if existing is not None:
            if self._process_alive(existing):
                logger.warning(
                    f"PID file exists and process {existing} is alive."
                )
                return False
            else:
                logger.info(
                    f"Cleaning up orphaned PID file (pid={existing})."
                )
                self.release()

        try:
            with open(self.path, "w") as f:
                f.write(str(os.getpid()))
            return True
        except OSError as e:
            logger.error(f"Failed to write PID file {self.path}: {e}")
            return False

    def release(self) -> None:
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except OSError as e:
            logger.warning(f"Failed to remove PID file: {e}")

    def read(self) -> Optional[int]:
        try:
            with open(self.path, "r") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _process_alive(self, pid: int) -> bool:
        """Check if a process with this PID is still running."""
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except OSError:
            return False

    def is_held(self) -> bool:
        pid = self.read()
        if pid is None:
            return False
        return self._process_alive(pid)


# ---------------------------------------------------------------------------
# Session state — persisted to a JSON file for recovery
# ---------------------------------------------------------------------------

class SessionState:
    """
    Lightweight JSON state file tracking the active session.
    Written on every meaningful state change so that if the process
    crashes, we can recover the session from the heartbeat records.
    """

    FIELDS = [
        "session_id", "repo_id", "repo_path",
        "started_at", "paused_at", "is_paused",
        "tags", "notes",
    ]

    def __init__(self, path: str = DEFAULT_STATE_FILE):
        self.path = path
        self.session_id: Optional[int] = None
        self.repo_id: Optional[int] = None
        self.repo_path: str = ""
        self.started_at: str = ""
        self.paused_at: Optional[str] = None
        self.is_paused: bool = False
        self.tags: List[str] = []
        self.notes: str = ""

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {f: getattr(self, f) for f in self.FIELDS}
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.warning(f"Failed to save session state: {e}")

    def load(self) -> bool:
        """
        Load state from file. Returns True if loaded successfully.
        Returns False if no state file exists.
        """
        if not os.path.exists(self.path):
            return False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for field in self.FIELDS:
                if field in data:
                    setattr(self, field, data[field])
            return True
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to load session state: {e}")
            return False

    def clear(self) -> None:
        self.session_id = None
        self.repo_id = None
        self.repo_path = ""
        self.started_at = ""
        self.paused_at = None
        self.is_paused = False
        self.tags = []
        self.notes = ""
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except OSError:
            pass

    def is_active(self) -> bool:
        return self.session_id is not None and not self.is_paused

    def duration_so_far(self) -> int:
        """Return elapsed seconds since session start (excluding paused time)."""
        if not self.started_at:
            return 0
        start = parse_iso(self.started_at)
        if not start:
            return 0
        if self.is_paused and self.paused_at:
            end = parse_iso(self.paused_at) or __import__("datetime").datetime.now()
        else:
            end = __import__("datetime").datetime.now()
        return max(0, int((end - start).total_seconds()))


# ---------------------------------------------------------------------------
# Heartbeat writer — keeps the session alive in DB
# ---------------------------------------------------------------------------

class HeartbeatWriter:
    """
    Writes a heartbeat record to the DB every interval_s seconds
    while a session is active. This allows recovery of session duration
    even if the tracker crashes without a clean stop.
    """

    def __init__(self, conn: sqlite3.Connection,
                 interval_s: int = 30):
        self.conn = conn
        self.interval_s = interval_s
        self._session_id: Optional[int] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, session_id: int) -> None:
        self._session_id = session_id
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="heartbeat"
        )
        self._thread.start()
        logger.debug(f"HeartbeatWriter started for session {session_id}")

    def _loop(self) -> None:
        while not self._stop.wait(timeout=self.interval_s):
            if self._session_id is None:
                break
            try:
                HeartbeatQueries.insert(
                    self.conn,
                    session_id=self._session_id,
                    event_type="heartbeat",
                )
                logger.debug(
                    f"Heartbeat written for session {self._session_id}"
                )
            except Exception as e:
                logger.warning(f"Heartbeat write failed: {e}")

    def record_file_save(self, file_path: str) -> None:
        """Record a file-save event as a heartbeat."""
        if self._session_id is None:
            return
        try:
            HeartbeatQueries.insert(
                self.conn,
                session_id=self._session_id,
                file_path=file_path,
                event_type="save",
            )
        except Exception as e:
            logger.debug(f"Failed to record file save heartbeat: {e}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._session_id = None


# ---------------------------------------------------------------------------
# Session recovery
# ---------------------------------------------------------------------------

class SessionRecovery:
    """
    Recovers interrupted sessions from heartbeat records.
    Called at tracker startup to detect and close any sessions
    that were left open by a previous crash.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def recover(self) -> Optional[Dict]:
        """
        Find the most recent open session and attempt to close it
        based on the last heartbeat timestamp.
        Returns the recovered session dict if one was found, else None.
        """
        active = SessionQueries.get_active(self.conn)
        if not active:
            return None

        session_id = active["id"]
        logger.info(
            f"Found interrupted session {session_id} "
            f"(started {active.get('started_at', '?')}). Recovering..."
        )

        last_hb = HeartbeatQueries.last_for_session(self.conn, session_id)
        hb_count = HeartbeatQueries.count_for_session(self.conn, session_id)

        if last_hb:
            ended_at = last_hb.get("recorded_at", now_iso())
        else:
            ended_at = active.get("started_at", now_iso())

        started = parse_iso(active.get("started_at", ""))
        ended  = parse_iso(ended_at)
        if started and ended:
            duration_s = max(0, int((ended - started).total_seconds()))
        else:
            duration_s = 0

        SessionQueries.close(
            self.conn,
            session_id=session_id,
            ended_at=ended_at,
            duration_s=duration_s,
            focus_score=0.0,
            notes="[recovered from heartbeat]",
        )

        logger.info(
            f"Recovered session {session_id}: "
            f"duration={format_duration(duration_s)}, "
            f"heartbeats={hb_count}"
        )

        return {
            "session_id": session_id,
            "started_at": active.get("started_at"),
            "ended_at": ended_at,
            "duration_s": duration_s,
            "heartbeat_count": hb_count,
            "recovered": True,
        }


# ---------------------------------------------------------------------------
# Session summary builder
# ---------------------------------------------------------------------------

class SessionSummary:
    """
    Builds a human-readable and machine-readable summary
    for a completed session.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def build(self, session_id: int) -> Dict:
        session = SessionQueries.get_by_id(self.conn, session_id)
        if not session:
            return {}

        tags = TagQueries.list_for_session(self.conn, session_id)
        hb_count = HeartbeatQueries.count_for_session(self.conn, session_id)

        duration_s = session.get("duration_s") or 0
        focus = session.get("focus_score") or 0.0

        repo_name = ""
        if session.get("repo_id"):
            repo = RepoQueries.get_by_id(
                self.conn, session["repo_id"]
            )
            repo_name = repo.get("name", "") if repo else ""

        return {
            "session_id": session_id,
            "repo_name": repo_name,
            "started_at": session.get("started_at", ""),
            "ended_at": session.get("ended_at", ""),
            "duration_s": duration_s,
            "duration_human": format_duration(duration_s),
            "focus_score": round(focus, 3),
            "focus_label": _focus_label(focus),
            "heartbeat_count": hb_count,
            "tags": tags,
            "notes": session.get("notes", ""),
        }


def _focus_label(score: float) -> str:
    if score >= 0.8:
        return "excellent"
    if score >= 0.6:
        return "good"
    if score >= 0.4:
        return "moderate"
    if score >= 0.2:
        return "low"
    return "minimal"


# ---------------------------------------------------------------------------
# Main tracker
# ---------------------------------------------------------------------------

class SessionTracker:
    """
    Top-level session tracker.
    Manages the full lifecycle of a coding session:
      start → (auto-pause on idle) → (auto-resume on save) → stop

    Integrates:
      - PidFile for single-instance enforcement
      - SessionState for crash recovery
      - HeartbeatWriter for heartbeat persistence
      - CodeWatcher for file event detection
      - IdleDetector for auto-pause/resume
      - SessionRecovery for startup cleanup

    Designed to run as a background daemon started by `devpulse track start`
    and stopped by `devpulse track stop`.
    """

    def __init__(self, conn: sqlite3.Connection,
                 config=None,
                 pid_file: Optional[str] = None,
                 state_file: Optional[str] = None):
        self.conn = conn
        self.config = config

        # If a pid/state file was not provided, derive per-instance paths
        # from the config's db_path when available to avoid cross-test
        # collisions in shared temp directories.
        derived_dir = None
        if config and getattr(config, '_data', None):
            db_path = config._data.get('db_path')
            if db_path:
                derived_dir = os.path.dirname(db_path)

        base_dir = derived_dir or DEFAULT_DATA_DIR
        os.makedirs(base_dir, exist_ok=True)

        pid_path = pid_file or os.path.join(base_dir, f"tracker_{os.getpid()}.pid")
        state_path = state_file or os.path.join(base_dir, f"tracker_{os.getpid()}.state")

        self.pid = PidFile(pid_path)
        self.state = SessionState(state_path)
        self.heartbeat = HeartbeatWriter(
            conn,
            interval_s=(
                config.heartbeat_interval if config else 30
            ),
        )
        self._watcher: Optional[CodeWatcher] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, repo_path: Optional[str] = None,
              tags: Optional[List[str]] = None) -> Dict:
        """
        Start a new session.
        Returns a status dict.
        """
        # Check for existing active session
        existing = SessionQueries.get_active(self.conn)
        if existing:
            return {
                "ok": False,
                "error": "A session is already active.",
                "session_id": existing["id"],
            }

        # Check if PID is already held
        if self.pid.is_held():
            return {
                "ok": False,
                "error": "Tracker daemon is already running.",
            }

        # Recover any interrupted sessions first
        recovery = SessionRecovery(self.conn)
        recovery.recover()

        # Resolve repo
        repo_id = None
        if repo_path:
            repo_path = os.path.abspath(repo_path)
            existing_repo = RepoQueries.get_by_path(self.conn, repo_path)
            if existing_repo:
                repo_id = existing_repo["id"]

        # Create session record
        started_at = now_iso()
        session_id = SessionQueries.insert(
            self.conn, repo_id=repo_id, started_at=started_at
        )

        # Save state
        self.state.session_id = session_id
        self.state.repo_id = repo_id
        self.state.repo_path = repo_path or ""
        self.state.started_at = started_at
        self.state.tags = list(tags or [])
        self.state.is_paused = False
        self.state.save()

        # Write initial tags
        if tags:
            for tag in tags:
                TagQueries.insert(
                    self.conn, name=tag,
                    session_id=session_id, repo_id=repo_id
                )

        # Start heartbeat writer
        self.heartbeat.start(session_id)

        # Start file watcher if repo path given
        if repo_path and os.path.isdir(repo_path):
            self._start_watcher(repo_path)

        # Acquire PID file
        self.pid.acquire()

        logger.info(
            f"Session {session_id} started at {started_at}. "
            f"repo_id={repo_id}"
        )

        return {
            "ok": True,
            "session_id": session_id,
            "started_at": started_at,
            "repo_id": repo_id,
            "repo_path": repo_path,
        }

    def stop(self, notes: str = "") -> Dict:
        """
        Stop the active session and return a summary.
        """
        session_id = self.state.session_id
        if session_id is None:
            active = SessionQueries.get_active(self.conn)
            if active:
                session_id = active["id"]

        if session_id is None:
            return {"ok": False, "error": "No active session to stop."}

        # Stop watcher and heartbeat first
        self._stop_watcher()
        self.heartbeat.stop()

        # Compute final stats
        ended_at = now_iso()
        duration_s = self.state.duration_so_far()
        focus_score = 0.0

        if self._watcher:
            focus_score = self._watcher.session_focus_score(duration_s)

        # Close session in DB
        SessionQueries.close(
            self.conn,
            session_id=session_id,
            ended_at=ended_at,
            duration_s=duration_s,
            focus_score=focus_score,
            notes=notes,
        )

        # Build summary before clearing state
        summary_builder = SessionSummary(self.conn)
        summary = summary_builder.build(session_id)

        # Clean up
        self.state.clear()
        self.pid.release()

        logger.info(
            f"Session {session_id} stopped. "
            f"Duration: {format_duration(duration_s)}. "
            f"Focus: {_focus_label(focus_score)} ({focus_score:.2f})."
        )

        return {"ok": True, "summary": summary}

    def pause(self) -> Dict:
        """Manually pause the active session."""
        if not self.state.session_id:
            return {"ok": False, "error": "No active session."}
        if self.state.is_paused:
            return {"ok": False, "error": "Session is already paused."}

        self.state.is_paused = True
        self.state.paused_at = now_iso()
        self.state.save()

        if self._watcher:
            self._watcher.reset_stats()

        logger.info(f"Session {self.state.session_id} paused.")
        return {"ok": True, "paused_at": self.state.paused_at}

    def resume(self) -> Dict:
        """Manually resume a paused session."""
        if not self.state.session_id:
            return {"ok": False, "error": "No active session."}
        if not self.state.is_paused:
            return {"ok": False, "error": "Session is not paused."}

        self.state.is_paused = False
        self.state.paused_at = None
        self.state.save()

        logger.info(f"Session {self.state.session_id} resumed.")
        return {"ok": True, "resumed_at": now_iso()}

    def add_tag(self, tag: str) -> Dict:
        """Tag the currently active session."""
        if not self.state.session_id:
            return {"ok": False, "error": "No active session."}

        name = tag.strip()
        if not name:
            return {"ok": False, "error": "Tag name cannot be empty."}

        TagQueries.insert(
            self.conn, name=name,
            session_id=self.state.session_id,
            repo_id=self.state.repo_id,
        )
        if name not in self.state.tags:
            self.state.tags.append(name)
        self.state.save()

        logger.info(
            f"Tagged session {self.state.session_id} with '{name}'."
        )
        return {"ok": True, "tag": name}

    def status(self) -> Dict:
        """Return current tracker status — no side effects."""
        loaded = self.state.load()
        if not loaded or self.state.session_id is None:
            # Double-check DB
            active = SessionQueries.get_active(self.conn)
            if not active:
                return {"running": False}
            return {
                "running": True,
                "session_id": active["id"],
                "started_at": active.get("started_at", ""),
                "duration_s": 0,
                "duration_human": "unknown",
                "is_paused": False,
                "tags": [],
            }

        duration_s = self.state.duration_so_far()
        return {
            "running": True,
            "session_id": self.state.session_id,
            "repo_path": self.state.repo_path,
            "started_at": self.state.started_at,
            "started_relative": format_relative(self.state.started_at),
            "duration_s": duration_s,
            "duration_human": format_duration(duration_s),
            "is_paused": self.state.is_paused,
            "paused_at": self.state.paused_at,
            "tags": self.state.tags,
            "notes": self.state.notes,
            "watcher_active": self._watcher is not None,
            "files_touched": (
                self._watcher.unique_files_touched()
                if self._watcher else 0
            ),
            "save_events": (
                self._watcher.event_count()
                if self._watcher else 0
            ),
        }

    def run_daemon(self) -> None:
        """
        Block until SIGTERM or SIGINT.
        Used when the tracker is running as a background daemon.
        """
        def _handle_signal(signum, frame):
            logger.info(f"Received signal {signum}, stopping tracker...")
            self._stop_event.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        logger.info("Tracker daemon running. Waiting for stop signal.")
        self._stop_event.wait()
        self.stop()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_watcher(self, repo_path: str) -> None:
        idle_s = (
            self.config.idle_timeout * 60 if self.config else 600
        )
        self._watcher = CodeWatcher(
            paths=[repo_path],
            on_event=self._on_file_event,
            on_idle=self._on_idle,
            on_resume=self._on_resume,
            idle_timeout_s=idle_s,
            debounce_ms=300,
        )
        self._watcher.start()

    def _stop_watcher(self) -> None:
        if self._watcher:
            self._watcher.stop()
            self._watcher = None

    def _on_file_event(self, event: FileEvent) -> None:
        """Called for every debounced file save."""
        if self.state.is_paused:
            return
        self.heartbeat.record_file_save(event.path)
        logger.debug(f"File event: {event.event_type} {event.path}")

    def _on_idle(self) -> None:
        """Auto-pause when idle timeout is reached."""
        if not self.state.session_id or self.state.is_paused:
            return
        logger.info("Auto-pausing session due to inactivity.")
        self.pause()

    def _on_resume(self) -> None:
        """Auto-resume when file activity detected after idle."""
        if not self.state.session_id or not self.state.is_paused:
            return
        logger.info("Auto-resuming session — file activity detected.")
        self.resume()