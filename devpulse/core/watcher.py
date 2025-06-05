# devpulse/core/watcher.py
# Filesystem watcher — detects active coding sessions by monitoring file saves.
# Uses inotify on Linux via /proc or a polling fallback on macOS/Windows.
# No watchdog, no inotify library — hand-written using os/select/threading.

import os
import sys
import time
import threading
import logging
import queue
import fnmatch
from typing import List, Dict, Optional, Callable, Set

logger = logging.getLogger(__name__)

# File event types
EVENT_MODIFIED = "modified"
EVENT_CREATED  = "created"
EVENT_DELETED  = "deleted"

# Default patterns to ignore — saves on noise
DEFAULT_IGNORE_PATTERNS = [
    "*.pyc", "*.pyo", "__pycache__",
    "*.swp", "*.swo", "*~",           # vim temp files
    ".git", ".svn", ".hg",
    "node_modules", ".venv", "venv",
    "*.log", "*.lock",
    ".DS_Store", "Thumbs.db",
    "*.tmp", "*.temp",
    "dist", "build", ".next",
    "*.min.js", "*.min.css",
]


# ---------------------------------------------------------------------------
# File event
# ---------------------------------------------------------------------------

class FileEvent:
    __slots__ = ("path", "event_type", "timestamp", "size")

    def __init__(self, path: str, event_type: str,
                 timestamp: Optional[float] = None):
        self.path = path
        self.event_type = event_type
        self.timestamp = timestamp or time.time()
        self.size = 0
        try:
            self.size = os.path.getsize(path)
        except OSError:
            pass

    def __repr__(self) -> str:
        return f"FileEvent({self.event_type}, {os.path.basename(self.path)})"


# ---------------------------------------------------------------------------
# Ignore filter
# ---------------------------------------------------------------------------

class IgnoreFilter:
    """
    Filters file paths against a list of glob patterns.
    Patterns are matched against both the filename and any path component.
    Also reads .gitignore-style patterns from a .devpulseignore file if present.
    """

    def __init__(self, patterns: Optional[List[str]] = None,
                 root_path: str = ""):
        self.patterns: List[str] = list(DEFAULT_IGNORE_PATTERNS)
        if patterns:
            self.patterns.extend(patterns)
        self.root_path = root_path
        self._load_devpulseignore(root_path)

    def _load_devpulseignore(self, root: str) -> None:
        if not root:
            return
        ignore_file = os.path.join(root, ".devpulseignore")
        if not os.path.isfile(ignore_file):
            return
        try:
            with open(ignore_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self.patterns.append(line)
            logger.debug(f"Loaded .devpulseignore from {ignore_file}")
        except OSError:
            pass

    def should_ignore(self, path: str) -> bool:
        """Return True if this path should be ignored."""
        # Normalize path separators
        path = path.replace("\\", "/")
        basename = os.path.basename(path)
        parts = path.split("/")

        for pattern in self.patterns:
            pattern = pattern.strip("/")
            # Match against basename
            if fnmatch.fnmatch(basename, pattern):
                return True
            # Match against any component
            for part in parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
            # Match against relative path
            if self.root_path:
                try:
                    rel = os.path.relpath(path, self.root_path)
                    if fnmatch.fnmatch(rel, pattern):
                        return True
                except ValueError:
                    pass

        return False

    def is_binary(self, path: str) -> bool:
        """Quick binary check — read first 4KB and look for null bytes."""
        try:
            with open(path, "rb") as f:
                chunk = f.read(4096)
                return b"\x00" in chunk
        except OSError:
            return True


# ---------------------------------------------------------------------------
# Debouncer — collapses rapid successive events for the same file
# ---------------------------------------------------------------------------

class Debouncer:
    """
    Collapses multiple rapid file events into a single event.
    When a file is saved rapidly (e.g. editor auto-save), we only want
    to fire one event after the activity settles.

    Implementation: tracks last event time per path. A background thread
    fires settled events after debounce_ms milliseconds of silence.
    """

    def __init__(self, debounce_ms: int = 300,
                 callback: Optional[Callable[[FileEvent], None]] = None):
        self.debounce_ms = debounce_ms
        self.callback = callback
        self._pending: Dict[str, FileEvent] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="debouncer"
        )
        self._thread.start()

    def push(self, event: FileEvent) -> None:
        """Accept an event — may be delayed or collapsed."""
        with self._lock:
            self._pending[event.path] = event

    def _flush_loop(self) -> None:
        threshold = self.debounce_ms / 1000.0
        while not self._stop.is_set():
            time.sleep(0.05)  # Check every 50ms
            now = time.time()
            to_fire = []
            with self._lock:
                expired = [
                    path for path, ev in self._pending.items()
                    if now - ev.timestamp >= threshold
                ]
                for path in expired:
                    to_fire.append(self._pending.pop(path))

            for event in to_fire:
                if self.callback:
                    try:
                        self.callback(event)
                    except Exception as e:
                        logger.error(f"Debouncer callback error: {e}")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def flush_all(self) -> None:
        """Force-fire all pending events immediately."""
        with self._lock:
            to_fire = list(self._pending.values())
            self._pending.clear()
        for event in to_fire:
            if self.callback:
                try:
                    self.callback(event)
                except Exception as e:
                    logger.error(f"Debouncer flush error: {e}")


