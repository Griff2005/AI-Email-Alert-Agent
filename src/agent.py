"""
agent.py — CLI entry point for the Solucore Email Alert Triage Agent.

Usage:
  python src/agent.py ingest         Process sample emails from data/sample_emails.json
  python src/agent.py run            Start IMAP polling + scheduler + Flask web server
  python src/agent.py reply --case-id CASE_ID   Interactive reply handler
  python src/agent.py demo           Run all sample emails and display results
  python src/agent.py observability-report      Print local metrics/safety snapshot
  python src/agent.py test-demo-scale   Run the safe offline demo validation harness

All paths are resolved relative to the project root (parent of src/).
"""

import argparse
import inspect
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# Add src/ to path so all modules are importable regardless of CWD
_SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SRC_DIR))

from ai_gateway import AiBudgetExceeded, AiGateway, AiUsageConfig, get_ai_gateway
from case_manager import process_email, process_reply
from config import PROJECT_ROOT, config
from constants import (
    CASE_TYPE_CAT1_COMPLIANCE,
    CASE_TYPE_DATA_ABSENCE,
    CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL,
    EVENT_CASE_CLOSED,
    EVENT_CASE_CREATED,
    SUPPORTED_CASE_TYPES_SET,
)
import database as db
from email_reader import poll_inbox
import memory
from runtime_options import RuntimeOptions, runtime_options
from time_utils import utc_compact_timestamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_sample_emails() -> list[dict]:
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
    from content_safety import sanitize_email_content
    email_id = em.get("id") or em.get("email_id") or em.get("message_id") or str(uuid.uuid4())
    to_value = em.get("to", em.get("to_addr", em.get("to_addrs", "")))
    if isinstance(to_value, list):
        to_value = "; ".join(str(item) for item in to_value)
    normalized = sanitize_email_content(em.get("body", ""))
    db.insert_email(
        email_id=email_id,
        message_id=em.get("message_id") or em.get("id", email_id),
        thread_id=None,
        subject=em.get("subject", ""),
        from_addr=em.get("from", em.get("from_addr", "")),
        to_addr=str(to_value or ""),
        received_at=em.get("date", em.get("received_at", "")),
        raw_body=em.get("body", ""),
        normalized_text=normalized,
    )
    return email_id


def _parse_purpose_caps(values) -> dict[str, int]:
    """Parse ``--max-ai-calls-for`` values into a purpose-to-cap mapping."""
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
    """Return the default per-command AI usage report path."""
    timestamp = utc_compact_timestamp()
    return PROJECT_ROOT / "data" / "ai_usage" / f"{command_name}_{timestamp}.json"


def _configure_runtime_from_args(args: argparse.Namespace, command_name: str) -> Path:
    """Configure runtime options and the AI gateway from parsed CLI arguments."""
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
    """Write the AI usage report and print a compact CLI summary."""
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


def _append_command_event(event_name: str, **context: Any) -> None:
    """Append a best-effort structured event for a completed CLI command."""
    from observability import append_structured_event

    try:
        append_structured_event(
            component="agent",
            event_name=event_name,
            status="ok",
            command=context.pop("command", None),
            **context,
        )
    except OSError as exc:
        print(f"[AGENT] WARNING: could not write observability event: {exc}")


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
    _append_command_event(
        "ingest_completed",
        command="ingest",
        emails_processed=len(results),
        cases_created=created,
        cases_updated=updated,
        skipped=skipped,
        reviews_flagged=reviewed,
    )


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
    _append_command_event(
        "demo_completed",
        command="demo",
        emails_processed=len(results),
        cases_created=created,
        cases_updated=updated,
    )


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
                event_type=EVENT_CASE_CLOSED,
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
    if getattr(args, "resume", False):
        print("[BACKLOG] Resume mode: skipping already-imported message IDs.")

    import backlog_loader

    db.init_schema()
    result = backlog_loader.load_backlog(
        source=args.source,
        path=args.path,
        dry_run=bool(args.dry_run),
        limit=args.limit,
        report_dir=args.report_dir,
        progress_interval=getattr(args, "progress_interval", 50),
        report_detail=getattr(args, "report_detail", "summary"),
    )
    _append_command_event(
        "backlog_dry_run_completed" if args.dry_run else "backlog_commit_completed",
        command="load-backlog",
        dry_run=bool(args.dry_run),
        emails_scanned=result.get("emails_scanned"),
        accepted_kpi=result.get("accepted_kpi"),
        new_cases=result.get("new_cases_expected_or_created"),
        case_updates=result.get("case_updates_expected_or_done"),
        review_candidates=result.get("review_candidates"),
        rejected=result.get("rejected"),
        report_dir=result.get("report_dir"),
    )
    return result


def _stable_uuid(*parts: Any) -> str:
    """Return a deterministic UUID for repeatable demo seed records."""
    seed = "|".join(str(part).strip().lower() for part in parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"solucore-phase6:{seed}"))


def _demo_groups() -> list[dict[str, str]]:
    buildings = [
        "100 Demo Tower, Toronto",
        "200 Sample Plaza, Toronto",
        "300 Test Exchange, Toronto",
    ]
    contractors = [
        "Atlas Elevator Demo",
        "Northstar Lift Test",
    ]
    return [
        {"building": building, "contractor": contractor}
        for building in buildings
        for contractor in contractors
    ]


