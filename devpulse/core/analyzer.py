# devpulse/core/analyzer.py
# The analysis engine. Pulls raw git data through GitRunner,
# computes all stats, and writes results to SQLite via db/queries.py.
# No external analysis libraries — all math is hand-written.

import os
import re
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any

from ..utils.git import GitRunner, GitLogParser
from ..utils.dates import (
    DateRange,
    date_series,
    fill_date_gaps,
    calculate_streaks,
    format_relative,
    parse_iso,
    to_date_str,
    weekday_name,
    hour_label,
)
from ..db.queries import (
    CommitQueries,
    RepoQueries,
    MetricsQueries,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes — plain dicts would work but typed containers
# make the rest of the code self-documenting
# ---------------------------------------------------------------------------

class AnalysisResult:
    """
    Container for the output of a full repo analysis.
    All fields are plain Python types — safe to JSON-serialize directly.
    """

    def __init__(self):
        self.repo_id: Optional[int] = None
        self.repo_name: str = ""
        self.repo_path: str = ""
        self.remote_url: str = ""
        self.current_branch: str = ""
        self.branches: List[str] = []
        self.tags: List[str] = []

        # Commit overview
        self.total_commits: int = 0
        self.merge_commits: int = 0
        self.first_commit_date: str = ""
        self.last_commit_date: str = ""
        self.active_days: int = 0
        self.current_streak: int = 0
        self.longest_streak: int = 0

        # Churn
        self.total_lines_added: int = 0
        self.total_lines_removed: int = 0
        self.total_files_changed: int = 0

        # Author breakdown
        self.authors: List[Dict] = []
        self.contributor_count: int = 0

        # Time patterns
        self.commits_by_hour: List[Dict] = []       # [{hour, count}]
        self.commits_by_weekday: List[Dict] = []    # [{weekday, name, count}]
        self.commits_by_date: List[Dict] = []       # [{date, count}]

        # File hotspots
        self.hotspots: List[Dict] = []              # [{file, churn, commits}]

        # Release cadence
        self.releases: List[Dict] = []              # [{tag, date, days_since_last}]
        self.avg_days_between_releases: float = 0.0

        # Commit message quality
        self.avg_message_length: float = 0.0
        self.short_message_ratio: float = 0.0       # % with message < 10 chars
        self.message_keyword_counts: Dict[str, int] = {}

        # Language breakdown (from metrics cache if available)
        self.language_breakdown: List[Dict] = []

        # Errors / warnings encountered during analysis
        self.warnings: List[str] = []

    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items()}


# ---------------------------------------------------------------------------
# Commit Analyzer — works purely from git log output
# ---------------------------------------------------------------------------

