# devpulse/utils/validators.py
# Input validation helpers used by API route handlers and CLI commands.

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def is_positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def coerce_int(value: Any, default: int = 0, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# String sanitization
# ---------------------------------------------------------------------------

_TAG_RE    = re.compile(r"^[a-zA-Z0-9_\-\.]{1,64}$")
_METRIC_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,80}$")

SAFE_PERIODS = {"daily", "weekly", "monthly"}
SAFE_FORMATS = {"csv", "json", "markdown", "md"}
SAFE_ORDERS  = {"complexity", "churn", "loc", "lines"}


def sanitize_tag(value: Any) -> Optional[str]:
    s = str(value).strip() if value is not None else ""
    if _TAG_RE.match(s):
        return s
    return None


def sanitize_metric(value: Any) -> Optional[str]:
    s = str(value).strip() if value is not None else ""
    if _METRIC_RE.match(s):
        return s
    return None


def sanitize_period(value: Any) -> Optional[str]:
    s = str(value).strip().lower() if value is not None else ""
    return s if s in SAFE_PERIODS else None


def sanitize_format(value: Any) -> str:
    s = str(value).strip().lower() if value is not None else "csv"
    return s if s in SAFE_FORMATS else "csv"


def sanitize_order(value: Any) -> str:
    s = str(value).strip().lower() if value is not None else "complexity"
    return s if s in SAFE_ORDERS else "complexity"


def sanitize_text(value: Any, max_len: int = 2000) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_len]


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def validate_repo_path(path: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    Validate and normalise a filesystem path intended to be a git repo root.
    Returns (normalised_path, error_message). error_message is None on success.
    """
    if not path:
        return None, "Path is required."
    p = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isdir(p):
        return None, f"Directory does not exist: {p}"
    git_dir = os.path.join(p, ".git")
    if not os.path.exists(git_dir):
        return None, f"Not a git repository (no .git found): {p}"
    return p, None


# ---------------------------------------------------------------------------
# API request body validators
# ---------------------------------------------------------------------------

def validate_add_repo(body: Dict[str, Any]) -> Tuple[Dict, List[str]]:
    errors: List[str] = []
    out: Dict[str, Any] = {}

    path, err = validate_repo_path(body.get("path"))
    if err:
        errors.append(err)
    else:
        out["path"] = path

    name = sanitize_text(body.get("name"), max_len=128)
    if name:
        out["name"] = name

    return out, errors


def validate_start_session(body: Dict[str, Any]) -> Tuple[Dict, List[str]]:
    errors: List[str] = []
    out: Dict[str, Any] = {}

    repo_path = body.get("repo_path")
    if repo_path:
        p, err = validate_repo_path(repo_path)
        if err:
            errors.append(err)
        else:
            out["repo_path"] = p

    tags = body.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    clean_tags = []
    for t in tags:
        st = sanitize_tag(t)
        if st:
            clean_tags.append(st)
        else:
            errors.append(f"Invalid tag: {t!r}")
    out["tags"] = clean_tags

    return out, errors


def validate_patch_session(body: Dict[str, Any]) -> Tuple[Dict, List[str]]:
    errors: List[str] = []
    out: Dict[str, Any] = {}

    if "notes" in body:
        out["notes"] = sanitize_text(body["notes"], max_len=4000)

    if "tags" in body:
        tags = body["tags"] if isinstance(body["tags"], list) else []
        clean = []
        for t in tags:
            st = sanitize_tag(t)
            if st:
                clean.append(st)
            else:
                errors.append(f"Invalid tag: {t!r}")
        out["tags"] = clean

    return out, errors


def validate_add_goal(body: Dict[str, Any]) -> Tuple[Dict, List[str]]:
    errors: List[str] = []
    out: Dict[str, Any] = {}

    metric = sanitize_metric(body.get("metric"))
    if not metric:
        errors.append("metric is required and must be alphanumeric (max 80 chars).")
    else:
        out["metric"] = metric

    try:
        target = float(body["target"])
        if target <= 0:
            raise ValueError
        out["target"] = target
    except (KeyError, TypeError, ValueError):
        errors.append("target must be a positive number.")

    period = sanitize_period(body.get("period"))
    if not period:
        errors.append(f"period must be one of: {', '.join(sorted(SAFE_PERIODS))}.")
    else:
        out["period"] = period

    return out, errors


def validate_export_params(params: Dict[str, Any]) -> Tuple[Dict, List[str]]:
    errors: List[str] = []
    out: Dict[str, Any] = {}

    out["fmt"]  = sanitize_format(params.get("format", "csv"))
    out["days"] = coerce_int(params.get("days", 30), default=30, lo=1, hi=365)

    return out, errors