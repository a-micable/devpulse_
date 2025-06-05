# devpulse/utils/git.py
# Git subprocess wrapper. No GitPython, no pygit2.
# Everything goes through subprocess calls to the system git binary.
# Parses raw git output into Python dicts.

import os
import re
import subprocess
import logging
from typing import List, Dict, Optional, Tuple, Iterator

logger = logging.getLogger(__name__)

# Separator used in git log --format to split fields safely
_LOG_SEP = "\x1f"  # ASCII unit separator — never appears in commit messages
_RECORD_SEP = "\x1e"  # ASCII record separator — marks end of each commit


def _run(args: List[str], cwd: str,
         timeout: int = 30) -> Tuple[str, str, int]:
    """
    Run a git command in cwd. Returns (stdout, stderr, returncode).
    Never raises — callers check returncode.
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0",
                 "LANG": "en_US.UTF-8"},
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        logger.warning(f"git command timed out: git {' '.join(args)}")
        return "", "timeout", 1
    except FileNotFoundError:
        logger.error("git binary not found on PATH")
        return "", "git not found", 127
    except Exception as e:
        logger.error(f"git subprocess error: {e}")
        return "", str(e), 1


class GitRunner:
    """
    Low-level git command runner for a specific repository path.
    All methods return raw strings or structured data.
    Raises ValueError if path is not a git repository.
    """

    def __init__(self, repo_path: str):
        self.path = os.path.abspath(repo_path)
        if not self._is_git_repo():
            raise ValueError(f"Not a git repository: {self.path}")

    def _is_git_repo(self) -> bool:
        _, _, code = _run(["rev-parse", "--git-dir"], self.path)
        return code == 0

    def root(self) -> str:
        """Return the absolute path of the repo root."""
        out, _, code = _run(
            ["rev-parse", "--show-toplevel"], self.path
        )
        if code != 0:
            return self.path
        return out.strip()

    def current_branch(self) -> str:
        out, _, code = _run(
            ["rev-parse", "--abbrev-ref", "HEAD"], self.path
        )
        if code != 0:
            return "unknown"
        return out.strip()

    def branch_list(self) -> List[str]:
        out, _, code = _run(["branch", "--list"], self.path)
        if code != 0:
            return []
        return [b.strip().lstrip("* ") for b in out.splitlines() if b.strip()]

    def remote_url(self) -> str:
        out, _, code = _run(
            ["remote", "get-url", "origin"], self.path
        )
        if code != 0:
            return ""
        return out.strip()

    def tag_list(self) -> List[str]:
        out, _, code = _run(
            ["tag", "--sort=-creatordate"], self.path
        )
        if code != 0:
            return []
        return [t.strip() for t in out.splitlines() if t.strip()]

    def tag_date(self, tag: str) -> str:
        """Return ISO date string for a tag's commit."""
        out, _, code = _run(
            ["log", "-1", "--format=%ai", tag], self.path
        )
        if code != 0:
            return ""
        return out.strip()

    def total_commit_count(self, branch: str = "HEAD",
                           exclude_merges: bool = True) -> int:
        args = ["rev-list", "--count"]
        if exclude_merges:
            args.append("--no-merges")
        args.append(branch)
        out, _, code = _run(args, self.path)
        if code != 0:
            return 0
        try:
            return int(out.strip())
        except ValueError:
            return 0

    def first_commit_date(self) -> str:
        out, _, code = _run(
            ["log", "--reverse", "--format=%ai", "--max-count=1"], self.path
        )
        if code != 0:
            return ""
        return out.strip()

    def last_commit_date(self) -> str:
        out, _, code = _run(["log", "--format=%ai", "--max-count=1"], self.path)
        if code != 0:
            return ""
        return out.strip()

    def raw_log(self, since: str = "", until: str = "",
                max_count: int = 0,
                branch: str = "HEAD") -> str:
        """
        Return raw git log output using ASCII separators for safe parsing.
        Format: hash|author|email|date|subject|body + stat info
        """
        fmt = _LOG_SEP.join([
            "%H",    # full hash
            "%an",   # author name
            "%ae",   # author email
            "%ai",   # author date ISO
            "%s",    # subject (first line of message)
            "%b",    # body
        ]) + _RECORD_SEP

        args = ["log", f"--format={fmt}", "--no-merges"]

        if since:
            args += [f"--since={since}"]
        if until:
            args += [f"--until={until}"]
        if max_count > 0:
            args += [f"--max-count={max_count}"]

        args.append(branch)

        out, err, code = _run(args, self.path, timeout=60)
        if code != 0:
            logger.warning(f"git log failed: {err.strip()}")
            return ""
        return out

    def raw_log_with_stat(self, since: str = "", until: str = "",
                          max_count: int = 0) -> str:
        """
        Return git log with --numstat for lines added/removed per file.
        """
        fmt = _LOG_SEP.join([
            "%H", "%an", "%ae", "%ai", "%s",
        ]) + _RECORD_SEP

        args = [
            "log",
            f"--format={fmt}",
            "--numstat",
            "--no-merges",
        ]
        if since:
            args += [f"--since={since}"]
        if until:
            args += [f"--until={until}"]
        if max_count > 0:
            args += [f"--max-count={max_count}"]

        out, err, code = _run(args, self.path, timeout=120)
        if code != 0:
            logger.warning(f"git log --numstat failed: {err.strip()}")
            return ""
        return out

    def raw_log_merges_only(self, max_count: int = 50) -> str:
        fmt = _LOG_SEP.join(["%H", "%an", "%ae", "%ai", "%s"]) + _RECORD_SEP
        args = ["log", f"--format={fmt}", "--merges",
                f"--max-count={max_count}"]
        out, _, _ = _run(args, self.path)
        return out

    def diff_stat(self, ref_a: str = "HEAD~1",
                  ref_b: str = "HEAD") -> List[Dict]:
        """
        Return per-file diff stats between two refs.
        Returns list of {file, added, removed}
        """
        out, _, code = _run(
            ["diff", "--numstat", ref_a, ref_b], self.path
        )
        if code != 0:
            return []
        results = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added_str, removed_str, filepath = parts
            try:
                results.append({
                    "file": filepath.strip(),
                    "added": int(added_str) if added_str != "-" else 0,
                    "removed": int(removed_str) if removed_str != "-" else 0,
                })
            except ValueError:
                continue
        return results

    def blame_lines(self, file_path: str) -> List[Dict]:
        """
        Run git blame on a file. Returns list of
        {hash, author, date, line_number, content}
        Very slow on large files — use sparingly.
        """
        rel_path = os.path.relpath(file_path, self.path)
        out, _, code = _run(
            ["blame", "--porcelain", rel_path], self.path, timeout=60
        )
        if code != 0:
            return []
        return GitBlameParser.parse(out)

    def file_log(self, file_path: str,
                 max_count: int = 50) -> str:
        """Return commit history for a single file."""
        rel_path = os.path.relpath(file_path, self.path)
        fmt = _LOG_SEP.join(["%H", "%an", "%ai", "%s"]) + _RECORD_SEP
        out, _, code = _run(
            ["log", f"--format={fmt}", f"--max-count={max_count}",
             "--follow", "--", rel_path],
            self.path,
        )
        return out if code == 0 else ""

    def staged_diff(self) -> str:
        """Return diff of currently staged changes."""
        out, _, _ = _run(["diff", "--cached", "--numstat"], self.path)
        return out

    def is_dirty(self) -> bool:
        """True if repo has uncommitted changes."""
        _, _, code = _run(["diff", "--quiet", "HEAD"], self.path)
        return code != 0

    def contributor_count(self) -> int:
        out, _, code = _run(
            ["shortlog", "-s", "-n", "--no-merges", "HEAD"], self.path
        )
        if code != 0:
            return 0
        return len([l for l in out.splitlines() if l.strip()])


