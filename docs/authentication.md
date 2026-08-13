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

On startup, if authentication is on and no enabled Admin exists, the server creates that local Admin once. If authentication is on, no Admin exists, and the bootstrap password is empty, the server refuses to start.

After that first start, manage users in the UI. You can leave `bootstrap_admin.password` set; it is not used again once an Admin exists.

When `auth.enabled` is `false`, the rest of the `auth` block is ignored. There is no login page, no session cookie, and no Users page. Settings and Server Log stay visible to anyone who can reach the server.

## Sign in

Open the UI. Unauthenticated browser requests redirect to `/login`. After a successful sign-in, the original page is restored when it was a GET request.

- Use the username and password of a local account.
- Failed local logins always show **Invalid username or password.** That message is also used for unknown users, disabled accounts, and lockout, so a login form cannot be used to discover account names.
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

NUnit and other collectors are not people. `/ws/nunit` and test-client attachment upload stay open when UI authentication is on. Restrict who can reach the server with `localhost_only` or your network, as before.

Browser attachment list/download and the rest of the UI follow the signed-in user's role.

## Single sign-on

Corporate SSO (OpenID Connect through Microsoft Entra ID, Okta, Google Workspace, Keycloak, and similar) is not available. SAML is not available.

Use local accounts created by an Admin, or leave `auth.enabled` false and control access at the network (for example `localhost_only: true`, or a reverse proxy in front of the server).

## Related configuration

Full option list, defaults, and validation: [server_config.md](server_config.md). HTTP paths: [http_contracts.md](http_contracts.md).
