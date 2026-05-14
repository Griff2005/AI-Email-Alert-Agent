"""Readiness checks for manual building-group communication drafts."""

from __future__ import annotations

import database as db
from constants import QUEUE_TYPES, STATUS_CLOSED

_BLOCKING_DRAFT_STATUSES = ("draft_generated", "needs_review", "approved", "revised")
_DEFAULT_QUEUE_TYPE = "initial_outreach"


def evaluate_group_communication_status(group_id: str) -> dict:
    """Return readiness, blockers, suppression reasons, and next action for a group."""
    conn = db.get_connection()
    group = conn.execute(
        "SELECT * FROM building_issue_groups WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    blockers: list[str] = []
    suppression_reasons: list[str] = []
    if not group:
        blockers.append("building group not found")
        return {
            "ready": False,
            "blockers": blockers,
            "suppression_reasons": suppression_reasons,
            "recommended_action": "blocked",
        }

    if not group["building"]:
        suppression_reasons.append("missing building")
    if not group["contractor"]:
        suppression_reasons.append("missing contractor")
    if group["next_email_allowed_at"]:
        suppression_reasons.append("cooldown configured")

    open_cases = _open_child_cases(group_id)
    if not open_cases:
        blockers.append("no open child cases")

    blocking_reviews = _blocking_review_count(group_id)
    if blocking_reviews:
        blockers.append(f"{blocking_reviews} blocking open manual review(s)")

    pending_draft = _latest_pending_or_approved_draft(group_id)
    if pending_draft:
        blockers.append(
            f"existing {pending_draft['status']} group draft {pending_draft['group_email_id']}"
        )

    ready = not blockers and not suppression_reasons
    return {
        "ready": ready,
        "blockers": blockers,
        "suppression_reasons": suppression_reasons,
        "recommended_action": "generate_draft" if ready else "blocked",
    }


def list_groups_ready_for_draft() -> list[dict]:
    """Return building groups currently eligible for manual draft generation."""
    conn = db.get_connection()
    rows = conn.execute(
        """
        SELECT DISTINCT big.*
        FROM building_issue_groups big
        JOIN building_issue_group_cases bgc ON bgc.group_id = big.group_id
        JOIN cases c ON c.case_id = bgc.case_id
        WHERE bgc.status = 'active'
          AND c.status != ?
        ORDER BY big.updated_at DESC, big.created_at DESC
        """,
        (STATUS_CLOSED,),
    ).fetchall()
    ready_groups: list[dict] = []
    for row in rows:
        group = dict(row)
        evaluation = evaluate_group_communication_status(group["group_id"])
        if not evaluation["ready"]:
            continue
        group["communication_status"] = evaluation
        ready_groups.append(group)
    return ready_groups


def suppress_group_communication(group_id: str, reason: str) -> None:
    """Insert or update a suppressed communication queue row for a group."""
    queue_type = _DEFAULT_QUEUE_TYPE if _DEFAULT_QUEUE_TYPE in QUEUE_TYPES else QUEUE_TYPES[0]
    db.upsert_communication_queue_item(
        group_id=group_id,
        queue_type=queue_type,
        status="suppressed",
        reason=reason,
        suppression_reason=reason,
    )


def mark_case_new_since_last_email(case_id: str, group_id: str) -> None:
    """Mark one group-child case as new since the last group email."""
    db._execute_write(
        """
        UPDATE building_issue_group_cases
        SET new_since_last_email = 1
        WHERE group_id = ? AND case_id = ?
        """,
        (group_id, case_id),
    )


def _open_child_cases(group_id: str) -> list[dict]:
    conn = db.get_connection()
    rows = conn.execute(
        """
        SELECT c.*
        FROM building_issue_group_cases bgc
        JOIN cases c ON c.case_id = bgc.case_id
        WHERE bgc.group_id = ?
          AND bgc.status = 'active'
          AND c.status != ?
        ORDER BY c.created_at ASC
        """,
        (group_id, STATUS_CLOSED),
    ).fetchall()
    return [dict(row) for row in rows]


def _blocking_review_count(group_id: str) -> int:
    conn = db.get_connection()
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT mr.review_id) AS count
        FROM building_issue_group_cases bgc
        JOIN cases c ON c.case_id = bgc.case_id
        JOIN manual_reviews mr ON mr.case_id = c.case_id
        WHERE bgc.group_id = ?
          AND bgc.status = 'active'
          AND c.status != ?
          AND mr.resolved = 0
        """,
        (group_id, STATUS_CLOSED),
    ).fetchone()
    return int(row["count"] or 0)


def _latest_pending_or_approved_draft(group_id: str) -> dict | None:
    conn = db.get_connection()
    placeholders = ", ".join("?" for _ in _BLOCKING_DRAFT_STATUSES)
    row = conn.execute(
        f"""
        SELECT *
        FROM building_group_emails
        WHERE group_id = ?
          AND status IN ({placeholders})
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """,
        (group_id, *_BLOCKING_DRAFT_STATUSES),
    ).fetchone()
    return dict(row) if row else None
