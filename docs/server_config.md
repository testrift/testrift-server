# Server Configuration

The server can be configured using a YAML configuration file named `testrift_server.yaml`.

## Configuration File

### Where to put `testrift_server.yaml`

Use one of these options:

- **Working directory config**: create `testrift_server.yaml` in the directory you run `testrift-server` from.
- **Explicit config path**: set `TESTRIFT_SERVER_YAML` to the path of your config file (absolute path recommended). If this is set, the server will **fail to start** if the file does not exist.

Example (PowerShell):

```powershell
$env:TESTRIFT_SERVER_YAML = "C:\\path\\to\\testrift_server.yaml"
testrift-server
```

### Configuration structure

Create a `testrift_server.yaml` file with the following structure:

```yaml
server:
  # Port number for the server to listen on
  port: 8080
  
  # Whether to only accept connections from localhost (127.0.0.1)
  # Set to false to allow connections from any IP address
  localhost_only: true

data:
  # Directory path for storing test runs
  directory: "data"
  
  # Default retention days for test runs when not specified by client
  # Set to null for no automatic cleanup
  default_retention_days: 7

attachments:
  # Whether attachment upload is enabled
  enabled: true
  
  # Maximum attachment file size (supports B, KB, MB, GB, TB)
  max_size: "10MB"
```

## Configuration Options

### Path resolution

- `data.directory` is resolved relative to the **directory containing the config file**.
- If you run without a config file (packaged defaults), the default data directory is `./data` in your current working directory.

### Server Settings

- **port** (integer, default: 8080): The port number for the server to listen on. Must be between 1 and 65535.
- **localhost_only** (boolean, default: true): If true, the server only accepts connections from localhost (127.0.0.1). If false, it accepts connections from any IP address.

### Data Settings

- **directory** (string, default: "data"): The directory path where test run data is stored.
- **default_retention_days** (integer, null, or 0, default: 7): Default number of days to retain test runs when not specified by the client. Set to `null` or `0` for no automatic cleanup.

### Attachment Settings

- **enabled** (boolean, default: true): Whether attachment upload functionality is enabled. When disabled, attachment upload endpoints return 403 Forbidden.
- **max_size** (string, default: "10MB"): Maximum file size for attachments. Supports units: B (bytes), KB (kilobytes), MB (megabytes), GB (gigabytes), TB (terabytes). Examples: "10MB", "1GB", "500KB".

### Authentication Settings

User management is off by default. The UI and HTTP APIs stay open to anyone who can reach the server; `localhost_only` still applies at bind time. For setup (bootstrap Admin, roles, lockout, and what stays open for test clients), see [authentication.md](authentication.md).

```yaml
auth:
  enabled: false
  allow_local: true
  session_idle_hours: 12
  session_max_days: 7
  password_min_length: 8
  lockout_failures: 5
  lockout_minutes: 15
  ip_lockout_failures: 20
  bootstrap_admin:
    username: admin
    password: "${env:TESTRIFT_BOOTSTRAP_ADMIN_PASSWORD}"
  oidc:
    enabled: false
    issuer: ""
    client_id: "${env:TESTRIFT_OIDC_CLIENT_ID}"
    client_secret: "${env:TESTRIFT_OIDC_CLIENT_SECRET}"
    redirect_uri: ""
    scopes: ["openid", "profile", "email"]
    default_role: member
    role_claim: groups
    role_map: {}
    role_source: local_override
```

- **enabled** (boolean, default: `false`): Master switch. When `false`, the rest of the `auth` block is ignored and no login is required.
- **allow_local** (boolean, default: `true`): Allow username and password login. Ignored unless `enabled` is `true`.
- **session_idle_hours** (number, default: 12): Sign the session out after this much idle time.
- **session_max_days** (number, default: 7): Absolute session lifetime.
- **password_min_length** (integer, default: 8): Minimum length for local passwords.
- **lockout_failures** (integer, default: 5): Failed local logins per username in the lockout window before that username is locked. `0` disables username lockout.
- **lockout_minutes** (number, default: 15): Window for failed-login counters.
- **ip_lockout_failures** (integer, default: 20): Failed local logins per client IP in the lockout window. `0` disables IP lockout.
- **bootstrap_admin.username** (string, default: `admin`): Created once at startup when auth is on and no enabled Admin exists.
- **bootstrap_admin.password** (string, default: empty): Used to create that first Admin. Use `${env:TESTRIFT_BOOTSTRAP_ADMIN_PASSWORD}`. If authentication is on, no Admin exists, and this is empty, the server refuses to start unless OIDC is enabled with a `role_map` (or `default_role`) that can produce an Admin.
- **oidc.enabled** (boolean, default: `false`): Enable OpenID Connect sign-in. Ignored unless `auth.enabled` is `true`.
- **oidc.issuer** (string): IdP issuer URL, for example `https://login.microsoftonline.com/<tenant-id>/v2.0`.
- **oidc.client_id** (string): Application (client) ID. Typically `${env:TESTRIFT_OIDC_CLIENT_ID}`.
- **oidc.client_secret** (string): Client secret for confidential apps. Typically `${env:TESTRIFT_OIDC_CLIENT_SECRET}`.
- **oidc.redirect_uri** (string, default: empty): Must match the redirect URI registered at the IdP. When empty, the server uses `{origin}/auth/oidc/callback`.
- **oidc.scopes** (list, default: `openid`, `profile`, `email`): Scopes requested from the IdP. `openid` is always included.
- **oidc.default_role** (`member` or `admin`, default: `member`): Role for new SSO users when no `role_map` entry matches.
- **oidc.role_claim** (string, default: `groups`): Token or userinfo claim used for role mapping.
- **oidc.role_map** (mapping, default: `{}`): Maps claim values (group names or IDs) to `member` or `admin`.
- **oidc.role_source** (`local_override` or `mapped`, default: `local_override`): `local_override` applies mapping only when the user is created. `mapped` reapplies mapping on every SSO login.

When `auth.oidc.enabled` is true, `issuer` and `client_id` are required. Setup: [authentication.md](authentication.md).

## Examples

### Development Configuration
```yaml
server:
  port: 8080
  localhost_only: true

data:
  directory: "data"
  default_retention_days: 1

attachments:
  enabled: true
  max_size: "5MB"
```

### Production Configuration
```yaml
server:
  port: 9000
  localhost_only: false

data:
  directory: "/var/log/test_runs"
  default_retention_days: 30

attachments:
  enabled: true
  max_size: "50MB"
```

### No Cleanup Configuration
```yaml
server:
  port: 8080
  localhost_only: true

data:
  directory: "data"
  default_retention_days: null

attachments:
  enabled: true
  max_size: "10MB"
```

### Attachments Disabled Configuration
```yaml
server:
  port: 8080
  localhost_only: true

data:
  directory: "data"
  default_retention_days: 7

attachments:
  enabled: false
  max_size: "10MB"  # Ignored when disabled
```

## Default Behavior

If no `testrift_server.yaml` file is found, the server will use these defaults:
- Port: 8080
- Localhost only: true
- Default retention days: 7
- Data directory: "data"
- Attachments enabled: true
- Max attachment size: "10MB"
- Authentication enabled: false

## Error Handling

The server will exit with an error if:
- The configuration file contains invalid YAML syntax
- The required `server` section is missing
- Configuration values are invalid (e.g., port out of range)

A warning will be displayed if the configuration file is not found, and defaults will be used.
