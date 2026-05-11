"""
connection_discovery.py — AI-assisted connection hypothesis discovery.

Reads structured data from supported cases only, proposes possible hidden
relationships via a single budgeted AI call, stores them as proposed items,
and never mutates cases, sends emails, or takes autonomous action.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import database as db
from ai_gateway import get_ai_gateway
from constants import (
    SUPPORTED_CASE_TYPES,
    VALID_HYPOTHESIS_CONFIDENCES,
    VALID_HYPOTHESIS_RISK_LEVELS,
)
from time_utils import utc_now_iso

_DISCOVERY_SOURCE = "ai_connection_discovery"
_PROHIBITED_ACTIONS = re.compile(r"\b(send|escalate|close)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_supported_case_summaries(
    cases: list,
    events_by_case: Dict[str, list],
    fields_by_case: Dict[str, list],
    patterns_by_case: Dict[str, list],
) -> List[Dict[str, Any]]:
    """Build compact structured summaries for supported cases only.

    Asserts every case is a supported type. Never includes raw email bodies.
    """
    summaries = []
    for case in cases:
        case_dict = dict(case)
        assert case_dict["case_type"] in SUPPORTED_CASE_TYPES, (
            f"Unsupported case type reached discovery: {case_dict['case_type']}"
        )

        case_id = case_dict["case_id"]

        fields = {
            row["field_name"]: row["field_value"]
            for row in fields_by_case.get(case_id, [])
            if row.get("field_value")
        }

        # Only event types — never raw descriptions or email content
        event_types = [
            row["event_type"]
            for row in events_by_case.get(case_id, [])
        ][-10:]

        patterns = [
            {
                "pattern_type": row["pattern_type"],
                "severity": row["severity"],
                "summary": row["summary"],
            }
            for row in patterns_by_case.get(case_id, [])
        ]

        summaries.append({
            "case_id": case_id,
            "case_type": case_dict["case_type"],
            "status": case_dict["status"],
            "building": case_dict.get("building"),
            "device": case_dict.get("device"),
            "contractor": case_dict.get("contractor"),
            "due_date": case_dict.get("due_date"),
            "period": case_dict.get("period"),
            "created_at": case_dict.get("created_at"),
            "fields": fields,
            "recent_event_types": event_types,
            "active_patterns": patterns,
        })

    return summaries


def _build_discovery_prompt(case_summaries: List[Dict[str, Any]]) -> str:
    """Build the AI prompt with scope header asserting unsupported_emails_excluded."""
    scope_header = {
        "scope": "supported_kpi_cases_only",
        "unsupported_emails_excluded": True,
        "case_count": len(case_summaries),
        "supported_case_types": list(SUPPORTED_CASE_TYPES),
        "instruction": (
            "Analyze the following supported KPI case summaries for possible hidden "
            "connections or patterns. Propose reviewable hypotheses only. "
            "Do not modify cases, send emails, schedule follow-ups, escalate, or close cases. "
            "Use cautious language: 'Possible connection', 'Suggested relationship', "
            "'Needs review', 'Evidence indicates', 'May be related'. "
            "Avoid: 'Root cause', 'Confirmed', 'Contractor failure', 'Client must be notified'."
        ),
        "output_schema": {
            "hypotheses": [
                {
                    "hypothesis_type": "string",
                    "summary": "string — one sentence",
                    "confidence": "low|medium|high",
                    "risk_level": "info|review|management_review",
                    "evidence": {
                        "case_ids": ["list of case_id strings from the input above"],
                        "description": "brief description of the evidence",
                    },
                    "reasoning": "string — why this connection may exist",
                    "recommended_human_review": "string — what a reviewer should check",
                }
            ]
        },
    }

    prompt = (
        f"SCOPE HEADER:\n{json.dumps(scope_header, indent=2)}\n\n"
        f"CASE SUMMARIES ({len(case_summaries)} supported cases):\n"
        f"{json.dumps(case_summaries, indent=2)}\n\n"
        "Return ONLY a JSON object with a 'hypotheses' array. "
        "Each hypothesis must reference only the case_ids listed above. "
        "If no meaningful connections are found, return {\"hypotheses\": []}."
    )
    return prompt


def _validate_hypothesis(
    hypothesis: Dict[str, Any],
    valid_case_ids: set,
) -> Tuple[bool, str]:
    """Validate a single AI-produced hypothesis dict.

    Returns (is_valid, rejection_reason). Empty rejection_reason means valid.
    """
    confidence = hypothesis.get("confidence")
    if confidence not in VALID_HYPOTHESIS_CONFIDENCES:
        return False, f"Invalid confidence: {confidence!r}"

    risk_level = hypothesis.get("risk_level")
    if risk_level not in VALID_HYPOTHESIS_RISK_LEVELS:
        return False, f"Invalid risk_level: {risk_level!r}"

    evidence = hypothesis.get("evidence")
    if not isinstance(evidence, dict):
        return False, "Missing or invalid evidence object"

    case_ids = evidence.get("case_ids")
    if not case_ids or not isinstance(case_ids, list) or len(case_ids) == 0:
        return False, "Missing or empty evidence.case_ids"

    for cid in case_ids:
        if cid not in valid_case_ids:
            return False, f"case_id {cid!r} not in supported case set"

    reasoning = (hypothesis.get("reasoning") or "").strip()
    if not reasoning:
        return False, "Missing reasoning"

    review_text = (hypothesis.get("recommended_human_review") or "").strip()
    if not review_text:
        return False, "Missing recommended_human_review"

    combined = (
        f"{hypothesis.get('summary', '')} "
        f"{reasoning} "
        f"{review_text}"
    )
    if _PROHIBITED_ACTIONS.search(combined):
        return False, "Hypothesis contains prohibited action language (send/escalate/close)"

    return True, ""


def _store_hypothesis(hypothesis: Dict[str, Any], dry_run: bool) -> str:
    """Store a validated hypothesis and return its hypothesis_id."""
    hypothesis_id = str(uuid.uuid4())
    evidence = hypothesis.get("evidence") or {}
    case_ids: List[str] = evidence.get("case_ids", []) if isinstance(evidence, dict) else []

    if not dry_run:
        db.insert_connection_hypothesis(
            hypothesis_id=hypothesis_id,
            hypothesis_type=hypothesis.get("hypothesis_type", "unknown"),
            summary=hypothesis.get("summary", ""),
            confidence=hypothesis["confidence"],
            risk_level=hypothesis["risk_level"],
            evidence_json=json.dumps(evidence),
            reasoning=hypothesis.get("reasoning", ""),
            recommended_human_review=hypothesis.get("recommended_human_review", ""),
        )
        for cid in case_ids:
            db.insert_connection_hypothesis_case(hypothesis_id, cid)

    return hypothesis_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_discovery(
    max_ai_calls: int,
    limit: Optional[int] = None,
    building: Optional[str] = None,
    case_type_filter: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run the connection discovery workflow.

    Reads supported cases only, builds a prompt, calls the AI gateway,
    validates each hypothesis, and stores accepted ones as proposed items.

    Never modifies cases, sends emails, schedules follow-ups, escalates,
    or closes cases. The AI gateway must be configured by the caller before
    this function is invoked.

    Args:
        max_ai_calls: Explicit budget assertion — must be > 0.
        limit: Maximum cases to include in the analysis.
        building: Optional building name filter (substring match).
        case_type_filter: Optional exact case type filter.
        dry_run: Print hypotheses without writing to the database.

    Returns:
        Summary dict: cases_analyzed, hypotheses_proposed, hypotheses_rejected,
        dry_run, and a hypotheses list.
    """
    if not max_ai_calls or max_ai_calls <= 0:
        raise ValueError("max_ai_calls must be a positive integer.")

    cases = db.get_supported_cases_for_discovery(
        building=building,
        case_type=case_type_filter,
        limit=limit,
    )

    if not cases:
        print("[DISCOVERY] No supported cases found to analyze.")
        _write_observability_event(cases_analyzed=0, ai_outcome="skipped", dry_run=dry_run)
        return {
            "cases_analyzed": 0,
            "hypotheses_proposed": 0,
            "hypotheses_rejected": 0,
            "dry_run": dry_run,
            "hypotheses": [],
        }

    events_by_case: Dict[str, list] = {}
    fields_by_case: Dict[str, list] = {}
    patterns_by_case: Dict[str, list] = {}
    for case in cases:
        cid = case["case_id"]
        events_by_case[cid] = [dict(row) for row in db.get_events_for_case(cid)]
        fields_by_case[cid] = [dict(row) for row in db.get_fields_for_case(cid)]
        patterns_by_case[cid] = [dict(row) for row in db.get_active_pattern_flags_for_case(cid)]

    case_summaries = _build_supported_case_summaries(
        cases, events_by_case, fields_by_case, patterns_by_case
    )
    valid_case_ids = {s["case_id"] for s in case_summaries}

    prompt = _build_discovery_prompt(case_summaries)
    gateway = get_ai_gateway()
    outcome = gateway.call_json(
        prompt=prompt,
        purpose="other",
        prompt_type="connection_discovery",
        caller="connection_discovery",
    )

    _write_observability_event(
        cases_analyzed=len(cases),
        ai_outcome=outcome.status,
        dry_run=dry_run,
    )

    if outcome.status == "blocked" or outcome.payload is None:
        print(f"[DISCOVERY] AI call blocked or returned no result: {outcome.reason}")
        return {
            "cases_analyzed": len(cases),
            "hypotheses_proposed": 0,
            "hypotheses_rejected": 0,
            "dry_run": dry_run,
            "hypotheses": [],
            "ai_outcome": outcome.status,
        }

    raw_hypotheses: list = []
    if isinstance(outcome.payload, dict):
        raw_hypotheses = outcome.payload.get("hypotheses", [])
    if not isinstance(raw_hypotheses, list):
        raw_hypotheses = []

    proposed = []
    rejected = []

    for hyp in raw_hypotheses:
        if not isinstance(hyp, dict):
            rejected.append({"reason": "Not a dict"})
            continue

        is_valid, reason = _validate_hypothesis(hyp, valid_case_ids)
        if not is_valid:
            rejected.append({"reason": reason, "hypothesis_type": hyp.get("hypothesis_type")})
            print(f"[DISCOVERY] Rejected: {reason}")
            continue

        hyp_id = _store_hypothesis(hyp, dry_run=dry_run)
        proposed.append({
            "hypothesis_id": hyp_id,
            "hypothesis_type": hyp.get("hypothesis_type"),
            "confidence": hyp["confidence"],
        })
        action = "[DRY RUN]" if dry_run else "[STORED]"
        print(
            f"[DISCOVERY] {action} {hyp.get('hypothesis_type')} "
            f"| {hyp['confidence']} "
            f"| {(hyp.get('summary') or '')[:80]}"
        )

    print(
        f"\n[DISCOVERY] Complete: {len(proposed)} proposed, "
        f"{len(rejected)} rejected, {len(cases)} cases analyzed."
    )
    if dry_run:
        print("[DISCOVERY] Dry run — no database writes.")

    return {
        "cases_analyzed": len(cases),
        "hypotheses_proposed": len(proposed),
        "hypotheses_rejected": len(rejected),
        "dry_run": dry_run,
        "hypotheses": proposed,
    }


def _write_observability_event(
    cases_analyzed: int,
    ai_outcome: str,
    dry_run: bool,
) -> None:
    from observability import append_structured_event

    try:
        append_structured_event(
            component="connection_discovery",
            event_name="discovery_run",
            status="ok" if ai_outcome not in ("blocked",) else "blocked",
            cases_analyzed=cases_analyzed,
            unsupported_kpi_included=0,
            ai_outcome=ai_outcome,
            dry_run=dry_run,
        )
    except OSError:
        pass
