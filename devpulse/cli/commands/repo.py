# devpulse/cli/commands/repo.py
# CLI commands for repo management.
# devpulse repo add    <path> [--name <name>]
# devpulse repo list
# devpulse repo remove <id|path>
# devpulse repo sync   <id|path> [--full]

import sys
import os
from ...core.analyzer import RepoAnalyzer
from ...db.queries   import RepoQueries, CommitQueries
from ...utils.colors import Color
from ...utils.dates  import format_relative


USAGE = """
devpulse repo — manage tracked repositories

Subcommands:
  add     <path> [--name <name>]   Register a repo
  list                              List all tracked repos
  remove  <id|path>                 Remove a repo from tracking
  sync    <id|path> [--full]        Sync commits and metrics from git
""".strip()


class RepoCommand:
    def __init__(self, conn, config):
        self.conn   = conn
        self.config = config

    def run(self, argv: list) -> None:
        if not argv or argv[0] in ("--help", "-h"):
            print(USAGE)
            return

        sub  = argv[0]
        rest = argv[1:]

        dispatch = {
            "add":    self._add,
            "list":   self._list,
            "remove": self._remove,
            "sync":   self._sync,
        }

        if sub not in dispatch:
            print(Color.red(f"Unknown subcommand: repo {sub!r}"))
            sys.exit(1)

        dispatch[sub](rest)

    def _add(self, argv: list) -> None:
        if not argv:
            print(Color.red("Usage: devpulse repo add <path> [--name <name>]"))
            sys.exit(1)

        path = os.path.abspath(argv[0])
        name = None
        i = 1
        while i < len(argv):
            if argv[i] == "--name" and i + 1 < len(argv):
                name = argv[i + 1]; i += 2
            else:
                i += 1

        if not os.path.isdir(path):
            print(Color.red(f"Directory not found: {path}"))
            sys.exit(1)

        existing = RepoQueries.get_by_path(self.conn, path)
        if existing:
            print(Color.yellow(f"Repo already tracked (id={existing['id']})"))
            return

        if not name:
            name = os.path.basename(path)

        # Try to detect remote URL from git
        remote = _detect_remote(path)

        repo_id = RepoQueries.insert(
            self.conn, name=name, path=path, remote_url=remote
        )
        print(Color.green(f"✓ Repo added (id={repo_id})"))
        print(f"  Name:   {name}")
        print(f"  Path:   {path}")
        if remote:
            print(f"  Remote: {remote}")
        print(Color.muted("  Run 'devpulse repo sync' to index commits."))

    def _list(self, argv: list) -> None:
        repos = RepoQueries.list_all(self.conn)
        if not repos:
            print(Color.muted("No repos tracked yet."))
            print("  devpulse repo add <path>")
            return

        print(Color.bold(f"Tracked repos ({len(repos)}):"))
        for repo in repos:
            commit_count = CommitQueries.count_by_repo(self.conn, repo["id"])
            active_str   = "" if repo.get("active", 1) else Color.muted(" [inactive]")
            print(f"  [{repo['id']}] {repo['name']:<24} "
                  f"{commit_count:>6,} commits  "
                  f"{repo.get('path', '')}{active_str}")

    def _remove(self, argv: list) -> None:
        if not argv:
            print(Color.red("Usage: devpulse repo remove <id|path>"))
            sys.exit(1)

        repo = self._resolve(argv[0])
        if not repo:
            return

        confirm = input(
            f"Remove '{repo['name']}' from devpulse? "
            f"(This does not delete the directory.) [y/N] "
        ).strip().lower()

        if confirm != "y":
            print("Aborted.")
            return

        RepoQueries.delete(self.conn, repo["id"])
        print(Color.green(f"✓ Repo '{repo['name']}' removed."))

    def _sync(self, argv: list) -> None:
        if not argv:
            print(Color.red("Usage: devpulse repo sync <id|path> [--full]"))
            sys.exit(1)

        full = "--full" in argv
        ref  = next((a for a in argv if not a.startswith("--")), None)
        if not ref:
            print(Color.red("Provide a repo id or path."))
            sys.exit(1)

        repo = self._resolve(ref)
        if not repo:
            return

        repo_path = repo.get("path", "")
        if not os.path.isdir(repo_path):
            print(Color.red(f"Repo directory not found: {repo_path}"))
            sys.exit(1)

        print(f"Syncing {Color.cyan(repo_path)}…")

        analyzer = RepoAnalyzer(
            repo_path=repo_path,
            conn=self.conn,
            config=self.config,
        )
        result = analyzer.run(force_refresh=full)

        print(Color.green("✓ Sync complete"))
        print(f"  Commits indexed: {result.total_commits:,}")
        print(f"  Files analyzed:  {result.file_count:,}")
        print(f"  Contributors:    {result.contributor_count}")

    def _resolve(self, ref: str):
        abs_path = os.path.abspath(ref)
        if os.path.isdir(abs_path):
            repo = RepoQueries.get_by_path(self.conn, abs_path)
            if repo:
                return repo

        try:
            rid  = int(ref)
            repo = RepoQueries.get_by_id(self.conn, rid)
            if repo:
                return repo
        except (ValueError, TypeError):
            pass

        print(Color.red(f"Repo not found: {ref!r}"))
        print("Run 'devpulse repo list' to see tracked repos.")
        return None


def _detect_remote(repo_path: str) -> str:
    try:
        import subprocess
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""