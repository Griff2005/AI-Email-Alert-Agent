"""
memory.py - Deterministic entity memory and pattern detection for demo cases.

The memory layer stores normalized entities, structured observations, case
links, and active pattern flags. It never uses Claude to decide whether a
pattern exists. Model output can contribute extracted facts, but recurrence and
escalation decisions are made entirely from database state and hard-coded
rules.
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import database as db
from time_utils import utc_now_iso, utc_now_naive

HIGH_PRIORITY_OBSERVATIONS = (
    "major_work_overdue",
    "government_directive",
    "maintenance_hours_shortfall",
)

PATTERN_SEVERITY_ORDER = {
    "info": 1,
    "medium": 2,
    "high": 3,
    "review": 4,
}

MECHANIC_PATTERNS = {"mechanic_recurrence", "mechanic_rotation"}

_MONTH_NAME_DATE_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?\b",
    re.IGNORECASE,
)
_SLASH_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MECHANIC_PATTERNS = (
    re.compile(r"\b(?:mechanic|technician)\s*:\s*([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3})"),
    re.compile(r"\bassigned\s+(?:mechanic|technician)\s+([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3})"),
)


def normalize_text(value: str) -> str:
    """Normalize free text for deterministic entity matching."""
    if not value:
        return ""
    normalized = value.lower().strip().replace("&", " and ")
    normalized = re.sub(r"[^\w\s#/\-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _now_iso() -> str:
    """Return the current UTC timestamp in the existing database format."""
    return utc_now_iso()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _json_dumps(value: Optional[Dict[str, Any]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _highest_severity(values: Sequence[str]) -> str:
    if not values:
        return "info"
    return max(values, key=lambda value: PATTERN_SEVERITY_ORDER.get(value, 0))


def _extract_date_like_value(text: str) -> Optional[str]:
    for pattern in (_ISO_DATE_RE, _SLASH_DATE_RE, _MONTH_NAME_DATE_RE):
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _get_observed_at(case_id: Optional[str], email_id: Optional[str], observed_at: Optional[str]) -> str:
    if observed_at:
        return observed_at
    if email_id:
        email_row = db.get_email_by_id(email_id)
        if email_row and email_row["received_at"]:
            return str(email_row["received_at"])
    if case_id:
        case_row = db.get_case_by_id(case_id)
        if case_row and case_row["updated_at"]:
            return str(case_row["updated_at"])
    return _now_iso()


def _case_anchor(case_id: str) -> datetime:
    case = db.get_case_by_id(case_id)
    anchors: List[datetime] = []
    if case:
        for value in (case["created_at"], case["updated_at"]):
            parsed = _parse_iso(value)
            if parsed:
                anchors.append(parsed)
    conn = db.get_connection()
    row = conn.execute(
        """
        SELECT MAX(COALESCE(observed_at, created_at)) AS anchor
        FROM observations
        WHERE case_id = ?
        """,
        (case_id,),
    ).fetchone()
    parsed_anchor = _parse_iso(row["anchor"] if row else None)
    if parsed_anchor:
        anchors.append(parsed_anchor)
    return max(anchors) if anchors else utc_now_naive()


def _cases_between(anchor: datetime, days: int) -> List[Dict[str, Any]]:
    conn = db.get_connection()
    lower = (anchor - timedelta(days=days)).isoformat()
    upper = anchor.isoformat()
    rows = conn.execute(
        """
        SELECT *
        FROM cases
        WHERE created_at >= ? AND created_at <= ?
        ORDER BY created_at DESC
        """,
        (lower, upper),
    ).fetchall()
    return [dict(row) for row in rows]


def _observations_between(anchor: datetime, days: int, observation_types: Sequence[str]) -> List[Dict[str, Any]]:
    if not observation_types:
        return []
    conn = db.get_connection()
    placeholders = ", ".join("?" for _ in observation_types)
    lower = (anchor - timedelta(days=days)).isoformat()
    upper = anchor.isoformat()
    rows = conn.execute(
        f"""
        SELECT
            o.*,
            c.case_type,
            c.building,
            c.device,
            c.contractor,
            c.status AS case_status
        FROM observations o
        LEFT JOIN cases c ON c.case_id = o.case_id
        WHERE o.observation_type IN ({placeholders})
          AND COALESCE(o.observed_at, o.created_at) >= ?
          AND COALESCE(o.observed_at, o.created_at) <= ?
        ORDER BY COALESCE(o.observed_at, o.created_at) DESC, o.id DESC
        """,
        (*observation_types, lower, upper),
    ).fetchall()
    return [dict(row) for row in rows]


def _case_matches(case_row: Dict[str, Any], field_name: str, target_value: Optional[str]) -> bool:
    if not target_value:
        return False
    return normalize_text(case_row.get(field_name) or "") == normalize_text(target_value)


def _link_related_cases(current_case: Dict[str, Any], cases: Sequence[Dict[str, Any]]) -> None:
    case_id = current_case["case_id"]
    building = current_case.get("building")
    device = current_case.get("device")
    contractor = current_case.get("contractor")
    current_work_items = _get_case_work_items(case_id)

    for other in cases:
        other_id = other["case_id"]
        if other_id == case_id:
            continue
        if building and _case_matches(other, "building", building):
            db.insert_case_link_record(
                source_case_id=case_id,
                target_case_id=other_id,
                link_type="same_building",
                reason=f"Shared building: {building}",
                confidence=1.0,
            )
        if device and _case_matches(other, "device", device):
            db.insert_case_link_record(
                source_case_id=case_id,
                target_case_id=other_id,
                link_type="same_device",
                reason=f"Shared device: {device}",
                confidence=1.0,
            )
        if contractor and _case_matches(other, "contractor", contractor):
            db.insert_case_link_record(
                source_case_id=case_id,
                target_case_id=other_id,
                link_type="same_contractor",
                reason=f"Shared contractor: {contractor}",
                confidence=0.9,
            )
        if current_case["case_type"] == other.get("case_type"):
            if (building and _case_matches(other, "building", building)) or (
                device and _case_matches(other, "device", device)
            ):
                db.insert_case_link_record(
                    source_case_id=case_id,
                    target_case_id=other_id,
                    link_type="repeated_issue",
                    reason=f"Shared case type {current_case['case_type']} with overlapping location or device.",
                    confidence=0.8,
                )
        other_work_items = _get_case_work_items(other_id)
        if current_work_items and other_work_items and current_work_items.intersection(other_work_items):
            db.insert_case_link_record(
                source_case_id=case_id,
                target_case_id=other_id,
                link_type="related_work",
                reason="Shared work item reference.",
                confidence=0.8,
            )


def _build_pattern(
    case_id: str,
    pattern_type: str,
    severity: str,
    summary: str,
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    saved = db.upsert_pattern_flag_record(
        case_id=case_id,
        pattern_type=pattern_type,
        severity=severity,
        summary=summary,
        evidence_json=_json_dumps(evidence),
        status="active",
    )
    saved["summary"] = summary
    saved["evidence"] = evidence
    return saved


def _get_case_work_items(case_id: str) -> set:
    work_items = set()
    for row in db.get_observations_for_case(case_id):
        if row["observation_type"] == "work_item_seen" and row["entity_value"]:
            work_items.add(str(row["entity_value"]))
    return work_items


def _pattern_case_ids(rows: Sequence[Dict[str, Any]], case_ids: Optional[set] = None) -> List[str]:
    deduped: List[str] = []
    seen = case_ids or set()
    for row in rows:
        case_id = row.get("case_id")
        if case_id and case_id not in seen:
            deduped.append(case_id)
            seen.add(case_id)
    return deduped


def _pattern_observation_ids(rows: Sequence[Dict[str, Any]]) -> List[int]:
    deduped: List[int] = []
    seen = set()
    for row in rows:
        observation_id = row.get("id")
        if observation_id is None or observation_id in seen:
            continue
        deduped.append(int(observation_id))
        seen.add(observation_id)
    return deduped


def _pattern_evidence(
    rule: str,
    entity_type: str,
    entity_value: Optional[str],
    timeframe_days: int,
    threshold: int,
    observed_count: int,
    supporting_rows: Sequence[Dict[str, Any]],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    supporting_case_ids = _pattern_case_ids(supporting_rows)
    evidence = {
        "rule": rule,
        "entity_type": entity_type,
        "entity_value": entity_value,
        "time_window_days": timeframe_days,
        "threshold": threshold,
        "observed_count": observed_count,
        "supporting_case_ids": supporting_case_ids,
        "supporting_observation_ids": _pattern_observation_ids(supporting_rows),
        "related_case_ids": supporting_case_ids,
    }
    if extra:
        evidence.update(extra)
    return evidence


def upsert_entity(
    entity_type: str,
    name: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
    confidence: float = 1.0,
) -> Optional[int]:
    """Insert or refresh a canonical entity and return its ID."""
    if not name or not str(name).strip():
        return None
    canonical_name = str(name).strip()
    normalized_name = normalize_text(canonical_name)
    if not normalized_name:
        return None

    now = _now_iso()
    existing = db.get_entity_by_normalized_name(entity_type, normalized_name)
    existing_name = str(existing["canonical_name"]) if existing else None
    entity_id = db.upsert_entity_record(
        entity_type=entity_type,
        canonical_name=canonical_name,
        normalized_name=normalized_name,
        metadata_json=_json_dumps(metadata),
        seen_at=now,
    )
    if existing_name and existing_name != canonical_name:
        db.upsert_entity_alias_record(
            entity_id=entity_id,
            alias=canonical_name,
            normalized_alias=normalized_name,
            source=source,
            confidence=confidence,
            seen_at=now,
        )
    return entity_id


def add_observation(
    case_id: Optional[str] = None,
    email_id: Optional[str] = None,
    observation_type: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_value: Optional[str] = None,
    value_text: Optional[str] = None,
    value_json: Optional[Dict[str, Any]] = None,
    observed_at: Optional[str] = None,
    source: str = "system",
    confidence: float = 1.0,
) -> Optional[int]:
    """Insert an observation, upserting its entity first when applicable."""
    if not observation_type:
        return None
    observed_at = _get_observed_at(case_id=case_id, email_id=email_id, observed_at=observed_at)
    entity_id = None
    if entity_type and entity_value:
        entity_id = upsert_entity(
            entity_type=entity_type,
            name=entity_value,
            metadata={"source": source},
            source=source,
            confidence=confidence,
        )
    return db.insert_observation_record(
        case_id=case_id,
        email_id=email_id,
        entity_id=entity_id,
        observation_type=observation_type,
        entity_type=entity_type,
        entity_value=entity_value,
        value_text=value_text,
        value_json=_json_dumps(value_json),
        observed_at=observed_at,
        source=source,
        confidence=confidence,
    )


def record_case_observations(
    case_id: str,
    email_id: Optional[str],
    case_type: str,
    fields: Dict[str, Any],
    source: str = "inbound_email",
) -> None:
    """Record structured memory facts from extracted case fields."""
    observed_at = _get_observed_at(case_id=case_id, email_id=email_id, observed_at=None)
    safe_fields = {key: value for key, value in fields.items() if value not in (None, "")}

    add_observation(
        case_id=case_id,
        email_id=email_id,
        observation_type="case_seen",
        value_text=case_type,
        value_json=safe_fields,
        observed_at=observed_at,
        source=source,
    )
    add_observation(
        case_id=case_id,
        email_id=email_id,
        observation_type="issue_seen",
        entity_type="issue_type",
        entity_value=case_type,
        value_text=case_type,
        observed_at=observed_at,
        source=source,
    )

    if fields.get("building"):
        add_observation(
            case_id=case_id,
            email_id=email_id,
            observation_type="building_seen",
            entity_type="building",
            entity_value=fields["building"],
            observed_at=observed_at,
            source=source,
        )
    if fields.get("device"):
        add_observation(
            case_id=case_id,
            email_id=email_id,
            observation_type="device_seen",
            entity_type="device",
            entity_value=fields["device"],
            observed_at=observed_at,
            source=source,
        )
    if fields.get("contractor"):
        add_observation(
            case_id=case_id,
            email_id=email_id,
            observation_type="contractor_seen",
            entity_type="contractor",
            entity_value=fields["contractor"],
            observed_at=observed_at,
            source=source,
        )

    mechanic_name = fields.get("mechanic") or fields.get("technician")
    if mechanic_name:
        add_observation(
            case_id=case_id,
            email_id=email_id,
            observation_type="mechanic_seen",
            entity_type="mechanic",
            entity_value=mechanic_name,
            value_json={"role": "mechanic" if fields.get("mechanic") else "technician"},
            observed_at=observed_at,
            source=source,
        )

    if fields.get("work_item"):
        add_observation(
            case_id=case_id,
            email_id=email_id,
            observation_type="work_item_seen",
            entity_type="work_item",
            entity_value=fields["work_item"],
            observed_at=observed_at,
            source=source,
        )

    if fields.get("scheduled_date"):
        add_observation(
            case_id=case_id,
            email_id=email_id,
            observation_type="scheduled_date_provided",
            value_text=fields["scheduled_date"],
            observed_at=observed_at,
            source=source,
        )

    case_specific_type = {
        "CAT1_COMPLIANCE": "cat_compliance_reminder",
        "CAT5_COMPLIANCE": "cat_compliance_reminder",
        "DATA_ABSENCE": "data_absence",
        "MAINTENANCE_HOURS_SHORTFALL": "maintenance_hours_shortfall",
        "MAJOR_WORK_OVERDUE": "major_work_overdue",
        "GOVERNMENT_DIRECTIVE": "government_directive",
    }.get(case_type)
    if case_specific_type:
        add_observation(
            case_id=case_id,
            email_id=email_id,
            observation_type=case_specific_type,
            value_text=fields.get("description"),
            value_json=safe_fields,
            observed_at=observed_at,
            source=source,
        )


def record_reply_observations(case_id: str, reply_text: str, analysis: Optional[Dict[str, Any]] = None) -> None:
    """Record deterministic reply-derived observations without changing case status."""
    if not reply_text or not reply_text.strip():
        return
    case = db.get_case_by_id(case_id)
    if not case:
        return

    observed_at = _now_iso()
    contractor = case["contractor"]
    add_observation(
        case_id=case_id,
        observation_type="contractor_response_received",
        entity_type="contractor" if contractor else None,
        entity_value=contractor,
        value_text=reply_text[:240],
        observed_at=observed_at,
        source="reply",
    )

    seen_mechanics = set()
    for pattern in _MECHANIC_PATTERNS:
        for match in pattern.finditer(reply_text):
            mechanic_name = match.group(1).strip()
            normalized = normalize_text(mechanic_name)
            if normalized in seen_mechanics:
                continue
            seen_mechanics.add(normalized)
            add_observation(
                case_id=case_id,
                observation_type="mechanic_seen",
                entity_type="mechanic",
                entity_value=mechanic_name,
                value_json={"source_phrase": match.group(0)},
                observed_at=observed_at,
                source="reply",
            )

    lowered = reply_text.lower()
    negative_completion = re.search(r"\bnot\s+(?:completed|finished|resolved|repaired|done|closed)\b", lowered)
    completion_claimed = bool(analysis and analysis.get("satisfies_action"))
    if not completion_claimed and not negative_completion:
        completion_claimed = bool(
            re.search(r"\b(completed|finished|resolved|closed|done|repaired)\b", lowered)
        )
    if completion_claimed:
        add_observation(
            case_id=case_id,
            observation_type="completion_claimed",
            value_text=reply_text[:240],
            observed_at=observed_at,
            source="reply",
        )
        add_observation(
            case_id=case_id,
            observation_type="evidence_pending",
            value_text="Completion claim requires manual confirmation.",
            observed_at=observed_at,
            source="reply",
        )

    date_value = _extract_date_like_value(reply_text)
    if date_value:
        if re.search(r"\b(rescheduled|revised|moved to|changed to)\b", lowered):
            add_observation(
                case_id=case_id,
                observation_type="revised_date_provided",
                value_text=date_value,
                observed_at=observed_at,
                source="reply",
            )
        elif re.search(r"\b(schedule|scheduled|booked|planned|visit)\b", lowered):
            add_observation(
                case_id=case_id,
                observation_type="scheduled_date_provided",
                value_text=date_value,
                observed_at=observed_at,
                source="reply",
            )

    if analysis and analysis.get("flag_for_review"):
        add_observation(
            case_id=case_id,
            observation_type="manual_review_required",
            value_text=str(analysis.get("summary") or "Reply requires manual review."),
            observed_at=observed_at,
            source="reply",
        )


def record_no_response(case_id: str) -> None:
    """Record a missed response / follow-up escalation observation."""
    case = db.get_case_by_id(case_id)
    followup = db.get_followup_for_case(case_id)
    if not case:
        return
    add_observation(
        case_id=case_id,
        observation_type="no_response_followup",
        entity_type="contractor" if case["contractor"] else None,
        entity_value=case["contractor"],
        value_json={
            "follow_count": int(followup["follow_count"]) if followup else 0,
            "deadline": followup["deadline"] if followup else None,
        },
        observed_at=_now_iso(),
        source="followup",
    )


def detect_patterns_for_case(case_id: str) -> List[Dict[str, Any]]:
    """Run deterministic recurrence rules for a case and persist active flags.

    Evaluates up to nine pattern types against recent cases and observations,
    then upserts each detected flag via ``db.upsert_pattern_flag_record()``.

    Pattern types (all deterministic — no AI):

    1. ``repeated_building_issue`` — building has ≥ 3 cases in the last 60 days.
    2. ``repeated_device_issue`` — device has ≥ 2 cases in the last 90 days.
    3. ``repeated_contractor_issue`` — contractor has ≥ 3 cases, or ≥ 2
       high-priority cases, in the last 60 days.
    4. ``repeated_no_response`` — follow_count ≥ 2 or ≥ 3 no-response
       observations in the last 90 days.
    5. ``repeated_data_absence`` — ≥ 2 data-absence cases for the same
       building/device in the last 30 days.
    6. ``repeated_major_work_overdue`` — ≥ 2 major-work-overdue cases for the
       same building/contractor in the last 90 days.
    7. ``repeated_maintenance_shortfall`` — ≥ 2 maintenance-shortfall cases for
       the same building in 120 days, or ≥ 3 contractor buildings in 90 days.
    8. ``mechanic_recurrence`` — the same mechanic appears across ≥ 2 cases
       for the same device in the last 90 days.
    9. ``mechanic_rotation`` — ≥ 2 distinct mechanics appear across related
       records for the same device in the last 90 days.

    Args:
        case_id: UUID of the case to evaluate.

    Returns:
        List of pattern record dicts returned by
        ``db.upsert_pattern_flag_record()``, one per active pattern.
        Returns ``[]`` if the case is not found or no patterns are triggered.
    """
    case = db.get_case_by_id(case_id)
    if not case:
        return []

    case_dict = dict(case)
    anchor = _case_anchor(case_id)
    recent_cases_180 = _cases_between(anchor, 180)
    _link_related_cases(case_dict, recent_cases_180)

    detected: List[Dict[str, Any]] = []
    active_types: List[str] = []

    building = case_dict.get("building")
    device = case_dict.get("device")
    contractor = case_dict.get("contractor")
    building_norm = normalize_text(building or "")
    device_norm = normalize_text(device or "")
    contractor_norm = normalize_text(contractor or "")

    cases_60 = _cases_between(anchor, 60)
    cases_90 = _cases_between(anchor, 90)
    cases_120 = _cases_between(anchor, 120)

    if building:
        building_cases_60 = [
            row for row in cases_60 if _case_matches(row, "building", building)
        ]
        building_case_ids = _pattern_case_ids(building_cases_60)
        building_observation_rows = [
            row
            for row in _observations_between(anchor, 60, ["building_seen"])
            if normalize_text(row.get("entity_value") or row.get("building") or "") == building_norm
        ]
        if len(building_case_ids) >= 3:
            severity = "high" if len(building_case_ids) >= 5 else "medium"
            pattern = _build_pattern(
                case_id=case_id,
                pattern_type="repeated_building_issue",
                severity=severity,
                summary=(
                    f"Recurring building-linked issues detected for {building} across "
                    f"{len(building_case_ids)} cases in the last 60 days."
                ),
                evidence=_pattern_evidence(
                    rule="repeated_building_issue",
                    entity_type="building",
                    entity_value=building,
                    timeframe_days=60,
                    threshold=3,
                    observed_count=len(building_case_ids),
                    supporting_rows=building_observation_rows,
                    extra={
                        "count": len(building_case_ids),
                        "building": building,
                        "timeframe_days": 60,
                        "related_case_ids": building_case_ids,
                    },
                ),
            )
            detected.append(pattern)
            active_types.append(pattern["pattern_type"])

    if device:
        device_cases_90 = [
            row for row in cases_90 if _case_matches(row, "device", device)
        ]
        device_case_ids = _pattern_case_ids(device_cases_90)
        device_observation_rows = [
            row
            for row in _observations_between(anchor, 90, ["device_seen"])
            if normalize_text(row.get("entity_value") or row.get("device") or "") == device_norm
        ]
        if len(device_case_ids) >= 2:
            severity = "high" if len(device_case_ids) >= 3 else "medium"
            pattern = _build_pattern(
                case_id=case_id,
                pattern_type="repeated_device_issue",
                severity=severity,
                summary=(
                    f"Recurring device-linked issues detected for {device} across "
                    f"{len(device_case_ids)} cases in the last 90 days."
                ),
                evidence=_pattern_evidence(
                    rule="repeated_device_issue",
                    entity_type="device",
                    entity_value=device,
                    timeframe_days=90,
                    threshold=2,
                    observed_count=len(device_case_ids),
                    supporting_rows=device_observation_rows,
                    extra={
                        "count": len(device_case_ids),
                        "device": device,
                        "building": building,
                        "timeframe_days": 90,
                        "related_case_ids": device_case_ids,
                    },
                ),
            )
            detected.append(pattern)
            active_types.append(pattern["pattern_type"])

    if contractor:
        contractor_cases_60 = [
            row for row in cases_60 if _case_matches(row, "contractor", contractor)
        ]
        contractor_case_ids = _pattern_case_ids(contractor_cases_60)
        high_issue_rows = _observations_between(anchor, 60, HIGH_PRIORITY_OBSERVATIONS)
        contractor_high_issue_rows = [
            row
            for row in high_issue_rows
            if normalize_text(row.get("contractor") or "") == contractor_norm
        ]
        high_issue_case_ids = _pattern_case_ids(contractor_high_issue_rows)
        contractor_observation_rows = [
            row
            for row in _observations_between(anchor, 60, ["contractor_seen"])
            if normalize_text(row.get("entity_value") or row.get("contractor") or "") == contractor_norm
        ]
        contractor_support_rows = [
            row
            for row in (contractor_observation_rows + contractor_high_issue_rows)
            if row.get("case_id")
            and normalize_text(
                (str(db.get_case_by_id(str(row["case_id"]))["contractor"]) if db.get_case_by_id(str(row["case_id"])) and db.get_case_by_id(str(row["case_id"]))["contractor"] else "")
            ) == contractor_norm
        ]
        contractor_support_case_ids = sorted(
            {
                case_id
                for case_id in set(contractor_case_ids + high_issue_case_ids)
                if normalize_text(
                    (str(db.get_case_by_id(case_id)["contractor"]) if db.get_case_by_id(case_id) and db.get_case_by_id(case_id)["contractor"] else "")
                ) == contractor_norm
            }
        )
        if len(contractor_case_ids) >= 3 or len(high_issue_case_ids) >= 2:
            severity = "high" if len(contractor_case_ids) >= 5 or len(high_issue_case_ids) >= 3 else "medium"
            pattern = _build_pattern(
                case_id=case_id,
                pattern_type="repeated_contractor_issue",
                severity=severity,
                summary=(
                    f"Recurring contractor-linked cases detected for {contractor} based on "
                    f"{len(contractor_case_ids)} cases and {len(high_issue_case_ids)} high-priority "
                    f"issue records in the last 60 days."
                ),
                evidence=_pattern_evidence(
                    rule="repeated_contractor_issue",
                    entity_type="contractor",
                    entity_value=contractor,
                    timeframe_days=60,
                    threshold=3,
                    observed_count=max(len(contractor_case_ids), len(high_issue_case_ids)),
                    supporting_rows=contractor_support_rows,
                    extra={
                        "contractor": contractor,
                        "case_count_60_days": len(contractor_case_ids),
                        "high_priority_issue_count_60_days": len(high_issue_case_ids),
                        "related_case_ids": sorted(set(contractor_case_ids + high_issue_case_ids)),
                        "supporting_case_ids": contractor_support_case_ids,
                        "supporting_observation_ids": _pattern_observation_ids(contractor_support_rows),
                        "timeframe_days": 60,
                    },
                ),
            )
            detected.append(pattern)
            active_types.append(pattern["pattern_type"])

    followup = db.get_followup_for_case(case_id)
    no_response_rows = _observations_between(anchor, 90, ["no_response_followup"])
    contractor_no_response_rows = [
        row
        for row in no_response_rows
        if (contractor and normalize_text(row.get("contractor") or "") == contractor_norm)
        or row.get("case_id") == case_id
    ]
    contractor_no_response_case_ids = _pattern_case_ids(contractor_no_response_rows)
    current_follow_count = int(followup["follow_count"]) if followup else 0
    if current_follow_count >= 2 or len(contractor_no_response_case_ids) >= 3:
        severity = "high" if current_follow_count >= 3 else "medium"
        case_specific_no_response_rows = [
            row for row in no_response_rows if row.get("case_id") == case_id
        ]
        no_response_support_rows = contractor_no_response_rows or case_specific_no_response_rows
        no_response_support_case_ids = contractor_no_response_case_ids or ([case_id] if current_follow_count >= 2 else [])
        pattern = _build_pattern(
            case_id=case_id,
            pattern_type="repeated_no_response",
            severity=severity,
            summary=(
                "Repeated no-response behavior detected based on follow-up history for "
                f"this case{f' and contractor {contractor}' if contractor else ''}."
            ),
            evidence=_pattern_evidence(
                rule="repeated_no_response",
                entity_type="contractor" if contractor else "case",
                entity_value=contractor or case_id,
                timeframe_days=90,
                threshold=2,
                observed_count=max(current_follow_count, len(contractor_no_response_case_ids)),
                supporting_rows=no_response_support_rows,
                extra={
                    "follow_count": current_follow_count,
                    "contractor": contractor,
                    "contractor_no_response_case_ids": contractor_no_response_case_ids,
                    "supporting_case_ids": no_response_support_case_ids,
                    "supporting_observation_ids": _pattern_observation_ids(no_response_support_rows),
                    "timeframe_days": 90,
                },
            ),
        )
        detected.append(pattern)
        active_types.append(pattern["pattern_type"])

    data_absence_rows = _observations_between(anchor, 30, ["data_absence"])
    building_data_absence_case_ids = _pattern_case_ids(
        [row for row in data_absence_rows if building and normalize_text(row.get("building") or "") == building_norm]
    )
    device_data_absence_case_ids = _pattern_case_ids(
        [row for row in data_absence_rows if device and normalize_text(row.get("device") or "") == device_norm]
    )
    data_absence_case_ids = sorted(set(building_data_absence_case_ids + device_data_absence_case_ids))
    data_absence_support_rows = [
        row
        for row in data_absence_rows
        if row.get("case_id") in data_absence_case_ids
    ]
    if len(data_absence_case_ids) >= 2:
        pattern = _build_pattern(
            case_id=case_id,
            pattern_type="repeated_data_absence",
            severity="medium",
            summary=(
                f"Repeated data absence detected for {building or device} across "
                f"{len(data_absence_case_ids)} cases in the last 30 days."
            ),
            evidence=_pattern_evidence(
                rule="repeated_data_absence",
                entity_type="building" if building else "device",
                entity_value=building or device,
                timeframe_days=30,
                threshold=2,
                observed_count=len(data_absence_case_ids),
                supporting_rows=data_absence_support_rows,
                extra={
                    "building": building,
                    "device": device,
                    "count": len(data_absence_case_ids),
                    "timeframe_days": 30,
                    "related_case_ids": data_absence_case_ids,
                },
            ),
        )
        detected.append(pattern)
        active_types.append(pattern["pattern_type"])

    major_work_rows = _observations_between(anchor, 90, ["major_work_overdue"])
    major_work_case_ids = _pattern_case_ids(
        [
            row
            for row in major_work_rows
            if (building and normalize_text(row.get("building") or "") == building_norm)
            or (contractor and normalize_text(row.get("contractor") or "") == contractor_norm)
        ]
    )
    major_work_support_rows = [
        row
        for row in major_work_rows
        if row.get("case_id") in major_work_case_ids
    ]
    if len(major_work_case_ids) >= 2:
        severity = "high" if len(major_work_case_ids) >= 3 else "medium"
        pattern = _build_pattern(
            case_id=case_id,
            pattern_type="repeated_major_work_overdue",
            severity=severity,
            summary=(
                f"Recurring major work overdue records detected for {building or contractor} "
                f"across {len(major_work_case_ids)} cases in the last 90 days."
            ),
            evidence=_pattern_evidence(
                rule="repeated_major_work_overdue",
                entity_type="building" if building else "contractor",
                entity_value=building or contractor,
                timeframe_days=90,
                threshold=2,
                observed_count=len(major_work_case_ids),
                supporting_rows=major_work_support_rows,
                extra={
                    "building": building,
                    "contractor": contractor,
                    "count": len(major_work_case_ids),
                    "timeframe_days": 90,
                    "related_case_ids": major_work_case_ids,
                },
            ),
        )
        detected.append(pattern)
        active_types.append(pattern["pattern_type"])

    shortfall_rows_120 = _observations_between(anchor, 120, ["maintenance_hours_shortfall"])
    building_shortfall_case_ids = _pattern_case_ids(
        [row for row in shortfall_rows_120 if building and normalize_text(row.get("building") or "") == building_norm]
    )
    contractor_shortfall_rows_90 = _observations_between(anchor, 90, ["maintenance_hours_shortfall"])
    contractor_shortfall_rows = [
        row for row in contractor_shortfall_rows_90
        if contractor and normalize_text(row.get("contractor") or "") == contractor_norm
    ]
    contractor_buildings = {
        normalize_text(row.get("building") or "")
        for row in contractor_shortfall_rows
        if row.get("building")
    }
    if len(building_shortfall_case_ids) >= 2 or len(contractor_buildings) >= 3:
        severity = "review" if len(contractor_buildings) >= 3 else "medium"
        building_support_rows = [
            row
            for row in shortfall_rows_120
            if row.get("case_id") in building_shortfall_case_ids
            and normalize_text(row.get("building") or "") == building_norm
        ]
        contractor_support_rows = [
            row
            for row in contractor_shortfall_rows
            if row.get("case_id")
            and normalize_text(
                (str(db.get_case_by_id(str(row["case_id"]))["contractor"]) if db.get_case_by_id(str(row["case_id"])) and db.get_case_by_id(str(row["case_id"]))["contractor"] else "")
            ) == contractor_norm
        ]
        support_entity_type = "building" if len(building_shortfall_case_ids) >= 2 else "contractor"
        support_entity_value = building if len(building_shortfall_case_ids) >= 2 else contractor
        shortfall_support_rows = building_support_rows if support_entity_type == "building" else contractor_support_rows
        shortfall_support_case_ids = (
            building_shortfall_case_ids
            if support_entity_type == "building"
            else sorted({str(row["case_id"]) for row in contractor_support_rows if row.get("case_id")})
        )
        pattern = _build_pattern(
            case_id=case_id,
            pattern_type="repeated_maintenance_shortfall",
            severity=severity,
            summary=(
                "Repeated maintenance hours shortfall detected based on recent reporting periods "
                f"for {building or contractor}."
            ),
            evidence=_pattern_evidence(
                rule="repeated_maintenance_shortfall",
                entity_type=support_entity_type,
                entity_value=support_entity_value,
                timeframe_days=120 if support_entity_type == "building" else 90,
                threshold=2 if support_entity_type == "building" else 3,
                observed_count=max(len(building_shortfall_case_ids), len(contractor_buildings)),
                supporting_rows=shortfall_support_rows,
                extra={
                    "building": building,
                    "contractor": contractor,
                    "building_case_count_120_days": len(building_shortfall_case_ids),
                    "distinct_contractor_buildings_90_days": len(contractor_buildings),
                    "related_case_ids": _pattern_case_ids(contractor_shortfall_rows, set(building_shortfall_case_ids)),
                    "supporting_case_ids": shortfall_support_case_ids,
                    "supporting_observation_ids": _pattern_observation_ids(shortfall_support_rows),
                    "timeframe_days": 120 if support_entity_type == "building" else 90,
                },
            ),
        )
        detected.append(pattern)
        active_types.append(pattern["pattern_type"])

    if device:
        mechanic_rows = _observations_between(anchor, 90, ["mechanic_seen"])
        device_mechanic_rows = [
            row for row in mechanic_rows if normalize_text(row.get("device") or "") == device_norm
        ]
        mechanic_case_map: Dict[str, set] = defaultdict(set)
        for row in device_mechanic_rows:
            if row.get("entity_value") and row.get("case_id"):
                mechanic_case_map[str(row["entity_value"])].add(str(row["case_id"]))
        recurring_mechanics = {
            name: case_ids for name, case_ids in mechanic_case_map.items() if len(case_ids) >= 2
        }
        if recurring_mechanics:
            top_name, top_case_ids = max(recurring_mechanics.items(), key=lambda item: len(item[1]))
            severity = "medium" if len(top_case_ids) >= 3 else "info"
            pattern = _build_pattern(
                case_id=case_id,
                pattern_type="mechanic_recurrence",
                severity=severity,
                summary=(
                    f"Mechanic history detected for device {device} based on explicit email or reply data. "
                    f"{top_name} appears across {len(top_case_ids)} cases in the last 90 days."
                ),
                evidence=_pattern_evidence(
                    rule="mechanic_recurrence",
                    entity_type="mechanic",
                    entity_value=top_name,
                    timeframe_days=90,
                    threshold=2,
                    observed_count=len(top_case_ids),
                    supporting_rows=[
                        row for row in device_mechanic_rows
                        if row.get("entity_value") == top_name
                    ],
                    extra={
                        "device": device,
                        "mechanic": top_name,
                        "count": len(top_case_ids),
                        "related_case_ids": sorted(top_case_ids),
                        "timeframe_days": 90,
                    },
                ),
            )
            detected.append(pattern)
            active_types.append(pattern["pattern_type"])

        distinct_mechanics = {
            str(row["entity_value"])
            for row in device_mechanic_rows
            if row.get("entity_value")
        }
        if len(distinct_mechanics) >= 2:
            pattern = _build_pattern(
                case_id=case_id,
                pattern_type="mechanic_rotation",
                severity="medium" if len(distinct_mechanics) >= 3 else "info",
                summary=(
                    f"Multiple mechanics have appeared across related records for device {device}. "
                    "This depends on available email or reply data."
                ),
                evidence=_pattern_evidence(
                    rule="mechanic_rotation",
                    entity_type="device",
                    entity_value=device,
                    timeframe_days=90,
                    threshold=2,
                    observed_count=len(distinct_mechanics),
                    supporting_rows=device_mechanic_rows,
                    extra={
                        "device": device,
                        "mechanics": sorted(distinct_mechanics),
                        "count": len(distinct_mechanics),
                        "related_case_ids": _pattern_case_ids(device_mechanic_rows),
                        "timeframe_days": 90,
                    },
                ),
            )
            detected.append(pattern)
            active_types.append(pattern["pattern_type"])

    db.resolve_missing_pattern_flags(case_id, active_types)
    return detected


def get_memory_context_for_case(case_id: str) -> Dict[str, Any]:
    """Return active flags, counts, related cases, and recent observations for the UI."""
    case = db.get_case_by_id(case_id)
    if not case:
        return {
            "active_pattern_flags": [],
            "counts": {},
            "related_cases": [],
            "recent_observations": [],
            "mechanic_observations": [],
            "summary": "No recurring pattern detected.",
            "outbound_note": None,
        }

    anchor = _case_anchor(case_id)
    case_dict = dict(case)
    cases_60 = _cases_between(anchor, 60)
    cases_90 = _cases_between(anchor, 90)
    building = case_dict.get("building")
    device = case_dict.get("device")
    contractor = case_dict.get("contractor")

    building_count = len([row for row in cases_60 if building and _case_matches(row, "building", building)])
    device_count = len([row for row in cases_90 if device and _case_matches(row, "device", device)])
    contractor_count = len([row for row in cases_60 if contractor and _case_matches(row, "contractor", contractor)])

    active_flags = [dict(row) for row in db.get_active_pattern_flags_for_case(case_id)]
    related_cases = [dict(row) for row in db.get_related_cases_for_case(case_id, limit=8)]
    recent_observations = [dict(row) for row in db.get_observations_for_case(case_id, limit=8)]

    mechanic_rows = _observations_between(anchor, 90, ["mechanic_seen"])
    device_norm = normalize_text(device or "")
    mechanic_observations = [
        row for row in mechanic_rows
        if row.get("case_id") == case_id or (device and normalize_text(row.get("device") or "") == device_norm)
    ][:8]

    non_mechanic_outbound_flags = [
        row for row in active_flags
        if row["severity"] in ("medium", "high")
        and row["pattern_type"] not in MECHANIC_PATTERNS
    ]
    outbound_note = None
    if non_mechanic_outbound_flags:
        primary = non_mechanic_outbound_flags[0]["pattern_type"]
        if primary == "repeated_building_issue":
            outbound_note = "This item appears to be part of a recurring pattern for the same building and is being tracked accordingly."
        elif primary == "repeated_device_issue":
            outbound_note = "This item appears to be part of a recurring pattern for the same device and is being tracked accordingly."
        else:
            outbound_note = "This item appears to be part of a recurring pattern and is being tracked accordingly."

    context = {
        "active_pattern_flags": active_flags,
        "counts": {
            "building_cases_60_days": building_count,
            "device_cases_90_days": device_count,
            "contractor_cases_60_days": contractor_count,
        },
        "related_cases": related_cases,
        "recent_observations": recent_observations,
        "mechanic_observations": mechanic_observations,
        "outbound_note": outbound_note,
    }
    context["summary"] = get_memory_summary_for_case(case_id)
    return context


def get_memory_summary_for_case(case_id: str) -> str:
    """Return a short factual memory summary for UI display."""
    case = db.get_case_by_id(case_id)
    if not case:
        return "No recurring pattern detected."

    anchor = _case_anchor(case_id)
    case_dict = dict(case)
    cases_60 = _cases_between(anchor, 60)
    cases_90 = _cases_between(anchor, 90)

    parts: List[str] = []
    if case_dict.get("building"):
        building_count = len([row for row in cases_60 if _case_matches(row, "building", case_dict["building"])])
        if building_count >= 2:
            parts.append(f"{building_count} related cases for this building in the last 60 days")
    if case_dict.get("device"):
        device_count = len([row for row in cases_90 if _case_matches(row, "device", case_dict["device"])])
        if device_count >= 2:
            parts.append(f"{device_count} cases for the same device in the last 90 days")
    if case_dict.get("contractor"):
        contractor_count = len([row for row in cases_60 if _case_matches(row, "contractor", case_dict["contractor"])])
        if contractor_count >= 2:
            parts.append(f"{contractor_count} contractor-linked cases in the last 60 days")

    active_flags = [dict(row) for row in db.get_active_pattern_flags_for_case(case_id)]
    if not parts and not active_flags:
        return "No recurring pattern detected."
    if not parts and active_flags:
        return active_flags[0]["summary"]
    if active_flags:
        return "Memory shows " + " and ".join(parts) + "."
    return "Memory shows " + " and ".join(parts) + ", with no active pattern flag."


def rebuild_memory_from_existing_cases() -> Dict[str, int]:
    """Backfill memory from existing cases, extracted fields, and case events."""
    conn = db.get_connection()
    cases = db.get_all_cases()
    case_count = 0
    email_groups = 0
    reply_events = 0
    followup_events = 0

    for case in cases:
        case_count += 1
        case_id = case["case_id"]
        field_rows = db.get_fields_for_case(case_id)
        grouped_fields: Dict[Optional[str], Dict[str, Any]] = defaultdict(dict)
        for row in field_rows:
            grouped_fields[row["email_id"]][row["field_name"]] = row["field_value"]

        if grouped_fields:
            for email_id, fields in grouped_fields.items():
                email_groups += 1
                merged_fields = {
                    "building": case["building"],
                    "device": case["device"],
                    "contractor": case["contractor"],
                    "due_date": case["due_date"],
                    "period": case["period"],
                }
                merged_fields.update({key: value for key, value in fields.items() if value})
                record_case_observations(
                    case_id=case_id,
                    email_id=email_id,
                    case_type=case["case_type"],
                    fields=merged_fields,
                    source="system",
                )
        else:
            record_case_observations(
                case_id=case_id,
                email_id=None,
                case_type=case["case_type"],
                fields={
                    "building": case["building"],
                    "device": case["device"],
                    "contractor": case["contractor"],
                    "due_date": case["due_date"],
                    "period": case["period"],
                },
                source="system",
            )

        events = db.get_events_for_case(case_id)
        for event in events:
            if event["event_type"] == "reply_received":
                reply_events += 1
                add_observation(
                    case_id=case_id,
                    observation_type="contractor_response_received",
                    entity_type="contractor" if case["contractor"] else None,
                    entity_value=case["contractor"],
                    value_text=event["description"],
                    observed_at=event["created_at"],
                    source="reply",
                )
            elif event["event_type"] == "action_indicated":
                add_observation(
                    case_id=case_id,
                    observation_type="completion_claimed",
                    value_text=event["description"],
                    observed_at=event["created_at"],
                    source="reply",
                )
                add_observation(
                    case_id=case_id,
                    observation_type="evidence_pending",
                    value_text="Completion claim requires manual confirmation.",
                    observed_at=event["created_at"],
                    source="reply",
                )
            elif event["event_type"] == "followup_triggered":
                followup_events += 1
                add_observation(
                    case_id=case_id,
                    observation_type="no_response_followup",
                    entity_type="contractor" if case["contractor"] else None,
                    entity_value=case["contractor"],
                    value_text=event["description"],
                    observed_at=event["created_at"],
                    source="followup",
                )

    open_cases = [case for case in cases if case["status"] == "open"]
    for case in open_cases:
        detect_patterns_for_case(case["case_id"])

    return {
        "cases_processed": case_count,
        "email_groups_processed": email_groups,
        "reply_events_processed": reply_events,
        "followup_events_processed": followup_events,
        "open_cases_rechecked": len(open_cases),
    }
