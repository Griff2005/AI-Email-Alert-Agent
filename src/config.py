"""
config.py — Load and validate environment configuration from .env file.
All credentials are read from environment variables only; nothing is hardcoded.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Resolve project root: src/ is one level below project root
_SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SRC_DIR.parent

# Load .env from project root (silently ignore if missing — rely on real env vars)
_env_path = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_env_path)


class Config:
    """Central configuration object loaded from environment variables.

    All values are sourced exclusively from environment variables loaded
    from the project-root ``.env`` file via python-dotenv. Instantiated once
    at module level as the ``config`` singleton; all other modules import
    that instance directly.

    Attributes:
        AGENT_EMAIL: Inbox address the agent monitors via IMAP.
        AGENT_EMAIL_PASSWORD: App password for IMAP and SMTP authentication.
        IMAP_HOST: IMAP server hostname (e.g. ``imap.gmail.com``).
        IMAP_PORT: IMAP SSL port (default 993).
        SMTP_HOST: SMTP server hostname (e.g. ``smtp.gmail.com``).
        SMTP_PORT: SMTP STARTTLS port (default 587).
        DEMO_RECIPIENT_EMAIL: All outbound mail is redirected here in DEMO_MODE.
        DEMO_MODE: When True, enforces recipient redirect and demo disclaimers.
        CLAUDE_MODEL: Claude model identifier passed to the CLI (default Haiku).
        FLASK_HOST: Interface Flask binds to (default ``0.0.0.0``).
        FLASK_PORT: Port Flask listens on (default 5000).
        FLASK_DEBUG: Enable Flask debug mode (default False).
        DATABASE_PATH: Absolute path to the SQLite database file.
        CLAUDE_CACHE_ENABLED: Toggle the on-disk prompt/response cache.
        CLAUDE_CACHE_PATH: Absolute path to the JSON cache file.
        FOLLOWUP_CHECK_INTERVAL: Seconds between follow-up deadline checks.
    """

    # Agent mailbox
    AGENT_EMAIL: str = os.getenv("AGENT_EMAIL", "agent@placeholder.com")
    AGENT_EMAIL_PASSWORD: str = os.getenv("AGENT_EMAIL_PASSWORD", "PLACEHOLDER")

    # IMAP
    IMAP_HOST: str = os.getenv("IMAP_HOST", "imap.placeholder.com")
    IMAP_PORT: int = int(os.getenv("IMAP_PORT", "993"))

    # SMTP
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.placeholder.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))

    # Demo safety
    DEMO_RECIPIENT_EMAIL: str = os.getenv("DEMO_RECIPIENT_EMAIL", "demo@placeholder.com")
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "true").lower() == "true"

    # AI
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    # Flask
    FLASK_HOST: str = os.getenv("FLASK_HOST", "0.0.0.0")
    FLASK_PORT: int = int(os.getenv("FLASK_PORT", "5000"))
    FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    # Database
    DATABASE_PATH: Path = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/agent.db")

    # Claude response cache
    CLAUDE_CACHE_ENABLED: bool = os.getenv("CLAUDE_CACHE_ENABLED", "true").lower() == "true"
    CLAUDE_CACHE_PATH: Path = PROJECT_ROOT / os.getenv("CLAUDE_CACHE_PATH", "data/claude_cache.json")

    # Scheduler
    FOLLOWUP_CHECK_INTERVAL: int = int(os.getenv("FOLLOWUP_CHECK_INTERVAL", "300"))

    @classmethod
    def is_imap_configured(cls) -> bool:
        """Return True only if IMAP credentials are real, not placeholder values.

        Checks all three required values: host, email address, and password.
        Used by ``email_reader.poll_inbox`` and the IMAP loop in ``agent.py``
        to decide whether to attempt a real inbox connection.

        Returns:
            True if IMAP_HOST, AGENT_EMAIL, and AGENT_EMAIL_PASSWORD are all
            non-placeholder; False otherwise.
        """
        return (
            cls.IMAP_HOST != "imap.placeholder.com"
            and cls.AGENT_EMAIL_PASSWORD != "PLACEHOLDER"
            and cls.AGENT_EMAIL != "agent@placeholder.com"
        )

    @classmethod
    def is_smtp_configured(cls) -> bool:
        """Return True only if SMTP credentials are real, not placeholder values.

        Used by ``email_sender.send_draft`` to choose between a live send and
        a dry-run log. Only checks host and password — the sender address
        is the same credential as IMAP.

        Returns:
            True if SMTP_HOST and AGENT_EMAIL_PASSWORD are non-placeholder.
        """
        return (
            cls.SMTP_HOST != "smtp.placeholder.com"
            and cls.AGENT_EMAIL_PASSWORD != "PLACEHOLDER"
        )

    @classmethod
    def validate(cls) -> None:
        """Log configuration status at agent startup.

        Prints warnings when credentials are placeholder values and confirms
        the demo recipient address when DEMO_MODE is active. Does not raise —
        placeholder credentials are intentional for demo mode and must not
        prevent the agent from starting.
        """
        if not cls.is_imap_configured():
            print("[CONFIG] IMAP credentials are placeholder — inbox polling will be disabled.")
        if not cls.is_smtp_configured():
            print("[CONFIG] SMTP credentials are placeholder — email sending will be disabled.")
        if cls.DEMO_MODE:
            print(f"[CONFIG] DEMO_MODE=true — all outbound email redirected to {cls.DEMO_RECIPIENT_EMAIL}")


# Singleton instance
config = Config()
