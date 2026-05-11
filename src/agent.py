"""
agent.py — CLI entry point for the Solucore Email Alert Triage Agent.

Usage:
  python src/agent.py ingest         Process sample emails from data/sample_emails.json
  python src/agent.py run            Start IMAP polling + scheduler + Flask web server
  python src/agent.py reply --case-id CASE_ID   Interactive reply handler
  python src/agent.py demo           Run all sample emails and display results
  python src/agent.py test-demo-scale   Run the safe offline demo validation harness

All paths are resolved relative to the project root (parent of src/).
"""

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# Add src/ to path so all modules are importable regardless of CWD
_SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SRC_DIR))

from ai_gateway import AiUsageConfig, get_ai_gateway
from config import config, PROJECT_ROOT
import database as db
from case_manager import process_email, process_reply
from email_reader import poll_inbox
import memory
from runtime_options import RuntimeOptions, runtime_options


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


def _parse_purpose_caps(values) -> dict:
    caps = {}
    for raw in values or []:
        if "=" not in raw:
            raise SystemExit(f"Invalid --max-ai-calls-for value: {raw!r}. Use PURPOSE=N.")
        purpose, value = raw.split("=", 1)
        purpose = purpose.strip()
        try:
            caps[purpose] = int(value)
        except ValueError as exc:
            raise SystemExit(f"Invalid --max-ai-calls-for value: {raw!r}. Use PURPOSE=N.") from exc
    return caps


def _default_ai_report_path(command_name: str) -> Path:
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return PROJECT_ROOT / "data" / "ai_usage" / f"{command_name}_{timestamp}.json"


def _configure_runtime_from_args(args, command_name: str) -> Path:
    enable_ai = bool(getattr(args, "enable_ai", False))
    allow_uncapped_ai = bool(getattr(args, "allow_uncapped_ai", False))
    max_ai_calls = getattr(args, "max_ai_calls", 0)
    if enable_ai and max_ai_calls in (None, 0) and not allow_uncapped_ai:
        raise SystemExit(
            "AI is not enabled safely. Use --enable-ai with --max-ai-calls N, or add --allow-uncapped-ai explicitly."
        )
    if enable_ai and allow_uncapped_ai:
        print("[AGENT] WARNING: uncapped AI mode enabled explicitly.")

    report_path = getattr(args, "ai_report_path", None) or _default_ai_report_path(command_name)
    report_path = Path(report_path)
    csv_path = report_path.with_suffix(".csv")
    purpose_caps = _parse_purpose_caps(getattr(args, "max_ai_calls_for", []))
    template_outbound_only = bool(getattr(args, "template_outbound_only", True))
    if bool(getattr(args, "ai_outbound_enabled", False)):
        template_outbound_only = False

    runtime_options.configure(
        RuntimeOptions(
            ai_enabled=enable_ai,
            allow_uncapped_ai=allow_uncapped_ai,
            max_ai_calls=max_ai_calls,
            max_ai_calls_per_email=getattr(args, "max_ai_calls_per_email", 0),
            max_ai_calls_per_case=getattr(args, "max_ai_calls_per_case", 0),
            max_ai_calls_by_purpose=purpose_caps,
            ai_budget_mode=getattr(args, "ai_budget_mode", "manual_review"),
            ai_report_path=report_path,
            disable_outbound_generation=bool(getattr(args, "disable_outbound_generation", False)),
            template_outbound_only=template_outbound_only,
            ai_outbound_enabled=bool(getattr(args, "ai_outbound_enabled", False)),
            followups_enabled=not bool(getattr(args, "disable_followups", False)),
            max_followups=getattr(args, "max_followups", 3),
            max_followup_runs=getattr(args, "max_followup_runs", 1000),
        )
    )

    gateway = get_ai_gateway()
    gateway.reset()
    gateway.configure(
        AiUsageConfig(
            enabled=enable_ai,
            allow_uncapped_ai=allow_uncapped_ai,
            max_calls=max_ai_calls,
            max_calls_per_email=getattr(args, "max_ai_calls_per_email", 0),
            max_calls_per_case=getattr(args, "max_ai_calls_per_case", 0),
            max_calls_by_purpose=purpose_caps,
            budget_mode=getattr(args, "ai_budget_mode", "manual_review"),
            report_path=report_path,
            csv_report_path=csv_path,
            model_name=config.CLAUDE_MODEL,
            cache_path=config.CLAUDE_CACHE_PATH,
            config_version=f"agent-{command_name}",
        )
    )
    gateway.set_run_metadata(command=command_name)
    return report_path