# ---------------------------------------------------------------------------
# Polling watcher — works on all platforms
# ---------------------------------------------------------------------------

class PollingWatcher:
    """
    Filesystem watcher using mtime polling.
    Scans watched directories every poll_interval_ms milliseconds.
    Less efficient than inotify but works universally.
    Emits FileEvent objects to a callback.
    """

    def __init__(self, paths: List[str],
                 callback: Callable[[FileEvent], None],
                 poll_interval_ms: int = 1000,
                 ignore_filter: Optional[IgnoreFilter] = None):
        self.paths = [os.path.abspath(p) for p in paths]
        self.callback = callback
        self.interval = poll_interval_ms / 1000.0
        self.ignore = ignore_filter or IgnoreFilter()
        self._mtimes: Dict[str, float] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="polling-watcher"
        )

    def start(self) -> "PollingWatcher":
        # Take initial snapshot
        self._snapshot()
        self._thread.start()
        logger.debug(
            f"PollingWatcher started on {len(self.paths)} path(s), "
            f"interval={self.interval*1000:.0f}ms"
        )
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        logger.debug("PollingWatcher stopped.")

    def _snapshot(self) -> None:
        """Build initial mtime snapshot."""
        for root_path in self.paths:
            for dirpath, dirnames, filenames in os.walk(root_path):
                # Prune ignored directories
                dirnames[:] = [
                    d for d in dirnames
                    if not self.ignore.should_ignore(os.path.join(dirpath, d))
                ]
                for fname in filenames:
                    full = os.path.join(dirpath, fname)
                    if self.ignore.should_ignore(full):
                        continue
                    if self.ignore.is_binary(full):
                        continue
                    try:
                        self._mtimes[full] = os.path.getmtime(full)
                    except OSError:
                        pass

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._check()
            self._stop.wait(timeout=self.interval)

    def _check(self) -> None:
        current: Dict[str, float] = {}

        for root_path in self.paths:
            if not os.path.isdir(root_path):
                continue
            for dirpath, dirnames, filenames in os.walk(root_path):
                dirnames[:] = [
                    d for d in dirnames
                    if not self.ignore.should_ignore(os.path.join(dirpath, d))
                    and not d.startswith(".")
                ]
                for fname in filenames:
                    full = os.path.join(dirpath, fname)
                    if self.ignore.should_ignore(full):
                        continue
                    try:
                        mtime = os.path.getmtime(full)
                        current[full] = mtime
                    except OSError:
                        continue

        # Detect modified and created files
        for path, mtime in current.items():
            if path not in self._mtimes:
                self._emit(FileEvent(path, EVENT_CREATED))
            elif mtime != self._mtimes[path]:
                self._emit(FileEvent(path, EVENT_MODIFIED))

        # Detect deleted files
        for path in list(self._mtimes.keys()):
            if path not in current:
                self._emit(FileEvent(path, EVENT_DELETED))

        self._mtimes = current

    def _emit(self, event: FileEvent) -> None:
        try:
            self.callback(event)
        except Exception as e:
            logger.error(f"Watcher callback error: {e}", exc_info=True)

    def add_path(self, path: str) -> None:
        abs_path = os.path.abspath(path)
        if abs_path not in self.paths:
            self.paths.append(abs_path)
            # Snapshot the new path
            if os.path.isdir(abs_path):
                for dirpath, dirnames, filenames in os.walk(abs_path):
                    for fname in filenames:
                        full = os.path.join(dirpath, fname)
                        try:
                            self._mtimes[full] = os.path.getmtime(full)
                        except OSError:
                            pass

    def remove_path(self, path: str) -> None:
        abs_path = os.path.abspath(path)
        if abs_path in self.paths:
            self.paths.remove(abs_path)
            self._mtimes = {
                k: v for k, v in self._mtimes.items()
                if not k.startswith(abs_path)
            }


