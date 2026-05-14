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
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import database as db
from ai_gateway import AiBudgetExceeded, get_ai_gateway
from constants import (
    SUPPORTED_CASE_TYPES,
    SUPPORTED_CASE_TYPES_SET,
    VALID_HYPOTHESIS_CONFIDENCES,
    VALID_HYPOTHESIS_RISK_LEVELS,
)
from time_utils import utc_now_iso

_DISCOVERY_SOURCE = "ai_connection_discovery"

# Reject hypotheses that contain action, conclusion, or blame language.
_PROHIBITED_LANGUAGE = re.compile(
    r"\b(send|escalate|close|confirmed)\b"
    r"|root cause"
    r"|contractor failure"
    r"|mechanic caused"
    r"|client must be notified"
    r"|notify client"
    r"|contact client"
    r"|email the contractor",
    re.IGNORECASE,
)


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
    valid_pattern_ids: Optional[set] = None,
) -> Tuple[bool, str]:
    """Validate a single AI-produced hypothesis dict.

    Returns (is_valid, rejection_reason). Empty rejection_reason means valid.
    """
    hypothesis_type = (hypothesis.get("hypothesis_type") or "").strip()
    if not hypothesis_type:
        return False, "Missing or empty hypothesis_type"

    summary = (hypothesis.get("summary") or "").strip()
    if not summary:
        return False, "Missing or empty summary"

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

    if valid_pattern_ids is not None:
        pattern_ids = evidence.get("pattern_ids") or evidence.get("pattern_flag_ids") or []
        if pattern_ids and not isinstance(pattern_ids, list):
            return False, "Invalid evidence pattern ID list"
        for pattern_id in pattern_ids:
            if str(pattern_id) not in valid_pattern_ids:
                return False, f"pattern_id {pattern_id!r} not in packet pattern set"

    reasoning = (hypothesis.get("reasoning") or "").strip()
    if not reasoning:
        return False, "Missing reasoning"

    review_text = (hypothesis.get("recommended_human_review") or "").strip()
    if not review_text:
        return False, "Missing recommended_human_review"

    combined = f"{summary} {reasoning} {review_text}"
    if _PROHIBITED_LANGUAGE.search(combined):
        return False, "Hypothesis contains prohibited language (action, conclusion, or blame)"

    return True, ""


def _store_hypothesis(hypothesis: Dict[str, Any], dry_run: bool) -> str:
    """Store a validated hypothesis and return its hypothesis_id."""
    hypothesis_id = str(uuid.uuid4())
    evidence = hypothesis.get("evidence") or {}
    case_ids: List[str] = evidence.get("case_ids", []) if isinstance(evidence, dict) else []

    if not dry_run:
        db.insert_connection_hypothesis(
            hypothesis_id=hypothesis_id,
            hypothesis_type=hypothesis["hypothesis_type"],
            summary=hypothesis["summary"],
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
    scope: Optional[str] = None,
    packet_by: str = "entity",
    batch_size: int = 25,
    max_prompt_chars: int = 40000,
) -> Dict[str, Any]:
    """Run small-case or scoped packetized connection discovery."""
    if scope in (None, "", "small", "small-case"):
        if dry_run and max_ai_calls == 0:
            return _run_small_case_dry_run_preview(
                limit=limit,
                building=building,
                case_type_filter=case_type_filter,
            )
        return _run_small_case_discovery(
            max_ai_calls=max_ai_calls,
            limit=limit,
            building=building,
            case_type_filter=case_type_filter,
            dry_run=dry_run,
        )

    if scope not in {"patterns", "building-groups", "all-supported"}:
        raise ValueError(
            "scope must be one of patterns, building-groups, all-supported, or omitted"
        )

    return _run_packetized_discovery(
        scope=scope,
        max_ai_calls=max_ai_calls,
        packet_by=packet_by,
        batch_size=batch_size,
        max_prompt_chars=max_prompt_chars,
        dry_run=dry_run,
    )