def _finalize_ai_report(report_path: Path, **metadata) -> None:
    gateway = get_ai_gateway()
    if metadata:
        gateway.set_run_metadata(**metadata)
    gateway.write_report(report_path, report_path.with_suffix(".csv"))
    summary = gateway.build_report()
    print("AI Usage Summary")
    print(f"- AI enabled: {summary['ai_enabled']}")
    print(f"- Total AI calls: {summary['total_ai_calls']}")
    print(f"- Cache hits: {summary['cache_hits']}")
    print(f"- Blocked AI calls: {summary['total_ai_calls_blocked']}")
    print(f"- Usage report: {report_path}")


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
    report_path = _configure_runtime_from_args(args, "ingest")
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
    _finalize_ai_report(report_path, emails_processed=len(results), cases_created=created, cases_updated=updated)


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

    # Demo mode is for deterministic case history only; keep it free of
    # generated outbound drafts and scheduled follow-up rows unless a
    # separate command explicitly opts into those behaviors.
    args.disable_outbound_generation = True
    args.disable_followups = True
    report_path = _configure_runtime_from_args(args, "demo")
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
    created = sum(1 for _, result in results if result["action"] == "created")
    updated = sum(1 for _, result in results if result["action"] == "updated")
    _finalize_ai_report(report_path, emails_processed=len(results), cases_created=created, cases_updated=updated)


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
    report_path = _configure_runtime_from_args(args, "run")
    db.init_schema()
    config.validate()

    # Start follow-up scheduler
    if runtime_options.get().followups_enabled:
        from followup import start_scheduler
        scheduler = start_scheduler()
        del scheduler
    else:
        print("[AGENT] Follow-up scheduler disabled for this run.")

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
    print(f"[AGENT] AI usage report will be written to {report_path} on exit.")

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
    report_path = _configure_runtime_from_args(args, "reply")

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

    print("\n[AGENT] Analyzing reply...")
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
    _finalize_ai_report(report_path, case_id=case_id, reply_processed=True)


def cmd_memory_rebuild(args):
    """Backfill deterministic memory tables from existing cases and events."""
    print("[AGENT] Rebuilding memory from existing records...")
    db.init_schema()
    config.validate()

    summary = memory.rebuild_memory_from_existing_cases()
    active_patterns = db.get_active_pattern_flags()

    print("[AGENT] Memory rebuild complete.")
    print(f"  Cases processed:          {summary['cases_processed']}")
    print(f"  Email groups processed:   {summary['email_groups_processed']}")
    print(f"  Reply events processed:   {summary['reply_events_processed']}")
    print(f"  Follow-up events handled: {summary['followup_events_processed']}")
    print(f"  Open cases rechecked:     {summary['open_cases_rechecked']}")
    print(f"  Active pattern flags:     {len(active_patterns)}")


def cmd_patterns(args):
    """Print active pattern flags grouped by severity and pattern type."""
    db.init_schema()
    config.validate()

    rows = [dict(row) for row in db.get_active_pattern_flags()]
    if not rows:
        print("[AGENT] No active pattern flags.")
        return

    grouped = {}
    for row in rows:
        grouped.setdefault(row["severity"], {}).setdefault(row["pattern_type"], []).append(row)

    print("[AGENT] Active pattern flags")
    for severity in ("review", "high", "medium", "info"):
        severity_rows = grouped.get(severity)
        if not severity_rows:
            continue
        print(f"\n[{severity.upper()}]")
        for pattern_type, items in sorted(severity_rows.items()):
            print(f"  {pattern_type} ({len(items)})")
            for item in items:
                case_ref = item["case_id"][:8] + "…" if item.get("case_id") else "N/A"
                print(f"    - Case {case_ref}: {item['summary']}")


