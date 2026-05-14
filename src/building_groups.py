"""Building-level grouping helpers for supported KPI cases.

This module keeps Building Issue Groups as a coordination layer above cases.
It intentionally does not generate drafts, send email, schedule follow-ups, or
call AI.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

import database as db
from constants import (
    CASE_GROUP_SOURCE,
    GROUP_CASE_STATUS,
    GROUP_STATUSES,
    STATUS_CLOSED,
    SUPPORTED_CASE_TYPES,
)
from time_utils import utc_now_iso

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")
_SUPPORTED_CASE_TYPES = frozenset(SUPPORTED_CASE_TYPES)
_CASE_GROUP_SOURCES = frozenset(CASE_GROUP_SOURCE)
_GROUP_CASE_STATUSES = frozenset(GROUP_CASE_STATUS)
_GROUP_STATUSES = frozenset(GROUP_STATUSES)


def normalize_group_value(value: str | None) -> str:
    """Normalize building or contractor text for deterministic group matching."""
    if value is None:
        return ""
    lowered = str(value).lower().strip()
    without_specials = _NON_WORD_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", without_specials).strip()


def build_grouping_key(building: str, contractor: str) -> str:
    """Return the deterministic key for a building/contractor group."""
    normalized_building = normalize_group_value(building)
    normalized_contractor = normalize_group_value(contractor)
    return f"building_group::{normalized_building}::{normalized_contractor}"


def get_or_create_group(building: str, contractor: str, group_id: str | None = None) -> str:
    """Return the group ID for ``building`` and ``contractor``, creating it if needed."""
    normalized_building = normalize_group_value(building)
    normalized_contractor = normalize_group_value(contractor)
    if not normalized_building or not normalized_contractor:
        raise ValueError("building and contractor are required to create a group")

    grouping_key = build_grouping_key(building, contractor)
    now = utc_now_iso()
    with db._write_lock:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT group_id FROM building_issue_groups WHERE grouping_key = ?",
            (grouping_key,),
        ).fetchone()
        if row:
            return row["group_id"]

        group_id = group_id or str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO building_issue_groups
                (group_id, grouping_key, building, normalized_building,
                 contractor, normalized_contractor, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                group_id,
                grouping_key,
                str(building).strip(),
                normalized_building,
                str(contractor).strip(),
                normalized_contractor,
                now,
                now,
            ),
        )
        conn.commit()
        return group_id


def attach_case_to_group(
    case_id: str,
    source: str = "live_pipeline",
    enqueue: bool = True,
) -> str | None:
    """Attach a supported case to its building/contractor group.

    Args:
        case_id: Case UUID to attach.
        source: Source label for the link; must be one of
            ``CASE_GROUP_SOURCE``.
        enqueue: Reserved for future communication planning. In Phase 1 this
            function never enqueues, drafts, sends, or calls AI.

    Returns:
        The group ID, or None when the case is unsupported or lacks extracted
        building/contractor fields.
    """
    if source not in _CASE_GROUP_SOURCES:
        raise ValueError(f"Unsupported case group source: {source}")

    case_data = _eligible_case_group_data(
        case_id,
        include_closed=True,
        review_missing_grouping=enqueue and source != "backlog_import",
    )
    if case_data is None:
        return None

    group_id = get_or_create_group(case_data["building"], case_data["contractor"])
    link_status = "closed" if case_data["case"]["status"] == STATUS_CLOSED else "active"
    _upsert_group_case_link(
        group_id=group_id,
        case_id=case_id,
        source=source,
        status=link_status,
    )
    return group_id


def rebuild_all_groups(include_closed: bool = False, dry_run: bool = False) -> dict:
    """Rebuild building group links from existing supported cases.

    The rebuild is idempotent and deterministic. It does not call AI, enqueue
    communication, create drafts, send email, schedule follow-ups, or mutate
    cases.
    """
    conn = db.get_connection()
    cases = conn.execute("SELECT * FROM cases ORDER BY created_at ASC, case_id ASC").fetchall()
    summary = {
        "dry_run": dry_run,
        "include_closed": include_closed,
        "cases_scanned": len(cases),
        "eligible": 0,
        "attached": 0,
        "groups_created": 0,
        "skipped_unsupported": 0,
        "skipped_closed": 0,
        "skipped_missing_building": 0,
        "skipped_missing_contractor": 0,
    }
    planned_group_keys: set[str] = set()

    for case in cases:
        if case["case_type"] not in _SUPPORTED_CASE_TYPES:
            summary["skipped_unsupported"] += 1
            continue
        if not include_closed and case["status"] == STATUS_CLOSED:
            summary["skipped_closed"] += 1
            continue

        fields = _latest_group_fields(case["case_id"])
        building = fields.get("building")
        contractor = fields.get("contractor")
        if not building:
            summary["skipped_missing_building"] += 1
            if not dry_run and case["status"] != STATUS_CLOSED:
                _ensure_missing_grouping_review(case["case_id"], _missing_grouping_fields(fields))
            continue
        if not contractor:
            summary["skipped_missing_contractor"] += 1
            if not dry_run and case["status"] != STATUS_CLOSED:
                _ensure_missing_grouping_review(case["case_id"], _missing_grouping_fields(fields))
            continue

        summary["eligible"] += 1
        grouping_key = build_grouping_key(building, contractor)
        existing_group = _get_group_by_key(grouping_key)
        existing_link = bool(existing_group and _link_exists(existing_group["group_id"], case["case_id"]))
        if dry_run:
            if not existing_group and grouping_key not in planned_group_keys:
                summary["groups_created"] += 1
                planned_group_keys.add(grouping_key)
            if not existing_link:
                summary["attached"] += 1
            continue

        group_id = attach_case_to_group(case["case_id"], source="manual", enqueue=False)
        if group_id is None:
            continue
        if not existing_group:
            summary["groups_created"] += 1
        if not existing_link:
            summary["attached"] += 1

    return summary


def list_building_groups(filters: dict | None = None) -> list[dict]:
    """Return building groups with aggregate child-case and review counts."""
    filters = filters or {}
    where_clauses = []
    params: list[Any] = []

    status_filter = (filters.get("status") or "").strip()
    if status_filter:
        where_clauses.append("big.status = ?")
        params.append(status_filter)

    building_filter = normalize_group_value(filters.get("building"))
    if building_filter:
        where_clauses.append("(big.normalized_building LIKE ? OR lower(big.building) LIKE ?)")
        params.extend((f"%{building_filter}%", f"%{str(filters.get('building')).lower()}%"))

    contractor_filter = normalize_group_value(filters.get("contractor"))
    if contractor_filter:
        where_clauses.append("(big.normalized_contractor LIKE ? OR lower(big.contractor) LIKE ?)")
        params.extend((f"%{contractor_filter}%", f"%{str(filters.get('contractor')).lower()}%"))

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    conn = db.get_connection()
    rows = conn.execute(
        f"""
        SELECT
            big.*,
            COUNT(DISTINCT bgc.case_id) AS total_case_count,
            COUNT(DISTINCT CASE
                WHEN bgc.status = 'active' AND c.status != 'closed'
                THEN bgc.case_id
            END) AS open_case_count,
            COUNT(DISTINCT CASE
                WHEN bgc.status = 'active'
                 AND bgc.new_since_last_email = 1
                 AND c.status != 'closed'
                THEN bgc.case_id
            END) AS new_case_count,
            COUNT(DISTINCT CASE
                WHEN mr.resolved = 0
                THEN mr.review_id
            END) AS review_count
        FROM building_issue_groups big
        LEFT JOIN building_issue_group_cases bgc ON bgc.group_id = big.group_id
        LEFT JOIN cases c ON c.case_id = bgc.case_id
        LEFT JOIN manual_reviews mr ON mr.case_id = c.case_id AND mr.resolved = 0
        {where_sql}
        GROUP BY big.group_id
        ORDER BY big.updated_at DESC, big.created_at DESC
        """,
        tuple(params),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_group_summary(group_id: str) -> dict:
    """Return group details, child cases, counts, and timeline for a group."""
    conn = db.get_connection()
    group = conn.execute(
        "SELECT * FROM building_issue_groups WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    if not group:
        return {}

    case_rows = conn.execute(
        """
        SELECT
            c.*,
            bgc.added_at,
            bgc.included_in_email_at,
            bgc.new_since_last_email,
            bgc.status AS group_case_status,
            bgc.source AS group_source,
            COUNT(DISTINCT CASE WHEN mr.resolved = 0 THEN mr.review_id END) AS open_review_count
        FROM building_issue_group_cases bgc
        JOIN cases c ON c.case_id = bgc.case_id
        LEFT JOIN manual_reviews mr ON mr.case_id = c.case_id AND mr.resolved = 0
        WHERE bgc.group_id = ?
        GROUP BY c.case_id
        ORDER BY c.status ASC, c.updated_at DESC, c.created_at DESC
        """,
        (group_id,),
    ).fetchall()
    cases = [_row_to_dict(row) for row in case_rows]

    timeline = [
        {
            "created_at": group["created_at"],
            "event_type": "group_created",
            "description": "Building issue group created.",
            "case_id": None,
        }
    ]
    for case in cases:
        timeline.append(
            {
                "created_at": case["added_at"],
                "event_type": "case_attached",
                "description": f"Case attached from {case['group_source']}.",
                "case_id": case["case_id"],
            }
        )

    if cases:
        placeholders = ", ".join("?" for _ in cases)
        event_rows = conn.execute(
            f"""
            SELECT event_type, description, source_email_id, created_at, case_id
            FROM case_events
            WHERE case_id IN ({placeholders})
            ORDER BY created_at ASC
            """,
            tuple(case["case_id"] for case in cases),
        ).fetchall()
        timeline.extend(_row_to_dict(row) for row in event_rows)

    timeline.sort(key=lambda item: item.get("created_at") or "")
    counts = {
        "total_case_count": len(cases),
        "open_case_count": sum(
            1 for case in cases
            if case["group_case_status"] == "active" and case["status"] != STATUS_CLOSED
        ),
        "new_case_count": sum(
            1 for case in cases
            if case["group_case_status"] == "active"
            and case["status"] != STATUS_CLOSED
            and int(case["new_since_last_email"] or 0) == 1
        ),
        "review_count": sum(int(case["open_review_count"] or 0) for case in cases),
    }

    return {
        "group": _row_to_dict(group),
        "cases": cases,
        "counts": counts,
        "timeline": timeline,
    }


def _eligible_case_group_data(
    case_id: str,
    *,
    include_closed: bool,
    review_missing_grouping: bool = False,
) -> Optional[dict[str, Any]]:
    case = db.get_case_by_id(case_id)
    if not case:
        return None
    if case["case_type"] not in _SUPPORTED_CASE_TYPES:
        return None
    if not include_closed and case["status"] == STATUS_CLOSED:
        return None

    fields = _latest_group_fields(case_id)
    building = fields.get("building")
    contractor = fields.get("contractor")
    missing_fields = _missing_grouping_fields(fields)
    if missing_fields:
        if review_missing_grouping and case["status"] != STATUS_CLOSED:
            _ensure_missing_grouping_review(case_id, missing_fields)
        return None
    return {
        "case": dict(case),
        "building": building,
        "contractor": contractor,
    }


def _latest_group_fields(case_id: str) -> dict[str, str]:
    conn = db.get_connection()
    rows = conn.execute(
        """
        SELECT field_name, field_value
        FROM extracted_fields
        WHERE case_id = ?
          AND field_name IN ('building', 'contractor')
        ORDER BY rowid ASC
        """,
        (case_id,),
    ).fetchall()
    fields: dict[str, str] = {}
    for row in rows:
        value = str(row["field_value"]).strip() if row["field_value"] is not None else ""
        if value:
            fields[row["field_name"]] = value
    return fields


def _missing_grouping_fields(fields: dict[str, str]) -> list[str]:
    missing = []
    if not fields.get("building"):
        missing.append("building")
    if not fields.get("contractor"):
        missing.append("contractor")
    return missing


def _ensure_missing_grouping_review(case_id: str, missing_fields: list[str]) -> None:
    if not missing_fields:
        return
    reason = _missing_grouping_review_reason(missing_fields)
    if db.has_open_manual_review(case_id, reason):
        return
    db.insert_manual_review(
        review_id=str(uuid.uuid4()),
        case_id=case_id,
        email_id=None,
        reason=reason,
    )


def _missing_grouping_review_reason(missing_fields: list[str]) -> str:
    if len(missing_fields) == 1:
        field_text = missing_fields[0]
    else:
        field_text = ", ".join(missing_fields[:-1]) + f" and {missing_fields[-1]}"
    return f"Missing grouping data for building issue group: {field_text}."


def _get_group_by_key(grouping_key: str) -> Optional[dict]:
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM building_issue_groups WHERE grouping_key = ?",
        (grouping_key,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _link_exists(group_id: str, case_id: str) -> bool:
    conn = db.get_connection()
    row = conn.execute(
        """
        SELECT 1
        FROM building_issue_group_cases
        WHERE group_id = ? AND case_id = ?
        LIMIT 1
        """,
        (group_id, case_id),
    ).fetchone()
    return row is not None


def _upsert_group_case_link(group_id: str, case_id: str, source: str, status: str) -> None:
    if status not in _GROUP_CASE_STATUSES:
        raise ValueError(f"Unsupported group case status: {status}")

    now = utc_now_iso()
    with db._write_lock:
        conn = db.get_connection()
        stale_active_links = conn.execute(
            """
            SELECT group_id
            FROM building_issue_group_cases
            WHERE case_id = ?
              AND group_id != ?
              AND status = 'active'
            """,
            (case_id, group_id),
        ).fetchall()
        stale_group_ids = [row["group_id"] for row in stale_active_links]
        if stale_group_ids:
            conn.execute(
                """
                UPDATE building_issue_group_cases
                SET status = 'removed'
                WHERE case_id = ?
                  AND group_id != ?
                  AND status = 'active'
                """,
                (case_id, group_id),
            )
        existing = conn.execute(
            """
            SELECT *
            FROM building_issue_group_cases
            WHERE group_id = ? AND case_id = ?
            """,
            (group_id, case_id),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE building_issue_group_cases
                SET status = ?,
                    source = COALESCE(source, ?)
                WHERE group_id = ? AND case_id = ?
                """,
                (status, source, group_id, case_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO building_issue_group_cases
                    (group_id, case_id, added_at, new_since_last_email, status, source)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (group_id, case_id, now, status, source),
            )
        for stale_group_id in stale_group_ids:
            _refresh_group_status_locked(conn, stale_group_id, now)
        _refresh_group_status_locked(conn, group_id, now)
        conn.commit()


