## TLS

TestRift can encrypt test-client traffic (NUnit and collector) without putting the browser UI on an untrusted certificate. HTTP remains the default.

### Why the UI and ingest are separate

Browsers only trust certificates from a CA they already know, or a CA the operator installs. A certificate TestRift generates locally always shows a warning in Chrome, Edge, and Firefox.

NUnit and the collector are TestRift code. They can pin the CA fingerprint themselves, so ingest HTTPS can use an automatically generated certificate.

| Listener | Default | Serves | TLS |
|----------|---------|--------|-----|
| **UI** (`server.port`, 8080) | HTTP | Pages, `/ws/ui`, `/ws/logs/...`, `/health`, `/api/server-info` | Off by default. Optional `auto` or `files`. |
| **Ingest** (`server.ingest_port`, 8443) | HTTPS when ingest TLS is on | `/ws/nunit`, attachment upload, commit upload | `auto` generates a local CA. |

When ingest TLS is off, ingest stays on the UI port (unchanged).

```
                    +-----------------------------+
  Browser  HTTP --> | UI listener :8080           |
                    |  pages, /ws/ui, /ws/logs    |
                    +-----------------------------+
                    | same process                |
  NUnit    WSS  --> | Ingest listener :8443 TLS   |
  Collector HTTPS-> |  /ws/nunit, ingest POSTs    |
                    +-----------------------------+
```

Keep `serverUrl` in NUnit/collector YAML as the **UI** URL (`http://127.0.0.1:8080`). Clients read `ingest_url` from `GET /api/server-info` and send WebSocket and ingest POST traffic there.

### Config

TLS off (default):

```yaml
server:
  port: 8080
  localhost_only: true
```

Encrypt NUnit and collector only; UI stays HTTP:

```yaml
server:
  port: 8080
  ingest_port: 8443
  localhost_only: true
tls:
  ingest: auto
```

Browser HTTPS with a certificate the browser already trusts (mkcert or corporate PKI):

```yaml
tls:
  ui: files
  ingest: files
  cert_file: "${env:TESTRIFT_TLS_CERT}"
  key_file: "${env:TESTRIFT_TLS_KEY}"
```

When both sides use `files` and `server.ingest_port` is omitted, everything shares `server.port`.

| Key | Meaning |
|-----|---------|
| `tls.ingest` | `off` (default), `auto`, or `files` |
| `tls.ui` | `off` (default), `auto`, or `files` |
| `tls.cert_file` / `tls.key_file` | Required when any side is `files` |
| `server.ingest_port` | HTTPS ingest bind. Default `8443` when `tls.ingest` is on and the UI is HTTP |

`tls.ui` and `tls.ingest` cannot mix `auto` and `files`. `localhost_only` applies to both listeners.

Option details: [server_config.md](server_config.md).

### Automatic CA (`auto`)

On first start with `tls.ingest: auto` (or `tls.ui: auto`):

1. Create a local CA in `data/tls/` (`ca.crt`, `ca.key`). The CA key is mode `0600` where the OS allows it.
2. Issue a server certificate with SANs `localhost`, `127.0.0.1`, `::1`, and the machine hostname.
3. Reuse those files on later starts. They are replaced if missing or close to expiry (825-day lifetime).
4. Send the CA in the TLS chain so clients can pin it.
5. Log the CA path and fingerprint:

```
TLS CA fingerprint (SHA-256): AA:BB:CC:...
TLS CA certificate: .../data/tls/ca.crt
```

`GET /ca.crt` serves that CA when it was generated with `auto`. It returns 404 for `files` or when TLS is off.

Do not enable HSTS for `auto` certificates.

### Client trust (TOFU and manual fingerprint)

Clients do **not** treat `/api/server-info` as the trust source. They pin the CA SHA-256 fingerprint from the TLS connection.

**First connection (TOFU).** If no fingerprint is configured and none is stored for that origin (`https://host:port`), the client accepts an otherwise valid chain whose only problem is an untrusted root, then stores the CA fingerprint.

Storage:

- Windows: `%LOCALAPPDATA%\TestRift\known_tls.json`
- Other: `~/.config/testrift/known_tls.json`
- Override path: `TESTRIFT_TLS_KNOWN_HOSTS`

**Later connections.** If the fingerprint changed, the client fails with the expected and observed values, and how to reset (delete the stored entry, or set a manual fingerprint).

**Manual fingerprint.** `tlsFingerprint` / `TESTRIFT_TLS_FINGERPRINT` must match. The client does not write TOFU state. Copy the value from the server log.

Optional `tlsCaFile` / `TESTRIFT_TLS_CA_FILE` (PEM) is turned into that same fingerprint.

**Escape hatch.** `tlsInsecure: true` / `TESTRIFT_TLS_INSECURE=1` skips verification and logs a warning. Do not leave this on.

NUnit example:

```yaml
serverUrl: http://localhost:8080
tlsFingerprint: AA:BB:CC:...
```

### Browser recipes

| Situation | What to do |
|-----------|------------|
| Laptop lab, `localhost_only` | Leave `tls.ui: off`. Open `http://127.0.0.1:8080`. Set `tls.ingest: auto` to encrypt NUnit. |
| Padlock on localhost | [mkcert](https://github.com/FiloSottile/mkcert) for `localhost` and `127.0.0.1`, then `tls.ui: files` (and ingest `files`, often one port). |
| Use the auto CA in the browser | Import `data/tls/ca.crt` into the OS trust store, then `tls.ui: auto`. |
| Company server | Real certificate (or the company proxy). `tls.ui: files` and `tls.ingest: files`. |
| Click through a warning | Possible with `tls.ui: auto` and no import. Works; not the documented happy path. |

OIDC `redirect_uri` must use the same scheme and host the browser uses. If the UI is HTTP locally, keep the HTTP redirect URI. Session cookies are `Secure` only when the UI listener is HTTPS.

### Security notes

- Ingest TLS does not replace `auth.ingest_token`. TLS stops eavesdropping; the token stops strangers who can reach the port.
- `localhost_only` still matters. TLS on `0.0.0.0` without a token is encrypted but open.
- The CA key in `data/tls/` is as sensitive as `data/.session_secret`.

### Out of scope

- ACME / Let’s Encrypt inside TestRift (use a proxy or `files`)
- mTLS (client certificates for NUnit)
- Per-target certificates
- A GUI to install the CA into the browser
