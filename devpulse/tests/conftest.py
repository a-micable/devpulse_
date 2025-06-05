# tests/conftest.py
# Shared pytest fixtures for all test modules.

import pytest
import sqlite3
import tempfile
import os
from pathlib import Path

from devpulse.db.connection import open_connection
from devpulse.db.schema import create_tables
from devpulse.utils.config import Config


@pytest.fixture
def tmp_db():
    """In-memory SQLite connection with schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_tables(conn)
    yield conn
    conn.close()


@pytest.fixture
def tmp_db_file(tmp_path):
    """File-based SQLite connection (for tests that need a real path)."""
    db_path = str(tmp_path / "test.db")
    conn = open_connection(db_path)
    yield conn, db_path
    conn.close()


@pytest.fixture
def config(tmp_path):
    """Default Config instance pointing exports at a temp dir."""
    cfg = Config()
    cfg._data["export_dir"] = str(tmp_path / "exports")
    cfg._data["db_path"]    = str(tmp_path / "data.db")
    return cfg


@pytest.fixture
def git_repo(tmp_path):
    """
    Create a minimal real git repo with two commits.
    Returns the repo path as a string.
    """
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            ["git"] + list(args),
            cwd=str(repo),
            check=True,
            capture_output=True,
        )

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name",  "Test User")

    (repo / "README.md").write_text("# test\n")
    git("add", ".")
    git("commit", "-m", "initial commit")

    (repo / "main.py").write_text("print('hello')\n")
    git("add", ".")
    git("commit", "-m", "add main.py")

    return str(repo)


@pytest.fixture
def sample_repo(tmp_db, git_repo):
    """Insert a repo row for git_repo into tmp_db. Returns repo dict."""
    from devpulse.db.queries import RepoQueries
    repo_id = RepoQueries.insert(
        tmp_db,
        name="test-repo",
        path=git_repo,
        remote_url="",
    )
    return RepoQueries.get_by_id(tmp_db, repo_id)