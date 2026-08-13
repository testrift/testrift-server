"""Tests for OpenID Connect login and role mapping."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from testrift_server import auth, config, database, oidc
from testrift_server.tr_server import create_app
from testrift_server.websocket import WebSocketServer

ADMIN_PASSWORD = "bootstrap-pass"
ISSUER = "https://idp.example.invalid/realms/test"


def _oidc_auth_config(**overrides):
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
    oidc_cfg = dict(cfg["oidc"])
    oidc_cfg.update({
        "enabled": True,
        "issuer": ISSUER,
        "client_id": "testrift",
        "client_secret": "secret",
        "role_claim": "groups",
        "role_map": {"testrift-admins": "admin"},
        "role_source": "local_override",
        "default_role": "member",
    })
    if oidc_override:
        oidc_cfg.update(oidc_override)
    cfg["oidc"] = oidc_cfg
    return cfg


@pytest.fixture
def oidc_client(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    data_dir = Path(temp_dir)
    monkeypatch.setattr("testrift_server.config.DATA_DIR", data_dir)
    monkeypatch.setattr("testrift_server.tr_server.DATA_DIR", data_dir)
    monkeypatch.setattr("testrift_server.config.AUTH_CONFIG", _oidc_auth_config(enabled=True))
    monkeypatch.setattr("testrift_server.auth.LOCKOUT_DELAY_RANGE", (0, 0))
    auth.reset_session_secret_cache()
    oidc.reset_oidc_cache()
    database.initialize_database(data_dir)
    app = create_app(ws_server=WebSocketServer())
    with TestClient(app) as client:
        yield client
    shutil.rmtree(temp_dir, ignore_errors=True)


def _mock_idp(monkeypatch, claims):
    async def fake_metadata():
        return {
            "authorization_endpoint": "https://idp.example.invalid/authorize",
            "token_endpoint": "https://idp.example.invalid/token",
            "jwks_uri": "https://idp.example.invalid/jwks",
            "userinfo_endpoint": "https://idp.example.invalid/userinfo",
        }

    monkeypatch.setattr(oidc, "_oidc_metadata", fake_metadata)
    monkeypatch.setattr(oidc, "_http_form_post", lambda *args, **kwargs: {"id_token": "token", "access_token": "at"})
    monkeypatch.setattr(oidc, "_http_json_get", lambda *args, **kwargs: {})
    monkeypatch.setattr(oidc, "_verify_id_token", lambda *args, **kwargs: claims)


class TestOidcRoleMapping:
    def test_admin_group_wins(self, monkeypatch):
        monkeypatch.setattr(
            "testrift_server.config.AUTH_CONFIG",
            _oidc_auth_config(enabled=True),
        )
        assert oidc.map_oidc_role({"groups": ["other", "testrift-admins"]}) == "admin"

    def test_default_when_no_match(self, monkeypatch):
        monkeypatch.setattr(
            "testrift_server.config.AUTH_CONFIG",
            _oidc_auth_config(enabled=True),
        )
        assert oidc.map_oidc_role({"groups": ["users"]}) == "member"
        assert oidc.map_oidc_role({}) == "member"


class TestOidcConfig:
    def test_requires_issuer_when_enabled(self):
        with pytest.raises(ValueError):
            config.parse_auth_config({
                "enabled": True,
                "oidc": {"enabled": True, "client_id": "abc"},
            })

    def test_ignores_incomplete_oidc_when_auth_off(self):
        parsed = config.parse_auth_config({"oidc": {"enabled": True}})
        assert parsed["oidc"]["enabled"] is True
        assert parsed["oidc"]["issuer"] == ""


class TestOidcPages:
    def test_login_shows_sso_button(self, oidc_client):
        html = oidc_client.get("/login").text
        assert "Sign in with company account" in html
        assert 'href="/auth/oidc/login' in html
        assert "Username" in html

    def test_sso_only_hides_local_form(self, monkeypatch):
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir)
        monkeypatch.setattr("testrift_server.config.DATA_DIR", data_dir)
        monkeypatch.setattr("testrift_server.tr_server.DATA_DIR", data_dir)
        monkeypatch.setattr(
            "testrift_server.config.AUTH_CONFIG",
            _oidc_auth_config(enabled=True, allow_local=False),
        )
        auth.reset_session_secret_cache()
        oidc.reset_oidc_cache()
        database.initialize_database(data_dir)
        app = create_app(ws_server=WebSocketServer())
        try:
            with TestClient(app) as client:
                html = client.get("/login").text
                assert "Sign in with company account" in html
                assert "Username" not in html
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_start_redirects_to_idp(self, oidc_client, monkeypatch):
        _mock_idp(monkeypatch, {"sub": "u1"})
        response = oidc_client.get("/auth/oidc/login?next=/settings", follow_redirects=False)
        assert response.status_code == 302
        location = response.headers["location"]
        parsed = urlparse(location)
        assert parsed.netloc == "idp.example.invalid"
        query = parse_qs(parsed.query)
        assert query["client_id"] == ["testrift"]
        assert query["response_type"] == ["code"]
        assert query["code_challenge_method"] == ["S256"]
        assert "state" in query
        assert "nonce" in query


class TestOidcCallback:
    def test_creates_sso_user_and_sets_cookie(self, oidc_client, monkeypatch):
        claims = {
            "sub": "user-1",
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "groups": ["users"],
        }
        _mock_idp(monkeypatch, claims)
        start = oidc_client.get("/auth/oidc/login", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        callback = oidc_client.get(
            f"/auth/oidc/callback?code=abc&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "/"
        assert "testrift_session" in callback.cookies
        me = oidc_client.get("/api/auth/me")
        assert me.status_code == 200
        user = me.json()["user"]
        assert user["display_name"] == "Ada Lovelace"
        assert user["email"] == "ada@example.com"
        assert user["auth_source"] == "oidc"
        assert user["role"] == "member"

    def test_local_override_keeps_admin_role(self, oidc_client, monkeypatch):
        claims = {"sub": "user-2", "name": "Bea", "email": "bea@example.com", "groups": []}
        _mock_idp(monkeypatch, claims)
        start = oidc_client.get("/auth/oidc/login", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        oidc_client.get(f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
        oidc_client.post("/api/auth/logout")

        login = oidc_client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        assert login.status_code == 200
        users = oidc_client.get("/api/users").json()["users"]
        sso_user = next(user for user in users if user["email"] == "bea@example.com")
        promoted = oidc_client.put(f"/api/users/{sso_user['id']}", json={"role": "admin"})
        assert promoted.status_code == 200
        oidc_client.post("/api/auth/logout")

        start = oidc_client.get("/auth/oidc/login", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        oidc_client.get(f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
        me = oidc_client.get("/api/auth/me").json()["user"]
        assert me["role"] == "admin"

    def test_mapped_updates_role(self, monkeypatch):
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir)
        monkeypatch.setattr("testrift_server.config.DATA_DIR", data_dir)
        monkeypatch.setattr("testrift_server.tr_server.DATA_DIR", data_dir)
        monkeypatch.setattr(
            "testrift_server.config.AUTH_CONFIG",
            _oidc_auth_config(enabled=True, oidc={"role_source": "mapped"}),
        )
        auth.reset_session_secret_cache()
        oidc.reset_oidc_cache()
        database.initialize_database(data_dir)
        app = create_app(ws_server=WebSocketServer())
        try:
            with TestClient(app) as client:
                claims = {"sub": "user-3", "name": "Cara", "email": "cara@example.com", "groups": ["testrift-admins"]}
                _mock_idp(monkeypatch, claims)
                start = client.get("/auth/oidc/login", follow_redirects=False)
                state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
                client.get(f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
                assert client.get("/api/auth/me").json()["user"]["role"] == "admin"
                client.post("/api/auth/logout")

                claims["groups"] = ["users"]
                _mock_idp(monkeypatch, claims)
                start = client.get("/auth/oidc/login", follow_redirects=False)
                state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
                client.get(f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
                assert client.get("/api/auth/me").json()["user"]["role"] == "member"
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_invalid_state_redirects_to_login(self, oidc_client, monkeypatch):
        _mock_idp(monkeypatch, {"sub": "x"})
        response = oidc_client.get("/auth/oidc/callback?code=abc&state=nope", follow_redirects=False)
        assert response.status_code == 302
        assert "error=sso" in response.headers["location"]

    def test_reset_password_rejected_for_sso_user(self, oidc_client, monkeypatch):
        claims = {"sub": "user-pw", "name": "Dee", "email": "dee@example.com", "groups": []}
        _mock_idp(monkeypatch, claims)
        start = oidc_client.get("/auth/oidc/login", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        oidc_client.get(f"/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
        oidc_client.post("/api/auth/logout")
        oidc_client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        users = oidc_client.get("/api/users").json()["users"]
        sso_user = next(user for user in users if user["email"] == "dee@example.com")
        response = oidc_client.post(
            f"/api/users/{sso_user['id']}/reset-password",
            json={"password": "new-password-1"},
        )
        assert response.status_code == 400
        assert "SSO" in response.json()["error"]


@pytest.mark.asyncio
async def test_sso_only_startup_allows_mapped_admin(monkeypatch, tmp_path):
    monkeypatch.setattr("testrift_server.config.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "testrift_server.config.AUTH_CONFIG",
        _oidc_auth_config(
            enabled=True,
            allow_local=False,
            bootstrap_admin={"username": "admin", "password": ""},
        ),
    )
    database.initialize_database(tmp_path)
    await database.db.initialize()
    await auth.bootstrap_admin_if_needed()
    assert await database.db.count_enabled_admins() == 0
    user = await oidc.upsert_oidc_user({
        "sub": "first-admin",
        "name": "First",
        "email": "first@example.com",
        "groups": ["testrift-admins"],
    })
    assert user is not None
    assert user["role"] == "admin"


@pytest.mark.asyncio
async def test_sso_only_startup_refuses_without_admin_path(monkeypatch, tmp_path):
    monkeypatch.setattr("testrift_server.config.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "testrift_server.config.AUTH_CONFIG",
        _oidc_auth_config(
            enabled=True,
            allow_local=False,
            bootstrap_admin={"username": "admin", "password": ""},
            oidc={"role_map": {}, "default_role": "member"},
        ),
    )
    database.initialize_database(tmp_path)
    await database.db.initialize()
    with pytest.raises(RuntimeError, match="bootstrap_admin.password"):
        await auth.bootstrap_admin_if_needed()


@pytest.mark.asyncio
async def test_first_sso_member_refused_until_admin_exists(monkeypatch, tmp_path):
    monkeypatch.setattr("testrift_server.config.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "testrift_server.config.AUTH_CONFIG",
        _oidc_auth_config(
            enabled=True,
            allow_local=False,
            bootstrap_admin={"username": "admin", "password": ""},
        ),
    )
    database.initialize_database(tmp_path)
    await database.db.initialize()
    await auth.bootstrap_admin_if_needed()
    user = await oidc.upsert_oidc_user({
        "sub": "just-a-member",
        "name": "Mem",
        "email": "mem@example.com",
        "groups": [],
    })
    assert user is None