# ---------------------------------------------------------------------------
# Linux inotify watcher (optional fast path)
# ---------------------------------------------------------------------------

class InotifyWatcher:
    """
    Linux-only file watcher using inotify via ctypes.
    Falls back gracefully to PollingWatcher on import failure.
    Only used when sys.platform == 'linux'.

    Inotify event flags we care about:
      IN_MODIFY  = 0x00000002
      IN_CREATE  = 0x00000100
      IN_DELETE  = 0x00000200
      IN_MOVED_TO = 0x00000080
    """

    IN_MODIFY   = 0x00000002
    IN_CREATE   = 0x00000100
    IN_DELETE   = 0x00000200
    IN_MOVED_TO = 0x00000080
    IN_MASK     = IN_MODIFY | IN_CREATE | IN_DELETE | IN_MOVED_TO

    def __init__(self, paths: List[str],
                 callback: Callable[[FileEvent], None],
                 ignore_filter: Optional[IgnoreFilter] = None):
        self.paths = [os.path.abspath(p) for p in paths]
        self.callback = callback
        self.ignore = ignore_filter or IgnoreFilter()
        self._fd: int = -1
        self._wd_to_path: Dict[int, str] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._available = self._check_available()

    def _check_available(self) -> bool:
        if sys.platform != "linux":
            return False
        try:
            import ctypes
            self._libc = ctypes.CDLL("libc.so.6", use_errno=True)
            return True
        except OSError:
            return False

    def start(self) -> bool:
        """
        Returns True if inotify watcher started successfully.
        Returns False if not available (caller should use PollingWatcher).
        """
        if not self._available:
            return False

        import ctypes
        self._fd = self._libc.inotify_init()
        if self._fd < 0:
            logger.warning("inotify_init failed, falling back to polling")
            return False

        for path in self.paths:
            self._watch_tree(path)

        self._thread = threading.Thread(
            target=self._read_loop, daemon=True, name="inotify-watcher"
        )
        self._thread.start()
        logger.debug(
            f"InotifyWatcher started, watching {len(self._wd_to_path)} dirs"
        )
        return True

    def _watch_tree(self, root: str) -> None:
        import ctypes
        for dirpath, dirnames, _ in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if not self.ignore.should_ignore(os.path.join(dirpath, d))
                and not d.startswith(".")
            ]
            wd = self._libc.inotify_add_watch(
                self._fd,
                dirpath.encode(),
                ctypes.c_uint32(self.IN_MASK)
            )
            if wd > 0:
                self._wd_to_path[wd] = dirpath

    def _read_loop(self) -> None:
        """
        Read inotify events from the fd.
        Event structure: [wd(4), mask(4), cookie(4), len(4), name(len)]
        """
        import ctypes
        import struct
        import select

        EVENT_HEADER_SIZE = 16  # 4 ints

        while not self._stop.is_set():
            # Use select with timeout so we can check _stop
            try:
                readable, _, _ = select.select([self._fd], [], [], 0.5)
            except (ValueError, OSError):
                break

            if not readable:
                continue

            try:
                raw = os.read(self._fd, 4096)
            except OSError:
                break

            offset = 0
            while offset < len(raw):
                if offset + EVENT_HEADER_SIZE > len(raw):
                    break

                wd, mask, cookie, name_len = struct.unpack_from(
                    "iIII", raw, offset
                )
                offset += EVENT_HEADER_SIZE

                name = ""
                if name_len > 0:
                    name_bytes = raw[offset: offset + name_len]
                    name = name_bytes.rstrip(b"\x00").decode(
                        "utf-8", errors="replace"
                    )
                    offset += name_len

                dir_path = self._wd_to_path.get(wd, "")
                if not dir_path or not name:
                    continue

                full_path = os.path.join(dir_path, name)

                if self.ignore.should_ignore(full_path):
                    continue
                if os.path.isdir(full_path):
                    # New directory created — add watch
                    self._watch_tree(full_path)
                    continue

                if mask & self.IN_MODIFY or mask & self.IN_MOVED_TO:
                    event_type = EVENT_MODIFIED
                elif mask & self.IN_CREATE:
                    event_type = EVENT_CREATED
                elif mask & self.IN_DELETE:
                    event_type = EVENT_DELETED
                else:
                    continue

                self._emit(FileEvent(full_path, event_type))

    def _emit(self, event: FileEvent) -> None:
        try:
            self.callback(event)
        except Exception as e:
            logger.error(f"InotifyWatcher callback error: {e}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
        logger.debug("InotifyWatcher stopped.")


# ---------------------------------------------------------------------------
# Idle detector
# ---------------------------------------------------------------------------

class IdleDetector:
    """
    Detects when coding activity has gone idle.
    Tracks the timestamp of the last file event and fires an idle callback
    when the gap exceeds idle_timeout_seconds.
    Also fires a resume callback on the first event after an idle period.
    """

    def __init__(self, idle_timeout_seconds: int = 600,
                 on_idle: Optional[Callable[[], None]] = None,
                 on_resume: Optional[Callable[[], None]] = None):
        self.timeout = idle_timeout_seconds
        self.on_idle = on_idle
        self.on_resume = on_resume

        self._last_event_time: float = time.time()
        self._is_idle: bool = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._check_loop, daemon=True, name="idle-detector"
        )
        self._thread.start()

    def record_activity(self) -> None:
        """Call this whenever a file event occurs."""
        with self._lock:
            was_idle = self._is_idle
            self._last_event_time = time.time()
            self._is_idle = False

        if was_idle and self.on_resume:
            try:
                self.on_resume()
            except Exception as e:
                logger.error(f"IdleDetector on_resume error: {e}")

    def _check_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(timeout=30)  # Check every 30 seconds
            with self._lock:
                if self._is_idle:
                    continue
                elapsed = time.time() - self._last_event_time
                if elapsed >= self.timeout:
                    self._is_idle = True
                    should_fire = True
                else:
                    should_fire = False

            if should_fire and self.on_idle:
                logger.info(
                    f"Idle detected after {self.timeout}s of inactivity."
                )
                try:
                    self.on_idle()
                except Exception as e:
                    logger.error(f"IdleDetector on_idle error: {e}")

    def is_idle(self) -> bool:
        with self._lock:
            return self._is_idle

    def seconds_since_activity(self) -> float:
        with self._lock:
            return time.time() - self._last_event_time

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# Focus score calculator
# ---------------------------------------------------------------------------

