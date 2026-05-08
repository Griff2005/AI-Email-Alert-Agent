"""
followup.py — Background scheduler for follow-up deadline checking.

Runs every FOLLOWUP_CHECK_INTERVAL seconds (default: 300 = 5 minutes).
For each open case with a passed deadline:
  1. Generates a follow-up email draft
  2. Increments follow_count
  3. After 3 follow-ups, flags for manual escalation review
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:  # pragma: no cover - optional in lightweight test environments
    BackgroundScheduler = None  # type: ignore[assignment]

import database as db
import email_sender
import memory
from config import config
from extractor import generate_email_body
from runtime_options import runtime_options

# After this many unanswered follow-ups, the case is flagged for senior manual review.
_ESCALATION_THRESHOLD = 3
_FOLLOWUP_RESCHEDULE_DAYS = 7


def _ensure_manual_review(case_id: str, reason: str) -> None:
    """Insert a manual review item once for a specific case/reason pair."""
    if db.has_open_manual_review(case_id, reason):
        return
    db.insert_manual_review(
        review_id=str(uuid.uuid4()),
        case_id=case_id,
        email_id=None,
        reason=reason,
    )


def _record_memory_event(case_id: str, pattern_flags: list) -> None:
    """Log scheduler-triggered memory updates and surface high-severity patterns."""
    changed_flags = [flag for flag in pattern_flags if flag.get("created") or flag.get("updated")]
    if changed_flags:
        description = "Memory updated after follow-up. Pattern flags: " + ", ".join(
            f"{flag['pattern_type']} ({flag['severity']})" for flag in changed_flags
        )
    else:
        description = "Memory updated after follow-up. No new pattern flags detected."
    db.insert_case_event(
        event_id=str(uuid.uuid4()),
        case_id=case_id,
        event_type="memory_updated",
        description=description,
    )
    for flag in changed_flags:
        if flag["severity"] in ("high", "review"):
            _ensure_manual_review(
                case_id=case_id,
                reason=f"Pattern review: {flag['summary']} (severity: {flag['severity']}).",
            )


def _build_followup_subject(case: dict, follow_count: int) -> str:
    """Build a numbered follow-up subject line for a case.

    Args:
        case: Case dict with at least ``building`` and ``case_type`` keys.
        follow_count: Current follow-up number (1-based).

    Returns:
        String in format: ``'Follow-Up #N: Case Type Title — Building Name'``
    """
    building = case.get("building", "Unknown Building")
    case_type = case.get("case_type", "Compliance Issue").replace("_", " ").title()
    return f"Follow-Up #{follow_count}: {case_type} — {building}"


def check_and_process_followups() -> None:
    """Check for overdue follow-up deadlines and generate reminder emails.

    Called by APScheduler on every ``FOLLOWUP_CHECK_INTERVAL`` tick.
    For each open case whose deadline has passed:
    1. Increment ``follow_count`` in the ``followups`` table.
    2. Log a ``followup_triggered`` case event.
    3. Generate a follow-up email body via Claude (plain-text fallback on error).
    4. Create an email draft via ``email_sender.create_draft``.
    5. If ``follow_count >= _ESCALATION_THRESHOLD``, log ``escalated`` event
       and insert a ``manual_reviews`` record.
    """
    options = runtime_options.get()
    if not options.followups_enabled:
        print("[FOLLOWUP] Follow-up processing disabled for this run.")
        return {
            "disabled": True,
            "valid": True,
            "errors": [],
            "cases_touched": 0,
            "normal_followups_triggered": 0,
            "escalation_followups_triggered": 0,
            "duplicate_followups_skipped": 0,
        }

    now_str = datetime.utcnow().isoformat()
    print(f"[FOLLOWUP] Checking follow-up deadlines at {now_str}")

    overdue = db.get_overdue_followups()
    if not overdue:
        print("[FOLLOWUP] No overdue follow-ups.")
        return {
            "disabled": False,
            "valid": True,
            "errors": [],
            "cases_touched": 0,
            "normal_followups_triggered": 0,
            "escalation_followups_triggered": 0,
            "duplicate_followups_skipped": 0,
        }

    print(f"[FOLLOWUP] Found {len(overdue)} overdue follow-up(s).")
    errors = []
    processed = 0
    escalations = 0
    duplicates = 0

    for row in overdue[: options.max_followup_runs]:
        case_id = row["case_id"]
        case = db.get_case_by_id(case_id)
        if not case:
            continue
        if case["status"] == "closed":
            db.close_followup(case_id)
            continue
        if int(row["follow_count"]) >= options.max_followups:
            _ensure_manual_review(
                case_id=case_id,
                reason=f"Escalated: maximum configured follow-ups ({options.max_followups}) already reached with no resolution.",
            )
            continue

        target_follow_count = int(row["follow_count"]) + 1
        escalation_stage = "escalation" if target_follow_count >= _ESCALATION_THRESHOLD else "standard"
        scheduled_bucket = str(row["deadline"])[:10]
        idempotency_key = "|".join(
            [
                case_id,
                str(target_follow_count),
                escalation_stage,
                "contractor",
                scheduled_bucket,
            ]
        )
        if not db.reserve_followup_action(
            action_id=str(uuid.uuid4()),
            case_id=case_id,
            idempotency_key=idempotency_key,
            followup_level=target_follow_count,
            escalation_stage=escalation_stage,
            recipient_type="contractor",
            scheduled_bucket=scheduled_bucket,
        ):
            duplicates += 1
            print(f"[FOLLOWUP] Skipping duplicate follow-up for case {case_id} ({idempotency_key}).")
            continue

        fields = {f["field_name"]: f["field_value"] for f in db.get_fields_for_case(case_id)}
        case_dict = dict(case)
        for k in ("building", "device", "contractor", "due_date", "period"):
            if case_dict.get(k):
                fields[k] = case_dict[k]
        memory_context = memory.get_memory_context_for_case(case_id)

        try:
            email_body = generate_email_body(
                case_type=case_dict["case_type"],
                fields=fields,
                case_id=case_id,
                memory_context=memory_context,
                purpose="followup_generation",
                followup_count=target_follow_count,
            )
        except Exception as exc:
            print(f"[FOLLOWUP] Failed to generate email body for case {case_id}: {exc}")
            db.mark_followup_action_status(idempotency_key, "failed")
            errors.append(f"Failed to generate follow-up body for case {case_id}: {exc}")
            email_body = (
                f"This is a follow-up regarding an outstanding compliance issue.\n\n"
                f"Case ID: {case_id}\n"
                f"Case Type: {case_dict['case_type']}\n"
                f"Building: {case_dict.get('building', 'N/A')}\n\n"
                f"Please take immediate action to resolve this matter."
            )

        subject = _build_followup_subject(case_dict, target_follow_count)
        intended_to = case_dict.get("contractor", config.DEMO_RECIPIENT_EMAIL) or config.DEMO_RECIPIENT_EMAIL

        try:
            msg_id = email_sender.create_draft(
                case_id=case_id,
                subject=subject,
                body=email_body,
                intended_to=intended_to,
            )
        except Exception as exc:
            db.mark_followup_action_status(idempotency_key, "failed")
            errors.append(f"Failed to create follow-up draft for case {case_id}: {exc}")
            continue

        new_count = db.increment_followup_count(case_id)
        next_deadline = (datetime.utcnow() + timedelta(days=_FOLLOWUP_RESCHEDULE_DAYS)).isoformat()
        db.reschedule_followup(case_id, next_deadline)
        db.mark_followup_action_status(idempotency_key, "draft_created", outbound_msg_id=msg_id)
        processed += 1
        print(f"[FOLLOWUP] Processing case {case_id} — follow count now {new_count}")

        db.insert_case_event(
            event_id=str(uuid.uuid4()),
            case_id=case_id,
            event_type="followup_triggered",
            description=(
                f"Follow-up #{new_count} triggered. Previous deadline was {row['deadline']}. "
                f"Next deadline is {next_deadline}."
            ),
        )

        memory.record_no_response(case_id)
        pattern_flags = memory.detect_patterns_for_case(case_id)
        _record_memory_event(case_id, pattern_flags)

        # Escalate after threshold
        if new_count >= _ESCALATION_THRESHOLD:
            print(f"[FOLLOWUP] Case {case_id} reached escalation threshold ({_ESCALATION_THRESHOLD} follow-ups).")
            escalations += 1
            db.insert_case_event(
                event_id=str(uuid.uuid4()),
                case_id=case_id,
                event_type="escalated",
                description=(
                    f"Case escalated after {new_count} unanswered follow-ups. "
                    "Flagged for manual review."
                ),
            )
            _ensure_manual_review(
                case_id=case_id,
                reason=f"Escalated: {new_count} follow-ups sent with no resolution.",
            )

    return {
        "disabled": False,
        "valid": not errors,
        "errors": errors,
        "cases_touched": processed,
        "normal_followups_triggered": processed - escalations,
        "escalation_followups_triggered": escalations,
        "duplicate_followups_skipped": duplicates,
    }


def start_scheduler() -> BackgroundScheduler:
    """Start and return the APScheduler background follow-up checker.

    Creates a daemon ``BackgroundScheduler`` (exits when the main process stops)
    and registers ``check_and_process_followups`` on an interval of
    ``config.FOLLOWUP_CHECK_INTERVAL`` seconds.

    Returns:
        The running ``BackgroundScheduler`` instance. The caller should hold
        a reference to prevent premature garbage collection.
    """
    if BackgroundScheduler is None:
        raise RuntimeError("APScheduler is not installed. Follow-up scheduler cannot start.")
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        check_and_process_followups,
        trigger="interval",
        seconds=config.FOLLOWUP_CHECK_INTERVAL,
        id="followup_check",
        name="Follow-up deadline checker",
        replace_existing=True,
    )
    scheduler.start()
    print(
        f"[FOLLOWUP] Scheduler started — checking every {config.FOLLOWUP_CHECK_INTERVAL}s."
    )
    return scheduler
