# Authentication

TestRift can require people to sign in before they use the web UI and HTTP APIs. Authentication is **off** by default: anyone who can reach the server has full access, and `localhost_only` remains the bind-time restriction. See [server_config.md](server_config.md) for every `auth` option.

This page covers how to turn authentication on, create the first Admin, manage users, and what stays open for test clients.

## Enable authentication

Set `auth.enabled` to `true` and provide a bootstrap Admin password. The password is expanded from the environment like other secrets (`${env:VAR}`).

```yaml
auth:
  enabled: true
  allow_local: true
  bootstrap_admin:
    username: admin
    password: "${env:TESTRIFT_BOOTSTRAP_ADMIN_PASSWORD}"
```

PowerShell:

```powershell
$env:TESTRIFT_BOOTSTRAP_ADMIN_PASSWORD = "choose-a-long-password"
testrift-server
```

On startup, if authentication is on and no enabled Admin exists, the server creates that local Admin once. If authentication is on, no Admin exists, and the bootstrap password is empty, the server refuses to start unless OpenID Connect is enabled with a `role_map` (or `default_role`) that can produce an Admin. In that case the first SSO user mapped to Admin becomes Admin.

After a local bootstrap, manage users in the UI. You can leave `bootstrap_admin.password` set; it is not used again once an Admin exists.

When `auth.enabled` is `false`, login is not required. There is no login page, no session cookie, and no Users page. Settings and Server Log stay visible to anyone who can reach the server. `auth.ingest_token` is still checked if it is set.

## Sign in

Open the UI. Unauthenticated browser requests redirect to `/login`. After a successful sign-in, the original page is restored when it was a GET request.

- Use the username and password of a local account, or **Sign in with company account** when OpenID Connect is enabled.
- Failed local logins always show **Invalid username or password.** That message is also used for unknown users, disabled accounts, and lockout, so a login form cannot be used to discover account names.
- Failed SSO logins show **Could not sign in with company account.**
- The sidebar shows the signed-in display name and **Sign out**.

Unauthenticated JSON API calls return HTTP 401. `/health` and `/api/server-info` stay callable without a session (probes and local restart). Static files under `/static/` are public.

## Roles

| Role | Access |
|------|--------|
| **Member** | Test Runs, Targets, Collections, Analyzer, Matrix, Failures, ZIP export, attachments in the UI, and live UI WebSockets. |
| **Admin** | Everything a Member can do, plus Settings, Server Log, Users, and the matching APIs. |

Members who open an Admin URL see a short **Admin only** page (HTTP 403), not a redirect to login. The Admin sidebar section is hidden for Members.

New local users default to Member unless an Admin chooses Admin when creating the account.

## Manage users

Admins open **Admin → Users** (`/users`).

| Action | Notes |
|--------|--------|
| New local user | Username, password, role, optional display name and email. Usernames are case-insensitive. Passwords must meet `auth.password_min_length` (default 8). |
| Change role | Member or Admin. |
| Disable / enable | A disabled user cannot sign in. Existing sessions for that user are revoked. |
| Reset password | Local accounts only. Existing sessions for that user are revoked. |
| Clear lockout | Local accounts only. Clears failed-login counts for that username. |

SSO users are created on first successful company sign-in. They have no local password; their identity stays with the identity provider.

The last remaining enabled Admin cannot be disabled or changed to Member.

## Lockout

Local password guessing is limited:

| Counter | Default | Effect |
|---------|---------|--------|
| Per username | 5 failures in 15 minutes | That username is locked for the rest of the window |
| Per client IP | 20 failures in 15 minutes | That IP is locked for the rest of the window |

Locked attempts still return **Invalid username or password.** Admins can see lockout state on `/users` and clear a username lockout there. IP lockout expires with the window.

Set `lockout_failures: 0` or `ip_lockout_failures: 0` to disable the corresponding counter. See [server_config.md](server_config.md) for `lockout_minutes` and related options.

## Sessions

Signed-in browsers receive an HTTP-only `SameSite=Lax` session cookie. The server stores the session so disabling a user signs them out.

Defaults (overridable in config):

- Idle timeout: 12 hours
- Maximum lifetime: 7 days

Sign out clears the cookie and deletes the session. `/ws/ui` and `/ws/logs/...` use the same cookie as the HTML pages.