class FocusScoreCalculator:
    """
    Computes a focus score (0.0 to 1.0) for a session based on
    the pattern of file save events over time.

    High focus: frequent, evenly-spaced saves with few context switches.
    Low focus: sporadic saves, long idle gaps, many different files touched.

    Score components:
      - save_frequency_score: how regularly saves happened
      - context_switch_penalty: how many different files were touched
      - idle_gap_penalty: proportion of session time that was idle
    """

    IDLE_THRESHOLD_S = 300  # 5 minutes between saves = idle gap

    def __init__(self, session_duration_s: int,
                 save_timestamps: List[float],
                 file_paths: List[str]):
        self.duration = max(session_duration_s, 1)
        self.timestamps = sorted(save_timestamps)
        self.file_paths = file_paths

    def calculate(self) -> float:
        if not self.timestamps:
            return 0.0
        if self.duration < 60:
            return 0.5  # Too short to score meaningfully

        score = 1.0

        # Component 1: Save frequency
        # Ideal: at least 1 save per 10 minutes of session
        expected_saves = max(1, self.duration / 600)
        actual_saves = len(self.timestamps)
        frequency_ratio = min(actual_saves / expected_saves, 1.0)
        score *= 0.4 + (0.6 * frequency_ratio)

        # Component 2: Idle gap penalty
        # Each gap > IDLE_THRESHOLD_S reduces score
        if len(self.timestamps) > 1:
            total_idle = sum(
                max(0, self.timestamps[i] - self.timestamps[i-1] - self.IDLE_THRESHOLD_S)
                for i in range(1, len(self.timestamps))
            )
            idle_ratio = min(total_idle / self.duration, 1.0)
            score *= (1.0 - idle_ratio * 0.5)

        # Component 3: Context switch penalty
        # Touching many files rapidly suggests scattered work
        unique_files = len(set(self.file_paths))
        if unique_files > 1:
            # Each file beyond the first slightly reduces score
            # Up to 5 files is fine (working on a feature)
            # 20+ files suggests scattered/unfocused work
            switch_penalty = min((unique_files - 1) / 20.0, 0.3)
            score *= (1.0 - switch_penalty)

        return round(max(0.0, min(score, 1.0)), 3)


# ---------------------------------------------------------------------------
# Main watcher facade
# ---------------------------------------------------------------------------