class GitLogParser:
    """
    Parses raw git log output produced by GitRunner.raw_log()
    and GitRunner.raw_log_with_stat() into structured dicts.
    """

    @staticmethod
    def parse_simple(raw: str) -> List[Dict]:
        """
        Parse output from raw_log() — no file stats.
        Returns list of commit dicts.
        """
        records = raw.split(_RECORD_SEP)
        commits = []
        for record in records:
            record = record.strip()
            if not record:
                continue
            parts = record.split(_LOG_SEP)
            if len(parts) < 5:
                continue
            commits.append({
                "hash": parts[0].strip(),
                "author": parts[1].strip(),
                "author_email": parts[2].strip(),
                "committed_at": _normalize_date(parts[3].strip()),
                "message": parts[4].strip(),
                "body": parts[5].strip() if len(parts) > 5 else "",
                "lines_added": 0,
                "lines_removed": 0,
                "files_changed": 0,
                "is_merge": 0,
            })
        return commits

    @staticmethod
    def parse_with_stat(raw: str) -> List[Dict]:
        """
        Parse output from raw_log_with_stat() which interleaves
        commit header lines with numstat lines. Use record separator to
        split commits robustly.
        """
        commits = []

        # Split by record separator which marks the end of each commit header
        parts = raw.split(_RECORD_SEP)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            lines = part.splitlines()
            # First line is the header with LOG_SEP separators
            header = lines[0]
            fields = header.split(_LOG_SEP)
            if len(fields) < 5:
                continue
            current = {
                "hash": fields[0].strip(),
                "author": fields[1].strip(),
                "author_email": fields[2].strip(),
                "committed_at": _normalize_date(fields[3].strip()),
                "message": fields[4].strip(),
                "lines_added": 0,
                "lines_removed": 0,
                "files_changed": 0,
                "is_merge": 0,
            }

            # Remaining lines (if any) contain numstat entries
            for line in lines[1:]:
                line_stripped = line.strip()
                if "\t" in line_stripped:
                    parts = line_stripped.split("\t")
                    if len(parts) == 3:
                        added_str, removed_str, _ = parts
                        try:
                            added = int(added_str) if added_str != "-" else 0
                            removed = int(removed_str) if removed_str != "-" else 0
                            current["lines_added"] += added
                            current["lines_removed"] += removed
                            current["files_changed"] += 1
                        except ValueError:
                            pass

            commits.append(current)

        return commits

    @staticmethod
    def detect_merge(message: str) -> bool:
        """Heuristic merge commit detection from message."""
        lower = message.lower()
        patterns = [
            r"^merge\s",
            r"^merged\s",
            r"^merge pull request",
            r"^merge branch",
            r"^auto-merge",
        ]
        for pat in patterns:
            if re.match(pat, lower):
                return True
        return False


