# devpulse/core/metrics.py
# Metrics pipeline — sits on top of FileAnalyzer and the metrics_cache table.
# Provides higher-level aggregation, trend detection, and report-ready summaries.
# No external deps — pure Python + stdlib.

import os
import logging
import sqlite3
from typing import List, Dict, Optional, Any

from .analyzer import FileAnalyzer
from ..db.queries import MetricsQueries, CommitQueries, RepoQueries
from ..utils.dates import DateRange, date_series, parse_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Language-level aggregation
# ---------------------------------------------------------------------------

class LanguageSummary:

    def __init__(self, language: str):
        self.language = language
        self.file_count = 0
        self.total_loc = 0
        self.total_blank = 0
        self.total_comments = 0
        self.total_functions = 0
        self.total_classes = 0
        self.complexity_values: List[float] = []

    def add(self, m: Dict) -> None:
        self.file_count += 1
        self.total_loc += m.get("loc", 0)
        self.total_blank += m.get("blank_lines", 0)
        self.total_comments += m.get("comment_lines", 0)
        self.total_functions += m.get("function_count", 0)
        self.total_classes += m.get("class_count", 0)
        c = m.get("complexity", 0.0)
        if c > 0:
            self.complexity_values.append(c)

    @property
    def avg_complexity(self) -> float:
        if not self.complexity_values:
            return 0.0
        return round(sum(self.complexity_values) / len(self.complexity_values), 2)

    @property
    def comment_ratio(self) -> float:
        total = self.total_loc + self.total_comments
        if total == 0:
            return 0.0
        return round(self.total_comments / total, 3)

    def to_dict(self) -> Dict:
        return {
            "language": self.language,
            "file_count": self.file_count,
            "total_loc": self.total_loc,
            "total_blank": self.total_blank,
            "total_comments": self.total_comments,
            "total_functions": self.total_functions,
            "total_classes": self.total_classes,
            "avg_complexity": self.avg_complexity,
            "comment_ratio": self.comment_ratio,
        }


# ---------------------------------------------------------------------------
# Repo-level metrics summary
# ---------------------------------------------------------------------------

class MetricsSummary:

    def __init__(self, repo_id: int, conn: sqlite3.Connection):
        self.repo_id = repo_id
        self.conn = conn

    def build(self) -> Dict:
        raw_summary = MetricsQueries.repo_summary(self.conn, self.repo_id)
        all_files = MetricsQueries.list_by_repo(
            self.conn, self.repo_id,
            order_by="complexity",
            limit=100000
        )

        lang_map: Dict[str, LanguageSummary] = {}
        for m in all_files:
            lang = m.get("language", "unknown")
            if lang not in lang_map:
                lang_map[lang] = LanguageSummary(lang)
            lang_map[lang].add(m)

        complex_files = sorted(
            all_files,
            key=lambda x: x.get("complexity", 0.0),
            reverse=True
        )[:20]

        churn_files = sorted(
            all_files,
            key=lambda x: x.get("churn_score", 0.0),
            reverse=True
        )[:20]

        long_function_files = []
        for m in all_files:
            fc = m.get("function_count", 0)
            loc = m.get("loc", 0)
            if fc > 0 and loc > 0:
                avg_fn_len = loc / fc
                if avg_fn_len > 50:
                    long_function_files.append({
                        "file": m.get("file_path", ""),
                        "function_count": fc,
                        "loc": loc,
                        "avg_fn_length": round(avg_fn_len, 1),
                    })

        long_function_files.sort(
            key=lambda x: x["avg_fn_length"], reverse=True
        )

        total_loc = raw_summary.get("total_loc") or 0
        total_comments = raw_summary.get("total_comments") or 0
        overall_comment_ratio = 0.0
        if total_loc + total_comments > 0:
            overall_comment_ratio = round(
                total_comments / (total_loc + total_comments), 3
            )

        return {
            "repo_id": self.repo_id,
            "file_count": raw_summary.get("file_count") or 0,
            "total_loc": total_loc,
            "total_blank_lines": raw_summary.get("total_blank") or 0,
            "total_comment_lines": total_comments,
            "total_functions": raw_summary.get("total_functions") or 0,
            "total_classes": raw_summary.get("total_classes") or 0,
            "avg_complexity": round(raw_summary.get("avg_complexity") or 0.0, 2),
            "max_churn": round(raw_summary.get("max_churn") or 0.0, 2),
            "overall_comment_ratio": overall_comment_ratio,
            "language_breakdown": [s.to_dict() for s in
                                   sorted(lang_map.values(),
                                          key=lambda x: x.total_loc,
                                          reverse=True)],
            "most_complex_files": [
                {
                    "file": os.path.basename(m.get("file_path", "")),
                    "full_path": m.get("file_path", ""),
                    "language": m.get("language", ""),
                    "complexity": m.get("complexity", 0.0),
                    "loc": m.get("loc", 0),
                }
                for m in complex_files
            ],
            "most_churned_files": [
                {
                    "file": os.path.basename(m.get("file_path", "")),
                    "full_path": m.get("file_path", ""),
                    "language": m.get("language", ""),
                    "churn_score": m.get("churn_score", 0.0),
                    "loc": m.get("loc", 0),
                }
                for m in churn_files
            ],
            "long_function_files": long_function_files[:20],
        }


