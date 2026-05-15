"""Case-type response requirement checklists for consolidated drafts."""

from __future__ import annotations

import re
import uuid
from typing import Any

import database as db
from constants import (
    CASE_TYPE_CAT1_COMPLIANCE,
    CASE_TYPE_CAT5_COMPLIANCE,
    CASE_TYPE_DATA_ABSENCE,
    CASE_TYPE_GOVERNMENT_DIRECTIVE,
    CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL,
    CASE_TYPE_MAJOR_WORK_OVERDUE,
)
from time_utils import utc_now_iso

_SPACE_RE = re.compile(r"\s+")

_REQUIREMENTS: dict[str, list[dict[str, str]]] = {
    CASE_TYPE_DATA_ABSENCE: [
        {
            "key": "upload_confirmation",
            "label": "Upload confirmation",
            "description": "Confirm whether maintenance data has been uploaded.",
        },
        {
            "key": "maintenance_activity_date",
            "label": "Latest maintenance activity date",
            "description": "Provide the latest maintenance activity date.",
        },
        {
            "key": "data_delay_reason",
            "label": "Reason data was missing or delayed",
            "description": "Explain why the data was missing or delayed.",
        },
        {
            "key": "system_access_blocker",
            "label": "System or access blocker",
            "description": "Identify any system or access issue preventing upload.",
        },
        {
            "key": "correction_date",
            "label": "Expected correction date",
            "description": "Provide the expected date of correction if not yet resolved.",
        },
    ],
    CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL: [
        {
            "key": "completed_hours",
            "label": "Completed maintenance hours",
            "description": "Provide the maintenance hours completed for the period.",
        },
        {
            "key": "missing_time_records",
            "label": "Missing time records",
            "description": "Identify any missing time records.",
        },
        {
            "key": "shortfall_reason",
            "label": "Reason for shortfall",
            "description": "Explain the reason for the maintenance-hour shortfall.",
        },
        {
            "key": "corrective_plan",
            "label": "Corrective plan",
            "description": "Provide the corrective plan.",
        },
        {
            "key": "expected_completion_date",
            "label": "Expected completion date",
            "description": "Provide the expected completion date.",
        },
    ],
    CASE_TYPE_MAJOR_WORK_OVERDUE: [
        {
            "key": "current_work_status",
            "label": "Current work status",
            "description": "Provide the current work status.",
        },
        {
            "key": "overdue_reason",
            "label": "Reason work is overdue",
            "description": "Explain why the work is overdue.",
        },
        {
            "key": "revised_completion_date",
            "label": "Revised expected completion date",
            "description": "Provide the revised expected completion date.",
        },
        {
            "key": "blockers",
            "label": "Access, parts, approval, or scheduling blockers",
            "description": "Identify any access, parts, approval, or scheduling blockers.",
        },
        {
            "key": "supporting_documentation",
            "label": "Supporting documentation",
            "description": "Provide supporting documentation if available.",
        },
    ],
    CASE_TYPE_CAT1_COMPLIANCE: [
        {
            "key": "test_status",
            "label": "Test status",
            "description": "Provide the test status.",
        },
        {
            "key": "scheduled_or_completed_date",
            "label": "Scheduled or completed date",
            "description": "Provide the scheduled or completed date.",
        },
        {
            "key": "contractor_confirmation",
            "label": "Contractor confirmation",
            "description": "Provide contractor confirmation.",
        },
        {
            "key": "documentation",
            "label": "Supporting documentation",
            "description": "Provide supporting documentation if completed.",
        },
        {
            "key": "reason_if_delayed",
            "label": "Reason if delayed",
            "description": "Explain why the test cannot be completed by the due date, if applicable.",
        },
    ],
    CASE_TYPE_CAT5_COMPLIANCE: [
        {
            "key": "test_status",
            "label": "Test status",
            "description": "Provide the test status.",
        },
        {
            "key": "scheduled_or_completed_date",
            "label": "Scheduled or completed date",
            "description": "Provide the scheduled or completed date.",
        },
        {
            "key": "contractor_confirmation",
            "label": "Contractor confirmation",
            "description": "Provide contractor confirmation.",
        },
        {
            "key": "documentation",
            "label": "Supporting documentation",
            "description": "Provide supporting documentation if completed.",
        },
        {
            "key": "reason_if_delayed",
            "label": "Reason if delayed",
            "description": "Explain why the test cannot be completed by the due date, if applicable.",
        },
    ],
    CASE_TYPE_GOVERNMENT_DIRECTIVE: [
        {
            "key": "compliance_status",
            "label": "Current compliance status",
            "description": "Provide the current compliance status.",
        },
        {
            "key": "action_taken_or_planned",
            "label": "Action taken or planned",
            "description": "Describe the action taken or planned.",
        },
        {
            "key": "expected_completion_date",
            "label": "Expected completion date",
            "description": "Provide the expected completion date.",
        },
        {
            "key": "evidence_or_documentation",
            "label": "Evidence or documentation",
            "description": "Provide supporting evidence or documentation.",
        },
        {
            "key": "extension_or_blocker",
            "label": "Extension, blocker, or authority communication",
            "description": "Identify any extension, blocker, or authority communication.",
        },
    ],
}


