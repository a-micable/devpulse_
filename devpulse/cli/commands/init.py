# devpulse/cli/commands/init.py
# CLI command to initialize devpulse data directories and database.

import sys
from ...db.connection import initialize_database
from ...utils.config import Config
from ...utils.colors import Color

USAGE = """
devpulse init — bootstrap DevPulse configuration and database

Usage:
  devpulse init
""".strip()


class InitCommand:
    def __init__(self, conn, config: Config):
        self.conn = conn
        self.config = config

    def run(self, argv: list) -> None:
        if argv and argv[0] in ("--help", "-h"):
            print(USAGE)
            return

        self.config.ensure_data_dir()
        self.config.ensure_reports_dir()
        initialize_database(self.config.db_path)

        print(Color.green("✓ DevPulse initialized."))
        print(f"  DB path:    {self.config.db_path}")
        print(f"  Data dir:   {self.config.data_dir}")