def _demo_case_specs() -> list[dict[str, Any]]:
    case_types = [
        CASE_TYPE_DATA_ABSENCE,
        CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL,
        CASE_TYPE_CAT1_COMPLIANCE,
    ]
    specs: list[dict[str, Any]] = []
    for group_index, group in enumerate(_demo_groups()):
        case_count = 2 + (group_index % 2)
        for case_index in range(case_count):
            case_type = case_types[(group_index + case_index) % len(case_types)]
            sequence = f"{group_index + 1}-{case_index + 1}"
            device = f"Car {case_index + 1} #8{group_index + 1:02d}{case_index + 1:03d}"
            fields = {
                "building": group["building"],
                "contractor": group["contractor"],
                "device": device if case_type != CASE_TYPE_DATA_ABSENCE else "",
                "due_date": f"2026-06-{10 + group_index + case_index:02d}"
                if case_type == CASE_TYPE_CAT1_COMPLIANCE
                else "",
                "period": f"May 2026 - Demo {sequence}"
                if case_type == CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL
                else "",
            }
            specs.append(
                {
                    "group": group,
                    "case_type": case_type,
                    "sequence": sequence,
                    "fields": fields,
                    "subject": f"Demo {case_type.replace('_', ' ').title()} - {group['building']}",
                    "body": (
                        f"Building: {group['building']}\n"
                        f"Contractor: {group['contractor']}\n"
                        f"Device: {fields['device'] or 'Building portfolio'}\n"
                        f"Case Type: {case_type}\n"
                        "This deterministic demo alert was generated by build-demo-scenario."
                    ),
                }
            )
    return specs


