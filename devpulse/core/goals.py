# devpulse/core/goals.py
# Goal evaluation engine.
# Reads active goals from DB, computes current progress,
# and returns pass/fail status for each goal period.

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from ..db.queries import GoalQueries, SessionQueries, CommitQueries
from ..utils.dates import week_bounds, today_str

log = logging.getLogger(__name__)


class GoalEvaluator:
    """
    Evaluates all active goals against recent session and commit data.
    Returns a list of goal result dicts with current progress and status.
    """

    def __init__(self, conn, config=None):
        self.conn   = conn
        self.config = config

    def evaluate_all(self) -> List[Dict[str, Any]]:
        goals = GoalQueries.list_active(self.conn)
        results = []
        for goal in goals:
            result = self._evaluate(goal)
            results.append(result)
        return results

    def _evaluate(self, goal: Dict[str, Any]) -> Dict[str, Any]:
        metric = goal.get("metric", "")
        target = float(goal.get("target", 0))
        period = goal.get("period", "daily")

        current = self._measure(metric, period)
        pct     = (current / target * 100) if target > 0 else 0
        met     = current >= target

        return {
            "id":       goal["id"],
            "metric":   metric,
            "target":   target,
            "period":   period,
            "current":  current,
            "pct":      round(pct, 1),
            "met":      met,
            "status":   "met" if met else "in_progress",
        }

    def _measure(self, metric: str, period: str) -> float:
        start, end = self._period_bounds(period)

        if metric == "coding_time":
            sessions = SessionQueries.list_range(self.conn, start=start, end=end)
            return sum(s.get("duration_s") or 0 for s in sessions)

        if metric == "sessions":
            sessions = SessionQueries.list_range(self.conn, start=start, end=end)
            return float(len(sessions))

        if metric == "commits":
            # sum across all repos
            total = 0
            try:
                from ..db.queries import RepoQueries
                repos = RepoQueries.list_all(self.conn)
                for repo in repos:
                    total += CommitQueries.count_range(
                        self.conn, repo["id"], start=start, end=end
                    )
            except Exception:
                pass
            return float(total)

        if metric == "focus_score":
            sessions = SessionQueries.list_range(self.conn, start=start, end=end)
            if not sessions:
                return 0.0
            avg = sum(s.get("focus_score") or 0 for s in sessions) / len(sessions)
            return round(avg * 100, 1)

        log.warning("Unknown goal metric: %s", metric)
        return 0.0

    def _period_bounds(self, period: str):
        today = datetime.date.today()
        if period == "daily":
            s = today.isoformat()
            e = today.isoformat()
            return s + "T00:00:00", e + "T23:59:59"
        if period == "weekly":
            s, e = week_bounds(0)
            return s + "T00:00:00", e + "T23:59:59"
        if period == "monthly":
            first = today.replace(day=1)
            s = first.isoformat() + "T00:00:00"
            e = today.isoformat() + "T23:59:59"
            return s, e
        return today.isoformat() + "T00:00:00", today.isoformat() + "T23:59:59"