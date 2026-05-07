"""
followup.py — Background scheduler for follow-up deadline checking.

Runs every FOLLOWUP_CHECK_INTERVAL seconds (default: 300 = 5 minutes).
For each open case with a passed deadline:
  1. Generates a follow-up email draft
  2. Increments follow_count
  3. After 3 follow-ups, flags for manual escalation review
"""

import uuid
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

import database as db
import email_sender
from config import config
from extractor import generate_email_body

# After this many unanswered follow-ups, the case is flagged for senior manual review.
_ESCALATION_THRESHOLD = 3


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
    now_str = datetime.utcnow().isoformat()
    print(f"[FOLLOWUP] Checking follow-up deadlines at {now_str}")

    overdue = db.get_overdue_followups()
    if not overdue:
        print("[FOLLOWUP] No overdue follow-ups.")
        return

    print(f"[FOLLOWUP] Found {len(overdue)} overdue follow-up(s).")

    for row in overdue:
        case_id = row["case_id"]
        case = db.get_case_by_id(case_id)
        if not case:
            continue
        if case["status"] == "closed":
            db.close_followup(case_id)
            continue

        # Increment count
        new_count = db.increment_followup_count(case_id)
        print(f"[FOLLOWUP] Processing case {case_id} — follow count now {new_count}")

        # Log follow-up event
        db.insert_case_event(
            event_id=str(uuid.uuid4()),
            case_id=case_id,
            event_type="followup_triggered",
            description=f"Follow-up #{new_count} triggered. Deadline was {row['deadline']}.",
        )

        # Generate follow-up email body
        fields = {f["field_name"]: f["field_value"] for f in db.get_fields_for_case(case_id)}
        case_dict = dict(case)
        for k in ("building", "device", "contractor", "due_date", "period"):
            if case_dict.get(k):
                fields[k] = case_dict[k]

        try:
            email_body = generate_email_body(
                case_type=case_dict["case_type"],
                fields=fields,
                case_id=case_id,
            )
        except Exception as exc:
            print(f"[FOLLOWUP] Failed to generate email body for case {case_id}: {exc}")
            email_body = (
                f"This is a follow-up regarding an outstanding compliance issue.\n\n"
                f"Case ID: {case_id}\n"
                f"Case Type: {case_dict['case_type']}\n"
                f"Building: {case_dict.get('building', 'N/A')}\n\n"
                f"Please take immediate action to resolve this matter."
            )

        subject = _build_followup_subject(case_dict, new_count)
        intended_to = case_dict.get("contractor", config.DEMO_RECIPIENT_EMAIL) or config.DEMO_RECIPIENT_EMAIL

        email_sender.create_draft(
            case_id=case_id,
            subject=subject,
            body=email_body,
            intended_to=intended_to,
        )

        # Escalate after threshold
        if new_count >= _ESCALATION_THRESHOLD:
            print(f"[FOLLOWUP] Case {case_id} reached escalation threshold ({_ESCALATION_THRESHOLD} follow-ups).")
            db.insert_case_event(
                event_id=str(uuid.uuid4()),
                case_id=case_id,
                event_type="escalated",
                description=(
                    f"Case escalated after {new_count} unanswered follow-ups. "
                    "Flagged for manual review."
                ),
            )
            db.insert_manual_review(
                review_id=str(uuid.uuid4()),
                case_id=case_id,
                email_id=None,
                reason=f"Escalated: {new_count} follow-ups sent with no resolution.",
            )


def start_scheduler() -> BackgroundScheduler:
    """Start and return the APScheduler background follow-up checker.

    Creates a daemon ``BackgroundScheduler`` (exits when the main process stops)
    and registers ``check_and_process_followups`` on an interval of
    ``config.FOLLOWUP_CHECK_INTERVAL`` seconds.

    Returns:
        The running ``BackgroundScheduler`` instance. The caller should hold
        a reference to prevent premature garbage collection.
    """
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
