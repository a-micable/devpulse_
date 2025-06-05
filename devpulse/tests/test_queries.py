# tests/test_queries.py

import pytest
from devpulse.db.queries import RepoQueries, SessionQueries, CommitQueries, GoalQueries


class TestRepoQueries:
    def test_insert_and_get(self, tmp_db, git_repo):
        rid = RepoQueries.insert(tmp_db, name="myapp", path=git_repo)
        assert isinstance(rid, int) and rid > 0

        repo = RepoQueries.get_by_id(tmp_db, rid)
        assert repo is not None
        assert repo["name"] == "myapp"
        assert repo["path"] == git_repo

    def test_get_by_path(self, tmp_db, git_repo):
        RepoQueries.insert(tmp_db, name="myapp", path=git_repo)
        repo = RepoQueries.get_by_path(tmp_db, git_repo)
        assert repo is not None

    def test_list_all(self, tmp_db, git_repo):
        RepoQueries.insert(tmp_db, name="r1", path=git_repo)
        repos = RepoQueries.list_all(tmp_db)
        assert len(repos) >= 1

    def test_get_nonexistent(self, tmp_db):
        assert RepoQueries.get_by_id(tmp_db, 9999) is None

    def test_delete(self, tmp_db, git_repo):
        rid = RepoQueries.insert(tmp_db, name="myapp", path=git_repo)
        RepoQueries.delete(tmp_db, rid)
        assert RepoQueries.get_by_id(tmp_db, rid) is None


class TestSessionQueries:
    def test_insert_and_get(self, tmp_db, sample_repo):
        sid = SessionQueries.insert(
            tmp_db,
            repo_id=sample_repo["id"],
            started_at="2025-03-01T09:00:00",
        )
        assert sid > 0

        session = SessionQueries.get_by_id(tmp_db, sid)
        assert session is not None
        assert session["repo_id"] == sample_repo["id"]

    def test_list_recent(self, tmp_db, sample_repo):
        for i in range(3):
            SessionQueries.insert(
                tmp_db,
                repo_id=sample_repo["id"],
                started_at=f"2025-03-0{i+1}T09:00:00",
            )
        sessions = SessionQueries.list_recent(tmp_db, limit=10)
        assert len(sessions) == 3

    def test_get_active_none(self, tmp_db):
        active = SessionQueries.get_active(tmp_db)
        assert active is None

    def test_mark_ended(self, tmp_db, sample_repo):
        sid = SessionQueries.insert(
            tmp_db,
            repo_id=sample_repo["id"],
            started_at="2025-03-01T09:00:00",
        )
        SessionQueries.mark_ended(
            tmp_db, sid,
            ended_at="2025-03-01T10:00:00",
            duration_s=3600,
            focus_score=0.85,
        )
        session = SessionQueries.get_by_id(tmp_db, sid)
        assert session["ended_at"] is not None
        assert session["duration_s"] == 3600


class TestCommitQueries:
    def test_insert_and_count(self, tmp_db, sample_repo):
        CommitQueries.insert(
            tmp_db,
            repo_id=sample_repo["id"],
            hash="abc1234",
            author="Test User",
            author_email="test@example.com",
            committed_at="2025-03-01T12:00:00",
            message="initial commit",
            lines_added=10,
            lines_removed=0,
        )
        count = CommitQueries.count_by_repo(tmp_db, sample_repo["id"])
        assert count == 1

    def test_no_duplicates(self, tmp_db, sample_repo):
        for _ in range(3):
            CommitQueries.insert(
                tmp_db,
                repo_id=sample_repo["id"],
                hash="abc1234",
                author="Test User",
                author_email="test@example.com",
                committed_at="2025-03-01T12:00:00",
                message="same commit",
                lines_added=10,
                lines_removed=0,
            )
        count = CommitQueries.count_by_repo(tmp_db, sample_repo["id"])
        assert count == 1


class TestGoalQueries:
    def test_insert_and_list(self, tmp_db):
        gid = GoalQueries.insert(
            tmp_db,
            metric="coding_time",
            target=3600,
            period="daily",
        )
        assert gid > 0

        goals = GoalQueries.list_active(tmp_db)
        assert any(g["id"] == gid for g in goals)

    def test_deactivate(self, tmp_db):
        gid = GoalQueries.insert(
            tmp_db,
            metric="commits",
            target=5,
            period="weekly",
        )
        GoalQueries.deactivate(tmp_db, gid)
        goals = GoalQueries.list_active(tmp_db)
        assert not any(g["id"] == gid for g in goals)