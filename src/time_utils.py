"""Small UTC timestamp helpers shared across the demo agent.

The SQLite schema stores timestamp values as text. Existing rows use naive ISO
8601 UTC strings, so helpers below keep that storage format while avoiding
deprecated ``datetime.utcnow()`` calls in application code.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def utc_now_naive() -> datetime:
    """Return the current UTC time as a naive ``datetime`` for legacy comparisons."""
    return utc_now().replace(tzinfo=None)


def utc_now_iso() -> str:
    """Return the current UTC time in the repository's existing ISO format."""
    return utc_now_naive().isoformat()


def utc_compact_timestamp() -> str:
    """Return a UTC timestamp suitable for report filenames."""
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def utc_display_timestamp() -> str:
    """Return a UTC timestamp with a trailing ``Z`` for reports."""
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
