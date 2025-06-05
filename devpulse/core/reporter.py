# devpulse/core/reporter.py
# Export engine. Produces CSV, JSON, and Markdown reports
# from session and repo analysis data.
# No external templating libraries — all string formatting by hand.
# No pandas — raw csv module only.

import os
import csv
import json
import io
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from ..db.queries import (
    SessionQueries,
    CommitQueries,
    MetricsQueries,
    RepoQueries,
    GoalQueries,
)
from ..utils.dates import (
    DateRange,
    format_duration,
    format_duration_long,
    iso_to_display,
    today_str,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base exporter
# ---------------------------------------------------------------------------

class BaseExporter:
    """
    Base class for all exporters.
    Provides output path resolution and directory creation.
    """

    def __init__(self, output_dir: str = ""):
        self.output_dir = output_dir or os.path.expanduser(
            "~/.devpulse/reports"
        )

    def _ensure_dir(self) -> None:
        os.makedirs(self.output_dir, exist_ok=True)

    def _output_path(self, filename: str) -> str:
        self._ensure_dir()
        return os.path.join(self.output_dir, filename)

    def _timestamp_suffix(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# CSV exporter
# ---------------------------------------------------------------------------

class CsvExporter(BaseExporter):
    """
    Exports sessions and repo commit data to CSV files.
    Uses Python's stdlib csv module — no pandas.
    """

    def export_sessions(self,
                        rows: List[Dict],
                        filename: Optional[str] = None) -> str:
        """
        Export a list of session dicts to CSV.
        Returns the output file path.
        """
        fname = filename or f"sessions_{self._timestamp_suffix()}.csv"
        path = self._output_path(fname)

        if not rows:
            logger.warning("No session rows to export.")
            return ""

        fieldnames = [
            "session_id", "repo_name", "started_at", "ended_at",
            "duration_s", "duration_human", "focus_score", "tags", "notes",
        ]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, extrasaction="ignore"
            )
            writer.writeheader()
            for row in rows:
                # Normalize tags field
                tags = row.get("tags", [])
                if isinstance(tags, list):
                    row = dict(row)
                    row["tags"] = "|".join(tags)
                # Add human duration if missing
                if "duration_human" not in row:
                    row["duration_human"] = format_duration(
                        row.get("duration_s") or 0
                    )
                writer.writerow(row)

        logger.info(f"Exported {len(rows)} sessions to {path}")
        return path

    def export_commits(self,
                       rows: List[Dict],
                       filename: Optional[str] = None) -> str:
        """Export commit cache rows to CSV."""
        fname = filename or f"commits_{self._timestamp_suffix()}.csv"
        path = self._output_path(fname)

        if not rows:
            return ""

        fieldnames = [
            "hash", "author", "author_email", "committed_at",
            "message", "lines_added", "lines_removed",
            "files_changed", "is_merge",
        ]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)

        logger.info(f"Exported {len(rows)} commits to {path}")
        return path

    def export_metrics(self,
                       rows: List[Dict],
                       filename: Optional[str] = None) -> str:
        """Export file metrics to CSV."""
        fname = filename or f"metrics_{self._timestamp_suffix()}.csv"
        path = self._output_path(fname)

        if not rows:
            return ""

        fieldnames = [
            "file_path", "language", "loc", "blank_lines",
            "comment_lines", "function_count", "class_count",
            "complexity", "churn_score", "last_computed",
        ]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=fieldnames, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)

        logger.info(f"Exported {len(rows)} file metrics to {path}")
        return path

    def sessions_to_string(self, rows: List[Dict]) -> str:
        """
        Render sessions as a CSV string (no file write).
        Used by the API export endpoint to stream directly.
        """
        if not rows:
            return ""

        output = io.StringIO()
        fieldnames = [
            "session_id", "repo_name", "started_at", "ended_at",
            "duration_s", "duration_human", "focus_score", "tags", "notes",
        ]
        writer = csv.DictWriter(
            output, fieldnames=fieldnames, extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            row = dict(row)
            tags = row.get("tags", [])
            if isinstance(tags, list):
                row["tags"] = "|".join(tags)
            if "duration_human" not in row:
                row["duration_human"] = format_duration(
                    row.get("duration_s") or 0
                )
            writer.writerow(row)

        return output.getvalue()


# ---------------------------------------------------------------------------
# JSON exporter
# ---------------------------------------------------------------------------

class JsonExporter(BaseExporter):
    """
    Exports data as pretty-printed JSON.
    Wraps results in a standard envelope with metadata.
    """

    def _envelope(self, data: Any, record_type: str,
                  count: int) -> Dict:
        return {
            "meta": {
                "exported_at": datetime.now().isoformat(),
                "record_type": record_type,
                "count": count,
                "generator": "devpulse",
            },
            "data": data,
        }

    def export(self, data: Any, record_type: str,
               filename: Optional[str] = None) -> str:
        fname = filename or f"{record_type}_{self._timestamp_suffix()}.json"
        path = self._output_path(fname)

        count = len(data) if isinstance(data, (list, dict)) else 1
        envelope = self._envelope(data, record_type, count)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(envelope, f, indent=2, default=str)

        logger.info(f"Exported {record_type} ({count} records) to {path}")
        return path

    def to_string(self, data: Any, record_type: str) -> str:
        count = len(data) if isinstance(data, (list, dict)) else 1
        envelope = self._envelope(data, record_type, count)
        return json.dumps(envelope, indent=2, default=str)


# ---------------------------------------------------------------------------
# Markdown report engine
# ---------------------------------------------------------------------------

class MarkdownReporter(BaseExporter):
    """
    Generates human-readable Markdown reports.
    Two main templates: weekly summary and per-repo analysis.
    All formatting is hand-written string manipulation.
    """

    # ------------------------------------------------------------------
    # Weekly summary report
    # ------------------------------------------------------------------

    def weekly_summary(self,
                       conn,
                       repo_id: Optional[int] = None,
                       filename: Optional[str] = None) -> str:
        """
        Generate a weekly summary report covering the last 7 days.
        Writes to file and returns the path.
        """
        dr = DateRange.last_n_days(7)
        start = dr.start_iso()
        end   = dr.end_iso()

        sessions = SessionQueries.list_by_date_range(
            conn, start, end, limit=500
        )
        daily_totals = SessionQueries.total_duration_by_date(
            conn, start, end
        )
        avg_focus = SessionQueries.average_focus_score(conn, days=7)

        total_seconds = sum(
            s.get("duration_s") or 0 for s in sessions
        )
        session_count = len(sessions)

        lines = []
        lines += self._weekly_header(
            dr, total_seconds, session_count, avg_focus
        )
        lines += self._daily_breakdown_section(daily_totals)
        lines += self._session_list_section(sessions[:20])

        if repo_id:
            lines += self._repo_commit_section(conn, repo_id, start, end)

        content = "\n".join(lines)
        fname = filename or f"weekly_{today_str()}.md"
        path = self._output_path(fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Weekly summary written to {path}")
        return path

    def _weekly_header(self, dr: DateRange,
                       total_s: int, sessions: int,
                       avg_focus: float) -> List[str]:
        lines = [
            f"# Weekly Development Summary",
            f"",
            f"**Period:** {dr.start.strftime('%B %d')} – "
            f"{dr.end.strftime('%B %d, %Y')}",
            f"",
            f"## Overview",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total coding time | {format_duration_long(total_s)} |",
            f"| Sessions completed | {sessions} |",
            f"| Avg session length | "
            f"{format_duration(total_s // max(sessions, 1))} |",
            f"| Avg focus score | {avg_focus:.1%} |",
            f"",
        ]
        return lines

    def _daily_breakdown_section(self,
                                  daily: List[Dict]) -> List[str]:
        if not daily:
            return ["## Daily Breakdown", "", "_No data for this period._", ""]

        lines = [
            "## Daily Breakdown",
            "",
            "| Date | Coding Time | Sessions |",
            "|------|-------------|----------|",
        ]
        for row in daily:
            date_display = iso_to_display(row.get("date", ""))
            duration = format_duration(row.get("total_seconds") or 0)
            count = row.get("session_count", 0)
            lines.append(f"| {date_display} | {duration} | {count} |")

        lines.append("")
        return lines

    def _session_list_section(self,
                               sessions: List[Dict]) -> List[str]:
        if not sessions:
            return ["## Sessions", "", "_No sessions recorded._", ""]

        lines = [
            "## Sessions",
            "",
            "| # | Repo | Start | Duration | Focus |",
            "|---|------|-------|----------|-------|",
        ]
        for i, s in enumerate(sessions, 1):
            repo = s.get("repo_name") or "—"
            start = iso_to_display(s.get("started_at", ""),
                                   include_time=True)
            dur = format_duration(s.get("duration_s") or 0)
            focus = f"{(s.get('focus_score') or 0):.0%}"
            lines.append(f"| {i} | {repo} | {start} | {dur} | {focus} |")

        lines.append("")
        return lines

    def _repo_commit_section(self, conn,
                              repo_id: int,
                              start: str, end: str) -> List[str]:
        commits_by_day = CommitQueries.commits_per_day(
            conn, repo_id, start, end
        )
        total_commits = sum(r.get("count", 0) for r in commits_by_day)

        if not commits_by_day:
            return []

        lines = [
            "## Commit Activity",
            "",
            f"**Total commits this week:** {total_commits}",
            "",
            "| Date | Commits |",
            "|------|---------|",
        ]
        for row in commits_by_day:
            date_display = iso_to_display(row.get("date", ""))
            lines.append(
                f"| {date_display} | {row.get('count', 0)} |"
            )

        lines.append("")
        return lines

    # ------------------------------------------------------------------
    # Per-repo analysis report
    # ------------------------------------------------------------------

    def repo_analysis(self, conn,
                      analysis_result,
                      filename: Optional[str] = None) -> str:
        """
        Generate a full repo analysis report from an AnalysisResult object.
        Returns the output file path.
        """
        r = analysis_result
        lines = []
        lines += self._repo_header(r)
        lines += self._repo_overview_table(r)
        lines += self._repo_authors_section(r)
        lines += self._repo_hotspots_section(r)
        lines += self._repo_languages_section(r)
        lines += self._repo_release_section(r)
        lines += self._repo_message_quality_section(r)

        if r.warnings:
            lines += self._warnings_section(r.warnings)

        content = "\n".join(lines)
        safe_name = (r.repo_name or "repo").replace("/", "_").replace(" ", "_")
        fname = filename or f"analysis_{safe_name}_{today_str()}.md"
        path = self._output_path(fname)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Repo analysis report written to {path}")
        return path

    def _repo_header(self, r) -> List[str]:
        return [
            f"# Repository Analysis: {r.repo_name}",
            f"",
            f"> Generated by devpulse on "
            f"{datetime.now().strftime('%B %d, %Y at %H:%M')}",
            f"",
            f"**Path:** `{r.repo_path}`",
            f"**Branch:** `{r.current_branch}`",
            f"**Remote:** {r.remote_url or '_(none)_'}",
            f"",
        ]

    def _repo_overview_table(self, r) -> List[str]:
        from ..utils.dates import format_relative
        lines = [
            "## Overview",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total commits | {r.total_commits:,} |",
            f"| Merge commits | {r.merge_commits:,} |",
            f"| Contributors | {r.contributor_count} |",
            f"| Active days | {r.active_days:,} |",
            f"| Current streak | {r.current_streak} day{'s' if r.current_streak != 1 else ''} |",
            f"| Longest streak | {r.longest_streak} day{'s' if r.longest_streak != 1 else ''} |",
            f"| First commit | {iso_to_display(r.first_commit_date)} |",
            f"| Last commit | {iso_to_display(r.last_commit_date)} |",
            f"| Lines added | {r.total_lines_added:,} |",
            f"| Lines removed | {r.total_lines_removed:,} |",
            f"| Avg message length | {r.avg_message_length:.0f} chars |",
            f"",
        ]
        return lines

    def _repo_authors_section(self, r) -> List[str]:
        if not r.authors:
            return []

        lines = [
            "## Contributors",
            "",
            "| Author | Commits | Lines Added | Lines Removed | Active Days |",
            "|--------|---------|-------------|---------------|-------------|",
        ]
        for a in r.authors[:15]:
            lines.append(
                f"| {a.get('name', '?')} "
                f"| {a.get('commit_count', 0):,} "
                f"| {a.get('lines_added', 0):,} "
                f"| {a.get('lines_removed', 0):,} "
                f"| {a.get('active_days', 0)} |"
            )
        lines.append("")
        return lines

    def _repo_hotspots_section(self, r) -> List[str]:
        if not r.hotspots:
            return []

        lines = [
            "## Code Hotspots",
            "",
            "_Files with the highest churn-complexity score — "
            "most likely to contain bugs or need refactoring._",
            "",
            "| File | Language | LOC | Complexity | Score |",
            "|------|----------|-----|------------|-------|",
        ]
        for h in r.hotspots[:15]:
            fname = os.path.basename(h.get("file", "?"))
            lines.append(
                f"| `{fname}` "
                f"| {h.get('language', '?')} "
                f"| {h.get('loc', 0):,} "
                f"| {h.get('complexity', 0):.1f} "
                f"| {h.get('hotspot_score', 0):.3f} |"
            )
        lines.append("")
        return lines

    def _repo_languages_section(self, r) -> List[str]:
        if not r.language_breakdown:
            return []

        total_loc = sum(
            lb.get("total_loc", 0) for lb in r.language_breakdown
        )

        lines = [
            "## Language Breakdown",
            "",
            "| Language | Files | LOC | Share |",
            "|----------|-------|-----|-------|",
        ]
        for lb in r.language_breakdown:
            lang_loc = lb.get("total_loc", 0)
            share = (lang_loc / total_loc * 100) if total_loc > 0 else 0
            lines.append(
                f"| {lb.get('language', '?')} "
                f"| {lb.get('file_count', 0)} "
                f"| {lang_loc:,} "
                f"| {share:.1f}% |"
            )
        lines.append("")
        return lines

    def _repo_release_section(self, r) -> List[str]:
        if not r.releases:
            return []

        lines = [
            "## Releases",
            "",
            f"**Average days between releases:** "
            f"{r.avg_days_between_releases:.0f}",
            "",
            "| Tag | Date | Days Since Previous |",
            "|-----|------|---------------------|",
        ]
        for rel in r.releases[-10:]:
            days = rel.get("days_since_last", 0)
            days_str = str(days) if days > 0 else "_(first)_"
            lines.append(
                f"| `{rel.get('tag', '?')}` "
                f"| {iso_to_display(rel.get('date', ''))} "
                f"| {days_str} |"
            )
        lines.append("")
        return lines

    def _repo_message_quality_section(self, r) -> List[str]:
        kw = r.message_keyword_counts
        if not kw:
            return []

        lines = [
            "## Commit Message Patterns",
            "",
            f"**Average message length:** {r.avg_message_length:.0f} characters  ",
            f"**Short messages (<10 chars):** "
            f"{r.short_message_ratio:.0%} of commits",
            "",
            "### Top Commit Keywords",
            "",
            "| Keyword | Count |",
            "|---------|-------|",
        ]
        sorted_kw = sorted(kw.items(), key=lambda x: x[1], reverse=True)
        for word, count in sorted_kw[:10]:
            lines.append(f"| `{word}` | {count} |")

        lines.append("")
        return lines

    def _warnings_section(self, warnings: List[str]) -> List[str]:
        lines = [
            "## Warnings",
            "",
        ]
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")
        return lines

    def repo_analysis_to_string(self, conn,
                                 analysis_result) -> str:
        """Return the report as a string without writing to file."""
        r = analysis_result
        lines = []
        lines += self._repo_header(r)
        lines += self._repo_overview_table(r)
        lines += self._repo_authors_section(r)
        lines += self._repo_hotspots_section(r)
        lines += self._repo_languages_section(r)
        lines += self._repo_release_section(r)
        lines += self._repo_message_quality_section(r)
        if r.warnings:
            lines += self._warnings_section(r.warnings)
        return "\n".join(lines)

    def weekly_summary_to_string(self, conn,
                                  repo_id: Optional[int] = None) -> str:
        """Return weekly summary as string without writing to file."""
        dr = DateRange.last_n_days(7)
        start = dr.start_iso()
        end   = dr.end_iso()

        sessions = SessionQueries.list_by_date_range(
            conn, start, end, limit=500
        )
        daily_totals = SessionQueries.total_duration_by_date(
            conn, start, end
        )
        avg_focus = SessionQueries.average_focus_score(conn, days=7)

        total_seconds = sum(
            s.get("duration_s") or 0 for s in sessions
        )
        session_count = len(sessions)

        lines = []
        lines += self._weekly_header(dr, total_seconds, session_count, avg_focus)
        lines += self._daily_breakdown_section(daily_totals)
        lines += self._session_list_section(sessions[:20])

        if repo_id:
            lines += self._repo_commit_section(conn, repo_id, start, end)

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# High-level report dispatcher
# ---------------------------------------------------------------------------

class Reporter:
    """
    Top-level reporter that dispatches to CsvExporter, JsonExporter,
    or MarkdownReporter based on the requested format.
    Called by CLI commands and API export endpoints.
    """

    FORMATS = ("csv", "json", "markdown", "md")

    def __init__(self, conn, output_dir: str = "",
                 config=None):
        self.conn = conn
        out = output_dir or (
            config.get_str("reporter", "output_dir")
            if config else ""
        )
        self.csv      = CsvExporter(out)
        self.json     = JsonExporter(out)
        self.markdown = MarkdownReporter(out)

    def export_sessions(self, fmt: str = "csv",
                        days: int = 30,
                        repo_id: Optional[int] = None) -> str:
        """
        Export sessions in the requested format.
        Returns file path or string content for API responses.
        """
        dr = DateRange.last_n_days(days)
        sessions = SessionQueries.list_by_date_range(
            self.conn,
            dr.start_iso(), dr.end_iso(),
            limit=10000,
        )

        fmt = fmt.lower()
        if fmt == "csv":
            return self.csv.export_sessions(sessions)
        if fmt == "json":
            return self.json.export(sessions, "sessions")
        if fmt in ("markdown", "md"):
            return self.markdown.weekly_summary(self.conn)

        raise ValueError(f"Unknown format: {fmt!r}. Use csv/json/markdown.")

    def export_sessions_string(self, fmt: str = "csv",
                                days: int = 30) -> str:
        """
        Return export as a string (for API streaming).
        Does not write to disk.
        """
        dr = DateRange.last_n_days(days)
        sessions = SessionQueries.list_by_date_range(
            self.conn,
            dr.start_iso(), dr.end_iso(),
            limit=10000,
        )

        fmt = fmt.lower()
        if fmt == "csv":
            return self.csv.sessions_to_string(sessions)
        if fmt == "json":
            return self.json.to_string(sessions, "sessions")
        if fmt in ("markdown", "md"):
            return self.markdown.weekly_summary_to_string(self.conn)

        raise ValueError(f"Unknown format: {fmt!r}")

    def export_repo_analysis(self, analysis_result,
                              fmt: str = "markdown") -> str:
        fmt = fmt.lower()
        if fmt == "json":
            return self.json.export(
                analysis_result.to_dict(), "repo_analysis"
            )
        if fmt in ("markdown", "md"):
            return self.markdown.repo_analysis(
                self.conn, analysis_result
            )
        if fmt == "csv":
            metrics = MetricsQueries.list_by_repo(
                self.conn,
                analysis_result.repo_id or 0,
                limit=10000,
            )
            return self.csv.export_metrics(metrics)

        raise ValueError(f"Unknown format: {fmt!r}")

    def export_repo_analysis_string(self, analysis_result,
                                     fmt: str = "markdown") -> str:
        fmt = fmt.lower()
        if fmt == "json":
            return self.json.to_string(
                analysis_result.to_dict(), "repo_analysis"
            )
        if fmt in ("markdown", "md"):
            return self.markdown.repo_analysis_to_string(
                self.conn, analysis_result
            )
        raise ValueError(f"Unknown format: {fmt!r}")