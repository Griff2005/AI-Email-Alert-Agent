"""
case_manager.py — Case CRUD, grouping key logic, and case state machine.

Orchestrates the full pipeline from inbound email to case creation or update:
1. Classify email
2. If known type: extract fields, generate grouping key
3. If case with same key exists: update it; otherwise create new
4. Log case events throughout
5. Schedule follow-up
6. Generate an outbound draft for new cases when outbound generation is enabled
7. Flag for manual review if needed
"""

import uuid
from datetime import timedelta
from typing import Any, Dict, Optional

from constants import (
    CASE_TYPE_CAT1_COMPLIANCE,
    CASE_TYPE_CAT5_COMPLIANCE,
    CASE_TYPE_DATA_ABSENCE,
    CASE_TYPE_GOVERNMENT_DIRECTIVE,
    CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL,
    CASE_TYPE_MAJOR_WORK_OVERDUE,
    CASE_TYPE_UNKNOWN,
    EVENT_ACTION_INDICATED,
    EVENT_CASE_CREATED,
    EVENT_EMAIL_RECEIVED,
    EVENT_FLAGGED_FOR_REVIEW,
    EVENT_MEMORY_UPDATED,
    EVENT_REPLY_RECEIVED,
    REVIEW_REASON_PROMPT_INJECTION,
    REVIEW_REASON_REPLY_POSSIBLE_RESOLUTION,
)
import database as db
import email_sender
import memory
from classifier import classify_email, quick_filter
from extractor import extract_fields_with_meta, generate_grouping_key, generate_email_body
from reply_analyzer import analyze_reply
from runtime_options import runtime_options
from time_utils import utc_now_iso, utc_now_naive

# ---------------------------------------------------------------------------
# Priority mapping by case type
# ---------------------------------------------------------------------------
_CASE_TYPE_PRIORITY = {
    CASE_TYPE_CAT1_COMPLIANCE: "high",
    CASE_TYPE_CAT5_COMPLIANCE: "high",
    CASE_TYPE_DATA_ABSENCE: "medium",
    CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL: "medium",
    CASE_TYPE_MAJOR_WORK_OVERDUE: "high",
    CASE_TYPE_GOVERNMENT_DIRECTIVE: "critical",
    CASE_TYPE_UNKNOWN: "low",
}

_CASE_TYPE_SUBJECT = {
    CASE_TYPE_CAT1_COMPLIANCE: "Action Required: CAT1 Annual Test Compliance",
    CASE_TYPE_CAT5_COMPLIANCE: "Action Required: CAT5 Five-Year Test Compliance",
    CASE_TYPE_DATA_ABSENCE: "Attention Required: Maintenance Data Gap Identified",
    CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL: "Action Required: Maintenance Hours Shortfall",
    CASE_TYPE_MAJOR_WORK_OVERDUE: "Urgent: Major Scheduled Work Overdue",
    CASE_TYPE_GOVERNMENT_DIRECTIVE: "Urgent: Outstanding Government Directive",
}

_ACTIONABLE_PATTERN_REVIEW_TYPES = {
    "repeated_no_response",
    "repeated_maintenance_shortfall",
}


def _case_subject(case_type: str, fields: dict) -> str:
    base = _CASE_TYPE_SUBJECT.get(case_type, f"Alert: {case_type}")
    building = fields.get("building")
    if building:
        return f"{base} — {building}"
    return base

# Default follow-up deadline: 5 business days from case creation (approx 7 calendar days)
_DEFAULT_FOLLOWUP_DAYS = 7


def _new_id() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _ensure_manual_review(case_id: str, reason: str, email_id: Optional[str] = None) -> None:
    """Insert a manual review item only when the same reason is not already open."""
    if db.has_open_manual_review(case_id, reason):
        return
    db.insert_manual_review(
        review_id=_new_id(),
        case_id=case_id,
        email_id=email_id,
        reason=reason,
    )


