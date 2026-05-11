"""
pst_to_backlog_json.py — Convert a .pst file to a backlog-loader JSON file.

Usage:
    python src/pst_to_backlog_json.py <input.pst> [output.json]

If output.json is omitted, writes <input>.backlog.json next to the .pst file.

Requires: libpff-python  (`pip install libpff-python`, import name `pypff`)
"""

from __future__ import annotations

import email as _email
import email.header
import email.utils
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import pypff
except ImportError:
    sys.exit(
        "ERROR: libpff-python is not installed.\n"
        "Run: pip install libpff-python"
    )


def _to_str(value, *, fallback: str = "") -> str:
    """Safely convert a pypff value to a clean Python str.

    pypff can return either str or bytes depending on the libpff version and
    the MAPI property type. Handle both, plus strip MAPI subject-prefix control
    characters (bytes with value <= 0x1F that Outlook prepends to PR_SUBJECT).
    """
    if value is None:
        return fallback
    if isinstance(value, str):
        text = value
    elif isinstance(value, bytes):
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                text = value.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return fallback
    else:
        text = str(value)
    # Strip leading MAPI prefix bytes (control chars Outlook adds to subjects)
    text = text.lstrip("\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
                       "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f")
    return text.strip()


_decode_bytes = _to_str  # backward-compat alias


def _pff_time_to_iso(pff_time) -> str:
    """Return ISO 8601 UTC string from a pypff timestamp, or empty string."""
    if pff_time is None:
        return ""
    try:
        # pypff timestamps expose year/month/day/... attributes
        dt = datetime(
            pff_time.year,
            pff_time.month,
            pff_time.day,
            pff_time.hours,
            pff_time.minutes,
            pff_time.seconds,
            tzinfo=timezone.utc,
        )
        return dt.isoformat()
    except Exception:
        return ""


def _parse_address_list(raw: str) -> list[str]:
    """Parse a comma/semicolon-separated address string into a list of addresses."""
    if not raw:
        return []
    raw = raw.replace(";", ",")
    parsed = email.utils.getaddresses([raw])
    return [addr.strip().lower() for _, addr in parsed if addr.strip()]


def _parse_transport_headers(headers_raw) -> dict:
    """Extract all header fields from raw transport headers."""
    result = {
        "subject": "",
        "from_addr": "",
        "to_addrs": [],
        "cc_addrs": [],
        "bcc_addrs": [],
        "reply_to": "",
        "message_id": "",
    }
    if not headers_raw:
        return result
    raw_str = _to_str(headers_raw)
    if not raw_str:
        return result
    try:
        msg = _email.message_from_string(raw_str)

        # Subject — decode RFC 2047 encoded words (e.g. =?utf-8?q?...?=)
        raw_subj = msg.get("Subject", "")
        if raw_subj:
            parts = _email.header.decode_header(raw_subj)
            decoded_parts = []
            for part, charset in parts:
                if isinstance(part, bytes):
                    decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
                else:
                    decoded_parts.append(part)
            result["subject"] = "".join(decoded_parts).strip()

        # From — prefer the address, fall back to display name
        from_raw = msg.get("From", "")
        if from_raw:
            parsed_from = email.utils.getaddresses([from_raw])
            if parsed_from:
                name, addr = parsed_from[0]
                result["from_addr"] = addr.strip() or name.strip()

        result["to_addrs"] = _parse_address_list(msg.get("To", ""))
        result["cc_addrs"] = _parse_address_list(msg.get("Cc", ""))
        result["bcc_addrs"] = _parse_address_list(msg.get("Bcc", ""))
        reply_to_list = _parse_address_list(msg.get("Reply-To", ""))
        result["reply_to"] = reply_to_list[0] if reply_to_list else ""

        mid = msg.get("Message-ID", "").strip()
        result["message_id"] = re.sub(r"^<|>$", "", mid)
    except Exception:
        pass
    return result


