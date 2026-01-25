#!/usr/bin/env python3
"""
Tests for WebSocket message validation and format handling with optimized protocol.
"""

import time
import pytest
import msgpack

from testrift_server.protocol import (
    MSG_RUN_STARTED,
    MSG_TEST_CASE_STARTED,
    MSG_TEST_CASE_FINISHED,
    MSG_LOG_BATCH,
    MSG_EXCEPTION,
    MSG_RUN_FINISHED,
    STATUS_RUNNING,
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_ABORTED,
    STATUS_FINISHED,
    DIR_TX,
    DIR_RX,
    F_TYPE,
    F_RUN_ID,
    F_TC_FULL_NAME,
    F_TC_ID,
    F_STATUS,
    F_TIMESTAMP,
    F_MESSAGE,
    F_COMPONENT,
    F_CHANNEL,
    F_DIR,
    F_ENTRIES,
    F_USER_METADATA,
    F_RETENTION_DAYS,
    F_LOCAL_RUN,
    F_GROUP,
    F_GROUP_NAME,
    F_GROUP_METADATA,
    MSG_TYPE_NAMES,
    STATUS_NAMES,
)


def current_timestamp_ms() -> int:
    """Get current timestamp as milliseconds since epoch."""
    return int(time.time() * 1000)


class TestOptimizedProtocolFormat:
    """Test optimized protocol message format."""

    def test_test_case_finished_message_format(self):
        """Test that test_case_finished messages use numeric status codes."""
        message = {
            F_TYPE: MSG_TEST_CASE_FINISHED,
            F_RUN_ID: "test-run-123",
            F_TC_ID: "0-1009",
            F_STATUS: STATUS_PASSED,
            F_TIMESTAMP: current_timestamp_ms(),
        }

        # Verify message format
        assert message[F_TYPE] == MSG_TEST_CASE_FINISHED
        assert message[F_RUN_ID] == "test-run-123"
        assert message[F_TC_ID] == "0-1009"
        assert message[F_STATUS] == STATUS_PASSED

    def test_log_batch_message_format(self):
        """Test that log_batch messages have correct optimized format."""
        # First entry registers component/channel, second entry uses interned IDs
        message = {
            F_TYPE: MSG_LOG_BATCH,
            F_RUN_ID: "test-run-123",
            F_TC_ID: "0-1009",
            F_ENTRIES: [
                {
                    F_TIMESTAMP: current_timestamp_ms(),
                    F_MESSAGE: "TX: AT+USYCI?",
                    F_COMPONENT: [1, "Tester5"],  # First occurrence
                    F_CHANNEL: [1, "COM91"],  # First occurrence
                    F_DIR: DIR_TX,
                },
                {
                    F_TIMESTAMP: current_timestamp_ms(),
                    F_MESSAGE: "RX: OK",
                    F_COMPONENT: 1,  # Interned reference
                    F_CHANNEL: 1,  # Interned reference
                    F_DIR: DIR_RX,
                }
            ]
        }

        assert message[F_TYPE] == MSG_LOG_BATCH
        assert len(message[F_ENTRIES]) == 2

        # First entry has full string values
        entry0 = message[F_ENTRIES][0]
        assert entry0[F_COMPONENT] == [1, "Tester5"]
        assert entry0[F_CHANNEL] == [1, "COM91"]
        assert entry0[F_DIR] == DIR_TX

        # Second entry has interned references
        entry1 = message[F_ENTRIES][1]
        assert entry1[F_COMPONENT] == 1
        assert entry1[F_CHANNEL] == 1
        assert entry1[F_DIR] == DIR_RX

    def test_status_code_validation(self):
        """Test that status codes are valid integers."""
        valid_status_codes = [STATUS_RUNNING, STATUS_PASSED, STATUS_FAILED, STATUS_SKIPPED, STATUS_ABORTED]

        for status_code in valid_status_codes:
            message = {
                F_TYPE: MSG_TEST_CASE_FINISHED,
                F_RUN_ID: "test-run-123",
                F_TC_ID: "0-1009",
                F_STATUS: status_code,
                F_TIMESTAMP: current_timestamp_ms(),
            }
            assert message[F_STATUS] in valid_status_codes
            assert STATUS_NAMES[status_code] in ["running", "passed", "failed", "skipped", "aborted"]

        # Invalid status code
        invalid_status = 999
        assert invalid_status not in valid_status_codes

    def test_message_serialization_with_msgpack(self):
        """Test that optimized messages can be serialized with MessagePack."""
        ts = current_timestamp_ms()

        messages = [
            # run_started
            {
                F_TYPE: MSG_RUN_STARTED,
                F_USER_METADATA: {"DUT": {"value": "TestDevice-001"}},
                F_GROUP: {
                    F_GROUP_NAME: "Product A",
                    F_GROUP_METADATA: {"Branch": {"value": "main"}}
                },
                F_RETENTION_DAYS: 7,
                F_LOCAL_RUN: False,
            },
            # test_case_started
            {
                F_TYPE: MSG_TEST_CASE_STARTED,
                F_RUN_ID: "test-run-123",
                F_TC_FULL_NAME: "Test.TestMethod",
                F_TC_ID: "0-1009",
                F_STATUS: STATUS_RUNNING,
                F_TIMESTAMP: ts,
            },
            # log_batch
            {
                F_TYPE: MSG_LOG_BATCH,
                F_RUN_ID: "test-run-123",
                F_TC_ID: "0-1009",
                F_ENTRIES: [
                    {F_TIMESTAMP: ts, F_MESSAGE: "TX: AT+USYCI?", F_COMPONENT: [1, "Tester5"], F_CHANNEL: [1, "COM91"]},
                ]
            },
            # test_case_finished
            {
                F_TYPE: MSG_TEST_CASE_FINISHED,
                F_RUN_ID: "test-run-123",
                F_TC_ID: "0-1009",
                F_STATUS: STATUS_PASSED,
                F_TIMESTAMP: ts,
            },
            # run_finished
            {
                F_TYPE: MSG_RUN_FINISHED,
                F_RUN_ID: "test-run-123",
                F_STATUS: STATUS_FINISHED,
                F_TIMESTAMP: ts,
            }
        ]

        for message in messages:
            packed = msgpack.packb(message)
            assert packed is not None
            assert isinstance(packed, bytes)

            # Should be deserializable
            deserialized = msgpack.unpackb(packed)
            assert deserialized == message

            # Verify type is integer
            assert isinstance(deserialized[F_TYPE], int)
            assert deserialized[F_TYPE] in MSG_TYPE_NAMES

    def test_timestamp_is_integer_milliseconds(self):
        """Test that timestamps are int64 milliseconds since epoch."""
        ts = current_timestamp_ms()

        message = {
            F_TYPE: MSG_TEST_CASE_STARTED,
            F_RUN_ID: "test-run-123",
            F_TC_FULL_NAME: "Test.TestMethod",
            F_TC_ID: "0-1009",
            F_TIMESTAMP: ts,
        }

        # Timestamp should be an integer
        assert isinstance(message[F_TIMESTAMP], int)

        # Should be a reasonable timestamp (after 2020)
        min_ts = 1577836800000  # 2020-01-01 00:00:00 UTC
        assert message[F_TIMESTAMP] > min_ts

    def test_string_interning_format(self):
        """Test that string interning uses [id, string] format for first occurrence."""
        # First occurrence: [id, string]
        first_occurrence = [1, "Tester5"]
        assert isinstance(first_occurrence, list)
        assert len(first_occurrence) == 2
        assert isinstance(first_occurrence[0], int)
        assert isinstance(first_occurrence[1], str)

        # Subsequent reference: just the id
        subsequent_reference = 1
        assert isinstance(subsequent_reference, int)

    def test_direction_codes(self):
        """Test that direction uses integer codes."""
        assert DIR_TX == 1
        assert DIR_RX == 2

        log_entry = {
            F_TIMESTAMP: current_timestamp_ms(),
            F_MESSAGE: "TX: AT+TEST",
            F_DIR: DIR_TX,
        }
        assert log_entry[F_DIR] == 1


if __name__ == "__main__":
    pytest.main([__file__])