def cmd_build_demo_scenario(args):
    """Create deterministic curated demo seed data in the configured database."""
    if not config.DEMO_MODE:
        print("[DEMO] ERROR: build-demo-scenario only runs when DEMO_MODE=true.")
        sys.exit(1)

    import building_groups

    runtime_options.configure(
        RuntimeOptions(
            ai_enabled=False,
            max_ai_calls=0,
            ai_budget_mode="fail",
            disable_outbound_generation=True,
            template_outbound_only=True,
            ai_outbound_enabled=False,
            followups_enabled=False,
        )
    )
    gateway = get_ai_gateway()
    gateway.reset()
    gateway.configure(
        AiUsageConfig(
            enabled=False,
            max_calls=0,
            budget_mode="fail",
            model_name=config.CLAUDE_MODEL,
            config_version="agent-build-demo-scenario",
        )
    )

    db.init_schema()
    groups_created = 0
    cases_created = 0
    emails_created = 0
    drafts_created = 0
    pattern_flags_created = 0
    hypothesis_created = 0
    group_case_ids: dict[str, list[str]] = {}
    demo_case_ids: list[str] = []

    for group in _demo_groups():
        group_id = _stable_uuid("demo-group", group["building"], group["contractor"])
        existing_group = db.get_building_group(group_id)
        actual_group_id = building_groups.get_or_create_group(
            group["building"],
            group["contractor"],
            group_id=group_id,
        )
        if existing_group is None:
            groups_created += 1
        group_case_ids.setdefault(actual_group_id, [])

    for spec in _demo_case_specs():
        group = spec["group"]
        fields = spec["fields"]
        group_id = building_groups.get_or_create_group(
            group["building"],
            group["contractor"],
            group_id=_stable_uuid("demo-group", group["building"], group["contractor"]),
        )
        case_id = _stable_uuid(
            "demo-case",
            group["building"],
            group["contractor"],
            spec["case_type"],
            spec["sequence"],
        )
        email_id = _stable_uuid("demo-email", case_id)
        grouping_key = (
            "demo_scenario|"
            f"{group['building'].lower()}|{group['contractor'].lower()}|"
            f"{spec['case_type'].lower()}|{spec['sequence']}"
        )
        case_row = db.get_case_by_id(case_id) or db.get_case_by_grouping_key(grouping_key)
        if db.get_email_by_id(email_id) is None:
            db.insert_email(
                email_id=email_id,
                message_id=f"demo-scenario-{case_id}@example.test",
                thread_id=None,
                subject=spec["subject"],
                from_addr="demo-alerts@example.test",
                to_addr="triage@example.test",
                received_at="2026-05-14T09:00:00",
                raw_body=spec["body"],
                normalized_text=spec["body"],
            )
            db.mark_email_processed(email_id)
            emails_created += 1

        if case_row is None:
            db.insert_case(
                case_id=case_id,
                case_type=spec["case_type"],
                grouping_key=grouping_key,
                building=fields["building"],
                device=fields["device"] or None,
                contractor=fields["contractor"],
                due_date=fields["due_date"] or None,
                period=fields["period"] or None,
                priority="high" if spec["case_type"] == CASE_TYPE_CAT1_COMPLIANCE else "medium",
            )
            db.insert_case_event(
                event_id=_stable_uuid("demo-event-created", case_id),
                case_id=case_id,
                event_type=EVENT_CASE_CREATED,
                description="Deterministic demo case created by build-demo-scenario.",
                source_email_id=email_id,
            )
            for field_name, field_value in fields.items():
                if field_value:
                    db.insert_extracted_field(
                        field_id=_stable_uuid("demo-field", case_id, field_name),
                        case_id=case_id,
                        email_id=email_id,
                        field_name=field_name,
                        field_value=str(field_value),
                        confidence_score=1.0,
                    )
            cases_created += 1
        else:
            case_id = case_row["case_id"]

        building_groups.attach_case_to_group(case_id=case_id, source="manual", enqueue=False)
        if case_id not in demo_case_ids:
            demo_case_ids.append(case_id)
        group_case_ids.setdefault(group_id, [])
        if case_id not in group_case_ids[group_id]:
            group_case_ids[group_id].append(case_id)

    for group in _demo_groups():
        group_id = building_groups.get_or_create_group(
            group["building"],
            group["contractor"],
            group_id=_stable_uuid("demo-group", group["building"], group["contractor"]),
        )
        draft_id = _stable_uuid("demo-group-draft", group_id)
        if db.get_building_group_email(draft_id) is not None:
            continue
        case_ids = group_case_ids.get(group_id, [])
        subject = f"Action Required: Demo KPI Items for {group['building']}"
        body = "\n".join(
            [
                "Hello,",
                "",
                f"Please review the open demo KPI items for {group['building']}.",
                f"Contractor: {group['contractor']}",
                "",
                "Open demo cases:",
                *[f"- {case_id}" for case_id in case_ids],
                "",
                "Please provide the missing data, maintenance-hour evidence, or CAT1 compliance update listed for each item.",
                "",
                "Thank you,",
                "Solucore Demo",
            ]
        )
        db.insert_building_group_email(
            group_email_id=draft_id,
            group_id=group_id,
            email_type="initial",
            status="draft_generated",
            subject=subject,
            body=body,
            intended_to="contractor-contact@example.test",
            intended_cc="",
            actual_to=config.DEMO_RECIPIENT_EMAIL,
            summary_json=json.dumps(
                {
                    "source": "build-demo-scenario",
                    "group_id": group_id,
                    "case_ids": case_ids,
                    "building": group["building"],
                    "contractor": group["contractor"],
                },
                sort_keys=True,
            ),
            quality_check_json=json.dumps({"passed": True, "failures": []}, sort_keys=True),
        )
        drafts_created += 1

    demo_case_ids = sorted(demo_case_ids)
    if demo_case_ids:
        first_flag = db.upsert_pattern_flag_record(
            case_id=demo_case_ids[0],
            pattern_type="demo_repeated_data_absence",
            severity="review",
            summary="Demo pattern: repeated missing maintenance data across a building group.",
            evidence_json=json.dumps({"source": "build-demo-scenario"}, sort_keys=True),
        )
        second_flag = db.upsert_pattern_flag_record(
            case_id=demo_case_ids[min(1, len(demo_case_ids) - 1)],
            pattern_type="demo_shortfall_cluster",
            severity="medium",
            summary="Demo pattern: maintenance-hour shortfall cluster for review.",
            evidence_json=json.dumps({"source": "build-demo-scenario"}, sort_keys=True),
        )
        pattern_flags_created += int(first_flag["created"]) + int(second_flag["created"])

    hypothesis_id = _stable_uuid("demo-hypothesis", "building-contractor-pattern")
    existing_hypothesis_ids = {
        row["hypothesis_id"]
        for row in db.get_connection_hypotheses()
    }
    if hypothesis_id not in existing_hypothesis_ids:
        db.insert_connection_hypothesis(
            hypothesis_id=hypothesis_id,
            hypothesis_type="demo_building_contractor_pattern",
            summary="Demo hypothesis: repeated KPI issues may share a building/contractor operational cause.",
            confidence="medium",
            risk_level="review",
            evidence_json=json.dumps(
                {
                    "source": "build-demo-scenario",
                    "case_ids": demo_case_ids[:3],
                },
                sort_keys=True,
            ),
            reasoning="Seeded deterministic demo hypothesis for review workflow validation.",
            recommended_human_review="Compare source cases before accepting or rejecting this hypothesis.",
            status="proposed",
        )
        hypothesis_created = 1
    for case_id in demo_case_ids[:3]:
        db.insert_connection_hypothesis_case(hypothesis_id, case_id)

    print("[DEMO] Scenario built.")
    print(f"[DEMO] Groups created:       {groups_created}")
    print(f"[DEMO] Emails created:       {emails_created}")
    print(f"[DEMO] Cases created:        {cases_created}")
    print(f"[DEMO] Pattern flags created:{pattern_flags_created}")
    print(f"[DEMO] Drafts created:       {drafts_created}")
    print(f"[DEMO] Hypotheses created:   {hypothesis_created}")
    return {
        "groups_created": groups_created,
        "emails_created": emails_created,
        "cases_created": cases_created,
        "pattern_flags_created": pattern_flags_created,
        "drafts_created": drafts_created,
        "hypotheses_created": hypothesis_created,
    }


def _database_path_from_args(args: argparse.Namespace) -> tuple[Path, bool]:
    supplied = getattr(args, "database", None)
    target = Path(supplied) if supplied else Path(config.DATABASE_PATH)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    return target, supplied is not None


def _reset_refusal(reason: str) -> None:
    print(f"[RESET] REFUSED: {reason}")
    sys.exit(1)


def _is_safe_reset_path(path: Path) -> bool:
    # Check filename only (not full path) to avoid false-positives from parent
    # directories like /tmp/workspace or CI paths containing "test".
    lowered = path.name.lower()
    return any(token in lowered for token in ("demo", "test", "tmp"))