def _synthetic_id(subject: str, received_at: str, body: str) -> str:
    digest = hashlib.sha256(
        f"{subject}|{received_at}|{body}".encode("utf-8")
    ).hexdigest()
    return f"pst:{digest}"


def _extract_body(msg) -> str:
    """Return the best available plain-text body from a pypff message."""
    body = _decode_bytes(msg.plain_text_body)
    if body:
        return body
    html = _decode_bytes(msg.html_body)
    if html:
        # Very basic HTML strip — sufficient for KPI alert emails
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    return ""


def _walk_folder(folder, records: list, stats: dict, depth: int = 0) -> None:
    """Recursively walk a pypff folder and extract messages."""
    for i in range(folder.number_of_sub_items):
        item = folder.get_sub_item(i)
        if isinstance(item, pypff.folder):
            _walk_folder(item, records, stats, depth + 1)
        elif isinstance(item, pypff.message):
            stats["total"] += 1
            try:
                record = _convert_message(item)
                records.append(record)
                stats["converted"] += 1
            except Exception as exc:
                stats["errors"] += 1
                stats["error_list"].append(str(exc))


def _convert_message(msg) -> dict:
    headers_raw = msg.transport_headers
    headers = _parse_transport_headers(headers_raw)

    # Subject: transport headers are most reliable (handles RFC 2047 encoding).
    # Fall back to the MAPI PR_SUBJECT property, stripping any Outlook prefix byte.
    subject = headers["subject"]
    if not subject:
        try:
            subject = _to_str(msg.subject)
        except Exception:
            subject = ""

    body = _extract_body(msg)

    # Sender: prefer email address from headers, fall back to MAPI sender_name.
    from_addr = headers["from_addr"]
    if not from_addr:
        try:
            from_addr = _to_str(msg.sender_name)
        except Exception:
            from_addr = ""

    received_at = _pff_time_to_iso(msg.delivery_time) or _pff_time_to_iso(
        msg.client_submit_time
    )

    message_id = headers["message_id"] or _synthetic_id(subject, received_at, body)

    return {
        "message_id": message_id,
        "subject": subject,
        "from_addr": from_addr,
        "to_addrs": headers["to_addrs"],
        "cc_addrs": headers["cc_addrs"],
        "bcc_addrs": headers["bcc_addrs"],
        "reply_to": headers["reply_to"],
        "received_at": received_at,
        "body": body,
    }


def convert(pst_path: Path, output_path: Path) -> None:
    pf = pypff.file()
    pf.open(str(pst_path))

    root = pf.get_root_folder()
    records: list[dict] = []
    stats: dict = {"total": 0, "converted": 0, "errors": 0, "error_list": []}

    _walk_folder(root, records, stats)
    pf.close()

    output_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"PST messages found:   {stats['total']}")
    print(f"Converted to JSON:    {stats['converted']}")
    print(f"Errors skipped:       {stats['errors']}")
    if stats["error_list"]:
        for err in stats["error_list"][:5]:
            print(f"  ! {err}")
        if len(stats["error_list"]) > 5:
            print(f"  ... and {len(stats['error_list']) - 5} more")
    print(f"Output written to:    {output_path}")
    print()
    print("Next step — dry-run preview:")
    print(
        f"  python src/agent.py load-backlog --source json "
        f"--path {output_path} --dry-run"
    )


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(f"Usage: python {sys.argv[0]} <input.pst> [output.json]")

    pst_path = Path(sys.argv[1])
    if not pst_path.exists():
        sys.exit(f"ERROR: File not found: {pst_path}")
    if pst_path.suffix.lower() != ".pst":
        print(f"WARNING: {pst_path.name} does not have a .pst extension, continuing anyway.")

    if len(sys.argv) >= 3:
        output_path = Path(sys.argv[2])
    else:
        output_path = pst_path.with_suffix(".backlog.json")

    print(f"Reading:  {pst_path}")
    print(f"Writing:  {output_path}")
    print()
    convert(pst_path, output_path)


if __name__ == "__main__":
    main()
