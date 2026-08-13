# Commit Diff API

The TestRift server provides HTTP API endpoints for storing and retrieving commit diffs collected by `testrift-collector`.

## Endpoints

### Get last commits for a group

```
GET /api/runs/{run_id}/commit-baselines
```

Returns the commit SHAs from the most recent completed run in the specified group.

**Response:**

```json
{
  "success": true,
  "commits": {
    "my-app": "abc123def456...",
    "firmware": "789xyz..."
  }
}
```

If no previous run exists or the group has no commit data, returns an empty `commits` object.

### Upload commit diffs for a run

```
POST /api/runs/{run_id}/commits
```

When `auth.ingest_token` is set, send `X-TestRift-Ingest-Token` (or `Authorization: Bearer`) with the same value.

Uploads collected commit diffs and associates them with a test run.

**Request body:**

```json
{
  "diffs": [
    {
      "name": "my-app",
      "url": "https://github.com/org/my-app",
      "current_sha": "abc123def456...",
      "previous_sha": "oldsha789...",
      "commits": [
        {
          "sha": "abc123def456...",
          "subject": "Fix bug in login flow",
          "author": "Jane Developer",
          "timestamp": "2024-01-15T10:30:00+00:00",
          "files": [
            {"path": "src/auth/login.ts", "change_type": "M"},
            {"path": "tests/auth.test.ts", "change_type": "M"}
          ]
        }
      ]
    }
  ]
}
```

**Response:**

```json
{
  "success": true,
  "stored_commits": 2,
  "stored_diffs": 1
}
```

### Get commit diffs for a run

```
GET /api/runs/{run_id}/commits
```

Retrieves stored commit diff data for a run.

**Response:**

```json
{
  "success": true,
  "diffs": [
    {
      "name": "my-app",
      "url": "https://github.com/org/my-app",
      "current_sha": "abc123def456...",
      "previous_sha": "oldsha789...",
      "commits": [...]
    }
  ]
}
```

## Data model

### Repository diff

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Repository name (unique per run) |
| `url` | string | User-facing URL for UI links |
| `current_sha` | string | Current commit SHA |
| `previous_sha` | string | Previous commit SHA (from last run) |
| `commits` | array | List of commits between previous and current |

### Commit

| Field | Type | Description |
|-------|------|-------------|
| `sha` | string | Full commit SHA |
| `subject` | string | Commit message subject line |
| `author` | string | Author name |
| `timestamp` | string | ISO 8601 timestamp |
| `files` | array | List of changed files |

### File change

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | File path relative to repo root |
| `change_type` | string | Change type: `A` (added), `M` (modified), `D` (deleted), `R` (renamed) |

## Storage

Commit data is stored in two places:

1. **Database** (`run_commits` table): Stores the current SHA per repo for efficient group queries
2. **Filesystem** (`{run_dir}/commits.json`): Stores full diff data including commit history

The filesystem data is included in ZIP exports.
