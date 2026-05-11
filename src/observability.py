"""Lightweight in-repo observability for the demo agent.

This module intentionally avoids external telemetry services. It provides a
single metrics snapshot built from the existing SQLite audit tables and a small
JSONL event writer for local operational breadcrumbs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ai_gateway import get_ai_gateway
from config import config
from constants import EVENT_BACKLOG_CASE_CREATED, EVENT_CASE_CREATED
import database as db
from runtime_options import runtime_options
from time_utils import utc_display_timestamp


SNAPSHOT_SCHEMA_VERSION = "observability-snapshot-v1"
PARSER_VERSION = "deterministic-demo-v1"
POLICY_VERSION = "six-supported-demo-case-types-v1"


def build_metrics_snapshot() -> Dict[str, Any]:
    """Return a JSON-serializable operational snapshot for the current database.

    The snapshot is read-only and safe for demo/offline validation. It does not
    call external services, enable AI, send mail, or mutate pipeline state.
    """
    options = runtime_options.get()
    safety = _build_safety_section()
    ai_usage = _compact_ai_usage_report()
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_at": utc_display_timestamp(),
        "versions": {
            "parser": PARSER_VERSION,
            "policy": POLICY_VERSION,
        },
        "database": {
            "path": str(config.DATABASE_PATH),
            "exists": config.DATABASE_PATH.exists(),
        },
        "runtime": {
            "ai_enabled": bool(options.ai_enabled),
            "followups_enabled": bool(options.followups_enabled),
            "outbound_generation_disabled": bool(options.disable_outbound_generation),
            "template_outbound_only": bool(options.template_outbound_only),
        },
        "dashboard": db.get_dashboard_summary(),
        "email_pipeline": db.get_email_pipeline_summary(),
        "cases": {
            "by_status": db.get_case_counts_by_status(),
            "by_type": _case_type_counts(),
        },
        "manual_reviews": {
            "open": _count_rows("SELECT COUNT(*) AS count FROM manual_reviews WHERE resolved = 0"),
            "total": _count_rows("SELECT COUNT(*) AS count FROM manual_reviews"),
            "by_reason": _group_counts(
                """
                SELECT COALESCE(NULLIF(TRIM(reason), ''), 'Unspecified') AS label,
                       COUNT(*) AS count
                FROM manual_reviews
                WHERE resolved = 0
                GROUP BY label
                ORDER BY count DESC, label ASC
                """
            ),
        },
        "events": {
            "by_type": _group_counts(
                """
                SELECT event_type AS label, COUNT(*) AS count
                FROM case_events
                GROUP BY event_type
                ORDER BY count DESC, label ASC
                """
            ),
            "recent": db.get_recent_agent_activity(limit=10),
        },
        "outbound": {
            "by_status": _group_counts(
                """
                SELECT status AS label, COUNT(*) AS count
                FROM outbound_messages
                GROUP BY status
                ORDER BY count DESC, label ASC
                """
            ),
            "demo_recipient_violations": safety["outbound_recipient_violations"],
        },
        "followups": {
            "by_status": _group_counts(
                """
                SELECT status AS label, COUNT(*) AS count
                FROM followups
                GROUP BY status
                ORDER BY count DESC, label ASC
                """
            ),
            "actions_by_status": _group_counts(
                """
                SELECT status AS label, COUNT(*) AS count
                FROM followup_actions
                GROUP BY status
                ORDER BY count DESC, label ASC
                """
            ),
            "overdue_open": len(db.get_overdue_followups()),
        },
        "latency": {
            "note": (
                "Derived from email.received_at to case_events.created_at. "
                "Historical/backlog imports can reflect backlog age, not processor runtime."
            ),
            "email_to_case_created_seconds": _event_latency_summary(
                event_types=(EVENT_CASE_CREATED, EVENT_BACKLOG_CASE_CREATED)
            ),
        },
        "ai_usage": ai_usage,
        "safety": safety,
    }


def _compact_ai_usage_report() -> Dict[str, Any]:
    """Return AI usage totals without embedding per-call record details."""
    report = dict(get_ai_gateway().build_report())
    report.pop("records", None)
    return report


def write_metrics_snapshot(path: Path, snapshot: Optional[Dict[str, Any]] = None) -> Path:
    """Write an observability snapshot to ``path`` and return the written path.

    Args:
        path: JSON report target path.
        snapshot: Optional precomputed snapshot. If omitted, a fresh snapshot is
            built from the current SQLite database.

    Returns:
        The same path supplied by the caller.
    """
    payload = snapshot or build_metrics_snapshot()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def append_structured_event(
    component: str,
    event_name: str,
    *,
    log_path: Optional[Path] = None,
    status: str = "info",
    run_id: Optional[str] = None,
    **context: Any,
) -> Dict[str, Any]:
    """Append a single structured JSONL operational event.

    Args:
        component: Module or command emitting the event.
        event_name: Stable machine-readable event name.
        log_path: Optional override path. Defaults to
            ``config.OBSERVABILITY_LOG_PATH``.
        status: Short outcome/status label such as ``info``, ``ok``, or
            ``error``.
        run_id: Optional correlation ID for multi-step local runs.
        **context: Additional JSON-safe fields such as ``email_id``,
            ``case_id``, ``latency_ms``, ``outcome``, or ``error``.

    Returns:
        The event payload that was written.
    """
    target_path = log_path or config.OBSERVABILITY_LOG_PATH
    event: Dict[str, Any] = {
        "timestamp": utc_display_timestamp(),
        "component": component,
        "event_name": event_name,
        "status": status,
    }
    if run_id:
        event["run_id"] = run_id
    for key, value in context.items():
        if value is not None:
            event[key] = _json_safe(value)

    safe_event = _json_safe(event)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe_event, sort_keys=True) + "\n")
    return safe_event


def _build_safety_section() -> Dict[str, Any]:
    """Return demo safety checks that should stay visible in every report."""
    return {
        "demo_mode": bool(config.DEMO_MODE),
        "demo_recipient": config.DEMO_RECIPIENT_EMAIL,
        "smtp_configured": bool(config.is_smtp_configured()),
        "imap_configured": bool(config.is_imap_configured()),
        "outbound_recipient_violations": _demo_recipient_violation_count(),
    }


def _case_type_counts() -> Dict[str, int]:
    """Return case counts keyed by case type for easier JSON consumers."""
    return {
        str(row["case_type"]): int(row["count"])
        for row in db.get_case_counts_by_type()
    }


def _count_rows(sql: str, params: Iterable[Any] = ()) -> int:
    """Execute a count query that aliases its count column as ``count``."""
    row = db.get_connection().execute(sql, tuple(params)).fetchone()
    return int(row["count"]) if row else 0


def _group_counts(sql: str, params: Iterable[Any] = ()) -> Dict[str, int]:
    """Execute a grouped count query with ``label`` and ``count`` columns."""
    rows = db.get_connection().execute(sql, tuple(params)).fetchall()
    return {
        str(row["label"] if row["label"] is not None else "Unspecified"): int(row["count"])
        for row in rows
    }


def _demo_recipient_violation_count() -> int:
    """Return outbound rows that violate demo recipient override expectations."""
    if not config.DEMO_MODE:
        return 0
    return _count_rows(
        """
        SELECT COUNT(*) AS count
        FROM outbound_messages
        WHERE COALESCE(actual_to, '') != ?
        """,
        (config.DEMO_RECIPIENT_EMAIL,),
    )


def _event_latency_summary(event_types: Iterable[str]) -> Dict[str, Any]:
    """Summarize latency from inbound email receipt to selected case events."""
    event_type_values = tuple(event_types)
    placeholders = ", ".join("?" for _ in event_type_values)
    if not event_type_values:
        return {"count": 0, "average": None, "max": None}
    rows = db.get_connection().execute(
        f"""
        SELECT e.received_at AS start_at, ce.created_at AS end_at
        FROM case_events ce
        JOIN emails e ON e.email_id = ce.source_email_id
        WHERE ce.event_type IN ({placeholders})
        """,
        event_type_values,
    ).fetchall()
    latencies = []
    for row in rows:
        start_at = _parse_iso_datetime(row["start_at"])
        end_at = _parse_iso_datetime(row["end_at"])
        if start_at is None or end_at is None:
            continue
        latency = (end_at - start_at).total_seconds()
        if latency >= 0:
            latencies.append(latency)
    if not latencies:
        return {"count": 0, "average": None, "max": None}
    return {
        "count": len(latencies),
        "average": round(sum(latencies) / len(latencies), 3),
        "max": round(max(latencies), 3),
    }


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse existing SQLite timestamp strings into naive UTC datetimes."""
    if not value:
        return None
    normalized = str(value).strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _json_safe(value: Any) -> Any:
    """Return ``value`` converted into types accepted by ``json.dumps``."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