## Test clients

NUnit and other collectors are not people. They do not use the login page.

When `auth.ingest_token` is empty, `/ws/nunit` and test-client attachment upload (and commit upload) stay open even if UI authentication is on. That is suitable for `localhost_only` labs.

When `auth.ingest_token` is set, those ingest endpoints require the header `X-TestRift-Ingest-Token` with the same value (or `Authorization: Bearer <token>`). A missing or wrong token is rejected. A signed-in browser session is not a substitute. `/health` stays unauthenticated. Ingest TLS (`tls.ingest`) encrypts that traffic; it does not replace the token. See [tls.md](tls.md).

The NUnit plugin sends the token from `TESTRIFT_INGEST_TOKEN` or `ingestToken` in `TestRiftNUnit.yaml`.

Browser attachment list/download and the rest of the UI follow the signed-in user's role.

## Single sign-on (OpenID Connect)

SAML is not available. Corporate sign-in uses OpenID Connect Authorization Code flow with PKCE. Typical identity providers include Microsoft Entra ID, Okta, Google Workspace, and Keycloak.

### Enable OIDC

```yaml
auth:
  enabled: true
  allow_local: true
  oidc:
    enabled: true
    issuer: "https://login.microsoftonline.com/<tenant-id>/v2.0"
    client_id: "${env:TESTRIFT_OIDC_CLIENT_ID}"
    client_secret: "${env:TESTRIFT_OIDC_CLIENT_SECRET}"
    redirect_uri: "http://127.0.0.1:8080/auth/oidc/callback"
    scopes: ["openid", "profile", "email"]
    default_role: member
    role_claim: groups
    role_map:
      "<admin-group-id>": admin
    role_source: local_override
```

Register a **Web** application at the identity provider. The redirect URI must match `auth.oidc.redirect_uri` exactly. When `redirect_uri` is empty, TestRift uses `{origin}/auth/oidc/callback` from the incoming request (scheme and host, including `X-Forwarded-Proto` / `X-Forwarded-Host` when present).

If both `allow_local` and OIDC are on, the login page shows the company button and the username/password form. Set `allow_local: false` for company accounts only. In that case either set a bootstrap Admin password or include an Admin mapping in `role_map` (or `default_role: admin`) so someone can administer the server.

### Role mapping

On first SSO sign-in, TestRift creates a user (`auth_source` SSO) and assigns a role:

1. If the token/userinfo claim named `role_claim` contains a value listed in `role_map`, that role is used (`admin` wins if several match).
2. Otherwise `default_role` is used (usually Member).

`role_source`:

- **local_override** (default): mapping applies only when the user is created. An Admin can change the role later in TestRift; later SSO logins do not overwrite it.
- **mapped**: mapping is applied on every SSO login.

Display name and email are refreshed on each SSO login. SSO tokens are not stored; the usual session cookie is issued.

If no Admin exists yet, only an SSO user mapped to Admin is accepted (that user becomes Admin). Other SSO users are refused until an Admin exists.

### Microsoft Entra ID

1. In Azure Portal, register an application (single-tenant or multi-tenant as required).
2. Add a Web redirect URI: `http://127.0.0.1:8080/auth/oidc/callback` (or your public HTTPS origin plus `/auth/oidc/callback`).
3. Create a client secret. Store it in `TESTRIFT_OIDC_CLIENT_SECRET`. Store the Application (client) ID in `TESTRIFT_OIDC_CLIENT_ID`.
4. Set `issuer` to `https://login.microsoftonline.com/<tenant-id>/v2.0`.
5. Optional: under Token configuration, add a **groups** claim if you want `role_map` to use Entra group object IDs.

### Okta / Keycloak / Google

Use the provider's OIDC issuer URL (`/.well-known/openid-configuration` must exist under that issuer), a confidential client ID and secret, and the same redirect URI path `/auth/oidc/callback`. For Keycloak the issuer is typically `https://<host>/realms/<realm>`.

Callback endpoints are rate-limited per IP (30 requests per minute). Account lockout for SSO users is handled by the identity provider.

## Related configuration

Full option list, defaults, and validation: [server_config.md](server_config.md). HTTP paths: [http_contracts.md](http_contracts.md).
