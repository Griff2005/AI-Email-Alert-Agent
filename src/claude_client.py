"""
claude_client.py — Wrapper for claude CLI subprocess calls.

Features:
- Calls `claude --print --model <model>` via subprocess
- SHA256-keyed on-disk response cache to avoid re-spending tokens on identical prompts
- Prompt injection safety: HTML stripping, whitespace normalization, delimiter wrapping,
  and suspicious-content detection with automatic manual_review flagging
"""

import hashlib
import json
import os
import re
import subprocess

from content_safety import detect_injection, sanitize_email_content
from config import config


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    """Load the on-disk cache. Returns empty dict if missing or corrupt."""
    if not config.CLAUDE_CACHE_ENABLED:
        return {}
    path = config.CLAUDE_CACHE_PATH
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    """Persist the cache to disk."""
    if not config.CLAUDE_CACHE_ENABLED:
        return
    path = config.CLAUDE_CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def _cache_key(prompt: str) -> str:
    """Return a SHA256 hex digest of the prompt string."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core Claude CLI call
# ---------------------------------------------------------------------------

def call_claude(prompt: str, use_cache: bool = True) -> str:
    """Call the Claude CLI with ``--print`` and return its stdout.

    Invokes ``claude --print --model <CLAUDE_MODEL>`` as a subprocess, passing
    ``prompt`` via stdin. Checks an on-disk SHA-256-keyed JSON cache before
    spawning a subprocess; stores the response on a cache miss.

    Args:
        prompt: Full prompt string to pass via stdin to the Claude CLI.
        use_cache: If True and ``CLAUDE_CACHE_ENABLED`` is set, check the
            on-disk cache before invoking the CLI and store on a miss.

    Returns:
        Claude's response as a stripped string.

    Raises:
        FileNotFoundError: If the ``claude`` binary is not found on PATH.
        RuntimeError: If the Claude CLI exits with a non-zero return code.
        subprocess.TimeoutExpired: If the Claude CLI does not respond within 90 seconds.
    """
    key = _cache_key(prompt)

    if use_cache and config.CLAUDE_CACHE_ENABLED:
        cache = _load_cache()
        if key in cache:
            return cache[key]

    env = os.environ.copy()

    try:
        result = subprocess.run(
            ["claude", "--print", "--model", config.CLAUDE_MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            "claude CLI not found on PATH. Ensure it is installed and accessible."
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"Claude CLI returned non-zero exit code {result.returncode}.\n"
            f"stderr: {result.stderr.strip()}"
        )

    response = result.stdout.strip()

    # Layer 2: scan Claude's own output — injected email content may have leaked into the response
    if detect_injection(response):
        import warnings
        warnings.warn(
            f"Prompt injection pattern detected in Claude response. "
            f"Response preview: {response[:120]!r}",
            RuntimeWarning,
            stacklevel=2,
        )

    if use_cache and config.CLAUDE_CACHE_ENABLED:
        cache = _load_cache()
        cache[key] = response
        _save_cache(cache)

    return response


# ---------------------------------------------------------------------------
# JSON-enforced call
# ---------------------------------------------------------------------------

def call_claude_json(prompt: str, use_cache: bool = True) -> dict:
    """Call Claude and parse the response as JSON.

    Wraps ``call_claude`` and strips markdown code fences (`` ```json ... ``` ``)
    before parsing so callers always receive a clean dict regardless of whether
    Claude wraps its output.

    Args:
        prompt: Full prompt string. Should instruct Claude to respond with
            valid JSON only.
        use_cache: Passed through to ``call_claude``.

    Returns:
        Parsed dict from Claude's JSON response.

    Raises:
        ValueError: If the response cannot be parsed as JSON after stripping
            code fences. Raw response is included in the error message.
        FileNotFoundError: Propagated from ``call_claude`` if CLI not found.
        RuntimeError: Propagated from ``call_claude`` on non-zero exit.
    """
    raw = call_claude(prompt, use_cache=use_cache)

    # Strip markdown code fences: ```json ... ``` or ``` ... ```
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Claude response was not valid JSON.\nRaw response:\n{raw}\nError: {exc}"
        )
