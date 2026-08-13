"""Tests for local authentication, roles, and the auth.enabled master switch."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from testrift_server import auth, config, database
from testrift_server.tr_server import create_app
from testrift_server.websocket import WebSocketServer

ADMIN_PASSWORD = "bootstrap-pass"
MEMBER_PASSWORD = "member-pass"


def _auth_config(**overrides):
    cfg = config.default_auth_config()
    oidc_override = overrides.pop("oidc", None)
    bootstrap_override = overrides.pop("bootstrap_admin", None)
    cfg.update(overrides)
    bootstrap = dict(cfg["bootstrap_admin"])
    if bootstrap_override is not None:
        bootstrap.update(bootstrap_override)
    else:
        bootstrap["password"] = ADMIN_PASSWORD
    cfg["bootstrap_admin"] = bootstrap
    if oidc_override is not None:
        oidc = dict(cfg["oidc"])
        oidc.update(oidc_override)
        cfg["oidc"] = oidc
    return cfg


@pytest.fixture
def open_client(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    data_dir = Path(temp_dir)
    monkeypatch.setattr("testrift_server.config.DATA_DIR", data_dir)
    monkeypatch.setattr("testrift_server.tr_server.DATA_DIR", data_dir)
    monkeypatch.setattr("testrift_server.config.AUTH_CONFIG", _auth_config(enabled=False))
    auth.reset_session_secret_cache()
    database.initialize_database(data_dir)
    app = create_app(ws_server=WebSocketServer())
    with TestClient(app) as client:
        yield client
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def auth_client(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    data_dir = Path(temp_dir)
    monkeypatch.setattr("testrift_server.config.DATA_DIR", data_dir)
    monkeypatch.setattr("testrift_server.tr_server.DATA_DIR", data_dir)
    monkeypatch.setattr(
        "testrift_server.config.AUTH_CONFIG",
        _auth_config(enabled=True),
    )
    monkeypatch.setattr("testrift_server.auth.LOCKOUT_DELAY_RANGE", (0, 0))
    auth.reset_session_secret_cache()
    database.initialize_database(data_dir)
    app = create_app(ws_server=WebSocketServer())
    with TestClient(app) as client:
        yield client
    shutil.rmtree(temp_dir, ignore_errors=True)


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _login_admin(client):
    response = _login(client, "admin", ADMIN_PASSWORD)
    assert response.status_code == 200, response.text
    return response


def _create_member(client, username="member"):
    _login_admin(client)
    response = client.post(
        "/api/users",
        json={
            "username": username,
            "password": MEMBER_PASSWORD,
            "role": "member",
            "display_name": username,
        },
    )
    assert response.status_code == 201, response.text
    client.post("/api/auth/logout")
    return response.json()["user"]


class TestPermissions:
    def test_unknown_role_treated_as_member(self):
        perms = auth.permissions_for_role("nosuch")
        assert auth.PERM_RUNS_READ in perms
        assert auth.PERM_ADMIN_ACCESS not in perms

    def test_admin_routes_require_admin_access(self):
        for path in auth.admin_protected_paths():
            method = "GET"
            if path == "/api/migrate-data":
                method = "POST"
            assert auth.required_permission(method, path) == auth.PERM_ADMIN_ACCESS
        assert auth.required_permission("GET", "/api/users/3") == auth.PERM_ADMIN_ACCESS
        assert auth.required_permission("POST", "/api/users/3/unlock") == auth.PERM_ADMIN_ACCESS

    def test_health_and_static_are_public(self):
        assert auth.required_permission("GET", "/health") is None
        assert auth.required_permission("GET", "/static/app_shell.css") is None
        assert auth.required_permission("GET", "/api/server-info") is None
        assert auth.required_permission("POST", "/api/admin/shutdown") is None
        assert auth.required_permission("POST", "/api/attachments/run/tc/upload") is None

    def test_catalog_writes_use_catalog_permission(self):
        assert auth.required_permission("GET", "/api/targets") == auth.PERM_RUNS_READ
        assert auth.required_permission("POST", "/api/targets") == auth.PERM_CATALOG_WRITE
        assert auth.required_permission("DELETE", "/api/collections/demo") == auth.PERM_CATALOG_WRITE

    def test_safe_next_url_rejects_open_redirects(self):
        assert auth.safe_next_url("/settings") == "/settings"
        assert auth.safe_next_url("https://evil.example/") == "/"
        assert auth.safe_next_url("//evil.example") == "/"
        assert auth.safe_next_url("/\\evil") == "/"


class TestAuthConfig:
    def test_defaults_keep_auth_off(self):
        cfg = config.parse_auth_config({})
        assert cfg["enabled"] is False
        assert cfg["password_min_length"] == 8

    def test_rejects_invalid_enabled(self):
        with pytest.raises(ValueError):
            config.parse_auth_config({"enabled": "yes"})


class TestOpenMode:
    def test_pages_and_apis_stay_open(self, open_client):
        assert open_client.get("/").status_code == 200
        assert open_client.get("/settings").status_code == 200
        assert open_client.get("/logs").status_code == 200
        assert open_client.get("/api/test-runs").status_code == 200
        assert open_client.get("/api/settings/email-recipients").status_code == 200

    def test_login_and_users_are_hidden(self, open_client):
        assert open_client.get("/login").status_code == 404
        assert open_client.get("/users").status_code == 404
        assert open_client.get("/api/users").status_code == 404
        assert open_client.post("/api/auth/login", json={"username": "a", "password": "b"}).status_code == 404
        assert open_client.get("/auth/oidc/login").status_code == 404
        assert open_client.get("/auth/oidc/callback").status_code == 404

    def test_admin_nav_visible_without_users_link(self, open_client):
        html = open_client.get("/").text
        assert "Admin" in html
        assert 'href="/settings"' in html
        assert 'href="/users"' not in html
        assert "Sign out" not in html


class TestAuthEnabled:
    def test_unauthenticated_html_redirects_to_login(self, auth_client):
        response = auth_client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("/login")

    def test_unauthenticated_api_returns_401(self, auth_client):
        response = auth_client.get("/api/test-runs")
        assert response.status_code == 401
        assert response.json()["error"] == "Authentication required"

    def test_health_and_server_info_remain_public(self, auth_client):
        assert auth_client.get("/health").status_code == 200
        assert auth_client.get("/api/server-info").status_code == 200

    def test_login_success_and_generic_failure(self, auth_client):
        bad = _login(auth_client, "admin", "wrong-password")
        assert bad.status_code == 401
        assert bad.json()["error"] == auth.LOGIN_ERROR_MESSAGE
        unknown = _login(auth_client, "missing", "wrong-password")
        assert unknown.status_code == 401
        assert unknown.json()["error"] == auth.LOGIN_ERROR_MESSAGE
        good = _login_admin(auth_client)
        assert good.json()["user"]["role"] == "admin"
        home = auth_client.get("/")
        assert home.status_code == 200
        assert "Sign out" in home.text
        assert 'href="/users"' in home.text

    def test_member_cannot_open_admin_pages(self, auth_client):
        _create_member(auth_client)
        login = _login(auth_client, "member", MEMBER_PASSWORD)
        assert login.status_code == 200
        home = auth_client.get("/")
        assert home.status_code == 200
        assert '<details class="tr-nav-section' not in home.text
        assert 'href="/users"' not in home.text
        settings = auth_client.get("/settings")
        assert settings.status_code == 403
        assert "Admin only" in settings.text
        logs = auth_client.get("/api/logs")
        assert logs.status_code == 403
        users = auth_client.get("/api/users")
        assert users.status_code == 403

    def test_cannot_demote_last_admin(self, auth_client):
        _login_admin(auth_client)
        listed = auth_client.get("/api/users")
        admin_id = listed.json()["users"][0]["id"]
        response = auth_client.put(f"/api/users/{admin_id}", json={"role": "member"})
        assert response.status_code == 400
        assert "last Admin" in response.json()["error"]
        disabled = auth_client.put(f"/api/users/{admin_id}", json={"enabled": False})
        assert disabled.status_code == 400

    def test_lockout_then_unlock(self, auth_client):
        _create_member(auth_client, "lockeduser")
        for _ in range(5):
            failed = _login(auth_client, "lockeduser", "bad-password")
            assert failed.status_code == 401
            assert failed.json()["error"] == auth.LOGIN_ERROR_MESSAGE
        still_locked = _login(auth_client, "lockeduser", MEMBER_PASSWORD)
        assert still_locked.status_code == 401
        assert still_locked.json()["error"] == auth.LOGIN_ERROR_MESSAGE

        _login_admin(auth_client)
        users = auth_client.get("/api/users").json()["users"]
        locked = next(user for user in users if user["username"] == "lockeduser")
        assert locked["lockout"] and locked["lockout"]["locked"]
        unlock = auth_client.post(f"/api/users/{locked['id']}/unlock")
        assert unlock.status_code == 200
        auth_client.post("/api/auth/logout")
        recovered = _login(auth_client, "lockeduser", MEMBER_PASSWORD)
        assert recovered.status_code == 200

    def test_oidc_start_404_when_disabled(self, auth_client):
        assert auth_client.get("/auth/oidc/login").status_code == 404
        html = auth_client.get("/login").text
        assert "Sign in with company account" not in html
