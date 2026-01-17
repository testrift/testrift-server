# WebSocket Protocol Documentation

This document describes the WebSocket protocol used for communication between NUnit test clients and the server.

## Connection

**NUnit client endpoint:** `ws://localhost:8080/ws/nunit`

This endpoint is used by test runners (for example the NUnit plugin) to report runs, test cases, and log batches to the server.

**UI/Web log endpoints (server → browser only):**

- `ws://localhost:8080/ws/ui` – pushes run and test-case status updates to the web UI
- `ws://localhost:8080/ws/logs/{run_id}/{test_case_id}` – streams individual log entries (and related events) for a single test case to the test case log page

Unless otherwise noted, the message types below describe what **clients send to `/ws/nunit`**.

## Message Format

All messages are JSON objects with the following structure:

```json
{
  "type": "message_type",
  "run_id": "unique-run-identifier",
  // ... other fields specific to message type
}
```

## Message Types

### 1. Run Started

**Type:** `run_started`

**Purpose:** Initiates a new test run. The server generates the `run_id` and sends it back in a `run_started_response` message.

**Fields:**
- `type`: `"run_started"`
- `run_id`: `string` (optional) - Custom run ID for this test run. If provided, the server will validate that:
  - The run ID is URL-safe (can use percent encoding like `%2F` for special characters)
  - The run ID does not contain a raw slash character (`/`)
  - The run ID is not already in use (not in database or active runs)
  - If validation fails, the server returns an error in `run_started_response`
  - If omitted, the server generates a unique run ID automatically
- `run_name`: `string` (optional) - Human-readable name for the test run, displayed in the UI instead of `run_id`
- `user_metadata`: `object` - Optional metadata about the test run
- `group`: `object` (optional) - Information used to group runs together
  - `name`: `string` - Display name for the group (for example a product or pipeline)
  - `metadata`: `object` - Key/value map describing the group. Each key maps to an object with `value` and optional `url`, identical to `user_metadata`.
- `retention_days`: `number` - Optional number of days to retain the test data. If omitted, the server uses its configured default (`default_retention_days`).
- `local_run`: `boolean` - Optional flag indicating if this is a local run
- `start_time`: `string` - Optional ISO 8601 timestamp for when the run started. If omitted, the server uses the time it receives the message.

**Example (with custom run_id):**
```json
{
  "type": "run_started",
  "run_id": "nightly-build-1234",
  "run_name": "Nightly Build #1234",
  "user_metadata": {
    "DUT": {"value": "TestDevice-001", "url": "https://device-manager.example.com/devices/TestDevice-001"},
    "Firmware": {"value": "release/v2.1.0", "url": null},
    "TestSystem": {"value": "nuts-v2", "url": "https://nuts.example.com/dashboard"}
  },
  "group": {
    "name": "Product Phoenix",
    "metadata": {
      "Branch": {"value": "release/v2.1.0", "url": "https://git.example.com/repos/phoenix/tree/release/v2.1.0"},
      "Environment": {"value": "staging"}
    }
  },
  "retention_days": 2,
  "local_run": false
}
```

**Example (server-generated run_id):**
```json
{
  "type": "run_started",
  "run_name": "Nightly Build #1234",
  "user_metadata": {
    "DUT": {"value": "TestDevice-001", "url": "https://device-manager.example.com/devices/TestDevice-001"}
  },
  "retention_days": 2,
  "local_run": false
}
```

**Notes:**
- If `run_id` is provided, the server validates it and returns an error in `run_started_response` if invalid
- If `run_id` is omitted, the server generates a unique `run_id` and sends it back in `run_started_response`
- If `run_name` is omitted, the server generates one from the current timestamp
- If `run_name` already exists, the server appends a counter (e.g., "My Run" → "My Run 1")

When a `group` is provided the server computes a deterministic hash from the group name plus each metadata name/value pair. That hash appears in group-specific URLs (`/groups/<hash>`, `/analyzer?group=<hash>`, `/matrix?group=<hash>`) so the UI and APIs can focus on that subset of runs.

### 1a. Run Started Response (Server → Client)

**Type:** `run_started_response`

**Purpose:** Server response to `run_started`, providing the server-generated `run_id`, final `run_name`, and URLs. The client must use this `run_id` for all subsequent messages.

