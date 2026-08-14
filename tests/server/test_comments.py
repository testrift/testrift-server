"""Tests for user comments APIs."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import msgpack
import pytest
from starlette.testclient import TestClient

from testrift_server import auth, database
from testrift_server.config import default_auth_config
from testrift_server.protocol import (
    F_TYPE,
    F_RUN_ID,
    F_RETENTION_DAYS,
    F_LOCAL_RUN,
    F_USER_METADATA,
    F_TARGET_KEY,
    F_PURPOSE,
    F_SOURCES,
    F_SOURCE_BRANCH,
    F_SOURCE_REVISION,
    F_SOURCE_REPOSITORY_URL,
    F_ERROR,
    MSG_RUN_STARTED,
    MSG_RUN_STARTED_RESPONSE,
)
from testrift_server.tr_server import create_app
from testrift_server.websocket import WebSocketServer


@pytest.fixture
def asgi_client(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    data_dir = Path(temp_dir)
    monkeypatch.setattr("testrift_server.config.DATA_DIR", data_dir)
    monkeypatch.setattr("testrift_server.tr_server.DATA_DIR", data_dir)
    monkeypatch.setattr("testrift_server.utils.DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(
        "testrift_server.config.AUTH_CONFIG",
        {**default_auth_config(), "enabled": False},
    )
    database.initialize_database(data_dir)
    ws_server = WebSocketServer()
    app = create_app(ws_server=ws_server)
    with TestClient(app) as client:
        client.ws_server = ws_server
        client.data_dir = data_dir
        yield client
    shutil.rmtree(temp_dir, ignore_errors=True)


def _start_run(client, target_key="comment-target"):
    with client.websocket_connect("/ws/nunit") as ws:
        payload = {
            F_TYPE: MSG_RUN_STARTED,
            F_RETENTION_DAYS: 7,
            F_LOCAL_RUN: False,
            F_USER_METADATA: {},
            F_TARGET_KEY: target_key,
            F_PURPOSE: "manual",
            F_SOURCES: {
                "test-system": {
                    F_SOURCE_BRANCH: "main",
                    F_SOURCE_REVISION: "abc123",
                    F_SOURCE_REPOSITORY_URL: "https://example.invalid/repo",
                }
            },
        }
        ws.send_bytes(msgpack.packb(payload, use_bin_type=True))
        data = msgpack.unpackb(ws.receive_bytes(), raw=False)
        assert data.get(F_TYPE) == MSG_RUN_STARTED_RESPONSE
        assert F_ERROR not in data
        return data[F_RUN_ID]


class TestCommentsAPI:
    def test_run_comment_crud(self, asgi_client):
        run_id = _start_run(asgi_client)
        created = asgi_client.post(
            f"/api/runs/{run_id}/comments",
            json={
                "scope": "run",
                "body": "Shield box was open :thumbsup:",
                "author_name": "Andreas",
            },
        )
        assert created.status_code == 201, created.text
        comment = created.json()["comment"]
        assert comment["scope"] == "run"
        assert comment["author_name"] == "Andreas"
        assert comment["body"].startswith("Shield box")
        comment_id = comment["id"]

        listed = asgi_client.get(f"/api/runs/{run_id}/comments")
        assert listed.status_code == 200
        body = listed.json()
        assert body["success"] is True
        assert len(body["run_comments"]) == 1
        assert body["run_comments"][0]["id"] == comment_id
        assert body["auth_enabled"] is False

        patched = asgi_client.patch(
            f"/api/comments/{comment_id}",
            json={"body": "Updated **note**"},
        )
        assert patched.status_code == 200
        assert patched.json()["comment"]["body"] == "Updated **note**"
        assert patched.json()["comment"]["edited"] is True

        deleted = asgi_client.delete(f"/api/comments/{comment_id}")
        assert deleted.status_code == 200
        listed = asgi_client.get(f"/api/runs/{run_id}/comments")
        assert listed.json()["run_comments"] == []

    def test_log_comment_and_presence(self, asgi_client):
        run_id = _start_run(asgi_client, target_key="comment-log-target")
        created = asgi_client.post(
            f"/api/runs/{run_id}/comments",
            json={
                "scope": "log",
                "tc_id": "tc_demo",
                "line_start": 2,
                "line_end": 5,
                "body": "Looks like a :bug:",
                "author_name": "Ada",
            },
        )
        assert created.status_code == 201, created.text
        comment = created.json()["comment"]
        assert comment["line_start"] == 2
        assert comment["line_end"] == 5

        log_list = asgi_client.get(f"/api/runs/{run_id}/comments/log/tc_demo")
        assert log_list.status_code == 200
        comments = log_list.json()["comments"]
        assert len(comments) == 1
        assert comments[0]["id"] == comment["id"]

        summary = asgi_client.get(f"/api/runs/{run_id}/comments")
        assert summary.json()["test_cases"]["tc_demo"]["has_comments"] is True
        assert summary.json()["test_cases"]["tc_demo"]["first_comment_id"] == comment["id"]

        runs = asgi_client.get("/api/test-runs")
        assert runs.status_code == 200
        match = [item for item in runs.json()["data"] if item["run_id"] == run_id]
        assert match
        assert match[0]["has_comments"] is True
        assert match[0]["first_comment_id"] == comment["id"]

        presence = asgi_client.get(f"/api/comments/presence?run_ids={run_id}")
        assert presence.status_code == 200
        info = presence.json()["data"][run_id]
        assert info["has_comments"] is True
        assert info["test_cases"]["tc_demo"]["first_comment_id"] == comment["id"]

    def test_validation(self, asgi_client):
        run_id = _start_run(asgi_client, target_key="comment-val-target")
        missing_name = asgi_client.post(
            f"/api/runs/{run_id}/comments",
            json={"scope": "run", "body": "hello"},
        )
        assert missing_name.status_code == 400

        missing_body = asgi_client.post(
            f"/api/runs/{run_id}/comments",
            json={"scope": "run", "body": "   ", "author_name": "Ada"},
        )
        assert missing_body.status_code == 400

        bad_range = asgi_client.post(
            f"/api/runs/{run_id}/comments",
            json={
                "scope": "log",
                "tc_id": "tc_demo",
                "line_start": 5,
                "line_end": 1,
                "body": "nope",
                "author_name": "Ada",
            },
        )
        assert bad_range.status_code == 400

        unknown = asgi_client.get("/api/runs/not-a-real-run/comments")
        assert unknown.status_code == 404

    def test_static_comment_assets(self, asgi_client):
        for name in (
            "comments.js",
            "comments_md.js",
            "comments.css",
            "marked.min.js",
            "purify.min.js",
            "emoji_shortcodes.json",
        ):
            response = asgi_client.get(f"/static/{name}")
            assert response.status_code == 200, name

    def test_missing_comment(self, asgi_client):
        response = asgi_client.patch("/api/comments/99999", json={"body": "x"})
        assert response.status_code == 404
        response = asgi_client.delete("/api/comments/99999")
        assert response.status_code == 404


ADMIN_PASSWORD = "bootstrap-pass"
MEMBER_PASSWORD = "member-pass"


def _auth_cfg():
    cfg = default_auth_config()
    cfg["enabled"] = True
    bootstrap = dict(cfg["bootstrap_admin"])
    bootstrap["password"] = ADMIN_PASSWORD
    cfg["bootstrap_admin"] = bootstrap
    return cfg


@pytest.fixture
def auth_comments_client(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    data_dir = Path(temp_dir)
    monkeypatch.setattr("testrift_server.config.DATA_DIR", data_dir)
    monkeypatch.setattr("testrift_server.tr_server.DATA_DIR", data_dir)
    monkeypatch.setattr("testrift_server.utils.DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr("testrift_server.config.AUTH_CONFIG", _auth_cfg())
    monkeypatch.setattr("testrift_server.auth.LOCKOUT_DELAY_RANGE", (0, 0))
    auth.reset_session_secret_cache()
    database.initialize_database(data_dir)
    ws_server = WebSocketServer()
    app = create_app(ws_server=ws_server)
    with TestClient(app) as client:
        client.ws_server = ws_server
        yield client
    shutil.rmtree(temp_dir, ignore_errors=True)


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _login_admin(client):
    response = _login(client, "admin", ADMIN_PASSWORD)
    assert response.status_code == 200, response.text
    return response


def _create_member(client, username="member", display_name=None):
    _login_admin(client)
    response = client.post(
        "/api/users",
        json={
            "username": username,
            "password": MEMBER_PASSWORD,
            "role": "member",
            "display_name": display_name or username,
        },
    )
    assert response.status_code == 201, response.text
    client.post("/api/auth/logout")
    return response.json()["user"]


class TestCommentsAuth:
    def test_unauthenticated_write_rejected(self, auth_comments_client):
        run_id = _start_run(auth_comments_client, target_key="comment-auth-target")
        created = auth_comments_client.post(
            f"/api/runs/{run_id}/comments",
            json={"scope": "run", "body": "nope"},
        )
        assert created.status_code == 401

    def test_author_edit_and_admin_delete(self, auth_comments_client):
        run_id = _start_run(auth_comments_client, target_key="comment-auth-edit")
        _create_member(auth_comments_client, username="ada", display_name="Ada")
        _create_member(auth_comments_client, username="bob", display_name="Bob")

        _login(auth_comments_client, "ada", MEMBER_PASSWORD)
        created = auth_comments_client.post(
            f"/api/runs/{run_id}/comments",
            json={"scope": "run", "body": "Ada's note :thumbsup:"},
        )
        assert created.status_code == 201, created.text
        comment = created.json()["comment"]
        assert comment["author_name"] == "Ada"
        assert comment["author_user_id"] is not None
        comment_id = comment["id"]

        patched = auth_comments_client.patch(
            f"/api/comments/{comment_id}",
            json={"body": "Ada edited"},
        )
        assert patched.status_code == 200
        assert patched.json()["comment"]["edited"] is True

        auth_comments_client.post("/api/auth/logout")
        _login(auth_comments_client, "bob", MEMBER_PASSWORD)
        forbidden_edit = auth_comments_client.patch(
            f"/api/comments/{comment_id}",
            json={"body": "Bob rewrite"},
        )
        assert forbidden_edit.status_code == 403
        forbidden_delete = auth_comments_client.delete(f"/api/comments/{comment_id}")
        assert forbidden_delete.status_code == 403

        auth_comments_client.post("/api/auth/logout")
        _login_admin(auth_comments_client)
        admin_edit = auth_comments_client.patch(
            f"/api/comments/{comment_id}",
            json={"body": "Admin rewrite"},
        )
        assert admin_edit.status_code == 403
        admin_delete = auth_comments_client.delete(f"/api/comments/{comment_id}")
        assert admin_delete.status_code == 200
