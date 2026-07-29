# Server Tests

This directory contains server-specific unit and integration tests for the test results server.

## Test Files

### `test_database_schema.py`
Tests for database schema and migration functionality:
- Database initialization with the current schema (using `status` instead of `result`)
- Test case creation and updates using the `status` field
- Database queries that rely on `status` rather than `result`
- Test case counting based on the supported status values

### `test_websocket_protocol.py`
Tests for WebSocket protocol message handling:
- `test_case_finished` messages with `status` field
- Invalid status value handling
- Test case counting with status field
- WebSocket log stream connections

### `test_timestamp_handling.py`
Tests for timestamp handling and formatting:
- Correct ISO 8601 timestamp generation
- Malformed timestamp detection and fixing
- JavaScript timestamp parsing (simulated)
- Timestamp consistency across the system

### `test_live_log_streaming.py`
Tests for live log streaming functionality:
- Live run detection (in-memory and from disk)
- WebSocket log stream connections
- Log entry processing through WebSocket
- Connection cleanup and error handling

### `test_asgi_contracts.py`
End-to-end ASGI contract tests against the FastAPI app (no mocked requests):
- Health, index, server-info, targets/collections APIs
- Static file serving and tool redirects
- `/ws/nunit` MessagePack run_started roundtrip
- Attachment upload/list/download over real multipart HTTP

These catch framework regressions that unit tests with MagicMock requests would miss.

### `test_integration.py`
Server integration tests for client-server communication:
- Message processing flow
- Database storage integration
- WebSocket server functionality

### `test_database_functions.py`
Tests for database helper functions and utilities.

### `test_websocket_message_validation.py`
Tests for WebSocket message validation and error handling.

## Running Tests

### Run all server tests:
```bash
cd tests/server
python run_tests.py
```

Or from the root:
```bash
python -m pytest tests/server/ -v
```

### Run specific test file:
```bash
python -m pytest tests/server/test_database_schema.py -v
```

### Run with coverage:
```bash
python -m pytest tests/server/ --cov=server.tr_server --cov=server.database -v
```

## Test Dependencies

Install test dependencies from the root `tests/requirements.txt`:
```bash
pip install -r tests/requirements.txt
```

## Test Structure

Each test file follows the same structure:
- Test classes for related functionality
- Fixtures for common test data
- Async test methods where needed
- Comprehensive error handling tests
- Edge case coverage

The tests are designed to be run independently and as part of a CI/CD pipeline.