# ---------------------------------------------------------------------------
# Incremental metrics updater
# ---------------------------------------------------------------------------

class MetricsUpdater:
    """
    Handles incremental re-analysis of files that have changed since
    the last metrics run. Called by the watcher when files are saved,
    and by the analyzer when a fresh sync is triggered.
    """

    def __init__(self, repo_id: int, repo_path: str,
                 conn: sqlite3.Connection, config=None):
        self.repo_id = repo_id
        self.repo_path = repo_path
        self.conn = conn
        self.max_file_kb = (
            config.get_int("analyzer", "max_file_size_kb", 500)
            if config else 500
        )

    def update_file(self, file_path: str,
                    churn_delta: float = 0.0) -> Optional[Dict]:
        """
        Re-analyze a single file and update its metrics cache entry.
        Returns the new metrics dict, or None if file is not analyzable.
        """
        abs_path = os.path.abspath(file_path)
        if not FileAnalyzer.is_analyzable(abs_path, self.max_file_kb):
            return None

        metrics = FileAnalyzer.analyze_file(abs_path)
        if metrics.get("error"):
            logger.debug(f"Skipping {abs_path}: {metrics['error']}")
            return None

        # Fetch existing churn score so we don't overwrite it
        existing = MetricsQueries.get_by_file(
            self.conn, self.repo_id, abs_path
        )
        existing_churn = existing.get("churn_score", 0.0) if existing else 0.0
        new_churn = existing_churn + churn_delta

        MetricsQueries.upsert(
            self.conn,
            repo_id=self.repo_id,
            file_path=abs_path,
            language=metrics["language"],
            loc=metrics["loc"],
            blank_lines=metrics["blank_lines"],
            comment_lines=metrics["comment_lines"],
            function_count=metrics["function_count"],
            class_count=metrics["class_count"],
            complexity=metrics["complexity"],
            churn_score=new_churn,
        )
        self.conn.commit()
        logger.debug(f"Updated metrics for {abs_path}")
        return metrics

    def update_batch(self, file_paths: List[str]) -> int:
        """
        Update metrics for a list of files.
        Returns count of files successfully updated.
        """
        updated = 0
        for path in file_paths:
            result = self.update_file(path)
            if result is not None:
                updated += 1
        return updated

    def refresh_stale(self, ttl_hours: int = 24) -> int:
        """
        Find all stale cached files for this repo and re-analyze them.
        Returns count of files refreshed.
        """
        stale = MetricsQueries.stale_files(
            self.conn, self.repo_id, ttl_hours
        )
        paths = [r["file_path"] for r in stale]
        if not paths:
            logger.debug("No stale metrics files to refresh.")
            return 0
        logger.info(f"Refreshing {len(paths)} stale metrics entries...")
        return self.update_batch(paths)


# ---------------------------------------------------------------------------
# Churn tracker — updates per-file churn scores from commit diffs
# ---------------------------------------------------------------------------

