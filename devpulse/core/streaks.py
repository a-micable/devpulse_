# devpulse/core/streaks.py
# Streak calculation engine.
# A "coding day" is any calendar day with at least one completed session.
# Streaks reset if a day is skipped (no session that day).

from __future__ import annotations

import datetime
import logging
from typing import Dict, List, Optional, Set

from ..db.queries import SessionQueries

log = logging.getLogger(__name__)


class StreakCalculator:
    """
    Computes current streak, longest streak, and active days
    from the sessions table.
    """

    def __init__(self, conn, tz: str = "UTC"):
        self.conn = conn
        self.tz   = tz  # stored for future timezone support; currently UTC only

    def compute(self) -> Dict:
        """
        Returns:
            current_streak  — consecutive days ending today (or yesterday)
            longest_streak  — longest consecutive run ever
            total_days      — distinct days with at least one session
            last_active     — ISO date string of the most recent active day
            active_days     — sorted list of ISO date strings
        """
        days = self._active_days()

        if not days:
            return {
                "current_streak": 0,
                "longest_streak": 0,
                "total_days":     0,
                "last_active":    None,
                "active_days":    [],
            }

        current  = self._current_streak(days)
        longest  = self._longest_streak(days)
        last     = max(days)

        return {
            "current_streak": current,
            "longest_streak": longest,
            "total_days":     len(days),
            "last_active":    last,
            "active_days":    sorted(days),
        }

    def _active_days(self) -> Set[str]:
        """Return a set of ISO date strings (YYYY-MM-DD) for days with sessions."""
        sessions = SessionQueries.list_recent(self.conn, limit=10000)
        days: Set[str] = set()
        for s in sessions:
            started = s.get("started_at") or ""
            if started:
                day = str(started)[:10]
                if len(day) == 10:
                    days.add(day)
        return days

    def _current_streak(self, days: Set[str]) -> int:
        today     = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)

        # streak can end today or yesterday
        anchor = today if today.isoformat() in days else yesterday
        if anchor.isoformat() not in days:
            return 0

        streak  = 0
        current = anchor
        while current.isoformat() in days:
            streak  += 1
            current -= datetime.timedelta(days=1)
        return streak

    def _longest_streak(self, days: Set[str]) -> int:
        if not days:
            return 0

        sorted_days = sorted(
            datetime.date.fromisoformat(d) for d in days
        )

        longest = 1
        current = 1
        for i in range(1, len(sorted_days)):
            delta = (sorted_days[i] - sorted_days[i - 1]).days
            if delta == 1:
                current += 1
                longest = max(longest, current)
            elif delta > 1:
                current = 1
        return longest