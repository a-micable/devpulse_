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

    def test_duplicate_detector_ignores_boilerplate(self, tmp_db, tmp_path, config):
        import subprocess

        repo_path = tmp_path / "boilerplate-repo"
        repo_path.mkdir()

        def git(*args):
            subprocess.run(
                ["git"] + list(args),
                cwd=str(repo_path),
                check=True,
                capture_output=True,
            )

        git("init")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test User")

        (repo_path / "a.py").write_text(
            "# common header\n"
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n"
            "import json\n"
            "import logging\n"
            "import re\n"
        )
        (repo_path / "b.py").write_text(
            "# common header\n"
            "import os\n"
            "import sys\n"
            "from pathlib import Path\n"
            "import json\n"
            "import logging\n"
            "import re\n"
        )

        git("add", ".")
        git("commit", "-m", "add boilerplate files")

        from devpulse.db.queries import RepoQueries
        from devpulse.core.metrics import DuplicateDetector

        repo_id = RepoQueries.insert(
            tmp_db,
            name="boilerplate",
            path=str(repo_path),
            remote_url="",
        )

        detector = DuplicateDetector(repo_id, str(repo_path), tmp_db, config)
        report = detector.run(block_size=6)

        assert report["duplicate_groups"] == 0

    def test_idempotent(self, analyzer, tmp_db, sample_repo):
        from devpulse.db.queries import CommitQueries
        analyzer.run()
        count_first = CommitQueries.count_by_repo(tmp_db, sample_repo["id"])
        analyzer.run()
        count_second = CommitQueries.count_by_repo(tmp_db, sample_repo["id"])
        assert count_first == count_second