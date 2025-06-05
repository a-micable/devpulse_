# devpulse/utils/dates.py
# Date and time utilities. Zero external dependencies.
# All datetime math done with stdlib datetime module.

import re
from datetime import datetime, timedelta, timezone, date
from typing import List, Tuple, Optional


_FMT_ISO = "%Y-%m-%dT%H:%M:%S"
_FMT_DATE = "%Y-%m-%d"
_FMT_DISPLAY = "%b %d, %Y"
_FMT_DISPLAY_TIME = "%b %d, %Y %H:%M"


def now_iso() -> str:
    """Current UTC time as ISO string."""
    return datetime.now(timezone.utc).strftime(_FMT_ISO)


def today_str() -> str:
    """Today's date as YYYY-MM-DD."""
    return date.today().strftime(_FMT_DATE)


def parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO datetime string. Returns None on failure."""
    if not s:
        return None
    # Handle both "2024-03-15T14:22:01" and "2024-03-15 14:22:01"
    s = s.strip().replace(" ", "T")
    # Strip timezone suffix if present
    s = re.sub(r"[+-]\d{2}:?\d{2}$", "", s).strip()
    # Strip trailing Z
    s = s.rstrip("Z")
    # Try formats from most specific (microseconds) to least.
    fmts = ["%Y-%m-%dT%H:%M:%S.%f", _FMT_ISO, _FMT_DATE,
            "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H"]
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            # Try slicing in case extra fractional or timezone text remains
            try:
                return datetime.strptime(s[: len(fmt)], fmt)
            except Exception:
                continue
    return None


def parse_dt(s: str) -> Optional[datetime]:
    """Parse a datetime-like value into a datetime object."""
    if isinstance(s, datetime):
        return s
    if s is None:
        return None
    return parse_iso(str(s))


def parse_date(s: str) -> Optional[date]:
    """Parse a YYYY-MM-DD string into a date object."""
    try:
        return datetime.strptime(s.strip(), _FMT_DATE).date()
    except (ValueError, AttributeError):
        return None


def format_date(value, with_time: bool = False) -> str:
    """Format an ISO string or datetime object to a human-readable date."""
    if isinstance(value, str):
        dt = parse_iso(value)
    elif isinstance(value, datetime):
        dt = value
    else:
        return str(value)
    if not dt:
        return str(value)
    fmt = _FMT_DISPLAY_TIME if with_time else _FMT_DISPLAY
    return dt.strftime(fmt)


def date_range(days: int) -> List[str]:
    """Return the last N dates as YYYY-MM-DD strings."""
    if days <= 0:
        return []
    today_date = date.today()
    return [
        (today_date - timedelta(days=i)).strftime(_FMT_DATE)
        for i in reversed(range(days))
    ]


def week_bounds(offset: int = 0) -> Tuple[str, str]:
    """Return the start and end dates of the current or offset week."""
    today_date = date.today() + timedelta(weeks=offset)
    start = today_date - timedelta(days=today_date.weekday())
    end = start + timedelta(days=6)
    return start.strftime(_FMT_DATE), end.strftime(_FMT_DATE)


def iso_week_label(value=None) -> str:
    """Return an ISO week label like '2025-W12'."""
    dt = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = parse_iso(value)
    else:
        dt = datetime.now()
    if not dt:
        return ""
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def to_iso(dt: datetime) -> str:
    return dt.strftime(_FMT_ISO)


def to_date_str(dt: datetime) -> str:
    return dt.strftime(_FMT_DATE)


class DateRange:
    """
    Immutable date range with start and end as datetime objects.
    Provides factory methods for common ranges and iteration utilities.
    """

    def __init__(self, start: datetime, end: datetime):
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        self.start = start
        self.end = end

    @classmethod
    def today(cls) -> "DateRange":
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        return cls(start, end)

    @classmethod
    def yesterday(cls) -> "DateRange":
        yesterday = datetime.now() - timedelta(days=1)
        start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = yesterday.replace(hour=23, minute=59, second=59, microsecond=0)
        return cls(start, end)

    @classmethod
    def last_n_days(cls, n: int) -> "DateRange":
        end = datetime.now()
        start = (end - timedelta(days=n)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return cls(start, end)

    @classmethod
    def this_week(cls) -> "DateRange":
        now = datetime.now()
        # Week starts Monday
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = now
        return cls(start, end)

    @classmethod
    def last_week(cls) -> "DateRange":
        now = datetime.now()
        end_of_last = (now - timedelta(days=now.weekday() + 1)).replace(
            hour=23, minute=59, second=59
        )
        start_of_last = (end_of_last - timedelta(days=6)).replace(
            hour=0, minute=0, second=0
        )
        return cls(start_of_last, end_of_last)

    @classmethod
    def this_month(cls) -> "DateRange":
        now = datetime.now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return cls(start, now)

    @classmethod
    def last_month(cls) -> "DateRange":
        now = datetime.now()
        first_of_this = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end = first_of_this - timedelta(seconds=1)
        start = end.replace(day=1, hour=0, minute=0, second=0)
        return cls(start, end)

    @classmethod
    def this_year(cls) -> "DateRange":
        now = datetime.now()
        start = now.replace(
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return cls(start, now)

    @classmethod
    def last_12_months(cls) -> "DateRange":
        return cls.last_n_days(365)

    @classmethod
    def from_strings(cls, start_str: str, end_str: str) -> "DateRange":
        start = parse_iso(start_str)
        end = parse_iso(end_str)
        if not start or not end:
            raise ValueError(
                f"Cannot parse date range: {start_str!r} to {end_str!r}"
            )
        return cls(start, end)

    def start_iso(self) -> str:
        return to_iso(self.start)

    def end_iso(self) -> str:
        return to_iso(self.end)

    def days(self) -> int:
        return (self.end.date() - self.start.date()).days + 1

    def contains(self, dt: datetime) -> bool:
        return self.start <= dt <= self.end

    def date_list(self) -> List[str]:
        """Return all dates in range as YYYY-MM-DD strings."""
        result = []
        current = self.start.date()
        end = self.end.date()
        while current <= end:
            result.append(current.strftime(_FMT_DATE))
            current += timedelta(days=1)
        return result

    def split_by_week(self) -> List["DateRange"]:
        """Split range into weekly sub-ranges."""
        weeks = []
        cursor = self.start
        while cursor < self.end:
            week_end = min(cursor + timedelta(days=6), self.end)
            weeks.append(DateRange(cursor, week_end))
            cursor = week_end + timedelta(seconds=1)
        return weeks

    def __repr__(self) -> str:
        return (
            f"DateRange({self.start.strftime(_FMT_DATE)} "
            f"→ {self.end.strftime(_FMT_DATE)})"
        )


def date_series(start_str: str, end_str: str) -> List[str]:
    """
    Return a list of YYYY-MM-DD strings between start and end inclusive.
    Useful for filling in zero-value days in chart data.
    """
    start = parse_date(start_str)
    end = parse_date(end_str)
    if not start or not end:
        return []
    result = []
    current = start
    while current <= end:
        result.append(current.strftime(_FMT_DATE))
        current += timedelta(days=1)
    return result


def fill_date_gaps(data: List[dict], date_key: str,
                   value_keys: List[str],
                   start: str, end: str) -> List[dict]:
    """
    Given a list of dicts with a date field, fill in missing dates
    with zero values for all value_keys.
    Useful for chart data to avoid gaps in time series.
    """
    existing = {row[date_key]: row for row in data}
    all_dates = date_series(start, end)
    result = []
    for d in all_dates:
        if d in existing:
            result.append(existing[d])
        else:
            row = {date_key: d}
            for k in value_keys:
                row[k] = 0
            result.append(row)
    return result


def format_relative(dt_or_str) -> str:
    """
    Format a datetime as a human-readable relative string.
    e.g. 'just now', '5 minutes ago', '3 days ago', 'Jan 15, 2023'
    """
    if dt_or_str is None:
        return ""
    if isinstance(dt_or_str, str):
        dt = parse_iso(dt_or_str)
        if not dt:
            return dt_or_str
    else:
        dt = dt_or_str

    # Normalize dt to UTC-naive for comparison with utcnow()
    if getattr(dt, "tzinfo", None):
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    now = datetime.utcnow()
    diff = now - dt
    total_seconds = int(diff.total_seconds())

    if total_seconds < 0:
        return "in the future"
    if total_seconds < 30:
        return "just now"
    if total_seconds < 90:
        return "a minute ago"
    if total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes}m ago"
    if total_seconds < 7200:
        return "1h ago"
    if total_seconds < 86400:
        hours = total_seconds // 3600
        return f"{hours}h ago"
    if total_seconds < 172800:
        return "yesterday"
    if total_seconds < 604800:
        days = total_seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"
    if total_seconds < 2592000:
        weeks = total_seconds // 604800
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    if total_seconds < 31536000:
        months = total_seconds // 2592000
        return f"{months} month{'s' if months != 1 else ''} ago"

    return dt.strftime(_FMT_DISPLAY)


def format_duration(seconds: Optional[int]) -> str:
    """
    Format a duration in seconds as a readable string.
    e.g. 3661 → '1h 1m 1s', 90 → '1m 30s', 45 → '45s'
    """
    if seconds is None or seconds < 0:
        return "0s"
    if seconds == 0:
        return "0s"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs:
        parts.append(f"{secs}s")

    return " ".join(parts) if parts else "0s"


def format_duration_long(seconds: int) -> str:
    """
    Verbose duration: '2 hours 15 minutes'
    """
    if seconds <= 0:
        return "0 seconds"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if not parts:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return " ".join(parts)


def calculate_streaks(dates: List[str]) -> Tuple[int, int]:
    """
    Given a sorted list of YYYY-MM-DD date strings (may have duplicates),
    return (current_streak, longest_streak) in days.

    current_streak: consecutive days ending on today or yesterday.
    longest_streak: longest consecutive run anywhere in the list.
    """
    if not dates:
        return 0, 0

    unique_dates = sorted(set(dates))
    parsed = []
    for d in unique_dates:
        p = parse_date(d)
        if p:
            parsed.append(p)

    if not parsed:
        return 0, 0

    # Calculate longest streak
    longest = 1
    current_run = 1
    for i in range(1, len(parsed)):
        if (parsed[i] - parsed[i - 1]).days == 1:
            current_run += 1
            longest = max(longest, current_run)
        elif (parsed[i] - parsed[i - 1]).days > 1:
            current_run = 1

    # Calculate current streak (must end today or yesterday)
    today = date.today()
    yesterday = today - timedelta(days=1)

    if parsed[-1] not in (today, yesterday):
        return 0, longest

    current_streak = 1
    for i in range(len(parsed) - 1, 0, -1):
        if (parsed[i] - parsed[i - 1]).days == 1:
            current_streak += 1
        else:
            break

    return current_streak, longest


def weekday_name(weekday_int: int) -> str:
    """0=Sunday, 1=Monday, ..., 6=Saturday (git strftime %w format)."""
    names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    return names[weekday_int % 7]


def hour_label(hour: int) -> str:
    """Convert 0-23 hour to '12am', '1pm' etc."""
    if hour == 0:
        return "12am"
    if hour < 12:
        return f"{hour}am"
    if hour == 12:
        return "12pm"
    return f"{hour - 12}pm"


def iso_to_display(iso_str: str, include_time: bool = False) -> str:
    """Convert ISO string to human-readable display format."""
    dt = parse_iso(iso_str)
    if not dt:
        return iso_str
    fmt = _FMT_DISPLAY_TIME if include_time else _FMT_DISPLAY
    return dt.strftime(fmt)