"""Structured evidence packet builders for connection discovery.

Packets are built from supported case records and deterministic memory data
only. This module never reads email body columns and never includes unsupported
case types in returned packet payloads.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import database as db
from constants import SUPPORTED_CASE_TYPES_SET

_SCOPE = {
    "supported_case_types_only": True,
    "unsupported_emails_excluded": True,
}
_CASE_ID_EVIDENCE_KEYS = {
    "supporting_case_ids",
    "related_case_ids",
    "case_ids",
    "contractor_no_response_case_ids",
}
_RAW_EMAIL_MARKERS = ("raw_body", "normalized_text")


def build_pattern_packets(
    run_id: str,
    max_cases_per_packet: int = 25,
    max_prompt_chars: int = 40000,
) -> List[Dict[str, Any]]:
    """Build one or more packets for each active deterministic pattern type."""
    grouped_rows: Dict[str, list] = defaultdict(list)
    for row in db.get_active_pattern_flags():
        row_dict = dict(row)
        pattern_type = row_dict.get("pattern_type")
        if pattern_type:
            grouped_rows[str(pattern_type)].append(row_dict)

    packets: List[Dict[str, Any]] = []
    for pattern_type, rows in sorted(grouped_rows.items()):
        safe_flags: list[dict] = []
        case_ids: set[str] = set()
        for row in rows:
            safe_flag, supported_ids = _safe_pattern_flag(row)
            if safe_flag is None:
                continue
            safe_flags.append(safe_flag)
            case_ids.update(supported_ids)

        cases = _summaries_for_case_ids(case_ids)
        if not cases:
            _log_packet_skipped(
                run_id=run_id,
                packet_type="pattern_flag",
                reason="pattern group has no supported cases",
                entity_type="pattern_type",
                entity_value=pattern_type,
            )
            continue

        base_packet = _base_packet(
            run_id=run_id,
            packet_type="pattern_flag",
            entity_type="pattern_type",
            entity_value=pattern_type,
            entity={
                "entity_type": "pattern_type",
                "pattern_type": pattern_type,
            },
        )
        base_packet["pattern_flags"] = safe_flags
        packets.extend(
            _finalize_case_split_packets(
                base_packet=base_packet,
                cases=cases,
                max_cases_per_packet=max_cases_per_packet,
                max_prompt_chars=max_prompt_chars,
                identity_key=pattern_type,
            )
        )

    _assert_packet_batch_safe(packets)
    return packets


def build_building_group_packets(
    run_id: str,
    max_cases_per_packet: int = 25,
    max_prompt_chars: int = 40000,
) -> List[Dict[str, Any]]:
    """Build packets for building_issue_groups using supported child cases only."""
    packets: List[Dict[str, Any]] = []
    for group in db.list_building_groups():
        group_id = group["group_id"]
        child_links = db.list_building_issue_group_cases({"group_id": group_id})
        case_ids = [
            link["case_id"]
            for link in child_links
            if link.get("status") in (None, "active", "closed")
        ]
        cases = _summaries_for_case_ids(case_ids)
        if not cases:
            _log_packet_skipped(
                run_id=run_id,
                packet_type="building_group",
                reason="building group has no supported child cases",
                entity_type="building_contractor_group",
                entity_value=group_id,
            )
            continue

        pattern_flags = []
        for case in cases:
            pattern_flags.extend(case.get("active_patterns", []))

        base_packet = _base_packet(
            run_id=run_id,
            packet_type="building_group",
            entity_type="building_contractor_group",
            entity_value=f"{group.get('building') or ''} | {group.get('contractor') or ''}".strip(),
            entity={
                "entity_type": "building_contractor_group",
                "group_id": group_id,
                "building": group.get("building"),
                "contractor": group.get("contractor"),
                "status": group.get("status"),
                "health_status": group.get("health_status"),
            },
        )
        base_packet["pattern_flags"] = _dedupe_pattern_summaries(pattern_flags)
        base_packet["communication_summary"] = _group_communication_summary(group)
        base_packet["manual_review_summary"] = _manual_review_summary(cases)
        packets.extend(
            _finalize_case_split_packets(
                base_packet=base_packet,
                cases=cases,
                max_cases_per_packet=max_cases_per_packet,
                max_prompt_chars=max_prompt_chars,
                identity_key=group_id,
            )
        )

    _assert_packet_batch_safe(packets)
    return packets


def build_all_supported_packets(
    run_id: str,
    packet_by: str = "entity",
    batch_size: int = 25,
    max_prompt_chars: int = 40000,
) -> List[Dict[str, Any]]:
    """Packetize every supported case by entity, building, contractor, device, or case type."""
    if packet_by not in {"building", "contractor", "device", "case-type", "entity"}:
        raise ValueError(
            "packet_by must be one of building, contractor, device, case-type, entity"
        )

    all_cases = db.get_supported_cases_for_discovery()
    grouped: Dict[Tuple[str, str], list] = defaultdict(list)
    for case in all_cases:
        case_dict = dict(case)
        entity_type, entity_value = _packet_group_for_case(case_dict, packet_by)
        grouped[(entity_type, entity_value)].append(case_dict["case_id"])

    packets: List[Dict[str, Any]] = []
    for (entity_type, entity_value), case_ids in sorted(grouped.items()):
        cases = _summaries_for_case_ids(case_ids)
        if not cases:
            continue
        base_packet = _base_packet(
            run_id=run_id,
            packet_type=_packet_type_for_entity(entity_type),
            entity_type=entity_type,
            entity_value=entity_value,
            entity={
                "entity_type": entity_type,
                "entity_value": entity_value,
                "packet_by": packet_by,
            },
        )
        base_packet["pattern_flags"] = _dedupe_pattern_summaries(
            pattern
            for case in cases
            for pattern in case.get("active_patterns", [])
        )
        packets.extend(
            _finalize_case_split_packets(
                base_packet=base_packet,
                cases=cases,
                max_cases_per_packet=batch_size,
                max_prompt_chars=max_prompt_chars,
                identity_key=f"{entity_type}:{entity_value}",
            )
        )

    _assert_packet_batch_safe(packets)
    return packets


def _base_packet(
    run_id: str,
    packet_type: str,
    entity_type: Optional[str],
    entity_value: Optional[str],
    entity: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "packet_id": "",
        "run_id": run_id,
        "packet_type": packet_type,
        "scope": dict(_SCOPE),
        "entity": entity,
        "entity_type": entity_type,
        "entity_value": entity_value,
        "cases": [],
        "pattern_flags": [],
        "observations": [],
        "known_links": [],
        "manual_review_summary": {},
        "communication_summary": {},
        "unsupported_records_included": 0,
    }


def _finalize_case_split_packets(
    base_packet: Dict[str, Any],
    cases: Sequence[Dict[str, Any]],
    max_cases_per_packet: int,
    max_prompt_chars: int,
    identity_key: str,
) -> List[Dict[str, Any]]:
    safe_max_cases = max(1, int(max_cases_per_packet))
    chunks = [list(cases[idx:idx + safe_max_cases]) for idx in range(0, len(cases), safe_max_cases)]
    finalized: List[Dict[str, Any]] = []
    for chunk in chunks:
        finalized.extend(
            _split_chunk_to_prompt_size(
                base_packet=base_packet,
                chunk=chunk,
                max_prompt_chars=max_prompt_chars,
                identity_key=identity_key,
            )
        )

    for index, packet in enumerate(finalized, start=1):
        packet["packet_id"] = _make_packet_id(
            run_id=packet["run_id"],
            packet_type=packet["packet_type"],
            identity_key=identity_key,
            index=index,
        )
        packet["case_count"] = len(packet.get("cases") or [])
        packet["pattern_count"] = len(packet.get("pattern_flags") or [])
        packet["observations"] = _packet_observations(packet.get("cases") or [])
        packet["known_links"] = _packet_known_links(packet.get("cases") or [])
        packet["unsupported_records_included"] = _count_unsupported_records(packet)
        if packet["unsupported_records_included"] != 0:
            raise AssertionError("Unsupported records reached discovery packet")

    return finalized


def _split_chunk_to_prompt_size(
    base_packet: Dict[str, Any],
    chunk: Sequence[Dict[str, Any]],
    max_prompt_chars: int,
    identity_key: str,
) -> List[Dict[str, Any]]:
    packet = _copy_packet_with_cases(base_packet, chunk)
    if _estimate_prompt_chars(packet) <= max_prompt_chars:
        return [packet]

    if len(chunk) <= 1:
        _log_packet_skipped(
            run_id=base_packet["run_id"],
            packet_type=base_packet["packet_type"],
            reason="single case packet exceeds max_prompt_chars",
            entity_type=base_packet.get("entity_type"),
            entity_value=base_packet.get("entity_value") or identity_key,
            estimated_chars=_estimate_prompt_chars(packet),
            max_prompt_chars=max_prompt_chars,
        )
        return []

    midpoint = max(1, len(chunk) // 2)
    return (
        _split_chunk_to_prompt_size(base_packet, chunk[:midpoint], max_prompt_chars, identity_key)
        + _split_chunk_to_prompt_size(base_packet, chunk[midpoint:], max_prompt_chars, identity_key)
    )


def _copy_packet_with_cases(
    base_packet: Dict[str, Any],
    cases: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    packet = dict(base_packet)
    packet["scope"] = dict(base_packet["scope"])
    packet["entity"] = dict(base_packet.get("entity") or {})
    packet["pattern_flags"] = list(base_packet.get("pattern_flags") or [])
    packet["cases"] = list(cases)
    packet["manual_review_summary"] = dict(base_packet.get("manual_review_summary") or {})
    packet["communication_summary"] = dict(base_packet.get("communication_summary") or {})
    packet["observations"] = []
    packet["known_links"] = []
    return packet


def _make_packet_id(run_id: str, packet_type: str, identity_key: str, index: int) -> str:
    digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"{run_id}|{packet_type}|{identity_key}|{index}",
    ).hex[:12]
    return f"{run_id}-{packet_type}-{digest}"


def _packet_group_for_case(case: Dict[str, Any], packet_by: str) -> Tuple[str, str]:
    if packet_by == "case-type":
        return "case_type", case["case_type"]
    if packet_by in {"building", "contractor", "device"}:
        return packet_by, _entity_value(case.get(packet_by))
    for entity_type in ("building", "contractor", "device"):
        value = _entity_value(case.get(entity_type), fallback="")
        if value:
            return entity_type, value
    return "entity", "unscoped"


def _packet_type_for_entity(entity_type: str) -> str:
    mapping = {
        "building": "building_entity",
        "contractor": "contractor_entity",
        "device": "device_entity",
        "case_type": "case_type_window",
    }
    return mapping.get(entity_type, "case_chunk")


def _entity_value(value: Any, fallback: str = "unknown") -> str:
    cleaned = str(value or "").strip()
    return cleaned or fallback


def _summaries_for_case_ids(case_ids: Iterable[str]) -> List[Dict[str, Any]]:
    summaries = []
    seen: set[str] = set()
    for case_id in sorted({str(case_id) for case_id in case_ids if case_id}):
        if case_id in seen:
            continue
        seen.add(case_id)
        case_row = db.get_case_by_id(case_id)
        if not case_row:
            continue
        case = dict(case_row)
        if case.get("case_type") not in SUPPORTED_CASE_TYPES_SET:
            continue
        summaries.append(_case_summary(case))
    return summaries


def _case_summary(case: Dict[str, Any]) -> Dict[str, Any]:
    case_id = case["case_id"]
    fields = {
        row["field_name"]: _truncate(row["field_value"], 1000)
        for row in db.get_fields_for_case(case_id)
        if row["field_value"]
    }
    recent_event_types = [
        row["event_type"]
        for row in db.get_events_for_case(case_id)
    ][-10:]
    patterns = []
    for row in db.get_active_pattern_flags_for_case(case_id):
        pattern = dict(row)
        patterns.append(
            {
                "pattern_id": pattern.get("id"),
                "case_id": case_id,
                "pattern_type": pattern.get("pattern_type"),
                "severity": pattern.get("severity"),
                "summary": pattern.get("summary"),
                "created_at": pattern.get("created_at"),
            }
        )
    manual_reviews = [
        {
            "review_category": row.get("review_category"),
            "reason": row.get("reason"),
            "blocking": row.get("blocking"),
        }
        for row in db.get_manual_reviews_for_case(case_id)
    ]
    return {
        "case_id": case_id,
        "case_type": case["case_type"],
        "status": case["status"],
        "building": case.get("building"),
        "device": case.get("device"),
        "contractor": case.get("contractor"),
        "due_date": case.get("due_date"),
        "period": case.get("period"),
        "created_at": case.get("created_at"),
        "updated_at": case.get("updated_at"),
        "fields": fields,
        "recent_event_types": recent_event_types,
        "active_patterns": patterns,
        "manual_reviews": manual_reviews,
    }


def _safe_pattern_flag(row: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], set[str]]:
    row_case_id = row.get("case_id")
    if row_case_id:
        case = db.get_case_by_id(str(row_case_id))
        if not case or case["case_type"] not in SUPPORTED_CASE_TYPES_SET:
            return None, set()

    evidence = _safe_pattern_evidence(row.get("evidence_json"))
    supported_ids = _supported_case_ids_from_evidence(evidence)
    if row_case_id:
        supported_ids.add(str(row_case_id))

    return (
        {
            "pattern_id": row.get("id"),
            "case_id": row_case_id if row_case_id in supported_ids else None,
            "pattern_type": row.get("pattern_type"),
            "severity": row.get("severity"),
            "summary": row.get("summary"),
            "created_at": row.get("created_at"),
            "evidence": evidence,
        },
        supported_ids,
    )


def _safe_pattern_evidence(raw_evidence: Optional[str]) -> Dict[str, Any]:
    if not raw_evidence:
        return {}
    try:
        parsed = json.loads(raw_evidence)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    safe: Dict[str, Any] = {}
    for key, value in parsed.items():
        if key in _CASE_ID_EVIDENCE_KEYS:
            safe[key] = sorted(_filter_supported_case_ids(_as_list(value)))
        elif key.endswith("_ids") and isinstance(value, list):
            safe[key] = [
                item
                for item in value
                if isinstance(item, (str, int, float)) and "unsupported" not in str(item).lower()
            ][:50]
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = _truncate(value, 500) if isinstance(value, str) else value
        elif isinstance(value, list):
            safe[key] = [
                _truncate(item, 200) if isinstance(item, str) else item
                for item in value
                if isinstance(item, (str, int, float, bool))
            ][:50]
    return safe


def _supported_case_ids_from_evidence(evidence: Dict[str, Any]) -> set[str]:
    case_ids: set[str] = set()
    for key in _CASE_ID_EVIDENCE_KEYS:
        case_ids.update(_filter_supported_case_ids(_as_list(evidence.get(key))))
    return case_ids


def _filter_supported_case_ids(case_ids: Iterable[Any]) -> set[str]:
    supported = set()
    for raw_case_id in case_ids:
        case_id = str(raw_case_id)
        case = db.get_case_by_id(case_id)
        if case and case["case_type"] in SUPPORTED_CASE_TYPES_SET:
            supported.add(case_id)
    return supported


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _packet_observations(cases: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    observations: list[dict] = []
    for case in cases:
        for row in db.get_observations_for_case(case["case_id"], limit=5):
            item = dict(row)
            observations.append(
                {
                    "case_id": item.get("case_id"),
                    "observation_type": item.get("observation_type"),
                    "entity_type": item.get("entity_type"),
                    "entity_value": item.get("entity_value"),
                    "observed_at": item.get("observed_at"),
                    "source": item.get("source"),
                    "confidence": item.get("confidence"),
                }
            )
    return observations[:50]


def _packet_known_links(cases: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    links: list[dict] = []
    packet_case_ids = {case["case_id"] for case in cases}
    for case in cases:
        for row in db.get_related_cases_for_case(case["case_id"], limit=10):
            linked = dict(row)
            linked_case_id = linked.get("case_id")
            linked_case = db.get_case_by_id(linked_case_id) if linked_case_id else None
            if not linked_case or linked_case["case_type"] not in SUPPORTED_CASE_TYPES_SET:
                continue
            if linked_case_id not in packet_case_ids:
                continue
            links.append(
                {
                    "source_case_id": case["case_id"],
                    "target_case_id": linked_case_id,
                    "target_case_type": linked.get("case_type"),
                    "link_type": linked.get("link_type"),
                    "confidence": linked.get("confidence"),
                }
            )
    return links[:50]


def _group_communication_summary(group: Dict[str, Any]) -> Dict[str, Any]:
    queue_rows = db.list_communication_queue(group_id=group["group_id"], limit=10)
    return {
        "last_email_sent_at": group.get("last_email_sent_at"),
        "next_email_allowed_at": group.get("next_email_allowed_at"),
        "last_response_at": group.get("last_response_at"),
        "queue": [
            {
                "queue_type": row["queue_type"],
                "status": row["status"],
                "reason": row["reason"],
                "created_at": row["created_at"],
            }
            for row in queue_rows
        ],
    }


def _manual_review_summary(cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    categories: Dict[str, int] = defaultdict(int)
    blocking = 0
    for case in cases:
        for review in case.get("manual_reviews") or []:
            category = review.get("review_category") or "uncategorized"
            categories[category] += 1
            if review.get("blocking"):
                blocking += 1
    return {
        "open_review_categories": dict(sorted(categories.items())),
        "blocking_review_count": blocking,
    }


def _dedupe_pattern_summaries(patterns: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[tuple] = set()
    result = []
    for pattern in patterns:
        key = (
            pattern.get("pattern_id"),
            pattern.get("case_id"),
            pattern.get("pattern_type"),
            pattern.get("summary"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(pattern))
    return result[:50]


def _estimate_prompt_chars(packet: Dict[str, Any]) -> int:
    return len(json.dumps(packet, sort_keys=True, default=str))


def _count_unsupported_records(packet: Dict[str, Any]) -> int:
    unsupported = 0
    for case in packet.get("cases") or []:
        if case.get("case_type") not in SUPPORTED_CASE_TYPES_SET:
            unsupported += 1
    # Scan only JSON keys (not values) to avoid false positives from field data
    # that legitimately contains the marker strings as substrings.
    all_keys = re.findall(r'"([^"]+)"\s*:', json.dumps(packet, sort_keys=True, default=str))
    for marker in _RAW_EMAIL_MARKERS:
        if marker in all_keys:
            unsupported += 1
    return unsupported


def _assert_packet_batch_safe(packets: Sequence[Dict[str, Any]]) -> None:
    for packet in packets:
        if packet.get("unsupported_records_included") != 0:
            raise AssertionError("unsupported_records_included must be 0")
        if packet.get("scope") != _SCOPE:
            raise AssertionError("packet scope is missing the supported-only guard")


def _truncate(value: Any, limit: int) -> Any:
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[:limit] + "...[truncated]"


def _log_packet_skipped(
    run_id: str,
    packet_type: str,
    reason: str,
    entity_type: Optional[str] = None,
    entity_value: Optional[str] = None,
    estimated_chars: Optional[int] = None,
    max_prompt_chars: Optional[int] = None,
) -> None:
    from observability import append_structured_event

    try:
        append_structured_event(
            component="discovery_packets",
            event_name="packet_skipped",
            status="skipped",
            run_id=run_id,
            packet_type=packet_type,
            reason=reason,
            entity_type=entity_type,
            entity_value=entity_value,
            estimated_chars=estimated_chars,
            max_prompt_chars=max_prompt_chars,
            unsupported_records_included=0,
        )
    except OSError:
        pass
