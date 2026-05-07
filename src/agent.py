"""
agent.py — CLI entry point for the Solucore Email Alert Triage Agent.

Usage:
  python src/agent.py ingest         Process sample emails from data/sample_emails.json
  python src/agent.py run            Start IMAP polling + scheduler + Flask web server
  python src/agent.py reply --case-id CASE_ID   Interactive reply handler
  python src/agent.py demo           Run all sample emails and display results

All paths are resolved relative to the project root (parent of src/).
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

# Add src/ to path so all modules are importable regardless of CWD
_SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SRC_DIR))

from config import config, PROJECT_ROOT
import database as db
from case_manager import process_email, process_reply
from email_reader import poll_inbox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_sample_emails():
    """Load and return the sample email list from ``data/sample_emails.json``.

    Returns:
        List of email dicts with keys: ``id``, ``subject``, ``from``,
        ``to``, ``date``, and ``body``.

    Raises:
        SystemExit: If the file is not found at the expected path.
    """
    sample_path = PROJECT_ROOT / "data" / "sample_emails.json"
    if not sample_path.exists():
        print(f"[AGENT] ERROR: Sample emails file not found at {sample_path}")
        sys.exit(1)
    with open(sample_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _store_email(em: dict) -> str:
    """Insert a sample email dict into the database and return its email_id.

    Args:
        em: Email dict with at least ``subject``, ``from``, ``to``,
            ``date``, and ``body`` keys.

    Returns:
        The ``email_id`` string used for the inserted record.
    """
    from claude_client import sanitize_email_content
    email_id = em.get("id") or str(uuid.uuid4())
    normalized = sanitize_email_content(em.get("body", ""))
    db.insert_email(
        email_id=email_id,
        message_id=em.get("id", email_id),
        thread_id=None,
        subject=em.get("subject", ""),
        from_addr=em.get("from", ""),
        to_addr=em.get("to", ""),
        received_at=em.get("date", ""),
        raw_body=em.get("body", ""),
        normalized_text=normalized,
    )
    return email_id


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_ingest(args):
    """Process all sample emails from ``data/sample_emails.json``.

    Safe to run multiple times — duplicate emails are ignored and existing
    cases are updated rather than duplicated.

    Args:
        args: Parsed argparse namespace (no additional attributes used).
    """
    print("[AGENT] Starting ingest from sample_emails.json...")
    db.init_schema()
    config.validate()

    emails = _load_sample_emails()
    print(f"[AGENT] Loaded {len(emails)} sample email(s).")

    results = []
    for em in emails:
        email_id = _store_email(em)
        result = process_email(
            email_id=email_id,
            subject=em.get("subject", ""),
            body=em.get("body", ""),
            from_addr=em.get("from", ""),
            received_at=em.get("date", ""),
            verbose=True,
        )
        results.append(result)
        print(
            f"  -> action={result['action']}, "
            f"case_type={result['case_type']}, "
            f"case_id={result.get('case_id', 'N/A')}"
        )

    created = sum(1 for r in results if r["action"] == "created")
    updated = sum(1 for r in results if r["action"] == "updated")
    skipped = sum(1 for r in results if r["action"] == "skipped")
    reviewed = sum(1 for r in results if r["action"] == "review_flagged")

    print(
        f"\n[AGENT] Ingest complete: "
        f"{created} created, {updated} updated, "
        f"{skipped} skipped, {reviewed} flagged for review."
    )
    print(f"[AGENT] View cases at http://localhost:{config.FLASK_PORT}/cases")


def cmd_demo(args):
    """Run the demo: ingest all sample emails and print a formatted results table.

    Same pipeline as ``cmd_ingest`` but with formatted terminal output to
    showcase the system. Lists all case UUIDs at the end for use with
    the ``reply`` command.

    Args:
        args: Parsed argparse namespace (no additional attributes used).
    """
    print("=" * 70)
    print("  Solucore Email Alert Triage Agent — DEMO")
    print("=" * 70)

    db.init_schema()
    config.validate()

    emails = _load_sample_emails()
    print(f"\nProcessing {len(emails)} sample emails...\n")

    results = []
    for i, em in enumerate(emails, 1):
        print(f"[{i}/{len(emails)}] Subject: '{em.get('subject', 'N/A')}'")
        email_id = _store_email(em)
        result = process_email(
            email_id=email_id,
            subject=em.get("subject", ""),
            body=em.get("body", ""),
            from_addr=em.get("from", ""),
            received_at=em.get("date", ""),
            verbose=False,
        )
        results.append((em, result))

        action_symbol = {
            "created": "+",
            "updated": "~",
            "skipped": "-",
            "review_flagged": "!",
        }.get(result["action"], "?")

        print(
            f"  [{action_symbol}] {result['action'].upper():<16} "
            f"Type: {result['case_type']:<28} "
            f"Case: {result.get('case_id', 'N/A')}"
        )
        if result.get("injection_detected"):
            print("  [!] WARNING: Possible prompt injection detected.")
        print()

    print("=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)
    print(f"  {'#':<4} {'Subject':<45} {'Type':<30} {'Action'}")
    print(f"  {'-'*4} {'-'*45} {'-'*30} {'-'*10}")
    for i, (em, result) in enumerate(results, 1):
        subj = em.get("subject", "")[:44]
        print(f"  {i:<4} {subj:<45} {result['case_type']:<30} {result['action']}")

    print()
    all_cases = db.get_all_cases()
    print(f"  Total cases in database: {len(all_cases)}")
    print()
    print("  Full case IDs (use with reply command):")
    for c in all_cases:
        print(f"    {c['case_type']:<30} {c['case_id']}")
    print()
    print(f"  Run 'python src/agent.py run' and visit http://localhost:{config.FLASK_PORT}")
    print("=" * 70)


def cmd_run(args):
    """Start the full agent: IMAP polling, follow-up scheduler, and Flask.

    Startup sequence:
    1. Initialise database schema.
    2. Validate configuration and print status.
    3. Start APScheduler background follow-up checker.
    4. If IMAP is configured: start daemon polling thread (60s interval).
    5. Start Flask web server (blocking).

    Args:
        args: Parsed argparse namespace (no additional attributes used).
    """
    print("[AGENT] Starting Solucore Email Alert Triage Agent...")
    db.init_schema()
    config.validate()

    # Start follow-up scheduler
    from followup import start_scheduler
    scheduler = start_scheduler()

    # Start IMAP polling in a background thread (if configured)
    if config.is_imap_configured():
        import threading

        def imap_loop():
            print("[AGENT] IMAP polling started.")
            while True:
                try:
                    emails = poll_inbox(mark_seen=True)
                    for em in emails:
                        db.insert_email(
                            email_id=em["email_id"],
                            message_id=em["message_id"],
                            thread_id=None,
                            subject=em["subject"],
                            from_addr=em["from_addr"],
                            to_addr=em["to_addr"],
                            received_at=em["received_at"],
                            raw_body=em["raw_body"],
                            normalized_text=em["raw_body"],
                        )
                        process_email(
                            email_id=em["email_id"],
                            subject=em["subject"],
                            body=em["raw_body"],
                            from_addr=em["from_addr"],
                            received_at=em["received_at"],
                            verbose=True,
                        )
                except Exception as exc:
                    print(f"[IMAP] Error in polling loop: {exc}")
                time.sleep(60)  # Poll every 60 seconds

        imap_thread = threading.Thread(target=imap_loop, daemon=True)
        imap_thread.start()
    else:
        print("[AGENT] IMAP not configured — polling disabled.")

    # Start Flask
    print(f"[AGENT] Starting Flask on http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    from web.app import app
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
        # use_reloader=False prevents Werkzeug's reloader from forking the process,
        # which would start a second APScheduler instance and double all follow-up jobs.
        use_reloader=False,
    )


def cmd_reply(args):
    """Interactive CLI handler for processing a reply to a specific case.

    Prompts for reply content terminated by ``---END---``, calls
    ``case_manager.process_reply``, and if resolution is indicated, asks
    the user to confirm before closing. Cases are never closed without
    explicit human confirmation.

    Args:
        args: Parsed argparse namespace. Must include ``case_id``.
    """
    case_id = args.case_id

    db.init_schema()
    config.validate()

    case = db.get_case_by_id(case_id)
    if not case:
        print(f"[AGENT] ERROR: Case '{case_id}' not found.")
        all_cases = db.get_all_cases()
        if all_cases:
            print("[AGENT] Available case IDs:")
            for c in all_cases:
                print(f"  {c['case_id']}  ({c['case_type']}, {c['status']})")
        sys.exit(1)

    print(f"\n[AGENT] Reply handler for case: {case_id}")
    print(f"  Type:     {case['case_type']}")
    print(f"  Status:   {case['status']}")
    print(f"  Building: {case['building'] or 'N/A'}")
    print(f"  Device:   {case['device'] or 'N/A'}")
    print()

    print("Paste the reply email content below.")
    print("When done, enter a line with just '---END---' and press Enter.\n")

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "---END---":
            break
        lines.append(line)

    reply_text = "\n".join(lines).strip()
    if not reply_text:
        print("[AGENT] No reply content entered. Aborting.")
        sys.exit(0)

    print("\n[AGENT] Analyzing reply with Claude...")
    result = process_reply(case_id=case_id, reply_text=reply_text, verbose=True)

    print()
    print("[AGENT] Analysis complete:")
    print(f"  Satisfies action:    {result['satisfies_action']}")
    print(f"  Flagged for review:  {result['flagged_for_review']}")
    print()

    if result["satisfies_action"]:
        print("[AGENT] The reply suggests corrective action has been taken.")
        print("[AGENT] This case has been flagged for manual review.")
        print("[AGENT] NOTE: Cases are NEVER auto-closed. A human must close the case.")
        print()
        confirm = input("Do you want to keep this case open (recommended) or close it manually? [open/close]: ").strip().lower()
        if confirm == "close":
            db.update_case(case_id, {"status": "closed"})
            db.insert_case_event(
                event_id=str(uuid.uuid4()),
                case_id=case_id,
                event_type="case_closed",
                description="Case manually closed via CLI reply handler after reviewing reply.",
            )
            print(f"[AGENT] Case {case_id} closed.")
        else:
            print(f"[AGENT] Case {case_id} remains open for manual review.")
    else:
        print(f"[AGENT] Case {case_id} updated with reply analysis. No automatic state change.")

    print(f"\n[AGENT] View case at http://localhost:{config.FLASK_PORT}/cases/{case_id}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Solucore Email Alert Triage Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  ingest       Process sample emails from data/sample_emails.json
  demo         Run demo mode: ingest all samples and display results
  run          Start full agent (IMAP polling + scheduler + Flask)
  reply        Interactive reply handler

Examples:
  python src/agent.py ingest
  python src/agent.py demo
  python src/agent.py run
  python src/agent.py reply --case-id <CASE_ID>
        """,
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("ingest", help="Process sample emails")
    subparsers.add_parser("demo", help="Run demo with all sample emails")
    subparsers.add_parser("run", help="Start full agent")

    reply_parser = subparsers.add_parser("reply", help="Interactive reply handler")
    reply_parser.add_argument(
        "--case-id", required=True, metavar="CASE_ID",
        help="Case ID to process reply for"
    )

    args = parser.parse_args()

    if args.command == "ingest":
        cmd_ingest(args)
    elif args.command == "demo":
        cmd_demo(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "reply":
        cmd_reply(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
