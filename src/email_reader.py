"""
email_reader.py — IMAP inbox polling for inbound KPI alert emails.

Gracefully degrades when credentials are placeholder values:
logs a warning and returns an empty list rather than crashing.
"""

import email
import email.header
import email.message
import email.utils
import imaplib
import uuid
from datetime import datetime
from email.header import decode_header
from typing import Any, Dict, List, Optional

from config import config


def _decode_header_value(raw: Optional[Any]) -> str:
    """Decode an RFC 2047-encoded email header value to a plain Python string.

    Handles encoded-word sequences (e.g. ``=?UTF-8?B?...?=``) from non-ASCII
    subject lines. Falls back to UTF-8 with ``errors='replace'`` if the
    declared charset is unrecognised.

    Args:
        raw: Raw header value — str, bytes, or encoded-header object.
            Returns empty string for None.

    Returns:
        Decoded plain-text string.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    decoded_parts = decode_header(raw)
    parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            try:
                parts.append(part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, Exception):
                parts.append(part.decode("utf-8", errors="replace"))
        else:
            parts.append(str(part))
    return "".join(parts)


def _extract_body(msg: email.message.Message) -> str:
    """Extract the plain-text body from a parsed email message.

    Walks multipart messages collecting ``text/plain`` parts and ignoring
    attachments. Falls back to ``text/html`` if no plain-text part is found.
    All decoding uses ``errors='replace'`` to survive malformed charsets in
    third-party alert systems.

    Args:
        msg: Parsed email message from ``email.message_from_bytes``.

    Returns:
        Concatenated body text. Empty string if no suitable part is found.
    """
    body_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition:
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    body_parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    pass
            elif content_type == "text/html" and not body_parts:
                # Fall back to HTML if no plain text found
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    body_parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            body_parts.append(payload.decode(charset, errors="replace"))
        except Exception:
            body_parts.append(str(msg.get_payload()))
    return "\n".join(body_parts)


def poll_inbox(mark_seen: bool = True) -> List[Dict[str, str]]:
    """Connect to the IMAP inbox and fetch all unseen messages.

    Returns an empty list when IMAP credentials are placeholder values so the
    demo runs without a real inbox. All error paths (connection failure, IMAP
    error, per-message parse error) are caught and logged — the polling loop
    in ``agent.py`` continues rather than crashing.

    Args:
        mark_seen: If True, sets the ``\\Seen`` IMAP flag on each fetched
            message so it is not returned on the next poll.

    Returns:
        List of dicts with keys: ``email_id``, ``message_id``, ``subject``,
        ``from_addr``, ``to_addr``, ``received_at``, ``raw_body``.
        Empty list on any failure.
    """
    if not config.is_imap_configured():
        print("[EMAIL_READER] IMAP credentials are placeholder — inbox polling disabled.")
        return []

    results: List[Dict[str, str]] = []

    try:
        mail = imaplib.IMAP4_SSL(config.IMAP_HOST, config.IMAP_PORT)
        mail.login(config.AGENT_EMAIL, config.AGENT_EMAIL_PASSWORD)
        mail.select("INBOX")

        # Search for unseen messages
        _, message_numbers = mail.search(None, "UNSEEN")
        ids = message_numbers[0].split() if message_numbers[0] else []

        print(f"[EMAIL_READER] Found {len(ids)} unseen message(s).")

        for num in ids:
            try:
                _, msg_data = mail.fetch(num, "(RFC822)")
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = _decode_header_value(msg.get("Subject", ""))
                from_addr = _decode_header_value(msg.get("From", ""))
                to_addr = _decode_header_value(msg.get("To", ""))
                message_id = msg.get("Message-ID", str(uuid.uuid4()))
                date_str = msg.get("Date", "")
                raw_body = _extract_body(msg)

                # Parse received_at
                try:
                    received_at = email.utils.parsedate_to_datetime(date_str).isoformat()
                except Exception:
                    received_at = datetime.utcnow().isoformat()

                results.append({
                    "email_id": str(uuid.uuid4()),
                    "message_id": message_id.strip(),
                    "subject": subject,
                    "from_addr": from_addr,
                    "to_addr": to_addr,
                    "received_at": received_at,
                    "raw_body": raw_body,
                })

                if mark_seen:
                    mail.store(num, "+FLAGS", "\\Seen")

            except Exception as exc:
                print(f"[EMAIL_READER] Error parsing message {num}: {exc}")
                continue

        mail.logout()

    except imaplib.IMAP4.error as exc:
        print(f"[EMAIL_READER] IMAP error: {exc}")
    except OSError as exc:
        print(f"[EMAIL_READER] Connection error: {exc}")
    except Exception as exc:
        print(f"[EMAIL_READER] Unexpected error during polling: {exc}")

    return results
