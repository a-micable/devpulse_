# tests/test_api.py
# Integration tests for the aiohttp REST API.

import pytest
import json
from aiohttp.test_utils import TestClient, TestServer
from devpulse.server.app import create_app


@pytest.fixture
async def client(tmp_db, config):
    app = create_app(conn=tmp_db, config=config)
    async with TestClient(TestServer(app)) as c:
        yield c


class TestHealthEndpoint:
    async def test_health_ok(self, client):
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data.get("status") == "ok"


class TestReposEndpoint:
    async def test_list_empty(self, client):
        resp = await client.get("/api/repos")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data.get("data"), list)

    async def test_add_invalid_path(self, client):
        resp = await client.post(
            "/api/repos",
            data=json.dumps({"path": "/nonexistent/path"}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status in (400, 422)

    async def test_get_nonexistent_repo(self, client):
        resp = await client.get("/api/repos/9999")
        assert resp.status == 404


class TestSessionsEndpoint:
    async def test_list_sessions_empty(self, client):
        resp = await client.get("/api/sessions")
        assert resp.status == 200
        data = await resp.json()
        assert isinstance(data.get("data"), list)

    async def test_active_session_none(self, client):
        resp = await client.get("/api/sessions/active")
        assert resp.status == 200
        data = await resp.json()
        assert data.get("data") is None

    async def test_get_nonexistent_session(self, client):
        resp = await client.get("/api/sessions/9999")
        assert resp.status == 404


class TestSummaryEndpoint:
    async def test_summary_returns_structure(self, client):
        resp = await client.get("/api/summary")
        assert resp.status == 200
        data = await resp.json()
        d = data.get("data", {})
        assert "session_count" in d
        assert "repo_count" in d
        assert "total_commits" in d