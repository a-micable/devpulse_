# devpulse/cli/__main__.py
# Entry point for the devpulse command line interface.

import sys

from devpulse.db.connection import open_connection
from devpulse.utils.config import Config
from devpulse.cli.commands.analyze import AnalyzeCommand
from devpulse.cli.commands.init import InitCommand
from devpulse.cli.commands.repo import RepoCommand
from devpulse.cli.commands.report import ReportCommand
from devpulse.cli.commands.track import TrackCommand

USAGE = """
devpulse — developer productivity tracker

Usage:
  devpulse init
  devpulse repo <subcommand> [...]
  devpulse analyze <subcommand> [...]
  devpulse report <subcommand> [...]
  devpulse track <subcommand> [...]

Use 'devpulse <command> --help' for command-specific usage.
""".strip()


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    command = argv[0]
    args = argv[1:]

    config = Config()
    conn = open_connection(config.db_path)

    dispatch = {
        "init": InitCommand(conn, config),
        "repo": RepoCommand(conn, config),
        "analyze": AnalyzeCommand(conn, config),
        "report": ReportCommand(conn, config),
        "track": TrackCommand(conn, config),
    }

    if command not in dispatch:
        print(f"Unknown command: {command!r}\n")
        print(USAGE)
        return 1

    dispatch[command].run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
