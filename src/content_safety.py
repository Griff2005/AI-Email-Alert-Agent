"""Content safety helpers for untrusted email text.

These helpers are intentionally transport-agnostic. Product modules can use
them without importing the Claude client or implying a direct model boundary.
"""

from __future__ import annotations

import re

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - lightweight environments may omit bs4
    BeautifulSoup = None


_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|prior|all)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"new\s+persona", re.IGNORECASE),
    re.compile(r"disregard\s+(the\s+)?(above|previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"act\s+as\s+(a\s+)?(different|new|another)", re.IGNORECASE),
    re.compile(r"forget\s+(everything|your\s+instructions)", re.IGNORECASE),
    re.compile(r"override\s+(the\s+)?(above|previous|prior|your)", re.IGNORECASE),
]


def detect_injection(text: str) -> bool:
    """Return True when known prompt-injection wording appears in ``text``."""
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def sanitize_email_content(raw: str) -> str:
    """Strip HTML, normalize whitespace, and wrap content in safety delimiters."""
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(raw, "html.parser")
            text = soup.get_text(separator="\n")
        except Exception:
            text = re.sub(r"<[^>]+>", " ", raw)
    else:
        text = re.sub(r"<[^>]+>", " ", raw)

    lines = []
    for line in text.splitlines():
        stripped = re.sub(r"[ \t]+", " ", line).strip()
        if stripped:
            lines.append(stripped)
    text = "\n".join(lines)

    return (
        "--- EMAIL CONTENT START ---\n"
        + text
        + "\n--- EMAIL CONTENT END ---"
    )