class GitBlameParser:
    """Parse output from git blame --porcelain."""

    @staticmethod
    def parse(raw: str) -> List[Dict]:
        lines = raw.splitlines()
        results = []
        current: Dict = {}
        line_number = 0

        i = 0
        while i < len(lines):
            line = lines[i]

            # Porcelain format: 40-char hash starts a new entry
            if len(line) >= 40 and re.match(r"^[0-9a-f]{40}", line):
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        line_number = int(parts[2])
                    except (ValueError, IndexError):
                        line_number = 0
                current = {"hash": parts[0], "line_number": line_number}

            elif line.startswith("author "):
                current["author"] = line[7:].strip()

            elif line.startswith("author-time "):
                try:
                    current["timestamp"] = int(line[12:].strip())
                except ValueError:
                    current["timestamp"] = 0

            elif line.startswith("\t"):
                # Tab-prefixed line is the actual source line content
                current["content"] = line[1:]
                if current.get("hash"):
                    results.append(dict(current))
                current = {}

            i += 1

        return results


def _normalize_date(date_str: str) -> str:
    """
    Convert git date formats to ISO 8601 UTC string.
    Git outputs dates like: 2024-03-15 14:22:01 +0300
    We strip the timezone offset and store as-is (keeping local time).
    Callers should be aware dates are in committer local time.
    """
    if not date_str:
        return ""
    # Remove timezone offset for storage — keep for display
    # Format: "2024-03-15 14:22:01 +0300" → "2024-03-15T14:22:01"
    date_str = date_str.strip()
    match = re.match(
        r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s*([+-]\d{4})?",
        date_str,
    )
    if match:
        return f"{match.group(1)}T{match.group(2)}"
    return date_str