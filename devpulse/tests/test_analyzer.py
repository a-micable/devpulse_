# tests/test_analyzer.py

import pytest
from devpulse.core.analyzer import BatchAnalyzer


class TestBatchAnalyzer:
    @pytest.fixture
    def analyzer(self, tmp_db, sample_repo, config):
        return BatchAnalyzer(
            repo_id=sample_repo["id"],
            repo_path=sample_repo["path"],
            conn=tmp_db,
            config=config,
        )

    def test_run_returns_result(self, analyzer):
        result = analyzer.run(full=False)
        assert result is not None

    def test_run_indexes_commits(self, analyzer, tmp_db, sample_repo):
        from devpulse.db.queries import CommitQueries
        analyzer.run(full=False)
        count = CommitQueries.count_by_repo(tmp_db, sample_repo["id"])
        assert count >= 1

    def test_run_full(self, analyzer):
        result = analyzer.run(full=True)
        assert result.total_commits >= 1

    def test_contributor_count(self, analyzer):
        result = analyzer.run()
        assert result.contributor_count >= 1

    def test_idempotent(self, analyzer, tmp_db, sample_repo):
        from devpulse.db.queries import CommitQueries
        analyzer.run()
        count_first = CommitQueries.count_by_repo(tmp_db, sample_repo["id"])
        analyzer.run()
        count_second = CommitQueries.count_by_repo(tmp_db, sample_repo["id"])
        assert count_first == count_second