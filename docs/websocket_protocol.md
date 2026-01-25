# WebSocket Protocol Documentation

This document describes the optimized WebSocket protocol used for communication between NUnit test clients and the server.

## Connection

**NUnit client endpoint:** `ws://localhost:8080/ws/nunit`

This endpoint is used by test runners (for example the NUnit plugin) to report runs, test cases, and log batches to the server.

**UI/Web log endpoints (server → browser only):**

- `ws://localhost:8080/ws/ui` – pushes run and test-case status updates to the web UI
- `ws://localhost:8080/ws/logs/{run_id}/{tc_id}` – streams individual log entries for a single test case

## Message Encoding

All WebSocket messages use **MessagePack** binary encoding with optimizations:

1. **Numeric message types** instead of string type names
2. **Millisecond timestamps** (int64) instead of ISO 8601 strings
3. **Numeric status/direction/phase codes** instead of strings
4. **Short field keys** (1-2 characters) instead of full names
5. **String interning** for repeated component/channel names

Messages are sent as binary WebSocket frames (not text frames).

## Protocol Constants

### Message Types (`t` field)

| Code | Name | Description |
|------|------|-------------|
| 1 | run_started | Initiates a new test run |
| 2 | run_started_response | Server response with run_id |
| 3 | test_case_started | Test case begins execution |
| 4 | log_batch | Batch of log entries |
| 5 | exception | Exception/stack trace report |
| 6 | test_case_finished | Test case completed |
| 7 | run_finished | Test run completed |
| 8 | batch | Container for multiple events |
| 9 | heartbeat | Keep-alive message |

### Status Codes (`s` field)

| Code | Name |
|------|------|
| 1 | running |
| 2 | passed |
| 3 | failed |
| 4 | skipped |
| 5 | aborted |
| 6 | finished |

### Direction Codes (`d` field)

| Code | Name | Description |
|------|------|-------------|
| 1 | tx | Transmit (host → device) |
| 2 | rx | Receive (device → host) |

### Phase Codes (`p` field)

| Code | Name |
|------|------|
| 1 | teardown |

### Field Key Reference

| Key | Full Name | Type | Description |
|-----|-----------|------|-------------|
| `t` | type | int | Message type code |
| `r` | run_id | string | Run identifier |
| `n` | run_name | string | Human-readable run name |
| `s` | status | int | Status code |
| `ts` | timestamp | int64 | Milliseconds since Unix epoch |
| `f` | tc_full_name | string | Test case full name |
| `i` | tc_id | string | Test case ID |
| `m` | message | string | Log message |
| `c` | component | int/array | Component (ID or [ID, name]) |
| `ch` | channel | int/array | Channel (ID or [ID, name]) |
| `d` | dir | int | Direction code |
| `p` | phase | int | Phase code |
| `e` | entries | array | Log entries |
| `ev` | events | array | Batch events |
| `et` | event_type | int | Event type in batch |
| `xt` | exception_type | string | Exception type name |
| `st` | stack_trace | array | Stack trace lines |
| `ie` | is_error | bool | Is infrastructure error |
| `md` | user_metadata | object | User metadata |
| `g` | group | object | Group info |
| `rd` | retention_days | int | Days to retain data |
| `lr` | local_run | bool | Local run flag |
| `err` | error | string | Error message |
| `ru` | run_url | string | Run page URL |
| `gu` | group_url | string | Group page URL |
| `gh` | group_hash | string | Group hash |

## String Interning

Component and channel names are **interned** to avoid sending the same string repeatedly:

- **First occurrence:** `[id, "string_value"]` - registers the string with an ID
- **Subsequent uses:** `id` - just the integer ID

Example:
```
Entry 1: {"c": [1, "Tester5"], "ch": [1, "COM91"], ...}  // First occurrence
Entry 2: {"c": 1, "ch": 1, ...}                          // ID reference
Entry 3: {"c": 1, "ch": [2, "COM92"], ...}               // Same component, new channel
```

The string table is per-connection and managed by the client. The server tracks received mappings to decode messages.

## Message Types

### 1. Run Started

**Type:** `1` (run_started)

Initiates a new test run.

**Fields:**
- `t`: `1`
- `r`: (optional) Custom run ID
- `n`: (optional) Human-readable run name
- `md`: (optional) User metadata object
- `g`: (optional) Group object with `n` (name) and `md` (metadata)
- `rd`: (optional) Retention days
- `lr`: (optional) Local run flag
- `ts`: (optional) Start timestamp in ms

**Example (MessagePack structure shown as JSON for readability):**
```json
{
  "t": 1,
  "n": "Nightly Build #1234",
  "md": {
    "DUT": {"value": "TestDevice-001", "url": "https://example.com/device/1"}
  },
  "g": {
    "n": "Product Phoenix",
    "md": {"Branch": {"value": "main"}}
  },
  "rd": 7,
  "lr": false
}
```

### 2. Run Started Response (Server → Client)

**Type:** `2` (run_started_response)

**Fields:**
- `t`: `2`
- `r`: Run ID (server-assigned or validated)
- `n`: Final run name
- `ru`: Run URL path
- `gh`: (optional) Group hash
- `gu`: (optional) Group URL
- `err`: (optional) Error message if validation failed