def _run_small_case_discovery(
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

    if outcome.status == "blocked" or outcome.payload is None:
        print(f"[DISCOVERY] AI call blocked or returned no result: {outcome.reason}")
        _write_observability_event(
            cases_analyzed=len(cases),
            ai_outcome=outcome.status,
            dry_run=dry_run,
            hypotheses_proposed=0,
            hypotheses_rejected=0,
            hypotheses_stored=0,
        )
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

    hypotheses_stored = 0 if dry_run else len(proposed)
    _write_observability_event(
        cases_analyzed=len(cases),
        ai_outcome=outcome.status,
        dry_run=dry_run,
        hypotheses_proposed=len(proposed),
        hypotheses_rejected=len(rejected),
        hypotheses_stored=hypotheses_stored,
    )

    return {
        "cases_analyzed": len(cases),
        "hypotheses_proposed": len(proposed),
        "hypotheses_rejected": len(rejected),
        "dry_run": dry_run,
        "hypotheses": proposed,
    }


def _run_small_case_dry_run_preview(
    limit: Optional[int] = None,
    building: Optional[str] = None,
    case_type_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Preview the small-case discovery scope without calling AI."""
    cases = db.get_supported_cases_for_discovery(
        building=building,
        case_type=case_type_filter,
        limit=limit,
    )
    print(
        f"[DISCOVERY] Dry run: small-case discovery would analyze "
        f"{len(cases)} supported case(s)."
    )
    _write_observability_event(
        cases_analyzed=len(cases),
        ai_outcome="skipped",
        dry_run=True,
        hypotheses_proposed=0,
        hypotheses_rejected=0,
        hypotheses_stored=0,
    )
    return {
        "cases_analyzed": len(cases),
        "hypotheses_proposed": 0,
        "hypotheses_rejected": 0,
        "dry_run": True,
        "hypotheses": [],
        "ai_outcome": "skipped",
    }


def _run_packetized_discovery(
    scope: str,
    max_ai_calls: int,
    packet_by: str,
    batch_size: int,
    max_prompt_chars: int,
    dry_run: bool,
) -> Dict[str, Any]:
    """Run packetized discovery over patterns, building groups, or supported cases."""
    if max_ai_calls is None or max_ai_calls < 0:
        raise ValueError("max_ai_calls must be a non-negative integer.")
    if not dry_run and max_ai_calls <= 0:
        raise ValueError("max_ai_calls must be positive unless dry_run=True.")

    import discovery_packets

    run_id = str(uuid.uuid4())
    config_json = json.dumps(
        {
            "packet_by": packet_by,
            "batch_size": batch_size,
            "max_prompt_chars": max_prompt_chars,
            "dry_run": dry_run,
        },
        sort_keys=True,
    )
    db.insert_discovery_run(
        run_id=run_id,
        scope=scope,
        status="running",
        max_ai_calls=max_ai_calls,
        config_json=config_json,
    )

    summary: Dict[str, Any] = {
        "run_id": run_id,
        "scope": scope,
        "dry_run": dry_run,
        "packets_created": 0,
        "packets_analyzed": 0,
        "ai_calls_used": 0,
        "hypotheses_proposed": 0,
        "hypotheses_created": 0,
        "hypotheses_rejected": 0,
        "unsupported_records_included": 0,
        "error_count": 0,
        "hypotheses": [],
    }

    try:
        packets = _build_packets_for_scope(
            discovery_packets=discovery_packets,
            run_id=run_id,
            scope=scope,
            packet_by=packet_by,
            batch_size=batch_size,
            max_prompt_chars=max_prompt_chars,
        )
        unsupported_records = sum(
            int(packet.get("unsupported_records_included", 0))
            for packet in packets
        )
        if unsupported_records != 0:
            raise AssertionError("unsupported_records_included must be 0 for discovery packets")

        summary["packets_created"] = len(packets)
        summary["unsupported_records_included"] = unsupported_records
        for packet in packets:
            _insert_packet_tracking_row(packet)

        db.update_discovery_run(
            run_id,
            {
                "packets_created": summary["packets_created"],
                "unsupported_records_included": 0,
            },
        )

        if dry_run or max_ai_calls == 0:
            for packet in packets:
                db.update_discovery_packet(
                    packet["packet_id"],
                    {
                        "status": "dry_run",
                        "completed_at": utc_now_iso(),
                    },
                )
            print(
                f"[DISCOVERY] Dry run: {scope} discovery would analyze "
                f"{len(packets)} packet(s) with max_ai_calls={max_ai_calls}."
            )
            _complete_discovery_run(run_id, summary, status="completed")
            return _packetized_result(summary)

        gateway = get_ai_gateway()
        for packet in packets:
            if summary["ai_calls_used"] >= max_ai_calls:
                db.update_discovery_packet(
                    packet["packet_id"],
                    {
                        "status": "skipped_budget",
                        "completed_at": utc_now_iso(),
                        "error": "max_ai_calls budget reached",
                    },
                )
                continue

            packet_result = _analyze_packet(packet, gateway, dry_run=dry_run)
            summary["ai_calls_used"] += packet_result["ai_call_used"]
            summary["packets_analyzed"] += packet_result["packets_analyzed"]
            summary["hypotheses_created"] += packet_result["hypotheses_created"]
            summary["hypotheses_proposed"] += packet_result["hypotheses_created"]
            summary["hypotheses_rejected"] += packet_result["hypotheses_rejected"]
            summary["error_count"] += packet_result["error_count"]
            summary["hypotheses"].extend(packet_result["hypotheses"])

            db.update_discovery_run(
                run_id,
                {
                    "ai_calls_used": summary["ai_calls_used"],
                    "packets_analyzed": summary["packets_analyzed"],
                    "hypotheses_created": summary["hypotheses_created"],
                    "hypotheses_rejected": summary["hypotheses_rejected"],
                    "error_count": summary["error_count"],
                },
            )

        _complete_discovery_run(run_id, summary, status="completed")
        print(
            f"\n[DISCOVERY] Packetized {scope} complete: "
            f"{summary['hypotheses_created']} proposed, "
            f"{summary['hypotheses_rejected']} rejected, "
            f"{summary['packets_analyzed']}/{summary['packets_created']} packets analyzed."
        )
        return _packetized_result(summary)
    except Exception as exc:
        summary["error_count"] += 1
        db.update_discovery_run(
            run_id,
            {
                "status": "failed",
                "completed_at": utc_now_iso(),
                "error_count": summary["error_count"],
                "unsupported_records_included": summary["unsupported_records_included"],
            },
        )
        _write_packetized_observability_event(run_id, scope, summary, status="error")
        raise exc


def _build_packets_for_scope(
    discovery_packets: Any,
    run_id: str,
    scope: str,
    packet_by: str,
    batch_size: int,
    max_prompt_chars: int,
) -> List[Dict[str, Any]]:
    if scope == "patterns":
        return discovery_packets.build_pattern_packets(
            run_id=run_id,
            max_cases_per_packet=batch_size,
            max_prompt_chars=max_prompt_chars,
        )
    if scope == "building-groups":
        return discovery_packets.build_building_group_packets(
            run_id=run_id,
            max_cases_per_packet=batch_size,
            max_prompt_chars=max_prompt_chars,
        )
    return discovery_packets.build_all_supported_packets(
        run_id=run_id,
        packet_by=packet_by,
        batch_size=batch_size,
        max_prompt_chars=max_prompt_chars,
    )


def _insert_packet_tracking_row(packet: Dict[str, Any]) -> None:
    db.insert_discovery_packet(
        packet_id=packet["packet_id"],
        run_id=packet["run_id"],
        packet_type=packet["packet_type"],
        entity_type=packet.get("entity_type"),
        entity_value=packet.get("entity_value"),
        case_count=len(packet.get("cases") or []),
        pattern_count=len(packet.get("pattern_flags") or []),
        status="pending",
    )


def _analyze_packet(
    packet: Dict[str, Any],
    gateway: Any,
    dry_run: bool,
) -> Dict[str, Any]:
    result = {
        "ai_call_used": 0,
        "packets_analyzed": 0,
        "hypotheses_created": 0,
        "hypotheses_rejected": 0,
        "error_count": 0,
        "hypotheses": [],
    }
    prompt = _build_packet_prompt(packet)
    try:
        outcome = gateway.call_json(
            prompt=prompt,
            purpose="other",
            prompt_type="connection_discovery_packet",
            caller="connection_discovery",
        )
    except AiBudgetExceeded as exc:
        db.update_discovery_packet(
            packet["packet_id"],
            {
                "status": "blocked",
                "completed_at": utc_now_iso(),
                "error": str(exc),
            },
        )
        result["error_count"] = 1
        return result

    if outcome.status in {"allowed", "mocked", "cached"}:
        result["ai_call_used"] = 1

    if outcome.status == "blocked" or outcome.payload is None:
        db.update_discovery_packet(
            packet["packet_id"],
            {
                "status": "blocked",
                "completed_at": utc_now_iso(),
                "ai_call_used": result["ai_call_used"],
                "error": outcome.reason,
            },
        )
        result["error_count"] = 1
        return result

    raw_hypotheses: list = []
    if isinstance(outcome.payload, dict):
        raw_hypotheses = outcome.payload.get("hypotheses", [])
    if not isinstance(raw_hypotheses, list):
        raw_hypotheses = []

    valid_case_ids = {case["case_id"] for case in packet.get("cases") or []}
    valid_pattern_ids = {
        str(pattern.get("pattern_id"))
        for pattern in packet.get("pattern_flags") or []
        if pattern.get("pattern_id") is not None
    }

    for hyp in raw_hypotheses:
        if not isinstance(hyp, dict):
            result["hypotheses_rejected"] += 1
            continue

        is_valid, reason = _validate_hypothesis(hyp, valid_case_ids, valid_pattern_ids)
        if not is_valid:
            result["hypotheses_rejected"] += 1
            print(f"[DISCOVERY] Rejected packet hypothesis: {reason}")
            continue

        _annotate_packet_evidence(hyp, packet)
        hyp_id = _store_hypothesis(hyp, dry_run=dry_run)
        result["hypotheses_created"] += 1
        result["hypotheses"].append(
            {
                "hypothesis_id": hyp_id,
                "hypothesis_type": hyp.get("hypothesis_type"),
                "confidence": hyp.get("confidence"),
                "packet_id": packet["packet_id"],
            }
        )
        action = "[DRY RUN]" if dry_run else "[STORED]"
        print(
            f"[DISCOVERY] {action} {hyp.get('hypothesis_type')} "
            f"| {hyp['confidence']} | packet={packet['packet_id']}"
        )

    result["packets_analyzed"] = 1
    db.update_discovery_packet(
        packet["packet_id"],
        {
            "status": "completed",
            "completed_at": utc_now_iso(),
            "ai_call_used": result["ai_call_used"],
            "hypotheses_created": result["hypotheses_created"],
        },
    )
    return result


def _annotate_packet_evidence(hypothesis: Dict[str, Any], packet: Dict[str, Any]) -> None:
    evidence = hypothesis.get("evidence")
    if not isinstance(evidence, dict):
        return
    evidence.setdefault("packet_id", packet["packet_id"])
    evidence.setdefault("run_id", packet["run_id"])
    evidence.setdefault("packet_type", packet["packet_type"])
    entity = packet.get("entity") or {}
    for key in ("building", "contractor", "device"):
        if entity.get(key) and not evidence.get(key):
            evidence[key] = entity.get(key)
    hypothesis["evidence"] = evidence


def _build_packet_prompt(packet: Dict[str, Any]) -> str:
    scope_header = {
        "scope": packet.get("scope"),
        "packet_id": packet.get("packet_id"),
        "packet_type": packet.get("packet_type"),
        "unsupported_records_included": packet.get("unsupported_records_included", 0),
        "instruction": (
            "Analyze this structured supported-case evidence packet for possible "
            "reviewable connections. Do not modify cases, send emails, schedule "
            "follow-ups, escalate, or close cases. Reference only case IDs present "
            "in this packet. Avoid blame, conclusions, or action-command language."
        ),
        "output_schema": {
            "hypotheses": [
                {
                    "hypothesis_type": "string",
                    "summary": "string - one cautious sentence",
                    "confidence": "low|medium|high",
                    "risk_level": "info|review|management_review",
                    "evidence": {
                        "case_ids": ["case IDs from packet.cases only"],
                        "pattern_ids": ["optional pattern IDs from packet.pattern_flags"],
                        "description": "brief evidence description",
                    },
                    "reasoning": "why this connection may exist",
                    "recommended_human_review": "what a reviewer should check",
                }
            ]
        },
    }
    return (
        f"SCOPE HEADER:\n{json.dumps(scope_header, indent=2, sort_keys=True)}\n\n"
        f"EVIDENCE PACKET:\n{json.dumps(packet, indent=2, sort_keys=True)}\n\n"
        "Return ONLY a JSON object with a 'hypotheses' array. "
        "If no meaningful connections are found, return {\"hypotheses\": []}."
    )


def _complete_discovery_run(
    run_id: str,
    summary: Dict[str, Any],
    status: str,
) -> None:
    db.update_discovery_run(
        run_id,
        {
            "status": status,
            "completed_at": utc_now_iso(),
            "ai_calls_used": summary["ai_calls_used"],
            "packets_created": summary["packets_created"],
            "packets_analyzed": summary["packets_analyzed"],
            "hypotheses_created": summary["hypotheses_created"],
            "hypotheses_rejected": summary["hypotheses_rejected"],
            "unsupported_records_included": 0,
            "error_count": summary["error_count"],
        },
    )
    _write_packetized_observability_event(run_id, summary["scope"], summary, status=status)


def _packetized_result(summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": summary["run_id"],
        "scope": summary["scope"],
        "dry_run": summary["dry_run"],
        "packets_created": summary["packets_created"],
        "packets_analyzed": summary["packets_analyzed"],
        "ai_calls_used": summary["ai_calls_used"],
        "hypotheses_proposed": summary["hypotheses_created"],
        "hypotheses_created": summary["hypotheses_created"],
        "hypotheses_rejected": summary["hypotheses_rejected"],
        "unsupported_records_included": 0,
        "error_count": summary["error_count"],
        "hypotheses": summary["hypotheses"],
    }


def merge_duplicate_hypotheses(dry_run: bool = False) -> Dict[str, Any]:
    """Merge duplicate proposed hypotheses using a deterministic evidence key."""
    rows = [dict(row) for row in db.get_connection_hypotheses(status_filter="proposed")]
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        case_ids = db.get_cases_for_hypothesis(row["hypothesis_id"])
        groups[_hypothesis_duplicate_key(row, case_ids)].append(row)

    duplicate_groups = [group for group in groups.values() if len(group) > 1]
    result = {
        "dry_run": dry_run,
        "duplicate_groups": len(duplicate_groups),
        "hypotheses_marked_merged": 0,
        "hypotheses_updated": 0,
    }

    for group in duplicate_groups:
        winner = max(
            group,
            key=lambda row: (
                _confidence_rank(row.get("confidence")),
                str(row.get("created_at") or ""),
                str(row.get("hypothesis_id") or ""),
            ),
        )
        losers = [row for row in group if row["hypothesis_id"] != winner["hypothesis_id"]]
        combined_case_ids = sorted(
            {
                case_id
                for row in group
                for case_id in db.get_cases_for_hypothesis(row["hypothesis_id"])
            }
        )
        boosted_confidence = _boost_confidence(winner.get("confidence"), len(group))
        winner_evidence = _parse_evidence(winner.get("evidence_json"))
        winner_evidence["case_ids"] = combined_case_ids
        winner_evidence["merged_hypothesis_ids"] = [
            row["hypothesis_id"] for row in losers
        ]

        if dry_run:
            continue

        db.update_connection_hypothesis(
            winner["hypothesis_id"],
            {
                "confidence": boosted_confidence,
                "evidence_json": json.dumps(winner_evidence, sort_keys=True),
            },
        )
        for case_id in combined_case_ids:
            db.insert_connection_hypothesis_case(winner["hypothesis_id"], case_id)
        for loser in losers:
            db.update_connection_hypothesis(loser["hypothesis_id"], {"status": "merged"})
        result["hypotheses_marked_merged"] += len(losers)
        result["hypotheses_updated"] += 1

    return result


def _hypothesis_duplicate_key(row: Dict[str, Any], linked_case_ids: List[str]) -> Tuple[Any, ...]:
    evidence = _parse_evidence(row.get("evidence_json"))
    case_ids = sorted(set(linked_case_ids) | set(_safe_string_list(evidence.get("case_ids"))))
    cases = [
        dict(case)
        for case_id in case_ids
        for case in [db.get_case_by_id(case_id)]
        if case and case["case_type"] in SUPPORTED_CASE_TYPES_SET
    ]
    building = _first_evidence_or_case_value(evidence, cases, "building")
    contractor = _first_evidence_or_case_value(evidence, cases, "contractor")
    device = _first_evidence_or_case_value(evidence, cases, "device")
    case_types = sorted({case["case_type"] for case in cases})
    return (
        row.get("hypothesis_type"),
        _normalize_key_value(building),
        _normalize_key_value(contractor),
        _normalize_key_value(device),
        tuple(case_types),
        tuple(case_ids),
    )


def _parse_evidence(raw_evidence: Optional[str]) -> Dict[str, Any]:
    if not raw_evidence:
        return {}
    try:
        parsed = json.loads(raw_evidence)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _first_evidence_or_case_value(
    evidence: Dict[str, Any],
    cases: List[Dict[str, Any]],
    field_name: str,
) -> str:
    if evidence.get(field_name):
        return str(evidence[field_name])
    values = sorted({str(case.get(field_name) or "").strip() for case in cases if case.get(field_name)})
    return values[0] if len(values) == 1 else ""


def _normalize_key_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _confidence_rank(confidence: Optional[str]) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(str(confidence or ""), 0)


def _boost_confidence(confidence: Optional[str], support_count: int) -> str:
    ordered = ["low", "medium", "high"]
    if confidence not in ordered:
        return "low"
    if support_count <= 1:
        return str(confidence)
    return ordered[min(ordered.index(str(confidence)) + 1, len(ordered) - 1)]


def _write_observability_event(
    cases_analyzed: int,
    ai_outcome: str,
    dry_run: bool,
    hypotheses_proposed: int = 0,
    hypotheses_rejected: int = 0,
    hypotheses_stored: int = 0,
) -> None:
    from observability import append_structured_event

    try:
        append_structured_event(
            component="connection_discovery",
            event_name="discovery_run",
            status="ok" if ai_outcome != "blocked" else "blocked",
            cases_analyzed=cases_analyzed,
            hypotheses_proposed=hypotheses_proposed,
            hypotheses_rejected=hypotheses_rejected,
            hypotheses_stored=hypotheses_stored,
            unsupported_kpi_included=0,
            ai_outcome=ai_outcome,
            dry_run=dry_run,
        )
    except OSError:
        pass


def _write_packetized_observability_event(
    run_id: str,
    scope: str,
    summary: Dict[str, Any],
    status: str,
) -> None:
    from observability import append_structured_event

    try:
        append_structured_event(
            component="connection_discovery",
            event_name="discovery_run",
            status=status,
            run_id=run_id,
            scope=scope,
            packets_created=summary.get("packets_created", 0),
            packets_analyzed=summary.get("packets_analyzed", 0),
            ai_calls_used=summary.get("ai_calls_used", 0),
            hypotheses_created=summary.get("hypotheses_created", 0),
            hypotheses_rejected=summary.get("hypotheses_rejected", 0),
            unsupported_records_included=0,
            error_count=summary.get("error_count", 0),
            dry_run=summary.get("dry_run", False),
        )
    except OSError:
        pass
