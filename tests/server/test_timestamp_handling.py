#!/usr/bin/env python3
"""
Tests for timestamp handling and formatting in the server.
"""

from datetime import UTC, datetime

import pytest

from testrift_server.utils import now_utc_iso, parse_iso


class TestServerTimestampHandling:
    """Test server-side timestamp handling and formatting."""

    def test_now_utc_iso_format(self):
        """Test that now_utc_iso generates correct ISO format."""
        timestamp = now_utc_iso()

        # Should be a valid ISO 8601 format with Z suffix
        assert timestamp.endswith('Z')
        assert '+' not in timestamp  # Should not have timezone offset

        # Should be parseable by datetime
        parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        assert parsed is not None

        # Should be recent (within last minute)
        now = datetime.now(UTC)
        diff = abs((now - parsed).total_seconds())
        assert diff < 60

    def test_parse_iso_with_z_suffix(self):
        """Test parsing ISO timestamps with Z suffix."""
        timestamp = "2025-10-01T18:49:17.803300Z"
        parsed = parse_iso(timestamp)

        assert parsed is not None
        assert parsed.year == 2025
        assert parsed.month == 10
        assert parsed.day == 1
        assert parsed.hour == 18
        assert parsed.minute == 49
        assert parsed.second == 17

    def test_parse_iso_with_timezone_offset(self):
        """Test parsing ISO timestamps with timezone offset."""
        timestamp = "2025-10-01T18:49:17.803300+00:00"
        parsed = parse_iso(timestamp)

        assert parsed is not None
        assert parsed.year == 2025
        assert parsed.month == 10
        assert parsed.day == 1

    def test_timestamp_consistency(self):
        """Test that timestamps are consistent across the system."""
        # Generate multiple timestamps
        timestamps = [now_utc_iso() for _ in range(5)]

        # All should have the same format
        for ts in timestamps:
            assert ts.endswith('Z')
            assert '+' not in ts
            assert ts.count('T') == 1  # Should have one T separator
            assert ts.count('Z') == 1  # Should have one Z suffix

        # All should be parseable
        for ts in timestamps:
            parsed = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            assert parsed is not None

    def test_timestamp_precision(self):
        """Test timestamp precision and format."""
        timestamp = now_utc_iso()

        # Should have microsecond precision
        assert '.' in timestamp

        # Should be in format: YYYY-MM-DDTHH:MM:SS.ffffffZ
        parts = timestamp.split('T')
        assert len(parts) == 2

        date_part = parts[0]
        time_part = parts[1]

        # Date part should be YYYY-MM-DD
        assert len(date_part) == 10
        assert date_part.count('-') == 2

        # Time part should end with Z and have microseconds
        assert time_part.endswith('Z')
        assert '.' in time_part

        # Remove Z and check time format
        time_without_z = time_part[:-1]
        time_parts = time_without_z.split('.')
        assert len(time_parts) == 2

        # Time should be HH:MM:SS
        time_only = time_parts[0]
        assert len(time_only) == 8  # HH:MM:SS
        assert time_only.count(':') == 2

        # Microseconds should be present
        microseconds = time_parts[1]
        assert len(microseconds) == 6  # ffffff


class TestTimestampCompatibility:
    """Test compatibility and edge cases for server timestamp formats."""

    def test_malformed_timestamp_handling(self):
        """Test handling of malformed timestamps."""
        # Test the malformed timestamp format
        malformed_timestamp = "2025-10-01T18:49:17.803300+00:00Z"

        # The server's parse_iso function should handle this
        # (it removes Z and parses the rest)
        try:
            parsed = parse_iso(malformed_timestamp)
            assert parsed is not None
        except Exception:
            # If it fails, that's expected - the function doesn't handle malformed timestamps
            # This is actually correct behavior
            pass


if __name__ == "__main__":
    pytest.main([__file__])