**Fields:**
- `type`: `"run_started_response"`
- `run_id`: `string` - Unique identifier for the test run (server-generated if not provided by client, or the validated client-provided run_id)
- `run_name`: `string` - Final run name (may have counter appended if duplicate)
- `run_url`: `string` - Relative URL path to the test run page (e.g., `/testRun/{run_id}/index.html`)
- `group_hash`: `string` (optional) - Hash of the group, present only if the run belongs to a group
- `group_url`: `string` (optional) - Relative URL path to the group runs page (e.g., `/groups/{group_hash}`)
- `error`: `string` (optional) - Error message if the run_id validation failed. If present, the run was not started and the client should not proceed with sending test case messages.

**Notes:**
- If `run_name` was not provided in `run_started`, the server generates one from the current timestamp (e.g., "Run 2025-01-15 14:30:00")
- If a `run_name` already exists, the server appends a counter (e.g., "My Run" → "My Run 1" → "My Run 2")

**Example (success):**
```json
{
  "type": "run_started_response",
  "run_id": "a1b2c3d4",
  "run_name": "Nightly Build #1234",
  "run_url": "/testRun/a1b2c3d4/index.html",
  "group_hash": "abc123def456",
  "group_url": "/groups/abc123def456"
}
```

**Example (error - invalid run_id):**
```json
{
  "type": "run_started_response",
  "error": "Run ID 'nightly/build-1234' cannot contain raw slash character (use percent encoding %2F if needed)"
}
```

### 2. Test Case Started

**Type:** `test_case_started`

**Purpose:** Indicates that a test case has begun execution

**Fields:**
- `type`: `"test_case_started"`
- `run_id`: `string` - The run ID this test case belongs to
- `tc_full_name`: `string` - Full name/identifier for the test case (e.g., namespace.class.method)
- `tc_id`: `string` - Client-generated test case ID (8-character hexadecimal string, e.g., "00000001"). The client is responsible for generating sequential tc_ids for each test case in the run. The server validates that tc_id is exactly 8 hexadecimal characters.
- `tc_meta`: `object` (optional) - Additional metadata about the test case. Common fields:
  - `status`: `string` - Typically `"running"` when a test case starts
  - `start_time`: `string` - ISO 8601 timestamp when the test case started

**Note:** The `tc_id` is generated by the client as a sequential counter converted to an 8-character hexadecimal string (e.g., test case 1 is "00000001", test case 2 is "00000002"). The server uses this `tc_id` for URL routing and file storage. All subsequent messages for this test case (log_batch, exception, test_case_finished) must use the `tc_id` field.

**Example:**
```json
{
  "type": "test_case_started",
  "run_id": "run-tree-test-174600",
  "tc_full_name": "AuthenticationTest.LoginSuccess",
  "tc_id": "00000001",
  "tc_meta": {
    "status": "running",
    "start_time": "2025-09-20T15:46:05.800000Z"
  }
}
```

### 3. Log Batch

**Type:** `log_batch`

**Purpose:** Sends log entries for a test case

**Fields:**
- `type`: `"log_batch"`
- `run_id`: `string` - The run ID this log batch belongs to
- `tc_id`: `string` - The client-generated 8-character hexadecimal test case ID (e.g., "00000001")
- `entries`: `array` - Array of log entries
- `count`: `number` (optional) - Number of log entries in this batch. If omitted, the server derives the count from the length of `entries`.

**Log Entry Structure:**
- `timestamp`: `string` - ISO 8601 timestamp
- `message`: `string` - The log message
- `component`: `string` - Optional component name (first-level grouping)
- `channel`: `string` - Optional channel identifier (second-level grouping, child of component)
- `dir`: `string` (optional) - Logical direction of the underlying communication:
  - `"tx"` – message sent from the local side (host → component)
  - `"rx"` – message received by the local side (component → host)

**Example:**
```json
{
  "type": "log_batch",
  "run_id": "run-tree-test-174600",
  "tc_id": "00000001",
  "count": 2,
  "entries": [
    {
      "timestamp": "2025-09-20T15:46:05.858941Z",
      "message": "AT+USYCI?",
      "component": "Tester5",
      "channel": "COM91",
      "dir": "tx"
    },
    {
      "timestamp": "2025-09-20T15:46:05.859941Z",
      "message": "AT+USYCI?",
      "component": "Tester5",
      "channel": "COM91",
      "dir": "rx"
    }
  ]
}
```

### 4. Exception

**Type:** `exception`