def cmd_memory_report(args):
    """Print memory context for a single case as formatted JSON."""
    case_id = args.case_id
    db.init_schema()
    config.validate()

    case = db.get_case_by_id(case_id)
    if not case:
        print(f"[AGENT] ERROR: Case '{case_id}' not found.")
        sys.exit(1)

    context = memory.get_memory_context_for_case(case_id)
    print(json.dumps(context, indent=2, sort_keys=True))


def cmd_test_demo_scale(args):
    """Run the safe offline synthetic demo harness."""
    from demo_scale_harness import ScaleTestOptions, format_result_summary, run_demo_scale_test

    result = run_demo_scale_test(
        ScaleTestOptions(
            emails=args.emails,
            seed=args.seed,
            offline=args.offline,
            disable_outbound_generation=args.disable_outbound_generation,
            enable_followups=args.enable_followups,
            report_dir=args.report_dir,
            verbose=args.verbose,
        )
    )
    print(format_result_summary(result))
    if result.overall_result == "FAIL":
        sys.exit(1)


def cmd_load_backlog(args):
    """Import staged backlog KPI emails from a JSON source."""
    if bool(args.dry_run) == bool(args.commit):
        print("[AGENT] ERROR: Set exactly one of --dry-run or --commit.")
        sys.exit(1)

    import backlog_loader

    db.init_schema()
    return backlog_loader.load_backlog(
        source=args.source,
        path=args.path,
        dry_run=bool(args.dry_run),
        limit=args.limit,
        report_dir=args.report_dir,
    )


def _add_common_ai_args(parser, *, include_outbound: bool, include_followups: bool) -> None:
    parser.add_argument("--enable-ai", action="store_true", help="Allow AI usage for ambiguous work only")
    parser.add_argument("--no-ai", action="store_false", dest="enable_ai", help="Disable AI usage explicitly")
    parser.set_defaults(enable_ai=False)
    parser.add_argument("--max-ai-calls", type=int, default=0, help="Maximum live AI calls allowed for this run")
    parser.add_argument("--max-ai-calls-per-email", type=int, default=0, help="Maximum AI calls allowed per email")
    parser.add_argument("--max-ai-calls-per-case", type=int, default=0, help="Maximum AI calls allowed per case")
    parser.add_argument(
        "--max-ai-calls-for",
        action="append",
        default=[],
        metavar="PURPOSE=N",
        help="Purpose-specific AI cap, for example classification=5",
    )
    parser.add_argument(
        "--ai-budget-mode",
        choices=("fail", "manual_review", "skip"),
        default="manual_review",
        help="What to do when AI is disabled or its budget is exhausted",
    )
    parser.add_argument("--allow-uncapped-ai", action="store_true", help="Explicitly allow AI without a max call cap")
    parser.add_argument("--ai-report-path", type=Path, default=None, help="Write the AI usage report to this JSON path")
    if include_outbound:
        parser.add_argument("--disable-outbound-generation", action="store_true", help="Disable outbound draft generation")
        parser.add_argument(
            "--template-outbound-only",
            action="store_true",
            default=True,
            help="Use deterministic outbound templates only",
        )
        parser.add_argument(
            "--ai-outbound-enabled",
            action="store_true",
            help="Allow AI drafting for outbound emails when AI is enabled and budget allows",
        )
    if include_followups:
        parser.add_argument("--disable-followups", action="store_true", help="Disable follow-up processing for this run")
        parser.add_argument("--max-followups", type=int, default=3, help="Maximum follow-up reminders allowed per case")
        parser.add_argument("--max-followup-runs", type=int, default=1000, help="Maximum overdue follow-up records to process in one pass")


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
  load-backlog Import staged historical KPI emails from JSON
  reply        Interactive reply handler
  memory-rebuild  Backfill deterministic memory tables from existing records
  patterns     Print active pattern flags
  memory-report  Print detailed memory context for a case
  test-demo-scale  Run the safe offline demo validation harness

