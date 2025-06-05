# devpulse/core/exporter.py
# Exports session and commit data to CSV, JSON, and Markdown.
# Used by both the web API (/api/export/*) and the CLI (devpulse report export).

from __future__ import annotations

import csv
import io
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..db.queries import SessionQueries, CommitQueries, RepoQueries
from ..utils.dates import format_duration, format_date

log = logging.getLogger(__name__)


class SessionExporter:
    """Exports session rows to CSV, JSON, or Markdown."""

    def __init__(self, conn, export_dir: str = "/tmp"):
        self.conn       = conn
        self.export_dir = export_dir
        os.makedirs(export_dir, exist_ok=True)

    def export(self, fmt: str = "csv", days: int = 30) -> Optional[str]:
        sessions = SessionQueries.list_recent(self.conn, limit=10000)
        if not sessions:
            return None

        sessions = [dict(s) for s in sessions]

        ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        name = f"sessions_{ts}.{fmt if fmt != 'markdown' else 'md'}"
        path = os.path.join(self.export_dir, name)

        if fmt == "csv":
            self._write_csv(sessions, path)
        elif fmt == "json":
            self._write_json(sessions, path)
        elif fmt in ("markdown", "md"):
            self._write_markdown(sessions, path)
        else:
            log.warning("Unknown export format: %s", fmt)
            return None

        log.info("Exported %d sessions to %s", len(sessions), path)
        return path

    # ------------------------------------------------------------------

    def _write_csv(self, sessions: List[Dict], path: str) -> None:
        fields = [
            "id", "repo_name", "started_at", "ended_at",
            "duration_s", "duration_human", "focus_score",
            "heartbeat_count", "notes", "tags",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for s in sessions:
                row = dict(s)
                tags = row.get("tags") or []
                row["tags"] = ",".join(tags) if isinstance(tags, list) else tags
                writer.writerow(row)

    def _write_json(self, sessions: List[Dict], path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sessions": sessions, "count": len(sessions)}, f, indent=2, default=str)

    def _write_markdown(self, sessions: List[Dict], path: str) -> None:
        lines = [
            "# Session Export",
            "",
            f"Generated: {datetime.utcnow().isoformat()}Z",
            f"Total sessions: {len(sessions)}",
            "",
            "| # | Repo | Started | Duration | Focus | Tags |",
            "|---|------|---------|----------|-------|------|",
        ]
        for s in sessions:
            tags = s.get("tags") or []
            tag_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
            lines.append(
                f"| {s.get('id','')} "
                f"| {s.get('repo_name', '—')} "
                f"| {format_date(s.get('started_at'), with_time=True)} "
                f"| {s.get('duration_human', format_duration(s.get('duration_s')))} "
                f"| {round((s.get('focus_score') or 0) * 100)}% "
                f"| {tag_str} |"
            )
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


class RepoExporter:
    """Exports per-repo commit metrics to CSV."""

    def __init__(self, conn, export_dir: str = "/tmp"):
        self.conn       = conn
        self.export_dir = export_dir
        os.makedirs(export_dir, exist_ok=True)

    def export_commits(self, repo_id: int) -> Optional[str]:
        repo = RepoQueries.get_by_id(self.conn, repo_id)
        if not repo:
            return None

        commits = CommitQueries.list_by_repo(self.conn, repo_id, limit=50000)
        if not commits:
            return None

        ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        name = f"repo_{repo_id}_commits_{ts}.csv"
        path = os.path.join(self.export_dir, name)

        fields = [
            "hash", "author", "author_email", "committed_at",
            "message", "lines_added", "lines_removed", "files_changed",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for c in commits:
                writer.writerow(dict(c))

        return path