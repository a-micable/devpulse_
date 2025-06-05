# devpulse/server/handlers/api.py
# All JSON API route handlers.
# Each handler follows the signature: (req, res, params) -> None
# Registered onto the Router via register_api_routes().
# No business logic here — handlers call into db/queries and core/.

import json
import logging
import os
import sqlite3
from typing import Optional

from ..app import Request, Response
from ..router import Router
from ...db.queries import (
    SessionQueries,
    RepoQueries,
    CommitQueries,
    MetricsQueries,
    HeartbeatQueries,
    TagQueries,
    TokenQueries,
    GoalQueries,
)
from ...utils.dates import (
    DateRange,
    date_series,
    fill_date_gaps,
    format_duration,
    today_str,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

def _paginate(req: Request) -> tuple:
    """Extract and validate limit/offset from query params."""
    limit  = min(req.query_int("limit",  default=20), 500)
    offset = max(req.query_int("offset", default=0),  0)
    return limit, offset


def _date_range_from_req(req: Request,
                          default_days: int = 30) -> DateRange:
    """
    Build a DateRange from ?start=&end= query params.
    Falls back to last default_days days if not supplied.
    """
    start_str = req.query("start")
    end_str   = req.query("end")
    if start_str and end_str:
        try:
            return DateRange.from_strings(start_str, end_str)
        except ValueError:
            pass
    return DateRange.last_n_days(default_days)


def _repo_id_or_404(req: Request, res: Response,
                    conn: sqlite3.Connection,
                    param_key: str = "id") -> Optional[int]:
    """
    Parse repo id from path params, verify it exists in DB.
    Returns None and writes 404 if not found.
    """
    raw = req.path_params.get(param_key, "")
    try:
        repo_id = int(raw)
    except (ValueError, TypeError):
        res.json({"error": f"Invalid repo id: {raw!r}"}, status=400)
        return None

    repo = RepoQueries.get_by_id(conn, repo_id)
    if not repo:
        res.json({"error": f"Repo {repo_id} not found"}, status=404)
        return None
    return repo_id


def _session_id_or_404(req: Request, res: Response,
                        conn: sqlite3.Connection,
                        param_key: str = "id") -> Optional[int]:
    raw = req.path_params.get(param_key, "")
    try:
        sid = int(raw)
    except (ValueError, TypeError):
        res.json({"error": f"Invalid session id: {raw!r}"}, status=400)
        return None

    session = SessionQueries.get_by_id(conn, sid)
    if not session:
        res.json({"error": f"Session {sid} not found"}, status=404)
        return None
    return sid


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register_api_routes(router: Router,
                        conn: sqlite3.Connection,
                        config=None,
                        analyzer=None) -> None:
    """
    Register all /api/* routes onto the router.
    conn: open SQLite connection
    config: Config instance
    analyzer: optional BatchAnalyzer for summary endpoint
    """

    # ----------------------------------------------------------------
    # Health / meta
    # ----------------------------------------------------------------

    @router.get("/api")
    def api_root(req: Request, res: Response, params: dict) -> None:
        routes = router.all_routes()
        api_routes = [r for r in routes if r["pattern"].startswith("/api")]
        res.json({
            "name": "devpulse API",
            "version": "1.0.0",
            "endpoints": len(api_routes),
            "routes": [
                f"{r['method']} {r['pattern']}" for r in api_routes
            ],
        })

    # ----------------------------------------------------------------
    # Auth
    # ----------------------------------------------------------------

    @router.post("/api/auth/token")
    def create_token(req: Request, res: Response,
                     params: dict) -> None:
        body = req.body_json() or {}
        label = str(body.get("label", "")).strip()
        expires_at = body.get("expires_at")

        token = TokenQueries.create(conn, label=label,
                                    expires_at=expires_at)
        res.json({"token": token, "label": label}, status=201)

    @router.delete("/api/auth/token")
    def revoke_token(req: Request, res: Response,
                     params: dict) -> None:
        body = req.body_json() or {}
        token = str(body.get("token", "")).strip()
        if not token:
            res.json({"error": "token field required"}, status=400)
            return
        TokenQueries.revoke(conn, token)
        res.json({"revoked": True})

    @router.get("/api/auth/tokens")
    def list_tokens(req: Request, res: Response,
                    params: dict) -> None:
        tokens = TokenQueries.list_active(conn)
        res.json_envelope(tokens)

    # ----------------------------------------------------------------
    # Repos
    # ----------------------------------------------------------------

    @router.get("/api/repos")
    def list_repos(req: Request, res: Response, params: dict) -> None:
        include_inactive = req.query("inactive") == "1"
        repos = (RepoQueries.list_all(conn) if include_inactive
                 else RepoQueries.list_active(conn))
        res.json_envelope(repos, meta={"count": len(repos)})

    @router.post("/api/repos")
    def create_repo(req: Request, res: Response,
                    params: dict) -> None:
        body = req.body_json()
        if not body:
            res.json({"error": "Request body required"}, status=400)
            return

        path = str(body.get("path", "")).strip()
        name = str(body.get("name", "")).strip()
        remote = str(body.get("remote_url", "")).strip()

        if not path:
            res.json({"error": "path is required"}, status=400)
            return

        path = os.path.abspath(path)
        if not os.path.isdir(path):
            res.json({"error": f"Directory not found: {path}"},
                     status=400)
            return

        existing = RepoQueries.get_by_path(conn, path)
        if existing:
            res.json({"error": "Repo already registered",
                      "repo": existing},
                     status=409)
            return

        if not name:
            name = os.path.basename(path)

        repo_id = RepoQueries.insert(
            conn, name=name, path=path, remote_url=remote
        )
        repo = RepoQueries.get_by_id(conn, repo_id)
        res.json_envelope(repo, status=201)

    @router.get("/api/repos/:id")
    def get_repo(req: Request, res: Response, params: dict) -> None:
        repo_id = _repo_id_or_404(req, res, conn)
        if repo_id is None:
            return
        repo = RepoQueries.get_by_id(conn, repo_id)
        res.json_envelope(repo)

    @router.delete("/api/repos/:id")
    def delete_repo(req: Request, res: Response,
                    params: dict) -> None:
        repo_id = _repo_id_or_404(req, res, conn)
        if repo_id is None:
            return
        RepoQueries.delete(conn, repo_id)
        res.json({"deleted": True, "repo_id": repo_id})

    @router.get("/api/repos/:id/stats")
    def repo_stats(req: Request, res: Response,
                   params: dict) -> None:
        repo_id = _repo_id_or_404(req, res, conn)
        if repo_id is None:
            return

        dr = _date_range_from_req(req, default_days=30)
        commit_count = CommitQueries.count_by_repo(conn, repo_id)
        session_count = SessionQueries.count_by_repo(conn, repo_id)
        metrics_summary = MetricsQueries.repo_summary(conn, repo_id)
        lang_breakdown = MetricsQueries.language_breakdown(conn, repo_id)
        author_stats = CommitQueries.author_stats(conn, repo_id)

        commits_by_day = CommitQueries.commits_per_day(
            conn, repo_id, dr.start_iso(), dr.end_iso()
        )
        filled = fill_date_gaps(
            commits_by_day, "date", ["count"],
            dr.start_iso()[:10], dr.end_iso()[:10]
        )

        res.json_envelope({
            "repo_id": repo_id,
            "commit_count": commit_count,
            "session_count": session_count,
            "metrics": metrics_summary,
            "language_breakdown": lang_breakdown,
            "author_count": len(author_stats),
            "commits_by_day": filled,
        })

    @router.get("/api/repos/:id/commits")
    def repo_commits(req: Request, res: Response,
                     params: dict) -> None:
        repo_id = _repo_id_or_404(req, res, conn)
        if repo_id is None:
            return

        limit, offset = _paginate(req)
        exclude_merges = req.query("merges") != "1"
        commits = CommitQueries.list_by_repo(
            conn, repo_id,
            limit=limit, offset=offset,
            exclude_merges=exclude_merges,
        )
        total = CommitQueries.count_by_repo(conn, repo_id)
        res.paginated(commits, total=total,
                      limit=limit, offset=offset)

    @router.get("/api/repos/:id/authors")
    def repo_authors(req: Request, res: Response,
                     params: dict) -> None:
        repo_id = _repo_id_or_404(req, res, conn)
        if repo_id is None:
            return
        authors = CommitQueries.author_stats(conn, repo_id)
        res.json_envelope(authors, meta={"count": len(authors)})

    @router.get("/api/repos/:id/hotspots")
    def repo_hotspots(req: Request, res: Response,
                      params: dict) -> None:
        repo_id = _repo_id_or_404(req, res, conn)
        if repo_id is None:
            return

        limit = min(req.query_int("limit", 20), 100)
        order = req.query("order", "churn_score")
        files = MetricsQueries.list_by_repo(
            conn, repo_id, order_by=order, limit=limit
        )
        res.json_envelope(files, meta={"count": len(files)})

    @router.get("/api/repos/:id/metrics")
    def repo_metrics(req: Request, res: Response,
                     params: dict) -> None:
        repo_id = _repo_id_or_404(req, res, conn)
        if repo_id is None:
            return

        from ...core.metrics import MetricsSummary
        summary = MetricsSummary(repo_id, conn).build()
        res.json_envelope(summary)

    @router.get("/api/repos/:id/commits/hourly")
    def repo_commits_hourly(req: Request, res: Response,
                             params: dict) -> None:
        repo_id = _repo_id_or_404(req, res, conn)
        if repo_id is None:
            return
        dist = CommitQueries.hourly_distribution(conn, repo_id)
        # Fill in missing hours
        hour_map = {r["hour"]: r["count"] for r in dist}
        filled = [
            {"hour": h, "count": hour_map.get(h, 0)}
            for h in range(24)
        ]
        res.json_envelope(filled)

    @router.get("/api/repos/:id/commits/weekday")
    def repo_commits_weekday(req: Request, res: Response,
                              params: dict) -> None:
        repo_id = _repo_id_or_404(req, res, conn)
        if repo_id is None:
            return
        dist = CommitQueries.weekday_distribution(conn, repo_id)
        weekday_map = {r["weekday"]: r["count"] for r in dist}
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        filled = [
            {"weekday": d, "name": names[d],
             "count": weekday_map.get(d, 0)}
            for d in range(7)
        ]
        res.json_envelope(filled)

    # ----------------------------------------------------------------
    # Sessions
    # ----------------------------------------------------------------

    @router.get("/api/sessions")
    def list_sessions(req: Request, res: Response,
                      params: dict) -> None:
        limit, offset = _paginate(req)
        dr = _date_range_from_req(req, default_days=30)

        sessions = SessionQueries.list_by_date_range(
            conn,
            dr.start_iso(), dr.end_iso(),
            limit=limit, offset=offset,
        )
        total = SessionQueries.total_count(conn)

        # Enrich with human duration
        for s in sessions:
            s["duration_human"] = format_duration(
                s.get("duration_s") or 0
            )

        res.paginated(sessions, total=total,
                      limit=limit, offset=offset)

    @router.get("/api/sessions/active")
    def active_session(req: Request, res: Response,
                       params: dict) -> None:
        session = SessionQueries.get_active(conn)
        if not session:
            res.json_envelope(None,
                              meta={"active": False})
            return
        session["duration_human"] = format_duration(
            session.get("duration_s") or 0
        )
        res.json_envelope(session, meta={"active": True})

    @router.get("/api/sessions/:id")
    def get_session(req: Request, res: Response,
                    params: dict) -> None:
        sid = _session_id_or_404(req, res, conn)
        if sid is None:
            return
        session = SessionQueries.get_by_id(conn, sid)
        session["duration_human"] = format_duration(
            session.get("duration_s") or 0
        )
        tags = TagQueries.list_for_session(conn, sid)
        session["tags"] = tags
        hb_count = HeartbeatQueries.count_for_session(conn, sid)
        session["heartbeat_count"] = hb_count
        res.json_envelope(session)

    @router.post("/api/sessions")
    def create_session(req: Request, res: Response,
                       params: dict) -> None:
        body = req.body_json() or {}
        repo_id = body.get("repo_id")
        started_at = body.get("started_at")

        if repo_id:
            try:
                repo_id = int(repo_id)
            except (ValueError, TypeError):
                res.json({"error": "repo_id must be integer"},
                         status=400)
                return

        sid = SessionQueries.insert(
            conn, repo_id=repo_id, started_at=started_at
        )
        session = SessionQueries.get_by_id(conn, sid)
        res.json_envelope(session, status=201)

    @router.patch("/api/sessions/:id")
    def update_session(req: Request, res: Response,
                       params: dict) -> None:
        sid = _session_id_or_404(req, res, conn)
        if sid is None:
            return

        body = req.body_json() or {}
        notes = body.get("notes")
        tags  = body.get("tags")

        if notes is not None:
            conn.execute(
                "UPDATE sessions SET notes = ? WHERE id = ?;",
                (str(notes), sid)
            )
            conn.commit()

        if tags is not None and isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and tag.strip():
                    TagQueries.insert(conn, name=tag.strip(),
                                      session_id=sid)

        session = SessionQueries.get_by_id(conn, sid)
        res.json_envelope(session)

    @router.get("/api/sessions/daily")
    def sessions_daily(req: Request, res: Response,
                       params: dict) -> None:
        dr = _date_range_from_req(req, default_days=30)
        daily = SessionQueries.total_duration_by_date(
            conn, dr.start_iso(), dr.end_iso()
        )
        filled = fill_date_gaps(
            daily, "date",
            ["total_seconds", "session_count"],
            dr.start_iso()[:10], dr.end_iso()[:10],
        )
        for row in filled:
            row["duration_human"] = format_duration(
                row.get("total_seconds") or 0
            )
        res.json_envelope(filled)

    # ----------------------------------------------------------------
    # Metrics
    # ----------------------------------------------------------------

    @router.get("/api/metrics")
    def global_metrics(req: Request, res: Response,
                       params: dict) -> None:
        repos = RepoQueries.list_active(conn)
        result = []
        for repo in repos:
            summary = MetricsQueries.repo_summary(conn, repo["id"])
            summary["repo_id"]   = repo["id"]
            summary["repo_name"] = repo.get("name", "")
            result.append(summary)
        res.json_envelope(result)

    # ----------------------------------------------------------------
    # Activity
    # ----------------------------------------------------------------

    @router.get("/api/activity/heatmap")
    def activity_heatmap(req: Request, res: Response,
                         params: dict) -> None:
        """
        Returns daily commit counts for the last 365 days
        in a format suitable for rendering a GitHub-style heatmap.
        [{date, count, level}] where level is 0-4.
        """
        dr = DateRange.last_n_days(365)
        repos = RepoQueries.list_active(conn)

        date_counts: dict = {}
        for repo in repos:
            rows = CommitQueries.commits_per_day(
                conn, repo["id"],
                dr.start_iso(), dr.end_iso()
            )
            for row in rows:
                d = row["date"]
                date_counts[d] = date_counts.get(d, 0) + row["count"]

        all_dates = date_series(
            dr.start_iso()[:10], dr.end_iso()[:10]
        )
        max_count = max(date_counts.values(), default=1)

        def _level(count: int) -> int:
            if count == 0: return 0
            ratio = count / max_count
            if ratio < 0.25: return 1
            if ratio < 0.50: return 2
            if ratio < 0.75: return 3
            return 4

        data = [
            {"date": d,
             "count": date_counts.get(d, 0),
             "level": _level(date_counts.get(d, 0))}
            for d in all_dates
        ]
        res.json_envelope(data, meta={"days": len(all_dates)})

    @router.get("/api/activity/leaderboard")
    def leaderboard(req: Request, res: Response,
                    params: dict) -> None:
        """
        Cross-repo author leaderboard by total commits.
        """
        dr = _date_range_from_req(req, default_days=30)
        repos = RepoQueries.list_active(conn)

        author_totals: dict = {}
        for repo in repos:
            authors = CommitQueries.author_stats(conn, repo["id"])
            for a in authors:
                key = a.get("author_email") or a.get("author", "?")
                if key not in author_totals:
                    author_totals[key] = {
                        "author": a.get("author", "?"),
                        "author_email": a.get("author_email", ""),
                        "commit_count": 0,
                        "lines_added": 0,
                        "lines_removed": 0,
                        "repos": [],
                    }
                t = author_totals[key]
                t["commit_count"]   += a.get("commit_count", 0)
                t["lines_added"]    += a.get("total_added", 0)
                t["lines_removed"]  += a.get("total_removed", 0)
                t["repos"].append(repo.get("name", "?"))

        board = sorted(
            author_totals.values(),
            key=lambda x: x["commit_count"],
            reverse=True,
        )[:20]

        res.json_envelope(board, meta={"count": len(board)})

    @router.get("/api/activity/streak")
    def commit_streak(req: Request, res: Response,
                      params: dict) -> None:
        """
        Returns current and longest commit streak across all repos.
        """
        from ...utils.dates import calculate_streaks
        repos = RepoQueries.list_active(conn)
        dr = DateRange.last_n_days(365)
        all_dates = []

        for repo in repos:
            rows = CommitQueries.commits_per_day(
                conn, repo["id"],
                dr.start_iso(), dr.end_iso()
            )
            for row in rows:
                if row.get("count", 0) > 0:
                    all_dates.append(row["date"])

        current, longest = calculate_streaks(sorted(all_dates))
        res.json_envelope({
            "current_streak": current,
            "longest_streak": longest,
        })

    # ----------------------------------------------------------------
    # Summary (dashboard)
    # ----------------------------------------------------------------

    @router.get("/api/summary")
    def dashboard_summary(req: Request, res: Response,
                           params: dict) -> None:
        """
        Aggregate summary for the main dashboard.
        Pulls from DB — does not re-run git analysis.
        """
        from ...utils.dates import calculate_streaks

        dr_30 = DateRange.last_n_days(30)
        dr_7  = DateRange.last_n_days(7)

        # Session stats
        total_sessions = SessionQueries.total_count(conn)
        avg_focus = SessionQueries.average_focus_score(conn, days=30)
        daily_totals = SessionQueries.total_duration_by_date(
            conn, dr_30.start_iso(), dr_30.end_iso()
        )
        total_coding_s = sum(
            r.get("total_seconds") or 0 for r in daily_totals
        )

        # Repo stats
        repo_count = RepoQueries.count(conn)
        repos = RepoQueries.list_active(conn)

        # Commit stats
        total_commits = sum(
            CommitQueries.count_by_repo(conn, r["id"])
            for r in repos
        )

        # Streak
        all_commit_dates = []
        for repo in repos:
            rows = CommitQueries.commits_per_day(
                conn, repo["id"],
                dr_30.start_iso(), dr_30.end_iso(),
            )
            for row in rows:
                if row.get("count", 0) > 0:
                    all_commit_dates.append(row["date"])

        current_streak, longest_streak = calculate_streaks(
            sorted(all_commit_dates)
        )

        # Recent sessions
        recent_sessions = SessionQueries.list_recent(conn, limit=5)
        for s in recent_sessions:
            s["duration_human"] = format_duration(
                s.get("duration_s") or 0
            )

        # Active goals
        goals = GoalQueries.list_active(conn)

        res.json_envelope({
            "session_count": total_sessions,
            "repo_count": repo_count,
            "total_commits": total_commits,
            "total_coding_seconds": total_coding_s,
            "total_coding_human": format_duration(total_coding_s),
            "avg_focus_score": avg_focus,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "recent_sessions": recent_sessions,
            "active_goals": goals,
            "period_days": 30,
        })

    # ----------------------------------------------------------------
    # Goals
    # ----------------------------------------------------------------

    @router.get("/api/goals")
    def list_goals(req: Request, res: Response,
                   params: dict) -> None:
        goals = GoalQueries.list_active(conn)
        res.json_envelope(goals, meta={"count": len(goals)})

    @router.post("/api/goals")
    def create_goal(req: Request, res: Response,
                    params: dict) -> None:
        body = req.body_json()
        if not body:
            res.json({"error": "Request body required"}, status=400)
            return

        metric = str(body.get("metric", "")).strip()
        target = body.get("target")
        period = str(body.get("period", "daily")).strip()
        repo_id = body.get("repo_id")

        if not metric:
            res.json({"error": "metric is required"}, status=400)
            return

        try:
            target = float(target)
        except (TypeError, ValueError):
            res.json({"error": "target must be a number"}, status=400)
            return

        valid_periods = ("daily", "weekly", "monthly")
        if period not in valid_periods:
            res.json(
                {"error": f"period must be one of {valid_periods}"},
                status=400,
            )
            return

        goal_id = GoalQueries.insert(
            conn, metric=metric, target=target,
            period=period, repo_id=repo_id,
        )
        goal = conn.execute(
            "SELECT * FROM goals WHERE id = ?;", (goal_id,)
        ).fetchone()
        res.json_envelope(dict(goal) if goal else {}, status=201)

    @router.delete("/api/goals/:id")
    def delete_goal(req: Request, res: Response,
                    params: dict) -> None:
        raw = req.path_params.get("id", "")
        try:
            goal_id = int(raw)
        except (ValueError, TypeError):
            res.json({"error": "Invalid goal id"}, status=400)
            return
        GoalQueries.deactivate(conn, goal_id)
        res.json({"deactivated": True, "goal_id": goal_id})

    @router.get("/api/goals/:id/progress")
    def goal_progress(req: Request, res: Response,
                      params: dict) -> None:
        raw = req.path_params.get("id", "")
        try:
            goal_id = int(raw)
        except (ValueError, TypeError):
            res.json({"error": "Invalid goal id"}, status=400)
            return
        days = req.query_int("days", 30)
        progress = GoalQueries.progress_for_goal(conn, goal_id, days)
        res.json_envelope(progress, meta={"goal_id": goal_id,
                                           "days": days})

    @router.post("/api/goals/:id/progress")
    def record_goal_progress(req: Request, res: Response,
                              params: dict) -> None:
        raw = req.path_params.get("id", "")
        try:
            goal_id = int(raw)
        except (ValueError, TypeError):
            res.json({"error": "Invalid goal id"}, status=400)
            return

        body = req.body_json() or {}
        date_str = str(body.get("date", today_str()))
        value = body.get("value", 0)
        try:
            value = float(value)
        except (TypeError, ValueError):
            res.json({"error": "value must be a number"}, status=400)
            return

        GoalQueries.record_progress(conn, goal_id, date_str, value)
        res.json({"recorded": True, "goal_id": goal_id,
                  "date": date_str, "value": value})

    # ----------------------------------------------------------------
    # Export
    # ----------------------------------------------------------------

    @router.get("/api/export/csv")
    def export_csv(req: Request, res: Response,
                   params: dict) -> None:
        from ...core.reporter import Reporter
        days = req.query_int("days", 30)
        reporter = Reporter(conn)
        content = reporter.export_sessions_string("csv", days=days)
        res.set_header(
            "Content-Disposition",
            f"attachment; filename=\"sessions_{today_str()}.csv\""
        )
        res.send(content, content_type="text/csv; charset=utf-8")

    @router.get("/api/export/json")
    def export_json(req: Request, res: Response,
                    params: dict) -> None:
        from ...core.reporter import Reporter
        days = req.query_int("days", 30)
        reporter = Reporter(conn)
        content = reporter.export_sessions_string("json", days=days)
        res.set_header(
            "Content-Disposition",
            f"attachment; filename=\"sessions_{today_str()}.json\""
        )
        res.send(content,
                 content_type="application/json; charset=utf-8")

    @router.get("/api/export/markdown")
    def export_markdown(req: Request, res: Response,
                        params: dict) -> None:
        from ...core.reporter import Reporter
        reporter = Reporter(conn)
        content = reporter.export_sessions_string("markdown")
        res.set_header(
            "Content-Disposition",
            f"attachment; filename=\"report_{today_str()}.md\""
        )
        res.send(content,
                 content_type="text/markdown; charset=utf-8")

    @router.get("/api/repos/:id/export/csv")
    def export_repo_metrics_csv(req: Request, res: Response,
                                 params: dict) -> None:
        repo_id = _repo_id_or_404(req, res, conn)
        if repo_id is None:
            return

        from ...core.reporter import CsvExporter
        metrics = MetricsQueries.list_by_repo(
            conn, repo_id, limit=100000
        )
        exporter = CsvExporter()
        content = io_metrics_to_string(metrics)
        res.set_header(
            "Content-Disposition",
            f"attachment; filename=\"metrics_repo{repo_id}_{today_str()}.csv\""
        )
        res.send(content, content_type="text/csv; charset=utf-8")

    # ----------------------------------------------------------------
    # Tags
    # ----------------------------------------------------------------

    @router.get("/api/tags")
    def list_all_tags(req: Request, res: Response,
                      params: dict) -> None:
        tags = TagQueries.all_unique(conn)
        res.json_envelope(tags, meta={"count": len(tags)})

    @router.get("/api/sessions/:id/tags")
    def session_tags(req: Request, res: Response,
                     params: dict) -> None:
        sid = _session_id_or_404(req, res, conn)
        if sid is None:
            return
        tags = TagQueries.list_for_session(conn, sid)
        res.json_envelope(tags)

    @router.post("/api/sessions/:id/tags")
    def add_session_tag(req: Request, res: Response,
                        params: dict) -> None:
        sid = _session_id_or_404(req, res, conn)
        if sid is None:
            return

        body = req.body_json() or {}
        name = str(body.get("name", "")).strip()
        if not name:
            res.json({"error": "name is required"}, status=400)
            return

        TagQueries.insert(conn, name=name, session_id=sid)
        res.json({"added": True, "tag": name,
                  "session_id": sid}, status=201)

    logger.info("API routes registered.")


# ---------------------------------------------------------------------------
# Internal helper for repo metrics CSV streaming
# ---------------------------------------------------------------------------

def io_metrics_to_string(rows: list) -> str:
    import csv
    import io

    if not rows:
        return ""

    output = io.StringIO()
    fieldnames = [
        "file_path", "language", "loc", "blank_lines",
        "comment_lines", "function_count", "class_count",
        "complexity", "churn_score", "last_computed",
    ]
    writer = csv.DictWriter(
        output, fieldnames=fieldnames, extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()