Examples:
  python src/agent.py ingest
  python src/agent.py demo
  python src/agent.py run
  python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run
  python src/agent.py reply --case-id <CASE_ID>
  python src/agent.py memory-rebuild
  python src/agent.py patterns
  python src/agent.py memory-report --case-id <CASE_ID>
  python src/agent.py test-demo-scale --offline --emails 25 --seed 42
        """,
    )
    subparsers = parser.add_subparsers(dest="command")

    ingest_parser = subparsers.add_parser("ingest", help="Process sample emails")
    _add_common_ai_args(ingest_parser, include_outbound=True, include_followups=False)

    demo_parser = subparsers.add_parser("demo", help="Run demo with all sample emails")
    _add_common_ai_args(demo_parser, include_outbound=True, include_followups=False)

    run_parser = subparsers.add_parser("run", help="Start full agent")
    _add_common_ai_args(run_parser, include_outbound=True, include_followups=True)

    subparsers.add_parser("memory-rebuild", help="Backfill deterministic memory tables")
    subparsers.add_parser("patterns", help="Print active pattern flags")

    reply_parser = subparsers.add_parser("reply", help="Interactive reply handler")
    _add_common_ai_args(reply_parser, include_outbound=False, include_followups=False)
    reply_parser.add_argument(
        "--case-id", required=True, metavar="CASE_ID",
        help="Case ID to process reply for"
    )
    memory_report_parser = subparsers.add_parser("memory-report", help="Print memory context for a case")
    memory_report_parser.add_argument(
        "--case-id", required=True, metavar="CASE_ID",
        help="Case ID to report on"
    )
    scale_parser = subparsers.add_parser("test-demo-scale", help="Run the safe offline demo validation harness")
    scale_parser.add_argument("--emails", type=int, default=50, help="Number of synthetic KPI emails to generate")
    scale_parser.add_argument("--seed", type=int, default=42, help="Deterministic random seed")
    scale_parser.add_argument("--offline", action="store_true", default=True, help="Offline-only mode; retained for explicit safety")
    scale_parser.add_argument("--disable-outbound-generation", action="store_true", help="Disable outbound draft generation")
    scale_parser.add_argument(
        "--enable-followups",
        action="store_true",
        help="Enable follow-up simulation for the scale harness",
    )
    scale_parser.add_argument(
        "--report-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "test_runs",
        help="Directory where the harness writes report output",
    )
    scale_parser.add_argument("--verbose", action="store_true", help="Print verbose pipeline output during the harness run")

    backlog_parser = subparsers.add_parser("load-backlog", help="Import staged backlog KPI emails from JSON")
    backlog_parser.add_argument("--source", required=True, choices=["json"], help="Source format (json only)")
    backlog_parser.add_argument("--path", required=True, type=Path, help="Path to backlog JSON file")
    backlog_parser.add_argument("--dry-run", action="store_true", default=False, help="Parse and classify without writing to database")
    backlog_parser.add_argument("--commit", action="store_true", default=False, help="Import accepted emails into database")
    backlog_parser.add_argument("--limit", type=int, default=None, help="Maximum number of records to process")
    backlog_parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Directory for report output (default: data/backlog_runs/<timestamp>/)",
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
    elif args.command == "memory-rebuild":
        cmd_memory_rebuild(args)
    elif args.command == "patterns":
        cmd_patterns(args)
    elif args.command == "memory-report":
        cmd_memory_report(args)
    elif args.command == "test-demo-scale":
        cmd_test_demo_scale(args)
    elif args.command == "load-backlog":
        cmd_load_backlog(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
