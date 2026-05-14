"""Consolidated building-group draft generation.

This module creates review-only draft records. It does not send mail, schedule
follow-ups, close cases, or call AI.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import communication_planner
import database as db
from config import config
from constants import EMAIL_TYPES, STATUS_CLOSED
from response_requirements import (
    calculate_case_completeness,
    get_required_response_items,
    validate_required_response_in_email,
)
from time_utils import utc_now_iso

_INTERNAL_MARKERS = (
    "ai hypothesis",
    "internal note",
    "internal ai",
    "reasoning:",
    "prompt injection",
    "confidence score",
    "model output",
)
_DEFAULT_INTENDED_TO = "contractor@solucore-production.com"


def build_consolidated_email(group_id: str) -> dict[str, Any]:
    """Build a deterministic consolidated email for all open cases in a group."""
    group = _get_group_or_raise(group_id)
    cases = _open_group_cases(group_id)
    if not cases:
        raise ValueError(f"Building group {group_id} has no open child cases.")

    subject_prefix = "Action Required"
    subject = f"{subject_prefix}: Open KPI Items for {group['building']}"
    intended_to = _resolve_intended_to(group, cases)
    actual_to = config.DEMO_RECIPIENT_EMAIL if config.DEMO_MODE else intended_to
    body = _build_consolidated_body(group, cases)
    summary_json = {
        "group_id": group_id,
        "building": group["building"],
        "contractor": group["contractor"],
        "case_ids": [case["case_id"] for case in cases],
        "case_count": len(cases),
        "case_types": sorted({case["case_type"] for case in cases}),
    }
    return {
        "subject": subject,
        "body": body,
        "intended_to": intended_to,
        "intended_cc": "",
        "actual_to": actual_to,
        "summary_json": summary_json,
    }


def build_clarification_email(case_ids: list[str]) -> dict[str, Any]:
    """Build a clarification draft asking only for missing requirement fields."""
    cases = [_case_to_dict(case_id) for case_id in case_ids]
    cases = [case for case in cases if case is not None]
    if not cases:
        raise ValueError("At least one valid case ID is required for a clarification email.")

    building = cases[0].get("building") or "the building"
    contractor = cases[0].get("contractor") or "the contractor"
    intended_to = _resolve_intended_to({"contractor": contractor}, cases)
    actual_to = config.DEMO_RECIPIENT_EMAIL if config.DEMO_MODE else intended_to
    lines = [
        "Hello,",
        "",
        f"Please provide the missing response details for {building}.",
        f"Contractor: {contractor}",
        "",
    ]
    for index, case in enumerate(cases, 1):
        completeness = calculate_case_completeness(case["case_id"])
        lines.append(f"{index}. Case {case['case_id']}")
        lines.append(f"- Case type: {case['case_type']}")
        lines.append("Missing details:")
        for key in completeness["missing_keys"]:
            item = _requirement_by_key(case["case_type"], key)
            label = item["label"] if item else key.replace("_", " ")
            lines.append(f"- {label}")
        lines.append("")
    lines.extend(["Thank you,", "Solucore"])
    return {
        "subject": f"Clarification Required: Open KPI Items for {building}",
        "body": "\n".join(lines).strip(),
        "intended_to": intended_to,
        "intended_cc": "",
        "actual_to": actual_to,
        "summary_json": {
            "case_ids": [case["case_id"] for case in cases],
            "case_count": len(cases),
            "email_type": "clarification",
        },
    }


def create_group_email_draft(group_id: str, email_type: str = "initial") -> str:
    """Create a review-only consolidated group draft and return its draft ID."""
    if email_type not in EMAIL_TYPES:
        raise ValueError(f"Unsupported group email type: {email_type}")
    evaluation = communication_planner.evaluate_group_communication_status(group_id)
    if not evaluation["ready"]:
        details = evaluation["blockers"] + evaluation["suppression_reasons"]
        raise ValueError("Group is not ready for draft generation: " + "; ".join(details))

    if email_type == "clarification":
        case_ids = [case["case_id"] for case in _open_group_cases(group_id)]
        draft = build_clarification_email(case_ids)
    else:
        draft = build_consolidated_email(group_id)
        if email_type == "followup":
            draft["subject"] = draft["subject"].replace("Action Required:", "Follow-up:", 1)
            draft["summary_json"]["email_type"] = "followup"
    draft["summary_json"].setdefault("email_type", email_type)
    quality_check = validate_draft_quality(draft)

    group_email_id = str(uuid.uuid4())
    db.insert_building_group_email(
        group_email_id=group_email_id,
        group_id=group_id,
        email_type=email_type,
        status="draft_generated",
        subject=draft["subject"],
        body=draft["body"],
        intended_to=draft["intended_to"],
        intended_cc=draft.get("intended_cc", ""),
        actual_to=draft["actual_to"],
        summary_json=json.dumps(draft["summary_json"], sort_keys=True),
        quality_check_json=json.dumps(quality_check, sort_keys=True),
    )
    return group_email_id


def validate_draft_quality(draft: dict[str, Any]) -> dict[str, Any]:
    """Validate consolidated draft safety and completeness, raising on failures."""
    failures: list[str] = []
    subject = str(draft.get("subject") or "").strip()
    body = str(draft.get("body") or "")
    intended_to = str(draft.get("intended_to") or "").strip()
    actual_to = str(draft.get("actual_to") or "").strip()
    summary = draft.get("summary_json") if isinstance(draft.get("summary_json"), dict) else {}
    case_ids = [str(case_id) for case_id in summary.get("case_ids", [])]

    if not subject:
        failures.append("subject is empty")
    if not intended_to:
        failures.append("intended_to is empty")
    if summary.get("building") and str(summary["building"]) not in body:
        failures.append("body does not contain building name")
    if summary.get("contractor") and str(summary["contractor"]) not in body:
        failures.append("body does not contain contractor name")
    if not _contains_required_instruction(body, case_ids):
        failures.append("body does not contain a required response instruction")
    if config.DEMO_MODE and actual_to != config.DEMO_RECIPIENT_EMAIL:
        failures.append("actual_to does not match demo recipient")

    body_lower = body.lower()
    for marker in _INTERNAL_MARKERS:
        if marker in body_lower:
            failures.append(f"body contains internal marker: {marker}")

    result = {"passed": not failures, "failures": failures}
    if failures:
        raise ValueError("Draft quality check failed: " + "; ".join(failures))
    return result


def approve_group_email_draft(group_email_id: str, notes: str | None = None) -> dict[str, Any]:
    """Move a group draft to approved status without sending it."""
    row = db.update_building_group_email_status(group_email_id, "approved", notes=notes)
    if row is None:
        raise ValueError(f"Group email draft {group_email_id} not found.")
    return dict(row)


def reject_group_email_draft(group_email_id: str, notes: str) -> dict[str, Any]:
    """Move a group draft to rejected status without sending it."""
    row = db.update_building_group_email_status(group_email_id, "rejected", notes=notes)
    if row is None:
        raise ValueError(f"Group email draft {group_email_id} not found.")
    return dict(row)


def update_new_since_last_email(group_id: str, group_email_id: str) -> None:
    """Clear new-since-last-email flags for cases included in a group draft."""
    row = db.get_building_group_email(group_email_id)
    if row is None:
        raise ValueError(f"Group email draft {group_email_id} not found.")
    if row["group_id"] != group_id:
        raise ValueError("Group email draft does not belong to the supplied group.")
    summary = _parse_summary_json(row["summary_json"])
    case_ids = [str(case_id) for case_id in summary.get("case_ids", []) if case_id]
    if not case_ids:
        return
    placeholders = ", ".join("?" for _ in case_ids)
    now = utc_now_iso()
    with db._write_lock:
        conn = db.get_connection()
        conn.execute(
            f"""
            UPDATE building_issue_group_cases
            SET new_since_last_email = 0,
                included_in_email_at = ?
            WHERE group_id = ?
              AND case_id IN ({placeholders})
            """,
            (now, group_id, *case_ids),
        )
        conn.execute(
            """
            UPDATE building_issue_groups
            SET last_email_sent_at = ?,
                updated_at = ?
            WHERE group_id = ?
            """,
            (now, now, group_id),
        )
        conn.commit()


def _get_group_or_raise(group_id: str) -> dict[str, Any]:
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM building_issue_groups WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Building group {group_id} not found.")
    return dict(row)


def _open_group_cases(group_id: str) -> list[dict[str, Any]]:
    conn = db.get_connection()
    rows = conn.execute(
        """
        SELECT
            c.*,
            bgc.new_since_last_email,
            bgc.added_at,
            bgc.included_in_email_at
        FROM building_issue_group_cases bgc
        JOIN cases c ON c.case_id = bgc.case_id
        WHERE bgc.group_id = ?
          AND bgc.status = 'active'
          AND c.status != ?
        ORDER BY c.created_at ASC, c.case_id ASC
        """,
        (group_id, STATUS_CLOSED),
    ).fetchall()
    return [dict(row) for row in rows]


def _case_to_dict(case_id: str) -> dict[str, Any] | None:
    row = db.get_case_by_id(case_id)
    return dict(row) if row else None


def _build_consolidated_body(group: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    lines = [
        "Hello,",
        "",
        f"The following KPI items are currently open for {group['building']}.",
        f"Contractor: {group['contractor']}",
        "",
        "Please review each item below and provide the requested information.",
        "",
    ]
    for index, case in enumerate(cases, 1):
        period_or_due = case.get("period") or case.get("due_date") or "N/A"
        device = case.get("device") or "N/A"
        lines.extend(
            [
                f"{index}. Case {case['case_id']}",
                f"- Case type: {case['case_type']}",
                f"- Device: {device}",
                f"- Period/Due date: {period_or_due}",
                f"- Current status: {case['status']}",
                f"- Summary: Open {case['case_type']} item for {group['building']}.",
                "Required response:",
            ]
        )
        for item in get_required_response_items(case["case_type"]):
            lines.append(f"- {item['label']}: {item['description']}")
        lines.append("")
    lines.extend(
        [
            "Please reply with updates for each item. Where applicable, include current status, expected completion date, supporting documentation, and any access, approval, scheduling, or system blockers.",
            "",
            "Thank you,",
            "Solucore",
        ]
    )
    return "\n".join(lines).strip()


def _resolve_intended_to(group: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    contractor = str(group.get("contractor") or "").strip()
    if "@" in contractor:
        return contractor
    for case in cases:
        for value in _contact_values_for_case(case["case_id"]):
            if "@" in value:
                return value
    return _DEFAULT_INTENDED_TO


def _contact_values_for_case(case_id: str) -> list[str]:
    conn = db.get_connection()
    rows = conn.execute(
        """
        SELECT field_value
        FROM extracted_fields
        WHERE case_id = ?
          AND field_name IN (
              'recipient', 'recipient_email', 'contractor_email',
              'contact_email', 'email', 'to_addr'
          )
        ORDER BY rowid ASC
        """,
        (case_id,),
    ).fetchall()
    return [str(row["field_value"]).strip() for row in rows if row["field_value"]]


def _contains_required_instruction(body: str, case_ids: list[str]) -> bool:
    if "required response" not in body.lower():
        return False
    if not case_ids:
        return True
    missing = validate_required_response_in_email(case_ids, body)
    total_keys = {
        item["key"]
        for case_id in case_ids
        for item in get_required_response_items((_case_to_dict(case_id) or {}).get("case_type", ""))
    }
    return len(missing) < len(total_keys)


def _requirement_by_key(case_type: str, key: str) -> dict[str, str] | None:
    for item in get_required_response_items(case_type):
        if item["key"] == key:
            return item
    return None


def _parse_summary_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