**Example:**
```json
{
  "t": 2,
  "r": "a1b2c3d4",
  "n": "Nightly Build #1234",
  "ru": "/testRun/a1b2c3d4/index.html"
}
```

### 3. Test Case Started

**Type:** `3` (test_case_started)

**Fields:**
- `t`: `3`
- `r`: Run ID
- `f`: Test case full name
- `i`: Test case ID
- `s`: Status code (typically `1` for running)
- `ts`: Start timestamp in ms

**Example:**
```json
{
  "t": 3,
  "r": "a1b2c3d4",
  "f": "MyTests.AuthTest.LoginSuccess",
  "i": "0-1009",
  "s": 1,
  "ts": 1737820282736
}
```

### 4. Log Batch

**Type:** `4` (log_batch)

**Fields:**
- `t`: `4`
- `r`: Run ID
- `i`: Test case ID
- `e`: Array of log entries

**Log Entry Structure:**
- `ts`: Timestamp in ms (int64)
- `m`: Message (string)
- `c`: Component (int ID or [ID, name])
- `ch`: Channel (int ID or [ID, name])
- `d`: Direction (int: 1=tx, 2=rx)
- `p`: Phase (int: 1=teardown)

**Example:**
```json
{
  "t": 4,
  "r": "a1b2c3d4",
  "i": "0-1009",
  "e": [
    {"ts": 1737820282736, "m": "AT+USYCI?", "c": [1, "Tester5"], "ch": [1, "COM91"], "d": 1},
    {"ts": 1737820282737, "m": "+USYCI: 1", "c": 1, "ch": 1, "d": 2}
  ]
}
```

### 5. Exception

**Type:** `5` (exception)

**Fields:**
- `t`: `5`
- `r`: Run ID
- `i`: Test case ID
- `ts`: Timestamp in ms
- `m`: Exception message
- `xt`: Exception type
- `st`: Stack trace (array of strings)
- `ie`: Is error (true for infrastructure errors)

**Example:**
```json
{
  "t": 5,
  "r": "a1b2c3d4",
  "i": "0-1010",
  "ts": 1737820282800,
  "m": "Expected true but was false",
  "xt": "NUnit.Framework.AssertionException",
  "st": ["at MyTests.LoginTest() in Test.cs:line 42"],
  "ie": false
}
```

### 6. Test Case Finished

**Type:** `6` (test_case_finished)

**Fields:**
- `t`: `6`
- `r`: Run ID
- `i`: Test case ID
- `s`: Status code (2=passed, 3=failed, 4=skipped, 5=aborted)
- `ts`: End timestamp in ms

**Example:**
```json
{
  "t": 6,
  "r": "a1b2c3d4",
  "i": "0-1009",
  "s": 2,
  "ts": 1737820283000
}
```

### 7. Run Finished

**Type:** `7` (run_finished)

**Fields:**
- `t`: `7`
- `r`: Run ID
- `s`: Status code (typically `6` for finished)
- `ts`: End timestamp in ms

**Example:**
```json
{
  "t": 7,
  "r": "a1b2c3d4",
  "s": 6,
  "ts": 1737820290000
}
```

### 8. Batch

**Type:** `8` (batch)

Container for multiple events to reduce WebSocket frame overhead.

**Fields:**
- `t`: `8`
- `r`: Run ID
- `ev`: Array of events (each with `et` for event type)

**Event Types in Batch (`et` field):**
- 3 = test_case_started
- 4 = log_batch
- 5 = exception
- 6 = test_case_finished

**Example:**
```json
{
  "t": 8,
  "r": "a1b2c3d4",
  "ev": [
    {"et": 3, "f": "MyTests.Test1", "i": "0-1001", "s": 1, "ts": 1737820282736},
    {"et": 4, "i": "0-1000", "e": [{"ts": 1737820282740, "m": "Log 1", "c": 1}]},
    {"et": 6, "i": "0-1000", "s": 2, "ts": 1737820282800}
  ]
}
```

### 9. Heartbeat

**Type:** `9` (heartbeat)

**Fields:**
- `t`: `9`
- `r`: Run ID

**Example:**
```json
{
  "t": 9,
  "r": "a1b2c3d4"
}
```

## Batching Guidelines

- Send batches every 200-300ms or when accumulated data exceeds ~128KB
- Always flush remaining events before sending `test_case_finished`
- The server processes events in array order
- Individual message types are still supported for simple use cases

## Connection Lifecycle

1. **Connect** to `ws://localhost:8080/ws/nunit`
2. **Send** run_started message (type `1`)
3. **Receive** run_started_response (type `2`) with run_id
4. **Send** test_case_started (type `3`) for each test
5. **Send** log_batch (type `4`) or batch (type `8`) with log entries
6. **Send** exception (type `5`) if test fails
7. **Send** test_case_finished (type `6`) for each test
8. **Send** run_finished (type `7`)
9. **Close** the WebSocket connection

## UI and Log Streaming Channels

These channels broadcast to browser clients using the same optimized format.

### Log Stream (`/ws/logs/{run_id}/{tc_id}`)

Streams log entries and events:
- Log entries with optimized field keys
- Error: `{"t": "error", "m": "..."}`
- Exception: type `5` message

### UI Updates (`/ws/ui`)

Broadcasts run/test status updates using the same message type codes.
