"""OpenID Connect login (Authorization Code + PKCE)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from typing import Any, Optional

import jwt
from jwt import PyJWKClient
from starlette.responses import RedirectResponse, Response

from . import database
from .auth import (
    apply_session_cookie,
    auth_config,
    create_session_cookie_value,
    is_auth_enabled,
    safe_next_url,
    would_remove_last_admin,
    _iso,
    _utcnow,
)

logger = logging.getLogger(__name__)

OIDC_START_PATH = "/auth/oidc/login"
OIDC_CALLBACK_PATH = "/auth/oidc/callback"
SSO_ERROR_MESSAGE = "Could not sign in with company account."
OIDC_STATE_MINUTES = 10
OIDC_CALLBACK_LIMIT = 30
OIDC_CALLBACK_WINDOW_MINUTES = 1
_OIDC_RATE_PREFIX = "__oidc__:"

_metadata_cache: dict[str, Any] = {}
_jwks_clients: dict[str, PyJWKClient] = {}


def oidc_config() -> dict:
    return auth_config().get("oidc") or {}


def is_oidc_enabled() -> bool:
    if not is_auth_enabled():
        return False
    cfg = oidc_config()
    return bool(cfg.get("enabled") and cfg.get("issuer") and cfg.get("client_id"))


def oidc_can_provision_admin() -> bool:
    """True when SSO can create the first Admin (role_map or default_role)."""
    if not is_oidc_enabled():
        return False
    cfg = oidc_config()
    if cfg.get("default_role") == "admin":
        return True
    return "admin" in set((cfg.get("role_map") or {}).values())


def map_oidc_role(claims: dict) -> str:
    """Map IdP claims to a TestRift role."""
    cfg = oidc_config()
    default_role = cfg.get("default_role") or "member"
    role_map = cfg.get("role_map") or {}
    claim_name = cfg.get("role_claim") or "groups"
    raw = claims.get(claim_name)
    values: list[str] = []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple)):
        values = [str(item) for item in raw if item is not None]
    mapped = None
    for value in values:
        role = role_map.get(value)
        if role == "admin":
            return "admin"
        if role == "member":
            mapped = "member"
    return mapped or default_role


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _request_origin(request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or "http").split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or "127.0.0.1"
    ).split(",")[0].strip()
    return f"{proto}://{host}"


def redirect_uri_for(request) -> str:
    configured = (oidc_config().get("redirect_uri") or "").strip()
    if configured:
        return configured
    return _request_origin(request) + OIDC_CALLBACK_PATH


def _sso_error_redirect() -> Response:
    return RedirectResponse(url="/login?error=sso", status_code=302)


def _http_json_get(url: str, headers: Optional[dict] = None, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_form_post(url: str, data: dict, timeout: int = 15) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _oidc_metadata() -> dict:
    issuer = oidc_config()["issuer"]
    cached = _metadata_cache.get(issuer)
    if cached:
        return cached
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    metadata = await asyncio.to_thread(_http_json_get, url)
    _metadata_cache[issuer] = metadata
    return metadata


def reset_oidc_cache() -> None:
    _metadata_cache.clear()
    _jwks_clients.clear()


def _verify_id_token(id_token: str, jwks_uri: str, nonce: str) -> dict:
    cfg = oidc_config()
    client = _jwks_clients.get(jwks_uri)
    if client is None:
        client = PyJWKClient(jwks_uri, cache_jwk_set=True)
        _jwks_clients[jwks_uri] = client
    signing_key = client.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256", "RS384", "RS512", "ES256"],
        audience=cfg["client_id"],
        issuer=cfg["issuer"],
        leeway=60,
    )
    if claims.get("nonce") != nonce:
        raise ValueError("OIDC nonce mismatch")
    if not claims.get("sub"):
        raise ValueError("OIDC token missing sub")
    return claims


def _display_name_from_claims(claims: dict) -> str:
    for key in ("name", "preferred_username", "email"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(claims.get("sub") or "user")


def _email_from_claims(claims: dict) -> Optional[str]:
    email = claims.get("email")
    if isinstance(email, str) and "@" in email:
        return email.strip()
    preferred = claims.get("preferred_username")
    if isinstance(preferred, str) and "@" in preferred:
        return preferred.strip()
    return None


async def _rate_limited(client_ip: str) -> bool:
    since = _iso(_utcnow() - timedelta(minutes=OIDC_CALLBACK_WINDOW_MINUTES))
    key = f"{_OIDC_RATE_PREFIX}{client_ip or 'unknown'}"
    count = await database.db.count_login_failures(username=key, since=since)
    return count >= OIDC_CALLBACK_LIMIT


async def _record_oidc_attempt(client_ip: str) -> None:
    key = f"{_OIDC_RATE_PREFIX}{client_ip or 'unknown'}"
    await database.db.record_login_attempt(
        attempted_at=_iso(_utcnow()),
        username=key,
        client_ip=client_ip or "",
        username_existed=False,
    )


async def upsert_oidc_user(claims: dict) -> Optional[dict]:
    """Create or update an OIDC user. Returns None if sign-in is not allowed."""
    cfg = oidc_config()
    issuer = cfg["issuer"]
    subject = str(claims["sub"])
    mapped_role = map_oidc_role(claims)
    display_name = _display_name_from_claims(claims)
    email = _email_from_claims(claims)
    now = _iso(_utcnow())

    user = await database.db.get_user_by_oidc(issuer, subject)
    if user is None:
        admin_count = await database.db.count_enabled_admins()
        role = mapped_role
        if admin_count == 0:
            if mapped_role != "admin":
                logger.warning("Refusing first SSO user %s; not mapped to admin", subject)
                return None
            role = "admin"
        user_id = await database.db.create_user(
            display_name=display_name,
            email=email,
            username=None,
            password_hash=None,
            role=role,
            enabled=True,
            auth_source="oidc",
            oidc_issuer=issuer,
            oidc_subject=subject,
            created_at=now,
        )
        await database.db.update_user(user_id, last_login_at=now)
        return await database.db.get_user_by_id(user_id)

    if not user.get("enabled"):
        logger.info("Disabled SSO user id=%s attempted login", user.get("id"))
        return None

    updates: dict[str, Any] = {
        "display_name": display_name,
        "email": email,
        "last_login_at": now,
    }
    if cfg.get("role_source") == "mapped" and mapped_role != user.get("role"):
        if await would_remove_last_admin(user, new_role=mapped_role):
            logger.warning("Not applying OIDC role map that would remove the last Admin")
        else:
            updates["role"] = mapped_role
    await database.db.update_user(user["id"], **updates)
    return await database.db.get_user_by_id(user["id"])


async def start_oidc_login(request) -> Response:
    """Redirect the browser to the identity provider."""
    if not is_oidc_enabled():
        return Response(content="Not found", status_code=404, media_type="text/plain")

    try:
        metadata = await _oidc_metadata()
        authorize_url = metadata.get("authorization_endpoint")
        if not authorize_url:
            raise ValueError("OIDC metadata missing authorization_endpoint")
    except Exception as exc:
        logger.error("OIDC discovery failed: %s", exc)
        return _sso_error_redirect()

    await database.db.delete_expired_oidc_states(
        _iso(_utcnow() - timedelta(minutes=OIDC_STATE_MINUTES))
    )
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    next_url = safe_next_url(request.query.get("next"))
    await database.db.create_oidc_state(
        state=state,
        nonce=nonce,
        code_verifier=verifier,
        next_url=next_url,
        created_at=_iso(_utcnow()),
    )
    cfg = oidc_config()
    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri_for(request),
        "scope": " ".join(cfg.get("scopes") or ["openid", "profile", "email"]),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = authorize_url + "?" + urllib.parse.urlencode(params)
    return RedirectResponse(url=url, status_code=302)


async def finish_oidc_login(request) -> Response:
    """Handle the IdP callback and issue a session cookie."""
    if not is_oidc_enabled():
        return Response(content="Not found", status_code=404, media_type="text/plain")
    client_ip = request.remote or ""
    if await _rate_limited(client_ip):
        logger.warning("OIDC callback rate-limited for %s", client_ip)
        return _sso_error_redirect()
    await _record_oidc_attempt(client_ip)

    if request.query.get("error"):
        logger.info("OIDC callback error from IdP: %s", request.query.get("error"))
        return _sso_error_redirect()

    code = request.query.get("code") or ""
    state = request.query.get("state") or ""
    if not code or not state:
        return _sso_error_redirect()

    pending = await database.db.take_oidc_state(state)
    if not pending:
        logger.info("OIDC callback with unknown or reused state")
        return _sso_error_redirect()
    created = pending.get("created_at")
    try:
        from .auth import _parse_iso
        age_ok = _utcnow() < _parse_iso(created) + timedelta(minutes=OIDC_STATE_MINUTES)
    except (TypeError, ValueError):
        age_ok = False
    if not age_ok:
        return _sso_error_redirect()

    try:
        metadata = await _oidc_metadata()
        token_url = metadata.get("token_endpoint")
        jwks_uri = metadata.get("jwks_uri")
        if not token_url or not jwks_uri:
            raise ValueError("OIDC metadata missing token_endpoint or jwks_uri")
        cfg = oidc_config()
        token_body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri_for(request),
            "client_id": cfg["client_id"],
            "code_verifier": pending["code_verifier"],
        }
        if cfg.get("client_secret"):
            token_body["client_secret"] = cfg["client_secret"]
        token_response = await asyncio.to_thread(_http_form_post, token_url, token_body)
        id_token = token_response.get("id_token")
        if not id_token:
            raise ValueError("token response missing id_token")
        claims = await asyncio.to_thread(
            _verify_id_token, id_token, jwks_uri, pending["nonce"]
        )
        userinfo_url = metadata.get("userinfo_endpoint")
        access_token = token_response.get("access_token")
        role_claim = cfg.get("role_claim") or "groups"
        if userinfo_url and access_token and (
            "email" not in claims or "name" not in claims or role_claim not in claims
        ):
            try:
                extra = await asyncio.to_thread(
                    _http_json_get,
                    userinfo_url,
                    {"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                )
                if isinstance(extra, dict):
                    merged = dict(extra)
                    merged.update(claims)
                    claims = merged
            except Exception as exc:
                logger.info("OIDC userinfo lookup failed: %s", exc)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, jwt.PyJWTError) as exc:
        logger.error("OIDC token exchange failed: %s", exc)
        return _sso_error_redirect()
    except Exception as exc:
        logger.error("OIDC callback failed: %s", exc)
        return _sso_error_redirect()

    user = await upsert_oidc_user(claims)
    if user is None:
        return _sso_error_redirect()
    cookie_value = await create_session_cookie_value(user["id"])
    response = RedirectResponse(url=safe_next_url(pending.get("next_url")), status_code=302)
    apply_session_cookie(response, cookie_value)
    return response
