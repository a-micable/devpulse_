# devpulse/cli/commands/report.py
# CLI commands for report generation and goal management.
# devpulse report weekly  [--format md|csv|json] [--output <path>]
# devpulse report export  [--format csv|json|md] [--days <n>]
# devpulse report goals   list | add | remove

import sys
import os
from ...core.reporter import Reporter
from ...db.queries    import GoalQueries
from ...utils.colors  import Color
from ...utils.dates   import format_duration, today_str


USAGE = """
devpulse report — generate reports and manage goals

Subcommands:
  weekly   [--format md|csv|json] [--output <path>]   Generate weekly summary
  export   [--format csv|json|md] [--days <n>]        Export session data
  goals    list | add <metric> <target> <period> | remove <id>
""".strip()


class ReportCommand:
    def __init__(self, conn, config):
        self.conn     = conn
        self.config   = config
        self.reporter = Reporter(conn, config=config)

    def run(self, argv: list) -> None:
        if not argv or argv[0] in ("--help", "-h"):
            print(USAGE)
            return

        sub  = argv[0]
        rest = argv[1:]

        dispatch = {
            "weekly": self._weekly,
            "export": self._export,
            "goals":  self._goals,
        }

        if sub not in dispatch:
            print(Color.red(f"Unknown subcommand: report {sub!r}"))
            sys.exit(1)

        dispatch[sub](rest)

    def _weekly(self, argv: list) -> None:
        fmt    = "markdown"
        output = None
        i = 0
        while i < len(argv):
            if argv[i] == "--format" and i + 1 < len(argv):
                fmt = argv[i + 1]; i += 2
            elif argv[i] == "--output" and i + 1 < len(argv):
                output = argv[i + 1]; i += 2
            else:
                i += 1

        path = self.reporter.export_sessions(fmt=fmt)
        if path:
            print(Color.green(f"✓ Weekly report written to:"))
            print(f"  {path}")
        else:
            print(Color.yellow("No data for this period."))

    def _export(self, argv: list) -> None:
        fmt  = "csv"
        days = 30
        i = 0
        while i < len(argv):
            if argv[i] == "--format" and i + 1 < len(argv):
                fmt = argv[i + 1]; i += 2
            elif argv[i] == "--days" and i + 1 < len(argv):
                days = int(argv[i + 1]); i += 2
            else:
                i += 1

        path = self.reporter.export_sessions(fmt=fmt, days=days)
        if path:
            print(Color.green(f"✓ Export written to:"))
            print(f"  {path}")
        else:
            print(Color.yellow("No sessions to export."))

    def _goals(self, argv: list) -> None:
        if not argv or argv[0] == "list":
            self._goals_list()
        elif argv[0] == "add":
            self._goals_add(argv[1:])
        elif argv[0] == "remove":
            self._goals_remove(argv[1:])
        else:
            print(Color.red(f"Unknown goals subcommand: {argv[0]!r}"))

    def _goals_list(self) -> None:
        goals = GoalQueries.list_active(self.conn)
        if not goals:
            print(Color.muted("No active goals."))
            return

        print(Color.bold("Active goals:"))
        for g in goals:
            print(f"  [{g['id']}] {g['metric']:<20} "
                  f"target={g['target']}  period={g['period']}")

    def _goals_add(self, argv: list) -> None:
        if len(argv) < 3:
            print(Color.red("Usage: devpulse report goals add <metric> <target> <period>"))
            print("  Periods: daily | weekly | monthly")
            sys.exit(1)

        metric = argv[0]
        try:
            target = float(argv[1])
        except ValueError:
            print(Color.red(f"Invalid target: {argv[1]!r} — must be a number"))
            sys.exit(1)

        period = argv[2]
        if period not in ("daily", "weekly", "monthly"):
            print(Color.red(f"Invalid period: {period!r} — use daily/weekly/monthly"))
            sys.exit(1)

        goal_id = GoalQueries.insert(
            self.conn,
            metric=metric,
            target=target,
            period=period,
        )
        print(Color.green(f"✓ Goal added (id={goal_id})"))
        print(f"  {metric}: {target} per {period}")

    def _goals_remove(self, argv: list) -> None:
        if not argv:
            print(Color.red("Usage: devpulse report goals remove <id>"))
            sys.exit(1)
        try:
            goal_id = int(argv[0])
        except ValueError:
            print(Color.red(f"Invalid goal id: {argv[0]!r}"))
            sys.exit(1)

        GoalQueries.deactivate(self.conn, goal_id)
        print(Color.green(f"✓ Goal {goal_id} deactivated."))