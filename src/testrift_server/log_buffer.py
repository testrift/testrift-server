"""
In-memory ring buffer log handler for the server log page.

Captures recent log records so they can be served via the /api/logs endpoint.
"""

import logging
import threading
from collections import deque
from datetime import datetime, timezone
UTC = timezone.utc


class RingBufferHandler(logging.Handler):
    """A logging handler that keeps the last N log records in memory."""

    def __init__(self, capacity=2000):
        super().__init__()
        self._buffer = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0

    def clear(self):
        with self._lock:
            self._buffer.clear()
            self._seq = 0

    def emit(self, record):
        try:
            entry = {
                "seq": self._next_seq(),
                "ts": datetime.fromtimestamp(record.created, tz=UTC)
                      .replace(tzinfo=None).isoformat(timespec="milliseconds") + "Z",
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            with self._lock:
                self._buffer.append(entry)
        except Exception:
            self.handleError(record)

    def _next_seq(self):
        with self._lock:
            self._seq += 1
            return self._seq

    def get_entries(self, after_seq=0, level=None, limit=500):
        """Return log entries with seq > after_seq, optionally filtered by level."""
        with self._lock:
            entries = list(self._buffer)

        if after_seq:
            entries = [e for e in entries if e["seq"] > after_seq]

        if level:
            level_upper = level.upper()
            level_value = getattr(logging, level_upper, None)
            if level_value is not None:
                allowed = {
                    name
                    for name, val in logging._nameToLevel.items()
                    if val >= level_value
                }
                entries = [e for e in entries if e["level"] in allowed]

        if limit and len(entries) > limit:
            entries = entries[-limit:]

        return entries


# Singleton instance — installed once in tr_server.py
log_buffer = RingBufferHandler(capacity=2000)