def get_required_response_items(case_type: str) -> list[dict[str, str]]:
    """Return required response items for one supported case type."""
    return [dict(item) for item in _REQUIREMENTS.get(case_type, [])]


def build_case_requirements(case_id: str) -> list[dict]:
    """Upsert case_data_requirements rows for the case based on case type.

    Reads the case's case_type, calls get_required_response_items(), and
    upserts one row per required item using upsert_case_data_requirement().
    Does not overwrite rows that are already 'provided' or 'partial'.
    Returns the current list of requirement rows.

    Args:
        case_id: Case to build requirements for.

    Returns:
        List of current requirement dicts from the database.
    """
    case = db.get_case_by_id(case_id)
    if not case:
        return []

    case_type = case["case_type"]
    items = get_required_response_items(case_type)

    # Check existing requirements to avoid overwriting 'provided' or 'partial'
    existing_reqs = {
        req["requirement_key"]: req
        for req in db.get_case_data_requirements(case_id)
    }

    for item in items:
        key = item["key"]
        existing = existing_reqs.get(key)

        # Skip if already provided or partial
        if existing and existing["status"] in ("provided", "partial"):
            continue

        requirement_id = str(uuid.uuid4())
        db.upsert_case_data_requirement(
            requirement_id=requirement_id,
            case_id=case_id,
            requirement_key=key,
            label=item["label"],
            status="missing" if not existing else existing["status"],
            required=1,
            source=None,
            evidence_json=None,
        )

    # Return current state
    return [dict(row) for row in db.get_case_data_requirements(case_id)]


def calculate_case_completeness(case_id: str) -> dict:
    """Return completeness metrics from case_data_requirements rows.

    Counts rows with status='provided' as completed, all rows as total.
    Returns percentage and lists of provided/missing keys.

    Args:
        case_id: Case to calculate completeness for.

    Returns:
        Dict with keys: completed (int), total (int), percentage (float),
        provided_keys (list[str]), missing_keys (list[str])
    """
    requirements = db.get_case_data_requirements(case_id)

    total = len(requirements)
    completed = sum(1 for r in requirements if r["status"] == "provided")
    percentage = (completed / total * 100) if total > 0 else 100

    provided_keys = [r["requirement_key"] for r in requirements if r["status"] == "provided"]
    missing_keys = [r["requirement_key"] for r in requirements if r["status"] in ("missing", "partial")]

    return {
        "completed": completed,
        "total": total,
        "percentage": percentage,
        "provided_keys": provided_keys,
        "missing_keys": missing_keys,
    }


def validate_required_response_in_email(case_ids: list[str], body: str) -> list[str]:
    """Return requirement keys not mentioned in an email body for the cases."""
    normalized_body = _normalize(body)
    missing: list[str] = []
    for case_id in case_ids:
        for item in build_case_requirements(case_id):
            key = item["key"]
            if key in missing:
                continue
            if _item_mentioned(item, normalized_body):
                continue
            missing.append(key)
    return missing


def _item_mentioned(item: dict[str, str], normalized_body: str) -> bool:
    candidates = {
        item["key"],
        item["key"].replace("_", " "),
        item["label"],
        item["description"],
    }
    return any(_normalize(candidate) in normalized_body for candidate in candidates)


def _normalize(value: str) -> str:
    lowered = str(value or "").lower()
    without_punctuation = re.sub(r"[^a-z0-9]+", " ", lowered)
    return _SPACE_RE.sub(" ", without_punctuation).strip()