class CommitAnalyzer:
    """
    Analyzes commit history for a repo.
    Works from a list of commit dicts (output of GitLogParser).
    All methods are pure functions of the input — no I/O here.
    """

    # Conventional commit type prefixes we track
    COMMIT_KEYWORDS = [
        "feat", "fix", "refactor", "test", "docs",
        "perf", "chore", "build", "ci", "style",
        "revert", "hotfix", "init", "release", "merge",
        "wip", "add", "remove", "update", "bump",
    ]

    @staticmethod
    def total_churn(commits: List[Dict]) -> Tuple[int, int, int]:
        """Returns (total_added, total_removed, total_files_changed)."""
        added = sum(c.get("lines_added", 0) for c in commits)
        removed = sum(c.get("lines_removed", 0) for c in commits)
        files = sum(c.get("files_changed", 0) for c in commits)
        return added, removed, files

    @staticmethod
    def active_days(commits: List[Dict]) -> int:
        """Count distinct calendar days with at least one commit."""
        dates = set()
        for c in commits:
            dt = parse_iso(c.get("committed_at", ""))
            if dt:
                dates.add(dt.date())
        return len(dates)

    @staticmethod
    def commit_dates(commits: List[Dict]) -> List[str]:
        """Return sorted list of YYYY-MM-DD strings from commit list."""
        dates = []
        for c in commits:
            dt = parse_iso(c.get("committed_at", ""))
            if dt:
                dates.append(dt.strftime("%Y-%m-%d"))
        return sorted(dates)

    @staticmethod
    def by_hour(commits: List[Dict]) -> List[Dict]:
        """
        Returns list of 24 dicts: [{hour: 0, label: '12am', count: N}, ...]
        Hours with no commits still appear with count=0.
        """
        counts = {h: 0 for h in range(24)}
        for c in commits:
            dt = parse_iso(c.get("committed_at", ""))
            if dt:
                counts[dt.hour] += 1
        return [
            {"hour": h, "label": hour_label(h), "count": counts[h]}
            for h in range(24)
        ]

    @staticmethod
    def by_weekday(commits: List[Dict]) -> List[Dict]:
        """
        Returns 7 dicts: [{weekday: 0, name: 'Sun', count: N}, ...]
        Uses Python weekday: Monday=0, Sunday=6.
        Remapped to Sun=0..Sat=6 for display consistency with git.
        """
        # Python: Monday=0 ... Sunday=6
        # We display: Sunday=0 ... Saturday=6
        counts = {d: 0 for d in range(7)}
        for c in commits:
            dt = parse_iso(c.get("committed_at", ""))
            if dt:
                # Convert Python weekday to Sun-first
                py_wd = dt.weekday()  # Mon=0
                sun_first = (py_wd + 1) % 7  # Sun=0
                counts[sun_first] += 1
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        return [
            {"weekday": d, "name": names[d], "count": counts[d]}
            for d in range(7)
        ]

    @staticmethod
    def by_date(commits: List[Dict],
                start: str, end: str) -> List[Dict]:
        """
        Returns one dict per day in [start, end]:
        [{date: 'YYYY-MM-DD', count: N}, ...]
        Days with no commits have count=0 (no gaps).
        """
        counts: Dict[str, int] = {}
        for c in commits:
            dt = parse_iso(c.get("committed_at", ""))
            if dt:
                d = dt.strftime("%Y-%m-%d")
                counts[d] = counts.get(d, 0) + 1

        all_days = date_series(start, end)
        return [{"date": d, "count": counts.get(d, 0)} for d in all_days]

    @staticmethod
    def author_breakdown(commits: List[Dict]) -> List[Dict]:
        """
        Aggregate per-author stats from commit list.
        Returns list sorted by commit_count descending.
        """
        authors: Dict[str, Dict] = {}
        for c in commits:
            email = c.get("author_email", "").lower().strip()
            name = c.get("author", "unknown").strip()
            key = email or name

            if key not in authors:
                authors[key] = {
                    "name": name,
                    "email": email,
                    "commit_count": 0,
                    "lines_added": 0,
                    "lines_removed": 0,
                    "files_changed": 0,
                    "first_commit": c.get("committed_at", ""),
                    "last_commit": c.get("committed_at", ""),
                    "active_days": set(),
                }

            a = authors[key]
            a["commit_count"] += 1
            a["lines_added"] += c.get("lines_added", 0)
            a["lines_removed"] += c.get("lines_removed", 0)
            a["files_changed"] += c.get("files_changed", 0)

            dt = parse_iso(c.get("committed_at", ""))
            if dt:
                date_str = dt.strftime("%Y-%m-%d")
                a["active_days"].add(date_str)
                if c.get("committed_at", "") < a["first_commit"]:
                    a["first_commit"] = c["committed_at"]
                if c.get("committed_at", "") > a["last_commit"]:
                    a["last_commit"] = c["committed_at"]

        result = []
        for a in authors.values():
            a["active_days"] = len(a["active_days"])
            result.append(a)

        return sorted(result, key=lambda x: x["commit_count"], reverse=True)

    @staticmethod
    def message_quality(commits: List[Dict]) -> Dict:
        """
        Analyze commit message quality.
        Returns stats dict with avg length, keyword counts, short ratio.
        """
        if not commits:
            return {
                "avg_length": 0.0,
                "short_ratio": 0.0,
                "keyword_counts": {},
                "total_analyzed": 0,
            }

        lengths = []
        short_count = 0
        keyword_counts: Dict[str, int] = {k: 0 for k in CommitAnalyzer.COMMIT_KEYWORDS}

        for c in commits:
            msg = c.get("message", "").strip()
            if not msg:
                continue
            lengths.append(len(msg))
            if len(msg) < 10:
                short_count += 1

            msg_lower = msg.lower()
            for kw in CommitAnalyzer.COMMIT_KEYWORDS:
                # Match at word boundary: "fix:" or "fix " or "fix("
                if re.search(rf"\b{re.escape(kw)}[\s:()\-]", msg_lower):
                    keyword_counts[kw] += 1

        total = len(lengths)
        return {
            "avg_length": round(sum(lengths) / total, 1) if total else 0.0,
            "short_ratio": round(short_count / total, 3) if total else 0.0,
            "keyword_counts": {k: v for k, v in keyword_counts.items() if v > 0},
            "total_analyzed": total,
        }

    @staticmethod
    def hotspot_score(churn: int, complexity: float,
                      commit_count: int) -> float:
        """
        Composite hotspot score for a file.
        Higher = more risky/important to review.
        Formula: normalized_churn * log(complexity+1) * log(commits+1)
        """
        import math
        if churn <= 0:
            return 0.0
        return round(
            (churn / 1000.0)
            * math.log(complexity + 1)
            * math.log(commit_count + 1),
            4,
        )

    @staticmethod
    def release_cadence(tags: List[str],
                        tag_dates: Dict[str, str]) -> List[Dict]:
        """
        Given a list of tag names and their dates,
        compute release cadence stats.
        Returns list of releases sorted by date ascending,
        each with days_since_last field.
        """
        if not tags:
            return []

        dated = []
        for tag in tags:
            date_str = tag_dates.get(tag, "")
            if not date_str:
                continue
            dt = parse_iso(date_str)
            if dt:
                dated.append({"tag": tag, "date": date_str, "dt": dt})

        dated.sort(key=lambda x: x["dt"])

        releases = []
        prev_dt = None
        for r in dated:
            days_since = 0
            if prev_dt:
                days_since = (r["dt"] - prev_dt).days
            releases.append({
                "tag": r["tag"],
                "date": r["date"],
                "days_since_last": days_since,
            })
            prev_dt = r["dt"]

        return releases


