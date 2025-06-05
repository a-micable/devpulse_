# tests/test_tracker.py

import pytest
import datetime
from devpulse.core.tracker import SessionTracker


class TestSessionTracker:
    @pytest.fixture
    def tracker(self, tmp_db, config):
        return SessionTracker(tmp_db, config)

    def test_start_without_repo(self, tracker):
        result = tracker.start()
        assert result.get("ok") is True
        assert "session_id" in result

    def test_start_with_repo(self, tracker, git_repo):
        result = tracker.start(repo_path=git_repo)
        assert result.get("ok") is True

    def test_double_start_fails(self, tracker):
        tracker.start()
        result = tracker.start()
        assert result.get("ok") is False
        assert "error" in result

    def test_stop_active_session(self, tracker):
        tracker.start()
        result = tracker.stop()
        assert result.get("ok") is True
        assert "summary" in result

    def test_stop_without_active_fails(self, tracker):
        result = tracker.stop()
        assert result.get("ok") is False

    def test_pause_and_resume(self, tracker):
        tracker.start()
        p = tracker.pause()
        assert p.get("ok") is True

        r = tracker.resume()
        assert r.get("ok") is True

    def test_pause_without_session_fails(self, tracker):
        result = tracker.pause()
        assert result.get("ok") is False

    def test_status_no_session(self, tracker):
        result = tracker.status()
        assert result.get("running") is False

    def test_status_active_session(self, tracker):
        tracker.start()
        result = tracker.status()
        assert result.get("running") is True
        assert "session_id" in result

    def test_add_tag(self, tracker):
        tracker.start()
        result = tracker.add_tag("feature-auth")
        assert result.get("ok") is True

    def test_add_tag_without_session_fails(self, tracker):
        result = tracker.add_tag("sometag")
        assert result.get("ok") is False

    def test_stop_returns_duration(self, tracker):
        tracker.start()
        result = tracker.stop()
        summary = result.get("summary", {})
        assert "duration_human" in summary