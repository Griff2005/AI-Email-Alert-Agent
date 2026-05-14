"""Reply email mapping and completeness analysis.

This module attaches inbound reply emails to building groups or cases,
proposes deterministic mappings, and analyzes reply completeness against
case requirements.

No AI calls. No automatic case closure. No automatic email sending.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

import database as db
import response_requirements
from config import config
from constants import REVIEW_REASON_REPLY_POSSIBLE_RESOLUTION
from time_utils import utc_now_iso

_COMPLETION_KEYWORDS = (
    "complete",
    "completed",
    "done",
    "finished",
    "resolved",
    "submitted",
    "uploaded",
    "confirmed",
    "installed",
    "tested",
)

_CASE_ID_RE = re.compile(r"(case-[a-z0-9\-]+)", re.IGNORECASE)


def attach_reply_to_group(
    reply_email_id: str,
    group_id: str,
    source: str = "manual",
) -> str:
    """Attach an inbound reply email to a building group.

    Inserts a reply_case_mappings row with group_id set and case_id=None.
    Does not auto-close any case.

    Args:
        reply_email_id: ID of the reply email to attach.
        group_id: ID of the building group to attach to.
        source: Mapping source (default 'manual').

    Returns:
        The mapping_id of the created mapping.
    """
    mapping_id = str(uuid.uuid4())
    db.insert_reply_case_mapping(
        mapping_id=mapping_id,
        reply_email_id=reply_email_id,
        case_id=None,
        group_id=group_id,
        mapping_source=source,
        confidence=None,
        status="proposed",
    )
    return mapping_id


def propose_reply_case_mappings(reply_email_id: str) -> list[dict]:
    """Return deterministic mapping suggestions for a reply email.

    Looks for case IDs in the reply subject/body.
    Looks for building+contractor matches against open building groups.

    Does not persist anything.

    Args:
        reply_email_id: Reply email to analyze.

    Returns:
        List of suggestion dicts with keys: case_id, group_id, confidence, reason.
    """
    email = db.get_email_by_id(reply_email_id)
    if not email:
        return []

    suggestions = []
    subject = email.get("subject", "") or ""
    body = email.get("raw_body", "") or ""
    combined_text = f"{subject} {body}".lower()

    # Look for case IDs in subject/body
    case_ids = _CASE_ID_RE.findall(combined_text)
    for case_id_match in case_ids:
        case = db.get_case_by_id(case_id_match)
        if case:
            suggestions.append(
                {
                    "case_id": case_id_match,
                    "group_id": None,
                    "confidence": "high",
                    "reason": "Case ID found in reply subject or body",
                }
            )

    # TODO: Implement building+contractor matching against groups
    # (deferred to future enhancement per scope)

    return suggestions


def save_reply_case_mapping(
    reply_email_id: str,
    case_id: Optional[str] = None,
    source: str = "manual",
    group_id: Optional[str] = None,
    confidence: str = "manual",
) -> str:
    """Persist a confirmed reply-to-case or reply-to-group mapping.

    Does not auto-close any case.

    Args:
        reply_email_id: Reply email to map.
        case_id: Target case ID (optional if group_id provided).
        source: Mapping source (one of REPLY_MAPPING_SOURCES).
        group_id: Target building group ID (optional if case_id provided).
        confidence: Confidence level.

    Returns:
        The mapping_id of the created mapping.
    """
    mapping_id = str(uuid.uuid4())
    db.insert_reply_case_mapping(
        mapping_id=mapping_id,
        reply_email_id=reply_email_id,
        case_id=case_id,
        group_id=group_id,
        mapping_source=source,
        confidence=confidence,
        status="confirmed",
    )
    return mapping_id


def analyze_reply_completeness(reply_email_id: str, case_id: str) -> dict:
    """Compare reply body to case_data_requirements for a case.

    Reads case_data_requirements for case_id.
    Looks for keywords from requirement labels in the reply body.

    Returns dict with:
    - result: One of REPLY_COMPLETENESS_RESULTS
    - addressed_keys: list of requirement keys addressed by the reply
    - missing_keys: list of requirement keys not addressed
    - completion_claimed: bool (if completion keywords found)
    - evidence_found: bool (if completion claimed + evidence found)

    If completion claimed but no evidence found:
    - Sets result='completion_claimed_no_evidence'
    - Creates a manual_reviews row for human review
    - Does NOT close the case

    Args:
        reply_email_id: Reply email to analyze.
        case_id: Case to check requirements against.

    Returns:
        Dict with analysis results.
    """
    email = db.get_email_by_id(reply_email_id)
    if not email:
        return {
            "result": "unrelated",
            "addressed_keys": [],
            "missing_keys": [],
            "completion_claimed": False,
            "evidence_found": False,
        }

    requirements = db.get_case_data_requirements(case_id)
    if not requirements:
        # Case has no requirements tracked
        return {
            "result": "unrelated",
            "addressed_keys": [],
            "missing_keys": [],
            "completion_claimed": False,
            "evidence_found": False,
        }

    reply_body = (email.get("raw_body") or "").lower()
    subject = (email.get("subject") or "").lower()
    combined = f"{subject} {reply_body}"

    addressed_keys = []
    missing_keys = []

    for req in requirements:
        key = req["requirement_key"]
        label = req["label"].lower()

        # Check if requirement is mentioned in reply
        if label in combined:
            addressed_keys.append(key)
        else:
            missing_keys.append(key)

    # Detect completion claim
    completion_claimed = any(kw in combined for kw in _COMPLETION_KEYWORDS)

    # Detect if evidence is present (if anything was addressed, consider evidence found)
    evidence_found = len(addressed_keys) > 0

    # Determine result
    if completion_claimed and not evidence_found:
        result = "completion_claimed_no_evidence"
        # Create manual review for human verification
        review_id = str(uuid.uuid4())
        db.insert_manual_review(
            review_id=review_id,
            case_id=case_id,
            email_id=reply_email_id,
            reason=f"Reply claims completion without supporting evidence",
            review_category="reply_claims_completion",
            blocking=1,
        )
    elif completion_claimed and evidence_found:
        result = "complete"
    elif len(addressed_keys) > 0:
        result = "partial"
    else:
        result = "unrelated"

    return {
        "result": result,
        "addressed_keys": addressed_keys,
        "missing_keys": missing_keys,
        "completion_claimed": completion_claimed,
        "evidence_found": evidence_found,
    }