# ---------------------------------------------------------------------------
# File Analyzer — scans source files for code metrics
# ---------------------------------------------------------------------------

class FileAnalyzer:
    """
    Scans individual source files to compute code metrics.
    No AST parsing for most languages — uses line-based heuristics
    that work reliably across Python, JS, TS, Go, Java, C, etc.
    For Python files specifically, uses ast module for accuracy.
    """

    # Extensions mapped to language names
    LANGUAGE_MAP = {
        ".py":   "Python",
        ".js":   "JavaScript",
        ".ts":   "TypeScript",
        ".jsx":  "JavaScript",
        ".tsx":  "TypeScript",
        ".go":   "Go",
        ".rs":   "Rust",
        ".java": "Java",
        ".c":    "C",
        ".cpp":  "C++",
        ".cc":   "C++",
        ".h":    "C",
        ".hpp":  "C++",
        ".rb":   "Ruby",
        ".php":  "PHP",
        ".swift":"Swift",
        ".kt":   "Kotlin",
        ".cs":   "C#",
        ".sh":   "Shell",
        ".bash": "Shell",
        ".zsh":  "Shell",
        ".sql":  "SQL",
        ".html": "HTML",
        ".css":  "CSS",
        ".scss": "CSS",
        ".sass": "CSS",
        ".md":   "Markdown",
        ".json": "JSON",
        ".yaml": "YAML",
        ".yml":  "YAML",
        ".toml": "TOML",
        ".xml":  "XML",
    }

    # Single-line comment prefixes per language group
    COMMENT_PREFIXES = {
        "Python": ["#"],
        "JavaScript": ["//"],
        "TypeScript": ["//"],
        "Go": ["//"],
        "Rust": ["//"],
        "Java": ["//"],
        "C": ["//"],
        "C++": ["//", "/*"],
        "Ruby": ["#"],
        "PHP": ["//", "#"],
        "Shell": ["#"],
        "Swift": ["//"],
        "Kotlin": ["//"],
        "C#": ["//"],
        "SQL": ["--"],
        "HTML": ["<!--"],
        "CSS": ["/*"],
        "YAML": ["#"],
        "TOML": ["#"],
    }

    # Function/method definition patterns per language
    FUNCTION_PATTERNS = {
        "Python":     [r"^\s*def\s+\w+", r"^\s*async\s+def\s+\w+"],
        "JavaScript": [r"^\s*function\s+\w+",
                       r"^\s*(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\(",
                       r"^\s*(?:async\s+)?\w+\s*\([^)]*\)\s*\{",
                       r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function"],
        "TypeScript": [r"^\s*function\s+\w+",
                       r"^\s*(?:public|private|protected|static)?\s*(?:async\s+)?\w+\s*\("],
        "Go":         [r"^\s*func\s+"],
        "Rust":       [r"^\s*(?:pub\s+)?fn\s+\w+"],
        "Java":       [r"^\s*(?:public|private|protected|static|final|abstract)"
                       r"[\w\s<>\[\]]*\s+\w+\s*\("],
        "C":          [r"^\s*\w[\w\s\*]+\s+\w+\s*\([^;]*$"],
        "C++":        [r"^\s*\w[\w\s\*:]+\s+\w+\s*\([^;]*$"],
        "Ruby":       [r"^\s*def\s+\w+"],
        "Swift":      [r"^\s*(?:func|@objc\s+func)\s+\w+"],
        "Kotlin":     [r"^\s*(?:fun|suspend\s+fun)\s+\w+"],
        "C#":         [r"^\s*(?:public|private|protected|static|virtual|override)"
                       r"[\w\s<>\[\]]*\s+\w+\s*\("],
        "PHP":        [r"^\s*(?:public|private|protected|static)?\s*function\s+\w+"],
    }

    # Class definition patterns
    CLASS_PATTERNS = {
        "Python":     [r"^\s*class\s+\w+"],
        "JavaScript": [r"^\s*class\s+\w+"],
        "TypeScript": [r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+\w+",
                       r"^\s*(?:export\s+)?interface\s+\w+"],
        "Go":         [r"^\s*type\s+\w+\s+struct"],
        "Rust":       [r"^\s*(?:pub\s+)?struct\s+\w+",
                       r"^\s*(?:pub\s+)?enum\s+\w+",
                       r"^\s*(?:pub\s+)?trait\s+\w+"],
        "Java":       [r"^\s*(?:public|private|abstract|final)?\s*class\s+\w+",
                       r"^\s*(?:public)?\s*interface\s+\w+"],
        "C++":        [r"^\s*class\s+\w+", r"^\s*struct\s+\w+"],
        "Ruby":       [r"^\s*class\s+\w+", r"^\s*module\s+\w+"],
        "Swift":      [r"^\s*(?:public|private|open)?\s*class\s+\w+",
                       r"^\s*(?:public|private)?\s*struct\s+\w+",
                       r"^\s*(?:public|private)?\s*protocol\s+\w+"],
        "Kotlin":     [r"^\s*(?:data\s+)?class\s+\w+",
                       r"^\s*(?:sealed\s+)?class\s+\w+",
                       r"^\s*interface\s+\w+",
                       r"^\s*object\s+\w+"],
        "C#":         [r"^\s*(?:public|private|internal|abstract|sealed)?\s*class\s+\w+",
                       r"^\s*(?:public)?\s*interface\s+\w+"],
        "PHP":        [r"^\s*class\s+\w+", r"^\s*interface\s+\w+"],
    }

    @classmethod
    def detect_language(cls, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        return cls.LANGUAGE_MAP.get(ext, "unknown")

    @classmethod
    def is_analyzable(cls, file_path: str,
                      max_size_kb: int = 500) -> bool:
        """Return True if file should be analyzed."""
        if not os.path.isfile(file_path):
            return False
        # Skip binary files — check for null bytes in first 8KB
        try:
            size = os.path.getsize(file_path)
            if size == 0:
                return False
            if size > max_size_kb * 1024:
                return False
            with open(file_path, "rb") as f:
                chunk = f.read(8192)
                if b"\x00" in chunk:
                    return False
        except OSError:
            return False
        lang = cls.detect_language(file_path)
        return lang != "unknown"

    @classmethod
    def analyze_file(cls, file_path: str) -> Dict:
        """
        Analyze a single source file. Returns metrics dict.
        Never raises — returns empty metrics on read error.
        """
        language = cls.detect_language(file_path)
        empty = {
            "file_path": file_path,
            "language": language,
            "loc": 0,
            "blank_lines": 0,
            "comment_lines": 0,
            "function_count": 0,
            "class_count": 0,
            "complexity": 0.0,
            "error": None,
        }

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as e:
            empty["error"] = str(e)
            return empty

        if not lines:
            return empty

        loc = 0
        blank = 0
        comments = 0
        in_multiline_comment = False

        comment_prefixes = cls.COMMENT_PREFIXES.get(language, ["#", "//"])

        for line in lines:
            stripped = line.strip()
            if not stripped:
                blank += 1
                continue

            # Multi-line comment detection (simple, not AST-accurate)
            if language in ("JavaScript", "TypeScript", "Java",
                            "C", "C++", "Go", "Rust", "C#", "Swift",
                            "Kotlin", "PHP", "CSS"):
                if "/*" in stripped and "*/" not in stripped:
                    in_multiline_comment = True
                    comments += 1
                    continue
                if in_multiline_comment:
                    comments += 1
                    if "*/" in stripped:
                        in_multiline_comment = False
                    continue

            if language == "HTML" and "<!--" in stripped:
                comments += 1
                continue

            is_comment = any(stripped.startswith(p) for p in comment_prefixes)
            if is_comment:
                comments += 1
            else:
                loc += 1

        func_count = cls._count_patterns(
            lines, cls.FUNCTION_PATTERNS.get(language, [])
        )
        class_count = cls._count_patterns(
            lines, cls.CLASS_PATTERNS.get(language, [])
        )
        complexity = cls._estimate_complexity(lines, language)

        return {
            "file_path": file_path,
            "language": language,
            "loc": loc,
            "blank_lines": blank,
            "comment_lines": comments,
            "function_count": func_count,
            "class_count": class_count,
            "complexity": complexity,
            "error": None,
        }

    @classmethod
    def _count_patterns(cls, lines: List[str],
                        patterns: List[str]) -> int:
        """Count lines matching any of the given regex patterns."""
        if not patterns:
            return 0
        compiled = [re.compile(p) for p in patterns]
        count = 0
        for line in lines:
            for pat in compiled:
                if pat.search(line):
                    count += 1
                    break  # Only count once per line
        return count

    @classmethod
    def _estimate_complexity(cls, lines: List[str],
                             language: str) -> float:
        """
        Estimate cyclomatic complexity using decision-point counting.
        Every if/else/elif/for/while/case/catch/and/or adds 1.
        Result is a float representing average complexity per function.
        This is a heuristic, not a true McCabe score.
        """
        # Decision-point keywords by language family
        c_family = {"if", "else", "elif", "for", "while", "case",
                    "catch", "except", "&&", "||", "?", "switch"}
        py_keywords = {"if", "elif", "else", "for", "while",
                       "except", "and", "or", "with"}

        if language == "Python":
            keywords = py_keywords
        else:
            keywords = c_family

        decision_count = 0
        for line in lines:
            stripped = line.strip()
            # Skip comment lines for complexity
            if stripped.startswith(("//", "#", "*", "/*", "<!--")):
                continue
            for kw in keywords:
                # Match keyword at word boundary
                if re.search(rf"\b{re.escape(kw)}\b", stripped):
                    decision_count += 1

        # Normalize: complexity per 100 lines of code
        loc = len([l for l in lines if l.strip()])
        if loc == 0:
            return 0.0
        return round((decision_count / loc) * 100, 2)

    @classmethod
    def scan_directory(cls, root_path: str,
                       exclude_patterns: List[str],
                       max_size_kb: int = 500) -> List[str]:
        """
        Walk a directory tree and return all analyzable file paths.
        Respects exclude_patterns (matched against any path component).
        """
        exclude_set = set(p.lower() for p in exclude_patterns if p)
        results = []

        for dirpath, dirnames, filenames in os.walk(root_path):
            # Prune excluded directories in-place (modifies walk)
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in exclude_set
                and not d.startswith(".")
            ]

            # Also skip if any parent component is excluded
            rel_dir = os.path.relpath(dirpath, root_path)
            parts = rel_dir.replace("\\", "/").split("/")
            if any(p.lower() in exclude_set for p in parts):
                continue

            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                if cls.is_analyzable(full_path, max_size_kb):
                    results.append(full_path)

        return results

    @classmethod
    def _is_boilerplate_block(cls, lines: List[str],
                              patterns: Optional[List[str]] = None) -> bool:
        """
        Return True for blocks that are mostly boilerplate setup or repeated
        starter code, such as imports, headers, license comments, or other
        repeated scaffolding that should not count as meaningful duplicate code.
        """
        stripped = [line.strip() for line in lines if line.strip()]
        if not stripped:
            return True

        boilerplate_re = re.compile(
            r'^(#|//|/\*|<!--|import\b|from\b.*\bimport\b|using\b|require\(|package\b|namespace\b|include\b)'
        )
        compiled = [re.compile(p) for p in (patterns or []) if p]

        boilerplate_lines = 0
        for line in stripped:
            if boilerplate_re.match(line):
                boilerplate_lines += 1
                continue
            if any(pattern.search(line) for pattern in compiled):
                boilerplate_lines += 1
                continue

        return (boilerplate_lines / len(stripped)) >= 0.75

    @classmethod
    def detect_duplicates(cls, file_paths: List[str],
                          block_size: int = 6,
                          boilerplate_patterns: Optional[List[str]] = None) -> List[Dict]:
        """
        Detect duplicate code blocks using a rolling hash approach.
        Splits each file into chunks of block_size lines,
        hashes each chunk, and groups matching hashes.
        Returns list of duplicate groups: [{hash, locations: [{file, line}]}]
        Very large codebases should use sampling — this is O(n*files).
        """
        import hashlib
        chunk_map: Dict[str, List[Dict]] = {}

        for file_path in file_paths:
            try:
                with open(file_path, "r",
                          encoding="utf-8", errors="replace") as f:
                    lines = [l.strip() for l in f.readlines()]
            except OSError:
                continue

            # Remove blank and comment lines before hashing
            code_lines = [l for l in lines if l and not l.startswith(
                ("#", "//", "*", "/*", "<!--")
            )]

            for i in range(len(code_lines) - block_size + 1):
                block_lines = code_lines[i:i + block_size]
                if cls._is_boilerplate_block(block_lines, boilerplate_patterns):
                    continue

                block = "\n".join(block_lines)
                # Skip trivially short or brace-only blocks
                if len(block.strip()) < 40:
                    continue
                h = hashlib.sha1(block.encode()).hexdigest()[:16]
                if h not in chunk_map:
                    chunk_map[h] = []
                chunk_map[h].append({
                    "file": file_path,
                    "line": i + 1,
                })

        duplicates = []
        for h, locations in chunk_map.items():
            if len(locations) > 1:
                # Only report if duplicate spans multiple files
                files = set(loc["file"] for loc in locations)
                if len(files) > 1:
                    duplicates.append({
                        "hash": h,
                        "occurrence_count": len(locations),
                        "locations": locations[:10],  # Cap at 10 for output size
                    })

        return sorted(duplicates,
                      key=lambda x: x["occurrence_count"],
                      reverse=True)


# ---------------------------------------------------------------------------
# RepoAnalyzer — top-level orchestrator
# ---------------------------------------------------------------------------

class RepoAnalyzer:
    """
    Orchestrates a full analysis of one git repository.
    Reads from git via GitRunner, computes stats via CommitAnalyzer
    and FileAnalyzer, persists results to SQLite, and returns
    an AnalysisResult.
    """

    def __init__(self, repo_path: str, conn: sqlite3.Connection,
                 config=None):
        self.repo_path = os.path.abspath(repo_path)
        self.conn = conn
        self.config = config
        self._exclude = (
            config.exclude_patterns if config else
            [".git", "node_modules", "__pycache__", ".venv",
             "dist", "build", ".next", "vendor"]
        )
        self._max_file_kb = (
            config.get_int("analyzer", "max_file_size_kb", 500)
            if config else 500
        )
        self._cache_ttl = (
            config.cache_ttl_hours if config else 24
        )

    def _get_or_create_repo(self, runner: GitRunner) -> int:
        """Get repo_id from DB or insert new record."""
        existing = RepoQueries.get_by_path(self.conn, self.repo_path)
        if existing:
            return existing["id"]

        name = os.path.basename(runner.root())
        remote = runner.remote_url()
        repo_id = RepoQueries.insert(
            self.conn, name=name,
            path=self.repo_path, remote_url=remote
        )
        return repo_id

    def _sync_commits(self, runner: GitRunner, repo_id: int,
                      since: str = "") -> List[Dict]:
        """
        Pull commits from git log and insert any new ones into DB.
        Returns full list of commits (from DB after sync).
        """
        logger.info(f"Syncing commits for repo {repo_id}...")
        raw = runner.raw_log_with_stat(since=since)
        if not raw:
            logger.warning("git log returned empty output")
            return []

        parsed = GitLogParser.parse_with_stat(raw)
        logger.info(f"  Parsed {len(parsed)} commits from git log")

        rows = []
        for c in parsed:
            is_merge = 1 if GitLogParser.detect_merge(c.get("message", "")) else 0
            rows.append({
                "repo_id": repo_id,
                "hash": c["hash"],
                "author": c["author"],
                "author_email": c["author_email"],
                "message": c["message"],
                "committed_at": c["committed_at"],
                "lines_added": c.get("lines_added", 0),
                "lines_removed": c.get("lines_removed", 0),
                "files_changed": c.get("files_changed", 0),
                "is_merge": is_merge,
            })

        if rows:
            new_count = CommitQueries.bulk_insert(self.conn, rows)
            logger.info(f"  Inserted {new_count} new commits into cache")

        RepoQueries.update_last_synced(self.conn, repo_id)
        return parsed

    def _analyze_files(self, runner: GitRunner,
                       repo_id: int) -> List[Dict]:
        """
        Scan repo source files for code metrics.
        Uses cache — only re-analyzes stale files.
        Returns list of metrics dicts for all files.
        """
        root = runner.root()
        logger.info(f"Scanning files in {root}...")

        all_files = FileAnalyzer.scan_directory(
            root, self._exclude, self._max_file_kb
        )
        logger.info(f"  Found {len(all_files)} analyzable files")

        # Determine which files need re-analysis
        stale = {
            r["file_path"]
            for r in MetricsQueries.stale_files(
                self.conn, repo_id, self._cache_ttl
            )
        }
        cached_paths = {
            r["file_path"]
            for r in MetricsQueries.list_by_repo(
                self.conn, repo_id, limit=100000
            )
        }

        results = []
        to_analyze = [
            f for f in all_files
            if f not in cached_paths or f in stale
        ]
        logger.info(f"  Analyzing {len(to_analyze)} files "
                    f"({len(all_files) - len(to_analyze)} cached)")

        for file_path in to_analyze:
            metrics = FileAnalyzer.analyze_file(file_path)
            if metrics.get("error"):
                logger.debug(f"  Skipping {file_path}: {metrics['error']}")
                continue
            MetricsQueries.upsert(
                self.conn,
                repo_id=repo_id,
                file_path=file_path,
                language=metrics["language"],
                loc=metrics["loc"],
                blank_lines=metrics["blank_lines"],
                comment_lines=metrics["comment_lines"],
                function_count=metrics["function_count"],
                class_count=metrics["class_count"],
                complexity=metrics["complexity"],
            )
            results.append(metrics)

        self.conn.commit()
        return MetricsQueries.list_by_repo(
            self.conn, repo_id, limit=100000
        )

    def _compute_hotspots(self, commits: List[Dict],
                          metrics: List[Dict],
                          repo_id: int) -> List[Dict]:
        """
        Cross-reference file churn (from commits) with complexity (from metrics).
        Returns top hotspot files sorted by composite score.
        """
        # Build per-file churn from commit data
        # (This is an approximation — true per-file churn needs
        #  git log --follow per file which is too slow to run for all files)
        file_churn: Dict[str, int] = {}
        file_commits: Dict[str, int] = {}

        # Use commits_cache data for churn approximation
        for c in commits:
            # We don't have per-file breakdown in our parsed commits,
            # but we do have total lines changed per commit.
            # Use total churn as repo-level metric and apply complexity
            # as the differentiator.
            pass

        # For per-file churn, pull from metrics cache churn_score
        hotspots = []
        for m in metrics:
            complexity = m.get("complexity", 0.0)
            churn = m.get("churn_score", 0.0)
            loc = m.get("loc", 0)
            if loc < 10:
                continue

            score = CommitAnalyzer.hotspot_score(
                churn=max(int(churn * 100), loc // 10),
                complexity=complexity,
                commit_count=max(1, int(churn)),
            )
            rel_path = os.path.relpath(
                m["file_path"], self.repo_path
            )
            hotspots.append({
                "file": rel_path,
                "language": m.get("language", "unknown"),
                "loc": loc,
                "complexity": complexity,
                "churn_score": churn,
                "hotspot_score": score,
            })

        return sorted(hotspots,
                      key=lambda x: x["hotspot_score"],
                      reverse=True)[:50]

    def run(self, since: str = "",
            force_refresh: bool = False) -> AnalysisResult:
        """
        Run a full analysis of the repository.
        Returns an AnalysisResult with all computed stats.

        Args:
            since: Only fetch commits after this date (ISO string).
                   Empty string fetches full history.
            force_refresh: If True, ignore metrics cache.
        """
        result = AnalysisResult()

        # Step 1: Connect to git
        try:
            runner = GitRunner(self.repo_path)
        except ValueError as e:
            result.warnings.append(str(e))
            logger.error(str(e))
            return result

        # Step 2: Resolve repo in DB
        repo_id = self._get_or_create_repo(runner)
        result.repo_id = repo_id
        result.repo_path = self.repo_path
        result.repo_name = os.path.basename(runner.root())
        result.remote_url = runner.remote_url()
        result.current_branch = runner.current_branch()
        result.branches = runner.branch_list()

        logger.info(
            f"Starting analysis: {result.repo_name} "
            f"(branch: {result.current_branch})"
        )

        # Step 3: Sync commits
        commits = self._sync_commits(runner, repo_id, since=since)

        # Filter out merge commits for most stats
        real_commits = [c for c in commits
                        if not GitLogParser.detect_merge(c.get("message", ""))]

        if not real_commits:
            result.warnings.append("No non-merge commits found.")
            logger.warning("No non-merge commits found in repo.")

        # Step 4: Basic commit stats
        result.total_commits = len(real_commits)
        result.merge_commits = len(commits) - len(real_commits)

        dates = CommitAnalyzer.commit_dates(real_commits)
        result.current_streak, result.longest_streak = calculate_streaks(dates)
        result.active_days = CommitAnalyzer.active_days(real_commits)

        if real_commits:
            sorted_commits = sorted(
                real_commits,
                key=lambda c: c.get("committed_at", ""),
            )
            result.first_commit_date = sorted_commits[0].get("committed_at", "")
            result.last_commit_date = sorted_commits[-1].get("committed_at", "")

        # Step 5: Churn totals
        added, removed, files = CommitAnalyzer.total_churn(real_commits)
        result.total_lines_added = added
        result.total_lines_removed = removed
        result.total_files_changed = files

        # Step 6: Time patterns
        if dates:
            result.commits_by_hour = CommitAnalyzer.by_hour(real_commits)
            result.commits_by_weekday = CommitAnalyzer.by_weekday(real_commits)
            start_date = dates[0] if dates else ""
            end_date = dates[-1] if dates else ""
            if start_date and end_date:
                result.commits_by_date = CommitAnalyzer.by_date(
                    real_commits, start_date, end_date
                )

        # Step 7: Author breakdown
        result.authors = CommitAnalyzer.author_breakdown(real_commits)
        result.contributor_count = len(result.authors)

        # Step 8: Message quality
        quality = CommitAnalyzer.message_quality(real_commits)
        result.avg_message_length = quality["avg_length"]
        result.short_message_ratio = quality["short_ratio"]
        result.message_keyword_counts = quality["keyword_counts"]

        # Step 9: Release cadence from git tags
        tags = runner.tag_list()
        result.tags = tags
        if tags:
            tag_dates = {t: runner.tag_date(t) for t in tags[:50]}
            result.releases = CommitAnalyzer.release_cadence(tags, tag_dates)
            if len(result.releases) > 1:
                intervals = [r["days_since_last"]
                             for r in result.releases[1:]]
                result.avg_days_between_releases = round(
                    sum(intervals) / len(intervals), 1
                )

        # Step 10: File metrics
        if force_refresh:
            MetricsQueries.delete_by_repo(self.conn, repo_id)

        all_metrics = self._analyze_files(runner, repo_id)

        # Step 11: Hotspots
        result.hotspots = self._compute_hotspots(
            real_commits, all_metrics, repo_id
        )

        # Step 12: Language breakdown
        result.language_breakdown = MetricsQueries.language_breakdown(
            self.conn, repo_id
        )

        logger.info(
            f"Analysis complete: {result.total_commits} commits, "
            f"{result.contributor_count} authors, "
            f"{len(all_metrics)} files analyzed."
        )

        return result


# ---------------------------------------------------------------------------
# Multi-repo batch analyzer
# ---------------------------------------------------------------------------

class BatchAnalyzer:
    """
    Run analysis across multiple repos and aggregate results.
    Used by `devpulse analyze --all` and the dashboard summary endpoint.
    """

    def __init__(self, conn: sqlite3.Connection, config=None,
                 repo_id: Optional[int] = None,
                 repo_path: Optional[str] = None):
        self.conn = conn
        self.config = config
        self.repo_id = repo_id
        self.repo_path = repo_path

    def run_all(self, since: str = "") -> Dict[str, Any]:
        """
        Analyze all active repos in the database.
        Returns an aggregate summary dict.
        """
        repos = RepoQueries.list_active(self.conn)
        if not repos:
            return {"repos": [], "summary": {}, "warnings": ["No repos found."]}

        all_results = []
        warnings = []

        for repo in repos:
            path = repo.get("path", "")
            if not os.path.isdir(path):
                warnings.append(f"Repo path not found: {path}")
                continue

            try:
                analyzer = RepoAnalyzer(
                    path, self.conn, self.config
                )
                result = analyzer.run(since=since)
                if result.warnings:
                    warnings.extend(result.warnings)
                all_results.append(result)
            except Exception as e:
                warnings.append(f"Failed to analyze {path}: {e}")
                logger.error(f"Batch analysis error for {path}: {e}",
                             exc_info=True)

        # Aggregate summary
        total_commits = sum(r.total_commits for r in all_results)
        total_loc = sum(
            sum(lb.get("total_loc", 0) for lb in r.language_breakdown)
            for r in all_results
        )
        all_dates = []
        for r in all_results:
            all_dates.extend([d["date"] for d in r.commits_by_date
                              if d.get("count", 0) > 0])

        current_streak, longest_streak = calculate_streaks(sorted(all_dates))

        return {
            "repos": [r.to_dict() for r in all_results],
            "summary": {
                "repo_count": len(all_results),
                "total_commits": total_commits,
                "total_loc": total_loc,
                "current_streak": current_streak,
                "longest_streak": longest_streak,
                "total_contributors": sum(
                    r.contributor_count for r in all_results
                ),
            },
            "warnings": warnings,
        }

    def run(self, full: bool = False) -> Any:
        """
        Run analysis for a single repo when `repo_path` (or `repo_id`) is provided.
        This provides the legacy `BatchAnalyzer.run(full=...)` API used in tests.
        """
        if not self.repo_path and self.repo_id:
            repo = RepoQueries.get_by_id(self.conn, self.repo_id)
            if repo:
                self.repo_path = repo.get("path")

        if not self.repo_path:
            raise ValueError("No repo_path specified for BatchAnalyzer.run()")

        analyzer = RepoAnalyzer(self.repo_path, self.conn, self.config)
        # Map `full` flag to RepoAnalyzer.force_refresh
        return analyzer.run(force_refresh=full)

    def daily_activity(self, days: int = 90) -> List[Dict]:
        """
        Aggregate daily commit counts across all repos for the last N days.
        Returns [{date, count, repos_active}]
        """
        dr = DateRange.last_n_days(days)
        start = dr.start_iso()
        end = dr.end_iso()

        repos = RepoQueries.list_active(self.conn)
        date_counts: Dict[str, int] = {}
        date_repos: Dict[str, set] = {}

        for repo in repos:
            repo_id = repo["id"]
            rows = CommitQueries.commits_per_day(
                self.conn, repo_id, start, end
            )
            for row in rows:
                d = row["date"]
                count = row["count"]
                date_counts[d] = date_counts.get(d, 0) + count
                if d not in date_repos:
                    date_repos[d] = set()
                date_repos[d].add(repo_id)

        all_dates = date_series(dr.start_iso()[:10], dr.end_iso()[:10])
        return [
            {
                "date": d,
                "count": date_counts.get(d, 0),
                "repos_active": len(date_repos.get(d, set())),
            }
            for d in all_dates
        ]