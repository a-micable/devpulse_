# devpulse/cli/commands/analyze.py
# CLI commands for repo analysis.
# devpulse analyze run   [--repo <path>] [--full]
# devpulse analyze summary [--repo <id|path>]
# devpulse analyze diff  [--repo <id>] [--days <n>]
# devpulse analyze duplicates [--repo <path>]

import sys
import json
from ...core.analyzer import RepoAnalyzer
from ...core.metrics  import MetricsSummary, DuplicateDetector, MetricsTrend
from ...db.queries    import RepoQueries
from ...utils.colors  import Color


USAGE = """
devpulse analyze — repo code analysis

Subcommands:
  run          [--repo <path>] [--full]      Run full analysis on a repo
  summary      [--repo <id|path>]            Print metrics summary
  diff         [--repo <id>] [--days <n>]    Show activity trend
  duplicates   [--repo <path>]               Detect duplicate code blocks
""".strip()


class AnalyzeCommand:
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
            "run":        self._run,
            "summary":    self._summary,
            "diff":       self._diff,
            "duplicates": self._duplicates,
        }

        if sub not in dispatch:
            print(Color.red(f"Unknown subcommand: analyze {sub!r}"))
            sys.exit(1)

        dispatch[sub](rest)

    def _run(self, argv: list) -> None:
        repo_path = None
        full      = False
        i = 0
        while i < len(argv):
            if argv[i] == "--repo" and i + 1 < len(argv):
                repo_path = argv[i + 1]; i += 2
            elif argv[i] == "--full":
                full = True; i += 1
            else:
                i += 1

        if not repo_path:
            import os
            repo_path = os.getcwd()

        import os
        repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(repo_path):
            print(Color.red(f"Directory not found: {repo_path}"))
            sys.exit(1)

        # Get or create repo record
        repo = RepoQueries.get_by_path(self.conn, repo_path)
        if not repo:
            print(Color.yellow(f"Repo not tracked. Register it first:"))
            print(f"  devpulse repo add {repo_path}")
            sys.exit(1)

        print(f"Analyzing {Color.cyan(repo_path)}…")

        analyzer = RepoAnalyzer(
            repo_path=repo_path,
            conn=self.conn,
            config=self.config,
        )
        result = analyzer.run(force_refresh=full)

        print(Color.green(f"✓ Analysis complete"))
        print(f"  Commits scanned:  {result.total_commits:,}")
        print(f"  Files analyzed:   {result.file_count:,}")
        print(f"  Lines of code:    {result.total_lines_added:,}")
        print(f"  Contributors:     {result.contributor_count}")
        print(f"  Current streak:   {result.current_streak} days")

    def _summary(self, argv: list) -> None:
        repo_ref = None
        i = 0
        while i < len(argv):
            if argv[i] == "--repo" and i + 1 < len(argv):
                repo_ref = argv[i + 1]; i += 2
            else:
                i += 1

        repo_id = self._resolve_repo(repo_ref)
        if repo_id is None:
            return

        summary = MetricsSummary(repo_id, self.conn).build()

        print(Color.bold(f"Metrics summary — repo {repo_id}"))
        print(f"  Files:            {summary.get('file_count', 0):,}")
        print(f"  Total LOC:        {summary.get('total_loc', 0):,}")
        print(f"  Functions:        {summary.get('total_functions', 0):,}")
        print(f"  Classes:          {summary.get('total_classes', 0):,}")
        print(f"  Avg complexity:   {summary.get('avg_complexity', 0):.2f}")
        print(f"  Comment ratio:    {summary.get('overall_comment_ratio', 0):.1%}")

        langs = summary.get("language_breakdown", [])
        if langs:
            print()
            print("  Language breakdown:")
            for lb in langs[:6]:
                bar_len = int(lb["total_loc"] / max(summary["total_loc"], 1) * 20)
                bar = "█" * bar_len
                print(f"    {lb['language']:<12} {lb['total_loc']:>7,} LOC  {bar}")

    def _diff(self, argv: list) -> None:
        repo_ref = None
        days     = 30
        i = 0
        while i < len(argv):
            if argv[i] == "--repo" and i + 1 < len(argv):
                repo_ref = argv[i + 1]; i += 2
            elif argv[i] == "--days" and i + 1 < len(argv):
                days = int(argv[i + 1]); i += 2
            else:
                i += 1

        repo_id = self._resolve_repo(repo_ref)
        if repo_id is None:
            return

        trend = MetricsTrend(repo_id, self.conn)
        tv    = trend.commits_today_vs_yesterday()
        wv    = trend.this_week_vs_last_week()

        def _arrow(t):
            if t == "up":   return Color.green("↑")
            if t == "down": return Color.red("↓")
            return Color.muted("→")

        print(Color.bold("Activity trend"))
        print(f"  Today vs yesterday:  "
              f"{tv['today']} vs {tv['yesterday']} commits  "
              f"{_arrow(tv['trend'])} {tv['delta_pct']:+.1f}%")
        print(f"  This week vs last:   "
              f"{wv['this_week']} vs {wv['last_week']} commits  "
              f"{_arrow(wv['trend'])} {wv['delta_pct']:+.1f}%")

    def _duplicates(self, argv: list) -> None:
        repo_path = None
        i = 0
        while i < len(argv):
            if argv[i] == "--repo" and i + 1 < len(argv):
                repo_path = argv[i + 1]; i += 2
            else:
                i += 1

        if not repo_path:
            import os
            repo_path = os.getcwd()

        repo = RepoQueries.get_by_path(self.conn, repo_path)
        if not repo:
            print(Color.yellow("Repo not tracked. Run: devpulse repo add <path>"))
            sys.exit(1)

        print(f"Scanning for duplicates in {Color.cyan(repo_path)}…")
        detector = DuplicateDetector(repo["id"], repo_path, self.conn, self.config)
        report   = detector.run()

        groups = report.get("duplicate_groups", 0)
        scanned = report.get("files_scanned", 0)
        print(f"  Files scanned:       {scanned}")
        print(f"  Duplicate groups:    {groups}")

        if groups == 0:
            print(Color.green("  No duplicate blocks found."))
            return

        for i, group in enumerate(report.get("duplicates", [])[:10], 1):
            locs = group.get("locations", [])
            print(f"\n  Group {i} ({len(locs)} occurrences, "
                  f"{group.get('line_count', '?')} lines):")
            for loc in locs:
                print(f"    {loc.get('file', '?')}:{loc.get('start_line', '?')}")

    def _resolve_repo(self, ref: str) -> int:
        if ref is None:
            import os
            ref = os.getcwd()

        import os
        abs_path = os.path.abspath(ref)
        if os.path.isdir(abs_path):
            repo = RepoQueries.get_by_path(self.conn, abs_path)
            if repo:
                return repo["id"]

        try:
            rid = int(ref)
            repo = RepoQueries.get_by_id(self.conn, rid)
            if repo:
                return repo["id"]
        except (ValueError, TypeError):
            pass

        print(Color.red(f"Repo not found: {ref}"))
        print("Run 'devpulse repo list' to see tracked repos.")
        return None