**Purpose:** Reports an exception for a specific test case. Exceptions are typically sent when a test fails, but the API can also be used explicitly from test code to record diagnostic information.

**Fields:**
- `type`: `"exception"`
- `run_id`: `string` - The run ID this exception belongs to
- `tc_id`: `string` - The client-generated 8-character hexadecimal test case ID (e.g., "00000001")
- `timestamp`: `string` - ISO 8601 timestamp indicating when the exception was captured
- `message`: `string` - Optional exception or failure message
- `exception_type`: `string` - Optional exception type (e.g., `System.InvalidOperationException`)
- `stack_trace`: `array` - List of strings representing the full multiline stack trace (one line per entry)
- `is_error`: `boolean` (optional) - `true` for unexpected/unhandled errors (infrastructure/runtime issues), `false` for normal assertion/test failures

**Example:**
```json
{
  "type": "exception",
  "run_id": "run-tree-test-174600",
  "tc_id": "00000002",
  "timestamp": "2025-09-20T15:46:05.912Z",
  "message": "Expected true but was false",
  "exception_type": "NUnit.Framework.AssertionException",
  "stack_trace": [
    "at AuthenticationTest.LoginFailure() in ExampleTests.cs:line 42"
  ],
  "is_error": false
}
```

### 5. Test Case Finished

**Type:** `test_case_finished`

**Purpose:** Indicates that a test case has completed execution

**Fields:**
- `type`: `"test_case_finished"`
- `run_id`: `string` - The run ID this test case belongs to
- `tc_id`: `string` - The client-generated 8-character hexadecimal test case ID (e.g., "00000001")
- `status`: `string` - Test status (see Valid Status Values below)

**Valid Status Values for `test_case_finished`:**
- `"passed"` - Test passed successfully
- `"failed"` - Test failed
- `"skipped"` - Test was skipped
- `"aborted"` - Test was aborted (e.g., due to timeout or connection loss)

**Example:**
```json
{
  "type": "test_case_finished",
  "run_id": "run-tree-test-174600",
  "tc_id": "00000001",
  "status": "passed"
}
```

### 6. Run Finished

**Type:** `run_finished`

**Purpose:** Indicates that the entire test run has completed

**Fields:**
- `type`: `"run_finished"`
- `run_id`: `string` - The run ID
- `status`: `string` - Run status (typically `"finished"`)

**Example:**
```json
{
  "type": "run_finished",
  "run_id": "run-tree-test-174600",
  "status": "finished"
}
```

## UI and Log Streaming Channels (Server → Browser)

These channels are used only between the server and the browser-based UI. Test clients do **not** connect to them.

### 1. Log Stream (`/ws/logs/{run_id}/{tc_id}`)

When the test case log page connects to:

- `ws://<host>:<port>/ws/logs/{run_id}/{tc_id}`

**Note:** The `{tc_id}` in the URL is the server-generated hash, not the full test case name.

the server sends:

- **Existing log entries first**, one WebSocket message per entry
- **New log entries** as they arrive, again one entry per message

**Normal log entry format (no `type` field):**

```json
{
  "timestamp": "2025-09-20T15:46:05.858941Z",
  "message": "AT+USYCI?",
  "dir": "tx",
  "component": "Tester5",
  "channel": "COM91"
}
```

Log entries may also include:

- `phase`: `string` (optional) - For example `"teardown"` when the entry occurred during teardown.

**Special messages on this channel:**

- Error:

  ```json
  {
    "type": "error",
    "message": "Test run not found"
  }
  ```

- Exception push (fields correspond to the `exception` message described above):

  ```json
  {
    "type": "exception",
    "timestamp": "2025-09-20T15:46:05.912Z",
    "message": "Expected true but was false",
    "exception_type": "NUnit.Framework.AssertionException",
    "stack_trace": [
      "at AuthenticationTest.LoginFailure() in ExampleTests.cs:line 42"
    ]
  }
  ```

### 2. UI Updates (`/ws/ui`)

The test run index and test case log pages connect to:

- `ws://<host>:<port>/ws/ui`

The server broadcasts JSON messages with a `type` field. Common message types include:

- `run_started` – `{ "type": "run_started", "run": { ...run metadata... } }`
- `run_finished` – `{ "type": "run_finished", "run": { ...run metadata... } }`
- `test_case_started` – `{ "type": "test_case_started", "run_id": "...", "tc_full_name": "...", "tc_id": "...", "tc_meta": { ... }, "counts": { "passed": 0, "failed": 0, "skipped": 0, "aborted": 0 } }`
- `test_case_updated` – same shape as `test_case_started`, used when a test case status changes
- `test_case_finished` – same shape as `test_case_updated`, sent when a test case completes
- `exception` – `{ "type": "exception", "run_id": "...", "tc_id": "...", "stack_trace": { ...fields as in the exception message type above... } }`

**Note:** The UI messages include both `tc_full_name` (for display) and `tc_id` (for routing/links).

## Server Response Format

The server logs all received messages in the following format:

```
{"event": "message_type", "run_id": "run-id", "tc_full_name": "test-case-full-name", "ts": "2025-09-20T15:46:02.868227Z"}
```

The server adds a `ts` (timestamp) field to all logged events.

## Error Handling

### Invalid Test Status

If a test case finishes with an invalid status, the server will log an error and ignore the test case:

```
Error: Invalid test status 'pass' for test case AuthenticationTest.Logout, ignoring test case
```

**Valid status values for `test_case_finished`:** `passed`, `failed`, `skipped`, `aborted`

### Missing Fields

If required fields are missing, the server will log an error:

```
Error: run_id missing from test_case_finished message
Error: Run 'run-id' not found for test_case_finished message
```

## Connection Lifecycle

1. **Connect** to `ws://localhost:8080/ws/nunit`
2. **Send** `run_started` message
3. **Send** multiple `test_case_started` messages
4. **Send** multiple `log_batch` messages for each test case
5. **Send** `test_case_finished` messages for each test case
6. **Send** `run_finished` message
7. **Close** the WebSocket connection

## Best Practices

1. **Use unique run IDs** - Generate unique identifiers for each test run
2. **Send messages in order** - Start test cases before finishing them
3. **Use valid status values** - Only use the accepted status strings
4. **Include timestamps** - Use ISO 8601 format for all timestamps
5. **Handle connection errors** - Implement proper error handling for connection issues
6. **Send log batches regularly** - Don't wait too long between log batches for long-running tests

## Example Client Implementation

```python
import asyncio
import aiohttp
import json
from datetime import datetime, UTC

async def send_test_run():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("ws://localhost:8080/ws/nunit") as ws:
            run_id = f"test-run-{datetime.now().strftime('%H%M%S')}"
            
            # Start run
            await ws.send_json({
                "type": "run_started",
                "run_id": run_id,
                "user_metadata": {},
                "retention_days": 2,
                "local_run": False
            })
            
            # Generate tc_id (8-character hex, sequential counter)
            tc_counter = 1
            tc_id = f"{tc_counter:08x}"  # "00000001"

            # Start test case
            await ws.send_json({
                "type": "test_case_started",
                "run_id": run_id,
                "tc_full_name": "MyTest.TestMethod",
                "tc_id": tc_id
            })
            
            # Send log batch
            await ws.send_json({
                "type": "log_batch",
                "run_id": run_id,
                "tc_id": tc_id,
                "count": 1,
                "entries": [{
                    "timestamp": datetime.now(UTC).isoformat() + "Z",
                    "message": "Test log message",
                    "component": "TestDevice",
                    "channel": "COM1"
                }]
            })
            
            # Finish test case
            await ws.send_json({
                "type": "test_case_finished",
                "run_id": run_id,
                "tc_id": tc_id,
                "status": "passed"
            })
            
            # Finish run
            await ws.send_json({
                "type": "run_finished",
                "run_id": run_id,
                "status": "finished"
            })

# Run the example
asyncio.run(send_test_run())
```

## Notes

- The server expects the `type` field, not `event`
- Test case full names (`tc_full_name`) can contain special characters like quotes (handled by the server)
- The server automatically handles HTML entity decoding for test case full names
- **Client tc_id generation**: Clients must generate a unique `tc_id` for each test case as an 8-character hexadecimal string (e.g., "00000001", "00000002"). The client sends both `tc_full_name` and `tc_id` in the `test_case_started` message, then uses only `tc_id` in all subsequent messages (`log_batch`, `exception`, `test_case_finished`).
- The server validates that `tc_id` is exactly 8 hexadecimal characters and normalizes it to lowercase
- Connection monitoring includes automatic cleanup of aborted runs
- All timestamps should be in UTC format with 'Z' suffix