def _record_memory_event(
    case_id: str,
    pattern_flags: list,
    source_email_id: Optional[str] = None,
) -> None:
    """Log a case event summarizing the latest memory update."""
    changed_flags = [flag for flag in pattern_flags if flag.get("created") or flag.get("updated")]
    if changed_flags:
        details = ", ".join(
            f"{flag['pattern_type']} ({flag['severity']})" for flag in changed_flags
        )
        description = f"Memory updated. Pattern flags detected or refreshed: {details}."
    else:
        description = "Memory updated. No new pattern flags detected."

    db.insert_case_event(
        event_id=_new_id(),
        case_id=case_id,
        event_type=EVENT_MEMORY_UPDATED,
        description=description,
        source_email_id=source_email_id,
    )

    for flag in changed_flags:
        should_create_review = flag["severity"] == "review" or (
            flag["severity"] == "high"
            and flag["pattern_type"] in _ACTIONABLE_PATTERN_REVIEW_TYPES
        )
        if not should_create_review:
            continue
        reason = (
            f"Pattern review: {flag['summary']} "
            f"(severity: {flag['severity']})."
        )
        _ensure_manual_review(case_id=case_id, reason=reason, email_id=source_email_id)


def _record_inbound_memory(case_id: str, email_id: str, case_type: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Record inbound-email memory observations and return the current memory context."""
    memory.record_case_observations(
        case_id=case_id,
        email_id=email_id,
        case_type=case_type,
        fields=fields,
        source="inbound_email",
    )
    pattern_flags = memory.detect_patterns_for_case(case_id)
    _record_memory_event(case_id=case_id, pattern_flags=pattern_flags, source_email_id=email_id)
    return memory.get_memory_context_for_case(case_id)


def process_email(
    email_id: str,
    subject: str,
    body: str,
    from_addr: str = "",
    received_at: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Run the full case pipeline for a single inbound KPI alert email.

    Seven-step pipeline:
    1. ``quick_filter`` — skip obvious noise emails.
    2. ``classify_email`` — deterministic classification first, AI only if enabled.
    3. Route ambiguous / UNKNOWN emails straight to manual review.
    4. ``extract_fields_with_meta`` — deterministic extraction first, AI only if enabled.
    5. ``generate_grouping_key`` — deterministic deduplication key.
    6. Create new case or update the existing one with the same key.
    7. Generate template outbound follow-up text for new cases only.

    Args:
        email_id: UUID of the email already stored in ``emails`` table.
        subject: Email subject line.
        body: Raw email body text.
        from_addr: Sender address (informational, not used for routing).
        received_at: ISO 8601 receipt timestamp. Defaults to current UTC.
        verbose: If True, print progress messages to stdout.

    Returns:
        Dict with keys:

        - ``action`` (str): ``'created'``, ``'updated'``, ``'skipped'``, ``'review_flagged'``.
        - ``case_id`` (str | None): UUID of the affected case.
        - ``case_type`` (str): Classified case type.
        - ``grouping_key`` (str | None): Generated key.
        - ``injection_detected`` (bool): True if injection patterns were found.

    Raises:
        subprocess.TimeoutExpired: Propagated from Claude CLI calls if they
            exceed 90 seconds.
        RuntimeError: Propagated from Claude CLI calls on non-zero exit.
        FileNotFoundError: If the ``claude`` binary is not on PATH.
    """
    if received_at is None:
        received_at = utc_now_iso()

    if verbose:
        print(f"[CASE_MGR] Processing email {email_id}: '{subject}'")

    # Step 1: Quick filter — skip obvious non-KPI noise
    if not quick_filter(subject):
        if verbose:
            print(f"[CASE_MGR] Subject matched an obvious non-KPI noise pattern — skipping.")
        db.mark_email_processed(email_id)
        return {"action": "skipped", "case_id": None, "case_type": CASE_TYPE_UNKNOWN,
                "grouping_key": None, "injection_detected": False}

    # Step 2: Classify with deterministic rules first, AI only if enabled
    classification = classify_email(subject, body)
    case_type = classification["case_type"]
    confidence = classification["confidence"]
    injection_detected = classification["injection_detected"]
    classification_source = classification.get("source", "manual_review")

    if verbose:
        print(
            f"[CASE_MGR] Classified as {case_type} "
            f"(confidence={confidence:.2f}, source={classification_source})"
        )

    if injection_detected:
        if verbose:
            print(f"[CASE_MGR] WARNING: Possible prompt injection detected in email {email_id}")

    # Step 3: If UNKNOWN or low confidence, skip normal case creation and flag for review
    if case_type == CASE_TYPE_UNKNOWN or confidence < 0.4 or classification_source == "manual_review":
        if verbose:
            print(f"[CASE_MGR] Classification was ambiguous — flagging for manual review.")
        case_id = _create_review_case(
            email_id=email_id,
            case_type=case_type,
            reason=classification.get("reason", f"Low classification confidence ({confidence:.2f})."),
            building=None,
            device=None,
            contractor=None,
        )
        db.mark_email_processed(email_id)
        return {
            "action": "review_flagged",
            "case_id": case_id,
            "case_type": case_type,
            "grouping_key": None,
            "injection_detected": injection_detected,
        }

    # Step 4: Extract fields deterministically first, AI only if required and enabled
    fields, extraction_meta = extract_fields_with_meta(
        subject=subject,
        body=body,
        case_type=case_type,
        email_id=email_id,
    )
    if verbose:
        print(
            f"[CASE_MGR] Extracted fields from {extraction_meta['source']}: "
            f"{', '.join(k for k, v in fields.items() if v)}"
        )

    if extraction_meta["source"] == "manual_review":
        if verbose:
            print("[CASE_MGR] Required extraction fields were unresolved — flagging for manual review.")
        case_id = _create_review_case(
            email_id=email_id,
            case_type=case_type,
            reason=extraction_meta["reason"],
            building=fields.get("building"),
            device=fields.get("device"),
            contractor=fields.get("contractor"),
            due_date=fields.get("due_date"),
            period=fields.get("period"),
            extracted_fields=fields,
        )
        if injection_detected:
            _ensure_manual_review(
                case_id=case_id,
                email_id=email_id,
                reason=REVIEW_REASON_PROMPT_INJECTION,
            )
        db.mark_email_processed(email_id)
        return {
            "action": "review_flagged",
            "case_id": case_id,
            "case_type": case_type,
            "grouping_key": None,
            "injection_detected": injection_detected,
        }

    # Step 5: Generate grouping key
    grouping_key = generate_grouping_key(
        case_type=case_type,
        building=fields.get("building"),
        device=fields.get("device"),
        period=fields.get("period"),
    )

    if verbose:
        print(f"[CASE_MGR] Grouping key: {grouping_key}")

    # Step 6: Check for existing case with same grouping key
    existing_case = db.get_case_by_grouping_key(grouping_key)

    if existing_case:
        case_id = existing_case["case_id"]
        if verbose:
            print(f"[CASE_MGR] Existing case found: {case_id} — updating.")
        _update_existing_case(case_id, email_id, case_type, fields, subject)
        action = "updated"
    else:
        case_id = _new_id()
        if verbose:
            print(f"[CASE_MGR] Creating new case: {case_id}")
        _create_new_case(case_id, case_type, grouping_key, email_id, fields, received_at, extraction_meta["source"])
        action = "created"

    # Step 7: Flag for injection review if needed
    if injection_detected:
        _ensure_manual_review(
            case_id=case_id,
            email_id=email_id,
            reason=REVIEW_REASON_PROMPT_INJECTION,
        )

    db.mark_email_processed(email_id)

    return {
        "action": action,
        "case_id": case_id,
        "case_type": case_type,
        "grouping_key": grouping_key,
        "injection_detected": injection_detected,
    }


def _create_new_case(
    case_id: str,
    case_type: str,
    grouping_key: str,
    email_id: str,
    fields: Dict[str, Any],
    received_at: str,
    extraction_source: str,
) -> None:
    """Create a new case record and optional initial outbound draft.

    Five actions in sequence:
    1. Insert the case row with priority from ``_CASE_TYPE_PRIORITY``.
    2. Store each extracted field as a separate ``extracted_fields`` row.
    3. Log a ``case_created`` event in the audit trail.
    4. Schedule a follow-up deadline 7 days out.
    5. Generate a deterministic outbound draft through demo safety.

    Args:
        case_id: Pre-generated UUID.
        case_type: Classified case type.
        grouping_key: Normalised deduplication key.
        email_id: UUID of the triggering email.
        fields: Extracted fields dict from ``extract_fields``.
        received_at: ISO 8601 timestamp for audit events.

    Raises:
        subprocess.TimeoutExpired: Propagated from ``generate_email_body``
            if the Claude CLI call exceeds 90 seconds.
        RuntimeError: Propagated from ``generate_email_body`` on non-zero
            CLI exit.
        FileNotFoundError: If the ``claude`` binary is not on PATH.
    """
    priority = _CASE_TYPE_PRIORITY.get(case_type, "medium")

    db.insert_case(
        case_id=case_id,
        case_type=case_type,
        grouping_key=grouping_key,
        building=fields.get("building"),
        device=fields.get("device"),
        contractor=fields.get("contractor"),
        due_date=fields.get("due_date"),
        period=fields.get("period"),
        priority=priority,
    )

    # Store extracted fields
    for field_name, field_value in fields.items():
        if field_value is not None:
            db.insert_extracted_field(
                field_id=_new_id(),
                case_id=case_id,
                email_id=email_id,
                field_name=field_name,
                field_value=field_value,
                confidence_score=0.95 if extraction_source == "deterministic" else 0.8,
            )

    # Log creation event
    db.insert_case_event(
        event_id=_new_id(),
        case_id=case_id,
        event_type=EVENT_CASE_CREATED,
        description=f"Case created from email. Type: {case_type}. Building: {fields.get('building', 'N/A')}.",
        source_email_id=email_id,
    )

    if runtime_options.get().followups_enabled:
        deadline = (utc_now_naive() + timedelta(days=_DEFAULT_FOLLOWUP_DAYS)).isoformat()
        db.upsert_followup(
            followup_id=_new_id(),
            case_id=case_id,
            deadline=deadline,
        )

    memory_context = _record_inbound_memory(
        case_id=case_id,
        email_id=email_id,
        case_type=case_type,
        fields=fields,
    )

    if not runtime_options.get().disable_outbound_generation:
        email_body = generate_email_body(case_type, fields, case_id, memory_context=memory_context)
        if email_body:
            subject = _case_subject(case_type, fields)
            email_sender.create_draft(
                case_id=case_id,
                subject=subject,
                body=email_body,
                intended_to="contractor@solucore-production.com",
            )


def _update_existing_case(
    case_id: str,
    email_id: str,
    case_type: str,
    fields: Dict[str, Any],
    subject: str,
) -> None:
    """Update an existing case with data from a subsequent alert email.

    Appends an ``email_received`` event and refreshes case fields where the
    new email provides non-null values. Does not send a new outbound email —
    the case is already in progress.

    Args:
        case_id: UUID of the existing case.
        email_id: UUID of the new alert email.
        fields: Extracted fields from the new email.
        subject: Subject line used in the event description.
    """
    db.insert_case_event(
        event_id=_new_id(),
        case_id=case_id,
        event_type=EVENT_EMAIL_RECEIVED,
        description=f"Additional alert received: '{subject}'. Case already open.",
        source_email_id=email_id,
    )

    # Update case fields that may have changed
    updates: Dict[str, Any] = {}
    for field in ("building", "device", "contractor", "due_date", "period"):
        val = fields.get(field)
        if val:
            updates[field] = val
    if updates:
        db.update_case(case_id, updates)

    _record_inbound_memory(
        case_id=case_id,
        email_id=email_id,
        case_type=case_type,
        fields=fields,
    )


def process_reply(
    case_id: str,
    reply_text: str,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Analyse a manually submitted reply and update the case event log.

    Analyses replies with deterministic rules first and the centralized AI
    gateway only if the reply is ambiguous and AI is enabled. Appends a
    ``reply_received`` event.
    If the reply suggests resolution, also inserts a ``manual_reviews`` record.

    Cases are NEVER auto-closed by this function. Only explicit human action
    (CLI confirmation or web UI) can change ``status`` to ``'closed'``.

    Args:
        case_id: UUID of the case the reply relates to.
        reply_text: Raw pasted reply content.
        verbose: If True, print analysis results to stdout.

    Returns:
        Dict with keys:

        - ``analysis`` (dict): Full parsed JSON from Claude's assessment.
        - ``satisfies_action`` (bool): Whether reply indicates resolution.
        - ``flagged_for_review`` (bool): Whether a manual review was created.
        - ``event_id`` (str): UUID of the ``reply_received`` event.

    Raises:
        ValueError: If ``case_id`` does not exist in the database.
    """
    case = db.get_case_by_id(case_id)
    if not case:
        raise ValueError(f"Case {case_id} not found.")

    result = analyze_reply(dict(case), reply_text, case_id=case_id)

    satisfies_action = bool(result.get("satisfies_action", False))
    flag_for_review = bool(result.get("flag_for_review", False))
    summary = str(result.get("summary", "Reply received."))
    action_described = result.get("action_described")

    description = f"Reply analyzed. {summary}"
    if action_described:
        description += f" Action committed: {action_described}"

    event_id = _new_id()
    db.insert_case_event(
        event_id=event_id,
        case_id=case_id,
        event_type=EVENT_REPLY_RECEIVED,
        description=description,
    )

    if satisfies_action:
        db.insert_case_event(
            event_id=_new_id(),
            case_id=case_id,
            event_type=EVENT_ACTION_INDICATED,
            description="Reply suggests corrective action may have been taken. Manual review required before closing.",
        )
        _ensure_manual_review(
            case_id=case_id,
            email_id=None,
            reason=REVIEW_REASON_REPLY_POSSIBLE_RESOLUTION,
        )
        flag_for_review = True

    elif flag_for_review:
        _ensure_manual_review(
            case_id=case_id,
            email_id=None,
            reason=f"Reply flagged for manual review. Summary: {summary}",
        )

    memory.record_reply_observations(case_id=case_id, reply_text=reply_text, analysis=result)
    pattern_flags = memory.detect_patterns_for_case(case_id)
    _record_memory_event(case_id=case_id, pattern_flags=pattern_flags, source_email_id=None)

    if verbose:
        print(f"[CASE_MGR] Reply analysis: satisfies_action={satisfies_action}, flag_for_review={flag_for_review}")
        print(f"[CASE_MGR] Summary: {summary}")

    # Cases must never be closed automatically — only explicit human confirmation
    # (CLI prompt or web UI button) can set status to 'closed'.
    return {
        "analysis": result,
        "satisfies_action": satisfies_action,
        "flagged_for_review": flag_for_review,
        "event_id": event_id,
    }


def _create_review_case(
    email_id: str,
    case_type: str,
    reason: str,
    building: Optional[str],
    device: Optional[str],
    contractor: Optional[str],
    due_date: Optional[str] = None,
    period: Optional[str] = None,
    extracted_fields: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a placeholder case for ambiguous classification or extraction."""
    review_case_type = case_type if case_type != CASE_TYPE_UNKNOWN else CASE_TYPE_UNKNOWN
    grouping_key = f"review|{review_case_type.lower()}|{email_id}"
    existing_case = db.get_case_by_grouping_key(grouping_key)
    if existing_case:
        case_id = existing_case["case_id"]
        _ensure_manual_review(case_id=case_id, email_id=email_id, reason=reason)
        return case_id

    case_id = _new_id()
    db.insert_case(
        case_id=case_id,
        case_type=review_case_type,
        grouping_key=grouping_key,
        building=building,
        device=device,
        contractor=contractor,
        due_date=due_date,
        period=period,
        priority=_CASE_TYPE_PRIORITY.get(review_case_type, "low"),
    )
    db.insert_case_event(
        event_id=_new_id(),
        case_id=case_id,
        event_type=EVENT_FLAGGED_FOR_REVIEW,
        description=f"Case flagged for manual review. Reason: {reason}",
        source_email_id=email_id,
    )
    if extracted_fields:
        for field_name, field_value in extracted_fields.items():
            if field_value is None:
                continue
            db.insert_extracted_field(
                field_id=_new_id(),
                case_id=case_id,
                email_id=email_id,
                field_name=field_name,
                field_value=field_value,
                confidence_score=0.6,
            )
    db.insert_manual_review(
        review_id=_new_id(),
        case_id=case_id,
        email_id=email_id,
        reason=reason,
    )
    return case_id


def get_case_summary(case_id: str) -> Dict[str, Any]:
    """Return a combined summary dict for a case including all related records.

    Assembles case row, event log, outbound messages, extracted fields, and
    follow-up status from four queries. Used by the web UI case detail route.

    Args:
        case_id: UUID of the case.

    Returns:
        Dict with keys ``'case'``, ``'events'``, ``'messages'``, ``'fields'``,
        ``'followup'``. Returns empty dict if the case is not found.
    """
    case = db.get_case_by_id(case_id)
    if not case:
        return {}

    events = db.get_events_for_case(case_id)
    messages = db.get_messages_for_case(case_id)
    fields = db.get_fields_for_case(case_id)
    followup = db.get_followup_for_case(case_id)

    return {
        "case": dict(case),
        "events": [dict(e) for e in events],
        "messages": [dict(m) for m in messages],
        "fields": [dict(f) for f in fields],
        "followup": dict(followup) if followup else None,
    }
