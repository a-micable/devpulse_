# devpulse/cli/commands/track.py
# CLI commands for session tracking.
# devpulse track start [--repo <path>] [--tag <tag>]
# devpulse track stop [--note <text>]
# devpulse track pause
# devpulse track resume
# devpulse track status
# devpulse track tag <name>

import sys
from ...core.tracker import SessionTracker
from ...utils.colors import Color
from ...utils.dates  import format_duration, format_relative


USAGE = """
devpulse track — manage coding sessions

Subcommands:
  start   [--repo <path>] [--tag <tag>...]   Start a new session
  stop    [--note <text>]                    Stop the active session
  pause                                      Pause the active session
  resume                                     Resume a paused session
  status                                     Show current session status
  tag     <name>                             Tag the active session
""".strip()


class TrackCommand:
    def __init__(self, conn, config):
        self.conn    = conn
        self.config  = config
        self.tracker = SessionTracker(conn, config)

    def run(self, argv: list) -> None:
        if not argv or argv[0] in ("--help", "-h"):
            print(USAGE)
            return

        sub  = argv[0]
        rest = argv[1:]

        dispatch = {
            "start":  self._start,
            "stop":   self._stop,
            "pause":  self._pause,
            "resume": self._resume,
            "status": self._status,
            "tag":    self._tag,
        }

        if sub not in dispatch:
            print(Color.red(f"Unknown subcommand: track {sub!r}"))
            print("Run 'devpulse track --help' for usage.")
            sys.exit(1)

        dispatch[sub](rest)

    def _start(self, argv: list) -> None:
        repo_path = None
        tags      = []
        i = 0
        while i < len(argv):
            if argv[i] == "--repo" and i + 1 < len(argv):
                repo_path = argv[i + 1]; i += 2
            elif argv[i] == "--tag" and i + 1 < len(argv):
                tags.append(argv[i + 1]); i += 2
            else:
                i += 1

        result = self.tracker.start(repo_path=repo_path, tags=tags)

        if not result.get("ok"):
            print(Color.yellow(f"Warning: {result.get('error', 'Unknown error')}"))
            return

        print(Color.green("✓ Session started"))
        print(f"  ID:   {result['session_id']}")
        if repo_path:
            print(f"  Repo: {repo_path}")
        if tags:
            print(f"  Tags: {', '.join(tags)}")
        print(Color.muted("  Run 'devpulse track stop' when done."))

    def _stop(self, argv: list) -> None:
        notes = ""
        i = 0
        while i < len(argv):
            if argv[i] == "--note" and i + 1 < len(argv):
                notes = argv[i + 1]; i += 2
            else:
                i += 1

        result = self.tracker.stop(notes=notes)

        if not result.get("ok"):
            print(Color.yellow(result.get("error", "No active session.")))
            return

        s = result.get("summary", {})
        print(Color.green("✓ Session stopped"))
        print(f"  Duration:    {s.get('duration_human', '0s')}")
        print(f"  Focus score: {s.get('focus_label', '—')} "
              f"({int((s.get('focus_score', 0)) * 100)}%)")
        if s.get("tags"):
            print(f"  Tags:        {', '.join(s['tags'])}")

    def _pause(self, argv: list) -> None:
        result = self.tracker.pause()
        if not result.get("ok"):
            print(Color.yellow(result.get("error", "")))
        else:
            print(Color.green("✓ Session paused."))

    def _resume(self, argv: list) -> None:
        result = self.tracker.resume()
        if not result.get("ok"):
            print(Color.yellow(result.get("error", "")))
        else:
            print(Color.green("✓ Session resumed."))

    def _status(self, argv: list) -> None:
        result = self.tracker.status()
        if not result.get("running"):
            print(Color.muted("No active session."))
            return

        is_paused = result.get("is_paused")
        state     = Color.yellow("paused") if is_paused else Color.green("active")
        print(f"Session {result.get('session_id')}  [{state}]")
        print(f"  Started:   {format_relative(result.get('started_at', ''))}")
        print(f"  Duration:  {result.get('duration_human', '0s')}")
        if result.get("repo_path"):
            print(f"  Repo:      {result['repo_path']}")
        if result.get("tags"):
            print(f"  Tags:      {', '.join(result['tags'])}")
        print(f"  Files touched: {result.get('files_touched', 0)}")
        print(f"  Save events:   {result.get('save_events', 0)}")

    def _tag(self, argv: list) -> None:
        if not argv:
            print(Color.red("Usage: devpulse track tag <name>"))
            sys.exit(1)
        name   = argv[0]
        result = self.tracker.add_tag(name)
        if not result.get("ok"):
            print(Color.yellow(result.get("error", "")))
        else:
            print(Color.green(f"✓ Tagged session with '{name}'."))