class CodeWatcher:
    """
    Top-level facade that selects the best available watcher backend
    (inotify on Linux, polling elsewhere), wires up debouncing and
    idle detection, and exposes a simple start/stop interface.

    Usage:
        watcher = CodeWatcher(
            paths=["/home/user/projects/myapp"],
            on_event=lambda e: print(e),
            on_idle=lambda: print("gone idle"),
            on_resume=lambda: print("back"),
            idle_timeout_s=600,
        )
        watcher.start()
        # ... later ...
        watcher.stop()
    """

    def __init__(self,
                 paths: List[str],
                 on_event: Optional[Callable[[FileEvent], None]] = None,
                 on_idle: Optional[Callable[[], None]] = None,
                 on_resume: Optional[Callable[[], None]] = None,
                 idle_timeout_s: int = 600,
                 debounce_ms: int = 300,
                 ignore_patterns: Optional[List[str]] = None,
                 poll_interval_ms: int = 1000):
        self.paths = paths
        self.on_event = on_event
        self.idle_timeout_s = idle_timeout_s
        self.debounce_ms = debounce_ms
        self.poll_interval_ms = poll_interval_ms

        self.ignore = IgnoreFilter(
            patterns=ignore_patterns,
            root_path=paths[0] if paths else ""
        )

        self._event_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._save_timestamps: List[float] = []
        self._touched_files: List[str] = []
        self._lock = threading.Lock()

        self._debouncer = Debouncer(
            debounce_ms=debounce_ms,
            callback=self._on_debounced_event
        )
        self._idle_detector = IdleDetector(
            idle_timeout_seconds=idle_timeout_s,
            on_idle=on_idle,
            on_resume=on_resume,
        )
        self._watcher: Optional[object] = None
        self._started = False

    def _on_raw_event(self, event: FileEvent) -> None:
        """Raw callback from the watcher backend — passes to debouncer."""
        if self.ignore.should_ignore(event.path):
            return
        if self.ignore.is_binary(event.path):
            return
        self._debouncer.push(event)

    def _on_debounced_event(self, event: FileEvent) -> None:
        """Called after debounce settles — records activity and fires user callback."""
        self._idle_detector.record_activity()

        with self._lock:
            self._save_timestamps.append(event.timestamp)
            self._touched_files.append(event.path)

        if self.on_event:
            try:
                self.on_event(event)
            except Exception as e:
                logger.error(f"CodeWatcher on_event error: {e}")

    def start(self) -> "CodeWatcher":
        if self._started:
            return self

        # Try inotify first on Linux
        if sys.platform == "linux":
            inotify = InotifyWatcher(
                self.paths, self._on_raw_event, self.ignore
            )
            if inotify.start():
                self._watcher = inotify
                logger.info("Using inotify watcher (Linux).")
            else:
                logger.info("inotify unavailable, falling back to polling.")

        if self._watcher is None:
            polling = PollingWatcher(
                self.paths, self._on_raw_event,
                poll_interval_ms=self.poll_interval_ms,
                ignore_filter=self.ignore,
            )
            polling.start()
            self._watcher = polling
            logger.info(
                f"Using polling watcher "
                f"(interval={self.poll_interval_ms}ms)."
            )

        self._started = True
        logger.info(
            f"CodeWatcher started on {len(self.paths)} path(s). "
            f"Idle timeout: {self.idle_timeout_s}s."
        )
        return self

    def stop(self) -> None:
        if not self._started:
            return
        self._debouncer.flush_all()
        self._debouncer.stop()
        self._idle_detector.stop()
        if self._watcher:
            self._watcher.stop()
        self._started = False
        logger.info("CodeWatcher stopped.")

    def add_path(self, path: str) -> None:
        if hasattr(self._watcher, "add_path"):
            self._watcher.add_path(path)

    def is_idle(self) -> bool:
        return self._idle_detector.is_idle()

    def seconds_since_activity(self) -> float:
        return self._idle_detector.seconds_since_activity()

    def session_focus_score(self, session_duration_s: int) -> float:
        with self._lock:
            timestamps = list(self._save_timestamps)
            files = list(self._touched_files)
        calc = FocusScoreCalculator(session_duration_s, timestamps, files)
        return calc.calculate()

    def event_count(self) -> int:
        with self._lock:
            return len(self._save_timestamps)

    def unique_files_touched(self) -> int:
        with self._lock:
            return len(set(self._touched_files))

    def reset_stats(self) -> None:
        """Reset event tracking — call at session start."""
        with self._lock:
            self._save_timestamps.clear()
            self._touched_files.clear()

    def __enter__(self) -> "CodeWatcher":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()