def cmd_reset_demo_db(args):
    """Safely delete and recreate a demo/test SQLite database."""
    target_path, explicit_database = _database_path_from_args(args)
    if not config.DEMO_MODE and not explicit_database:
        _reset_refusal("DEMO_MODE=false and --database was not provided.")
    if not getattr(args, "yes", False):
        _reset_refusal("confirmation required; pass --yes to reset a safe demo/test/tmp database.")
    if not _is_safe_reset_path(target_path):
        _reset_refusal("database path must contain demo, test, or tmp.")

    config.DATABASE_PATH = target_path
    db.close_connection()
    for path in (
        target_path,
        target_path.with_name(target_path.name + "-wal"),
        target_path.with_name(target_path.name + "-shm"),
    ):
        if path.exists():
            path.unlink()
    db.init_schema()
    print(f"[RESET] Database reset: {target_path}")
    return str(target_path)


def _set_config_runtime_value(name: str, value: Any) -> None:
    setattr(config, name, value)
    setattr(type(config), name, value)


def cmd_replay(args):
    """Replay a JSON list of demo emails through the normal local pipeline."""
    replay_path = Path(args.path)
    if not replay_path.exists():
        print(f"[REPLAY] ERROR: replay file not found: {replay_path}")
        sys.exit(1)
    try:
        emails = json.loads(replay_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[REPLAY] ERROR: invalid JSON: {exc}")
        sys.exit(1)
    if not isinstance(emails, list) or not all(isinstance(item, dict) for item in emails):
        print("[REPLAY] ERROR: replay JSON must be a list of email objects.")
        sys.exit(1)

    forced_names = {
        "DEMO_MODE": True,
        "IMAP_HOST": "imap.placeholder.com",
        "SMTP_HOST": "smtp.placeholder.com",
        "AGENT_EMAIL": "agent@placeholder.com",
        "AGENT_EMAIL_PASSWORD": "PLACEHOLDER",
    }
    originals = {
        name: (getattr(config, name), getattr(type(config), name))
        for name in forced_names
    }
    try:
        for name, value in forced_names.items():
            _set_config_runtime_value(name, value)
        runtime_options.configure(
            RuntimeOptions(
                ai_enabled=False,
                max_ai_calls=0,
                ai_budget_mode="manual_review",
                disable_outbound_generation=False,
                template_outbound_only=True,
                ai_outbound_enabled=False,
                followups_enabled=False,
            )
        )
        gateway = get_ai_gateway()
        gateway.reset()
        gateway.configure(
            AiUsageConfig(
                enabled=False,
                max_calls=0,
                budget_mode="manual_review",
                model_name=config.CLAUDE_MODEL,
                config_version="agent-replay",
            )
        )
        db.init_schema()

        summary = {"created": 0, "updated": 0, "review_flagged": 0, "skipped": 0, "drafts_generated": 0}
        for index, email in enumerate(emails, start=1):
            before_drafts = db.get_dashboard_summary()["draft_messages"]
            email_id = _store_email(email)
            result = process_email(
                email_id=email_id,
                subject=email.get("subject", ""),
                body=email.get("body", ""),
                from_addr=email.get("from", email.get("from_addr", "")),
                received_at=email.get("date", email.get("received_at", "")),
                verbose=False,
            )
            after_drafts = db.get_dashboard_summary()["draft_messages"]
            draft_generated = after_drafts > before_drafts
            summary[result["action"]] = summary.get(result["action"], 0) + 1
            if draft_generated:
                summary["drafts_generated"] += 1
            print(
                f"[REPLAY] {index}/{len(emails)} "
                f"action={result['action']} "
                f"case_type={result['case_type']} "
                f"case_id={result.get('case_id') or 'N/A'} "
                f"draft_generated={'yes' if draft_generated else 'no'}"
            )
        print(
            "[REPLAY] Summary: "
            f"{summary.get('created', 0)} created, "
            f"{summary.get('updated', 0)} updated, "
            f"{summary.get('review_flagged', 0)} review, "
            f"{summary.get('skipped', 0)} skipped, "
            f"{summary.get('drafts_generated', 0)} drafts generated."
        )
        return summary
    finally:
        for name, (instance_value, class_value) in originals.items():
            setattr(config, name, instance_value)
            setattr(type(config), name, class_value)


def cmd_rebuild_building_groups(args: argparse.Namespace) -> dict:
    """Rebuild building issue group links from existing cases."""
    import building_groups

    db.init_schema()
    summary = building_groups.rebuild_all_groups(
        include_closed=bool(args.include_closed),
        dry_run=bool(args.dry_run),
    )
    mode = "DRY RUN" if args.dry_run else "COMMIT"
    print(f"[AGENT] Building group rebuild ({mode})")
    print(f"  Cases scanned:              {summary['cases_scanned']}")
    print(f"  Eligible cases:             {summary['eligible']}")
    print(f"  Groups created:             {summary['groups_created']}")
    print(f"  Case links attached:        {summary['attached']}")
    print(f"  Skipped unsupported:        {summary['skipped_unsupported']}")
    print(f"  Skipped closed:             {summary['skipped_closed']}")
    print(f"  Skipped missing building:   {summary['skipped_missing_building']}")
    print(f"  Skipped missing contractor: {summary['skipped_missing_contractor']}")
    return summary


def cmd_show_building_groups(args: argparse.Namespace) -> list[dict]:
    """Print building issue groups with aggregate case counts."""
    import building_groups

    db.init_schema()
    filters = {
        "status": args.status,
        "building": args.building,
        "contractor": args.contractor,
    }
    groups = building_groups.list_building_groups(filters)
    if args.as_json:
        print(json.dumps(groups, indent=2, sort_keys=True))
        return groups

    if not groups:
        print("[AGENT] No building issue groups found.")
        return groups

    header = (
        f"{'Status':<24} {'Open':>4} {'New':>4} {'Reviews':>7} "
        f"{'Building':<32} {'Contractor':<28} Group ID"
    )
    print(header)
    print("-" * len(header))
    for group in groups:
        print(
            f"{group['status']:<24} "
            f"{int(group['open_case_count'] or 0):>4} "
            f"{int(group['new_case_count'] or 0):>4} "
            f"{int(group['review_count'] or 0):>7} "
            f"{(group['building'] or '')[:32]:<32} "
            f"{(group['contractor'] or '')[:28]:<28} "
            f"{group['group_id']}"
        )
    return groups


def cmd_generate_building_draft(args: argparse.Namespace) -> None:
    """Generate or preview review-only consolidated building group drafts."""
    import building_groups
    import communication_planner
    import group_email_builder

    db.init_schema()
    if args.all_ready:
        ready_groups = communication_planner.list_groups_ready_for_draft()
        ready_ids = {group["group_id"] for group in ready_groups}
        created: list[tuple[str, str]] = []
        failed: list[tuple[str, str]] = []
        for group in ready_groups:
            group_id = group["group_id"]
            try:
                if args.dry_run:
                    draft = _build_group_draft_preview(group_id, args.email_type)
                    quality = group_email_builder.validate_draft_quality(draft)
                    created.append((group_id, f"dry-run preview ({quality['passed']})"))
                else:
                    draft_id = group_email_builder.create_group_email_draft(
                        group_id,
                        email_type=args.email_type,
                    )
                    created.append((group_id, draft_id))
            except ValueError as exc:
                failed.append((group_id, str(exc)))

        skipped = []
        for group in building_groups.list_building_groups():
            if group["group_id"] in ready_ids:
                continue
            evaluation = communication_planner.evaluate_group_communication_status(group["group_id"])
            reasons = evaluation["blockers"] + evaluation["suppression_reasons"]
            skipped.append((group["group_id"], reasons or ["not ready"]))

        print("[AGENT] Building draft generation summary")
        print(f"  Ready groups processed: {len(ready_groups)}")
        print(f"  Drafts created:         {0 if args.dry_run else len(created)}")
        print(f"  Dry-run previews:       {len(created) if args.dry_run else 0}")
        print(f"  Failed:                 {len(failed)}")
        print(f"  Skipped:                {len(skipped)}")
        for group_id, draft_ref in created:
            print(f"  CREATED {group_id}: {draft_ref}")
        for group_id, reason in failed:
            print(f"  FAILED  {group_id}: {reason}")
        for group_id, reasons in skipped:
            print(f"  SKIPPED {group_id}: {'; '.join(reasons)}")
        return

    if not args.group_id:
        print("[AGENT] ERROR: Set --group-id GROUP_ID or --all-ready.")
        sys.exit(1)

    evaluation = communication_planner.evaluate_group_communication_status(args.group_id)
    if not evaluation["ready"]:
        reasons = evaluation["blockers"] + evaluation["suppression_reasons"]
        print("[AGENT] Group is not ready for draft generation:")
        for reason in reasons:
            print(f"  - {reason}")
        sys.exit(1)

    if args.dry_run:
        draft = _build_group_draft_preview(args.group_id, args.email_type)
        quality = group_email_builder.validate_draft_quality(draft)
        print("[AGENT] Building draft dry run")
        _print_group_draft_summary(draft, quality)
        print()
        print(draft["body"])
        return

    group_email_id = group_email_builder.create_group_email_draft(
        args.group_id,
        email_type=args.email_type,
    )
    row = db.get_building_group_email(group_email_id)
    quality = json.loads(row["quality_check_json"]) if row and row["quality_check_json"] else {}
    print(f"[AGENT] Building draft created: {group_email_id}")
    _print_group_draft_summary(dict(row), quality)


def _build_group_draft_preview(group_id: str, email_type: str) -> dict[str, Any]:
    """Build a group draft without writing it to the database."""
    import group_email_builder

    draft = group_email_builder.build_consolidated_email(group_id)
    draft["summary_json"].setdefault("email_type", email_type)
    if email_type == "followup":
        draft["subject"] = draft["subject"].replace("Action Required:", "Follow-up:", 1)
        draft["summary_json"]["email_type"] = "followup"
    return draft


def _print_group_draft_summary(draft: dict[str, Any], quality: dict[str, Any]) -> None:
    """Print a compact draft summary for the CLI."""
    print(f"  Subject:       {draft.get('subject', '')}")
    print(f"  Intended To:   {draft.get('intended_to', '')}")
    print(f"  Actual To:     {draft.get('actual_to', '')}")
    print(f"  Quality pass:  {quality.get('passed')}")
    failures = quality.get("failures") or []
    if failures:
        print(f"  Quality notes: {'; '.join(failures)}")


def cmd_discover_connections(args) -> None:
    """Discover possible hidden connections across supported KPI cases using AI.

    Requires an explicit AI budget via --max-ai-calls. Never modifies cases,
    sends emails, schedules follow-ups, escalates, or closes cases. All
    hypotheses are stored as 'proposed' for human review only.

    Args:
        args: Parsed argparse namespace. Must include ``max_ai_calls``.
    """
    max_ai_calls = getattr(args, "max_ai_calls", 0)
    dry_run = bool(getattr(args, "dry_run", False))
    if max_ai_calls is None or max_ai_calls < 0 or (max_ai_calls == 0 and not dry_run):
        print(
            "[AGENT] ERROR: --max-ai-calls is required and must be > 0 "
            "unless --dry-run is set."
        )
        sys.exit(1)

    import connection_discovery

    db.init_schema()
    config.validate()

    report_path = _default_ai_report_path("discover-connections")
    ai_enabled = not (dry_run and max_ai_calls == 0)
    runtime_options.configure(
        RuntimeOptions(
            ai_enabled=ai_enabled,
            max_ai_calls=max_ai_calls,
            ai_budget_mode="fail",
            ai_report_path=report_path,
        )
    )
    gateway = get_ai_gateway()
    gateway.reset()
    gateway.configure(
        AiUsageConfig(
            enabled=ai_enabled,
            max_calls=max_ai_calls,
            budget_mode="fail",
            model_name=config.CLAUDE_MODEL,
            config_version="agent-discover-connections",
        )
    )
    gateway.set_run_metadata(command="discover-connections")

    result = connection_discovery.run_discovery(
        max_ai_calls=max_ai_calls,
        limit=getattr(args, "limit", None),
        building=getattr(args, "building", None),
        case_type_filter=getattr(args, "case_type", None),
        dry_run=dry_run,
        scope=getattr(args, "scope", None),
        packet_by=getattr(args, "packet_by", "entity"),
        batch_size=getattr(args, "batch_size", 25),
        max_prompt_chars=getattr(args, "max_prompt_chars", 40000),
    )

    _finalize_ai_report(report_path)
    _append_command_event(
        "discover_connections_completed",
        command="discover-connections",
        dry_run=dry_run,
        scope=getattr(args, "scope", None) or "small-case",
        cases_analyzed=result.get("cases_analyzed"),
        packets_created=result.get("packets_created"),
        packets_analyzed=result.get("packets_analyzed"),
        ai_calls_used=result.get("ai_calls_used"),
        hypotheses_proposed=result.get("hypotheses_proposed"),
        hypotheses_rejected=result.get("hypotheses_rejected"),
        unsupported_records_included=result.get("unsupported_records_included"),
    )


def cmd_merge_connection_hypotheses(args) -> None:
    """Merge duplicate proposed connection hypotheses without calling AI."""
    max_ai_calls = getattr(args, "max_ai_calls", 0)
    if max_ai_calls is None or max_ai_calls < 0:
        print("[AGENT] ERROR: --max-ai-calls must be a non-negative integer.")
        sys.exit(1)

    import connection_discovery

    db.init_schema()
    report_path = _default_ai_report_path("merge-connection-hypotheses")
    runtime_options.configure(
        RuntimeOptions(
            ai_enabled=False,
            max_ai_calls=max_ai_calls,
            ai_report_path=report_path,
        )
    )
    gateway = get_ai_gateway()
    gateway.reset()
    gateway.configure(
        AiUsageConfig(
            enabled=False,
            max_calls=max_ai_calls,
            budget_mode="fail",
            model_name=config.CLAUDE_MODEL,
            config_version="agent-merge-connection-hypotheses",
        )
    )
    gateway.set_run_metadata(command="merge-connection-hypotheses")

    dry_run = bool(getattr(args, "dry_run", False))
    result = connection_discovery.merge_duplicate_hypotheses(dry_run=dry_run)
    action = "would mark" if dry_run else "marked"
    print(
        f"[DISCOVERY] Merge complete: {result['duplicate_groups']} duplicate group(s), "
        f"{action} {result['hypotheses_marked_merged']} duplicate hypothesis row(s)."
    )

    _finalize_ai_report(report_path)
    _append_command_event(
        "merge_connection_hypotheses_completed",
        command="merge-connection-hypotheses",
        dry_run=dry_run,
        duplicate_groups=result.get("duplicate_groups"),
        hypotheses_marked_merged=result.get("hypotheses_marked_merged"),
        hypotheses_updated=result.get("hypotheses_updated"),
        ai_calls_used=0,
    )


def cmd_observability_report(args: argparse.Namespace) -> None:
    """Print and optionally write a local observability snapshot.

    This command ensures the SQLite schema exists before reading. Aside from
    optional report/schema/log writes, it does not enable AI, poll IMAP, send
    SMTP, or schedule follow-ups.
    """
    from observability import (
        append_structured_event,
        build_metrics_snapshot,
        write_metrics_snapshot,
    )
    import contextlib
    import io

    # Keep stdout machine-readable for callers that pipe the JSON snapshot.
    with contextlib.redirect_stdout(io.StringIO()):
        db.init_schema()
    run_id = str(uuid.uuid4())
    snapshot = build_metrics_snapshot()
    output_path = getattr(args, "output", None)
    if output_path:
        write_metrics_snapshot(output_path, snapshot=snapshot)
        append_structured_event(
            component="agent",
            event_name="observability_report_written",
            status="ok",
            run_id=run_id,
            report_path=output_path,
            command="observability-report",
        )
    print(json.dumps(snapshot, indent=2, sort_keys=True))


def _print_safety_result(label: str, passed: bool, detail: str) -> bool:
    status = "PASS" if passed else "FAIL"
    print(f"[SAFETY] {status}: {label} - {detail}")
    return passed


def cmd_safety_check(args):
    """Run deterministic local safety checks for demo/import tooling."""
    checks: list[bool] = []

    demo_recipient_ok = (not config.DEMO_MODE) or bool(str(config.DEMO_RECIPIENT_EMAIL).strip())
    checks.append(
        _print_safety_result(
            "DEMO_MODE recipient",
            demo_recipient_ok,
            "demo recipient configured" if demo_recipient_ok else "DEMO_MODE=true but recipient is empty",
        )
    )

    ai_blocked = False
    # Intentionally instantiate a fresh, isolated AiGateway() rather than
    # using the module-level singleton (get_ai_gateway()).  This keeps the
    # safety-check probe completely out of the singleton's usage records and
    # telemetry, so it cannot skew budget counters or AI-call logs for any
    # real run that shares the same process.
    gateway = AiGateway()
    try:
        gateway.configure(
            AiUsageConfig(
                enabled=True,
                max_calls=0,
                budget_mode="fail",
                model_name=config.CLAUDE_MODEL,
                config_version="safety-check",
            )
        )
        gateway.call_json(
            prompt="{}",
            purpose="classification",
            prompt_type="safety_check",
            caller="agent.safety_check",
            use_cache=False,
        )
    except (ValueError, AiBudgetExceeded):
        ai_blocked = True
    checks.append(
        _print_safety_result(
            "AI max_calls=0",
            ai_blocked,
            "AI call was blocked" if ai_blocked else "AI call was not blocked",
        )
    )

    import backlog_loader

    backlog_options = backlog_loader.BacklogRunOptions()
    checks.append(
        _print_safety_result(
            "Backlog no outbound",
            backlog_options.outbound_enabled is False,
            f"outbound_enabled={backlog_options.outbound_enabled}",
        )
    )

    unsupported_ok = bool(SUPPORTED_CASE_TYPES_SET) and "UNKNOWN" not in SUPPORTED_CASE_TYPES_SET
    checks.append(
        _print_safety_result(
            "Unsupported exclusion",
            unsupported_ok,
            "UNKNOWN excluded from supported set" if unsupported_ok else "supported set is empty or includes UNKNOWN",
        )
    )

    import connection_discovery

    backlog_source = inspect.getsource(backlog_loader)
    discovery_source = inspect.getsource(connection_discovery)
    no_auto_close_ok = bool(EVENT_CASE_CLOSED) and EVENT_CASE_CLOSED not in backlog_source and EVENT_CASE_CLOSED not in discovery_source
    checks.append(
        _print_safety_result(
            "No auto-closure",
            no_auto_close_ok,
            "backlog/discovery do not reference case_closed"
            if no_auto_close_ok
            else "backlog or discovery references case_closed",
        )
    )

    passed = sum(1 for item in checks if item)
    print(f"[SAFETY] {passed}/5 checks passed")
    if passed != 5:
        sys.exit(1)
    return 0


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
  build-demo-scenario  Create deterministic demo seed data
  reset-demo-db  Safely reset a demo/test/tmp database
  replay       Replay a JSON email sequence through the local pipeline
  safety-check Run deterministic safety checks
  rebuild-building-groups  Rebuild building group links from cases
  show-building-groups  Print building issue groups
  reply        Interactive reply handler
  memory-rebuild  Backfill deterministic memory tables from existing records
  patterns     Print active pattern flags
  memory-report  Print detailed memory context for a case
  observability-report  Print local metrics and safety snapshot
  test-demo-scale  Run the safe offline demo validation harness
  discover-connections  Discover possible connections across supported cases (requires --max-ai-calls)

Examples:
  python src/agent.py ingest
  python src/agent.py demo
  python src/agent.py run
  python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run
  python src/agent.py build-demo-scenario
  python src/agent.py reset-demo-db --database data/demo_agent.db --yes
  python src/agent.py replay --path data/demo_replay.json
  python src/agent.py safety-check
  python src/agent.py rebuild-building-groups --dry-run
  python src/agent.py show-building-groups
  python src/agent.py reply --case-id <CASE_ID>
  python src/agent.py memory-rebuild
  python src/agent.py patterns
  python src/agent.py memory-report --case-id <CASE_ID>
  python src/agent.py observability-report --output data/observability/latest.json
  python src/agent.py test-demo-scale --offline --emails 25 --seed 42
  python src/agent.py discover-connections --max-ai-calls 5
  python src/agent.py discover-connections --max-ai-calls 5 --dry-run
  python src/agent.py discover-connections --scope patterns --max-ai-calls 20
  python src/agent.py discover-connections --scope all-supported --packet-by entity --max-ai-calls 100
  python src/agent.py merge-connection-hypotheses --max-ai-calls 0 --dry-run
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
    backlog_parser.add_argument("--resume", action="store_true", default=False, help="Make duplicate-message-id resume behavior explicit")
    backlog_parser.add_argument("--limit", type=int, default=None, help="Maximum number of records to process")
    backlog_parser.add_argument("--progress-interval", type=int, default=50, help="Print backlog progress every N records")
    backlog_parser.add_argument(
        "--report-detail",
        choices=("summary", "full"),
        default="summary",
        help="Written report detail level: summary or full",
    )
    backlog_parser.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="Directory for report output (default: data/backlog_runs/<timestamp>/)",
    )

    subparsers.add_parser("build-demo-scenario", help="Create deterministic demo seed data")

    reset_parser = subparsers.add_parser("reset-demo-db", help="Safely reset a demo/test/tmp database")
    reset_parser.add_argument("--yes", action="store_true", default=False, help="Confirm database reset")
    reset_parser.add_argument("--database", type=Path, default=None, help="Explicit database path to reset")

    replay_parser = subparsers.add_parser("replay", help="Replay a JSON email sequence through the local pipeline")
    replay_parser.add_argument("--path", required=True, type=Path, help="Path to replay JSON email list")

    rebuild_groups_parser = subparsers.add_parser(
        "rebuild-building-groups",
        help="Rebuild building group links from existing cases",
    )
    rebuild_groups_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview group/link changes without writing them",
    )
    rebuild_groups_parser.add_argument(
        "--include-closed",
        action="store_true",
        default=False,
        help="Include closed cases when rebuilding group links",
    )

    show_groups_parser = subparsers.add_parser(
        "show-building-groups",
        help="Print building issue groups",
    )
    show_groups_parser.add_argument("--status", type=str, default=None, help="Filter by group status")
    show_groups_parser.add_argument("--building", type=str, default=None, help="Filter by building text")
    show_groups_parser.add_argument("--contractor", type=str, default=None, help="Filter by contractor text")
    show_groups_parser.add_argument("--json", action="store_true", dest="as_json", help="Print JSON instead of a table")

    draft_parser = subparsers.add_parser(
        "generate-building-draft",
        help="Generate review-only consolidated building group drafts",
    )
    draft_target = draft_parser.add_mutually_exclusive_group(required=True)
    draft_target.add_argument("--group-id", type=str, default=None, help="Building group ID to draft")
    draft_target.add_argument(
        "--all-ready",
        action="store_true",
        default=False,
        help="Generate drafts for every group ready for communication",
    )
    draft_parser.add_argument(
        "--email-type",
        choices=("initial", "followup"),
        default="initial",
        help="Draft type to generate",
    )
    draft_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview draft content without writing to the database",
    )

    observability_parser = subparsers.add_parser(
        "observability-report",
        help="Print local metrics and safety snapshot",
    )
    observability_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON file path for the metrics snapshot",
    )

    subparsers.add_parser("safety-check", help="Run deterministic safety checks")

    discover_parser = subparsers.add_parser(
        "discover-connections",
        help="Discover possible connections across supported cases (AI required)",
    )
    discover_parser.add_argument(
        "--max-ai-calls",
        type=int,
        default=0,
        metavar="N",
        help="Maximum AI calls allowed for this run (required; may be 0 with --dry-run)",
    )
    discover_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of cases to include in the analysis",
    )
    discover_parser.add_argument(
        "--building",
        type=str,
        default=None,
        metavar="BUILDING",
        help="Filter cases by building name (substring match)",
    )
    discover_parser.add_argument(
        "--case-type",
        type=str,
        default=None,
        metavar="TYPE",
        help="Filter cases by supported case type",
    )
    discover_parser.add_argument(
        "--scope",
        type=str,
        default=None,
        metavar="SCOPE",
        choices=["patterns", "building-groups", "all-supported"],
        help="Discovery scope: patterns | building-groups | all-supported (default: small-case mode)",
    )
    discover_parser.add_argument(
        "--packet-by",
        type=str,
        default="entity",
        metavar="BY",
        choices=["building", "contractor", "device", "case-type", "entity"],
        help="Packetization strategy for all-supported scope (default: entity)",
    )
    discover_parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        metavar="N",
        help="Max cases per packet (default: 25)",
    )
    discover_parser.add_argument(
        "--max-prompt-chars",
        type=int,
        default=40000,
        metavar="N",
        help="Max prompt characters per packet before splitting (default: 40000)",
    )
    discover_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would run without calling AI when --max-ai-calls is 0",
    )

    merge_parser = subparsers.add_parser(
        "merge-connection-hypotheses",
        help="Merge duplicate connection hypotheses deterministically",
    )
    merge_parser.add_argument(
        "--max-ai-calls",
        type=int,
        default=0,
        metavar="N",
        help="Explicit AI call cap for this no-AI command; use 0 for dry-run",
    )
    merge_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show duplicate groups without marking any as merged",
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
    elif args.command == "build-demo-scenario":
        cmd_build_demo_scenario(args)
    elif args.command == "reset-demo-db":
        cmd_reset_demo_db(args)
    elif args.command == "replay":
        cmd_replay(args)
    elif args.command == "rebuild-building-groups":
        cmd_rebuild_building_groups(args)
    elif args.command == "show-building-groups":
        cmd_show_building_groups(args)
    elif args.command == "generate-building-draft":
        cmd_generate_building_draft(args)
    elif args.command == "observability-report":
        cmd_observability_report(args)
    elif args.command == "safety-check":
        cmd_safety_check(args)
    elif args.command == "discover-connections":
        cmd_discover_connections(args)
    elif args.command == "merge-connection-hypotheses":
        cmd_merge_connection_hypotheses(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