class ChurnTracker:
    """
    Updates churn_score for files in metrics_cache based on
    how frequently they appear in recent commits.
    Uses commit frequency as a proxy for churn — the more commits
    touch a file, the higher the churn score.
    """

    def __init__(self, repo_id: int, conn: sqlite3.Connection):
        self.repo_id = repo_id
        self.conn = conn

    def compute_from_git_log(self, file_commit_counts: Dict[str, int]) -> None:
        """
        Given a dict of {file_path: commit_count}, update churn_score
        for each file in metrics_cache. Normalizes to 0.0–1.0 range.

        file_commit_counts comes from git log --name-only parsing,
        which counts how many commits touched each file.
        """
        if not file_commit_counts:
            return

        max_count = max(file_commit_counts.values(), default=1)
        if max_count == 0:
            return

        for file_path, count in file_commit_counts.items():
            normalized = round(count / max_count, 4)
            existing = MetricsQueries.get_by_file(
                self.conn, self.repo_id, file_path
            )
            if existing:
                MetricsQueries.upsert(
                    self.conn,
                    repo_id=self.repo_id,
                    file_path=file_path,
                    language=existing.get("language", "unknown"),
                    loc=existing.get("loc", 0),
                    blank_lines=existing.get("blank_lines", 0),
                    comment_lines=existing.get("comment_lines", 0),
                    function_count=existing.get("function_count", 0),
                    class_count=existing.get("class_count", 0),
                    complexity=existing.get("complexity", 0.0),
                    churn_score=normalized,
                )

        self.conn.commit()
        logger.info(
            f"Updated churn scores for {len(file_commit_counts)} files."
        )

    def parse_file_commit_counts(self, raw_name_only: str) -> Dict[str, int]:
        """
        Parse output of `git log --name-only --pretty=format:''`
        to get per-file commit counts.
        Returns {relative_file_path: count}.
        """
        counts: Dict[str, int] = {}
        for line in raw_name_only.splitlines():
            line = line.strip()
            if not line:
                continue
            # Skip lines that look like commit hashes or headers
            if len(line) == 40 and all(c in "0123456789abcdef" for c in line):
                continue
            counts[line] = counts.get(line, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Duplicate detection pipeline
# ---------------------------------------------------------------------------

class DuplicateDetector:
    """
    Runs duplicate code block detection across a repo's source files
    and returns a structured report.
    """

    def __init__(self, repo_id: int, repo_path: str,
                 conn: sqlite3.Connection, config=None):
        self.repo_id = repo_id
        self.repo_path = repo_path
        self.conn = conn
        self.config = config
        self.exclude = (
            config.exclude_patterns if config else
            [".git", "node_modules", "__pycache__", ".venv"]
        )
        self.max_file_kb = (
            config.get_int("analyzer", "max_file_size_kb", 500)
            if config else 500
        )

    def run(self, block_size: int = 6,
            max_files: int = 500) -> Dict:
        """
        Scan the repo for duplicate code blocks.
        Limits to max_files to avoid extremely slow runs on huge repos.
        Returns a report dict with duplicate groups.
        """
        all_files = FileAnalyzer.scan_directory(
            self.repo_path, self.exclude, self.max_file_kb
        )

        # Only analyze code files — skip data, config, markup
        code_langs = {
            "Python", "JavaScript", "TypeScript", "Go", "Rust",
            "Java", "C", "C++", "Ruby", "PHP", "Swift", "Kotlin",
            "C#", "Shell",
        }
        code_files = [
            f for f in all_files
            if FileAnalyzer.detect_language(f) in code_langs
        ][:max_files]

        if not code_files:
            return {"duplicates": [], "files_scanned": 0, "warning": "No code files found."}

        logger.info(f"Running duplicate detection on {len(code_files)} files...")
        duplicates = FileAnalyzer.detect_duplicates(
            code_files,
            block_size,
            boilerplate_patterns=(
                self.config.boilerplate_patterns
                if self.config else []
            ),
        )

        # Make paths relative for output
        for group in duplicates:
            for loc in group.get("locations", []):
                try:
                    loc["file"] = os.path.relpath(loc["file"], self.repo_path)
                except ValueError:
                    pass

        return {
            "duplicates": duplicates,
            "files_scanned": len(code_files),
            "duplicate_groups": len(duplicates),
            "block_size_lines": block_size,
        }


# ---------------------------------------------------------------------------
# Metrics trend analyzer — compares current vs past
# ---------------------------------------------------------------------------

class MetricsTrend:
    """
    Compares current repo metrics against a snapshot taken N days ago.
    Used by the 'devpulse diff' command and the dashboard trend widgets.
    Since we don't snapshot historical file metrics, we proxy trends
    through commit activity changes.
    """

    def __init__(self, repo_id: int, conn: sqlite3.Connection):
        self.repo_id = repo_id
        self.conn = conn

    def commits_today_vs_yesterday(self) -> Dict:
        today_dr = DateRange.today()
        yesterday_dr = DateRange.yesterday()

        def count_commits(dr: DateRange) -> int:
            rows = CommitQueries.commits_per_day(
                self.conn, self.repo_id,
                dr.start_iso(), dr.end_iso()
            )
            return sum(r.get("count", 0) for r in rows)

        today = count_commits(today_dr)
        yesterday = count_commits(yesterday_dr)
        delta = today - yesterday
        pct = 0.0
        if yesterday > 0:
            pct = round((delta / yesterday) * 100, 1)

        return {
            "today": today,
            "yesterday": yesterday,
            "delta": delta,
            "delta_pct": pct,
            "trend": "up" if delta > 0 else ("down" if delta < 0 else "flat"),
        }

    def this_week_vs_last_week(self) -> Dict:
        this_dr = DateRange.this_week()
        last_dr = DateRange.last_week()

        def week_commits(dr: DateRange) -> int:
            rows = CommitQueries.commits_per_day(
                self.conn, self.repo_id,
                dr.start_iso(), dr.end_iso()
            )
            return sum(r.get("count", 0) for r in rows)

        this_week = week_commits(this_dr)
        last_week = week_commits(last_dr)
        delta = this_week - last_week
        pct = 0.0
        if last_week > 0:
            pct = round((delta / last_week) * 100, 1)

        return {
            "this_week": this_week,
            "last_week": last_week,
            "delta": delta,
            "delta_pct": pct,
            "trend": "up" if delta > 0 else ("down" if delta < 0 else "flat"),
        }

    def loc_growth_last_n_days(self, days: int = 30) -> List[Dict]:
        """
        Approximate LOC growth by looking at lines_added - lines_removed
        per day from commit churn data. Not exact (doesn't account for
        file deletions), but useful as a trend indicator.
        """
        dr = DateRange.last_n_days(days)
        rows = CommitQueries.commits_per_day(
            self.conn, self.repo_id,
            dr.start_iso(), dr.end_iso()
        )
        # Fill in zero days
        counts = {r["date"]: r["count"] for r in rows}
        all_dates = date_series(dr.start_iso()[:10], dr.end_iso()[:10])
        return [
            {"date": d, "commits": counts.get(d, 0)}
            for d in all_dates
        ]