def _refresh_group_status_locked(conn: Any, group_id: str, now: str) -> None:
    group = conn.execute(
        "SELECT status, last_email_sent_at FROM building_issue_groups WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    if not group or group["status"] == "blocked":
        return

    open_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM building_issue_group_cases bgc
        JOIN cases c ON c.case_id = bgc.case_id
        WHERE bgc.group_id = ?
          AND bgc.status = 'active'
          AND c.status != 'closed'
        """,
        (group_id,),
    ).fetchone()["count"]
    new_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM building_issue_group_cases bgc
        JOIN cases c ON c.case_id = bgc.case_id
        WHERE bgc.group_id = ?
          AND bgc.status = 'active'
          AND bgc.new_since_last_email = 1
          AND c.status != 'closed'
        """,
        (group_id,),
    ).fetchone()["count"]

    if open_count == 0:
        next_status = "closed"
    elif group["last_email_sent_at"] and new_count:
        next_status = "updated_since_last_email"
    else:
        next_status = "open"

    if next_status not in _GROUP_STATUSES:
        raise ValueError(f"Unsupported group status: {next_status}")
    conn.execute(
        """
        UPDATE building_issue_groups
        SET status = ?, updated_at = ?
        WHERE group_id = ?
        """,
        (next_status, now, group_id),
    )


def _row_to_dict(row: Any) -> dict:
    return dict(row)
