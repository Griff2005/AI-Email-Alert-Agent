"""
backlog_loader.py - Standalone importer for staged historical KPI emails.

Supports JSON source only. Deterministic only. No outbound. No follow-ups.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from email.utils import getaddresses
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Pattern, Tuple, Union

from classifier import NOISE_PATTERNS, classify_email_deterministic_only
from content_safety import detect_injection, sanitize_email_content
from constants import (
    CASE_TYPE_CAT1_COMPLIANCE,
    CASE_TYPE_CAT5_COMPLIANCE,
    CASE_TYPE_DATA_ABSENCE,
    CASE_TYPE_GOVERNMENT_DIRECTIVE,
    CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL,
    CASE_TYPE_MAJOR_WORK_OVERDUE,
    CASE_TYPE_UNKNOWN,
    EVENT_BACKLOG_CASE_CREATED,
    EVENT_BACKLOG_CASE_UPDATED,
    EVENT_BACKLOG_EMAIL_IMPORTED,
    EVENT_BACKLOG_MEMORY_UPDATED,
    SUPPORTED_CASE_TYPES,
)
from config import PROJECT_ROOT
import building_groups
import database as db
import extractor
import memory
from time_utils import utc_compact_timestamp, utc_display_timestamp

_SUPPORTED_CASE_TYPES: frozenset[str] = frozenset(SUPPORTED_CASE_TYPES)
_CAT1_SUBJECT_RE = re.compile(r"\bcat\s*1\b", re.IGNORECASE)
_CAT5_SUBJECT_RE = re.compile(r"\bcat\s*5\b", re.IGNORECASE)
_SubjectPattern = Union[str, Pattern[str]]

_BACKLOG_SUBJECT_PATTERNS: tuple[tuple[_SubjectPattern, str], ...] = (
    ("data absence:", CASE_TYPE_DATA_ABSENCE),
    (_CAT1_SUBJECT_RE, CASE_TYPE_CAT1_COMPLIANCE),
    (_CAT5_SUBJECT_RE, CASE_TYPE_CAT5_COMPLIANCE),
    ("data absence", CASE_TYPE_DATA_ABSENCE),
    ("maintenance data is not up to date", CASE_TYPE_DATA_ABSENCE),
    ("maintenance data has never been submitted", CASE_TYPE_DATA_ABSENCE),
    ("maintenance hours less than required", CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL),
    ("maintenance hours shortfall", CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL),
    ("major scheduled work is overdue", CASE_TYPE_MAJOR_WORK_OVERDUE),
    ("scheduled work is overdue", CASE_TYPE_MAJOR_WORK_OVERDUE),
    ("outstanding government directive", CASE_TYPE_GOVERNMENT_DIRECTIVE),
    ("government directive", CASE_TYPE_GOVERNMENT_DIRECTIVE),
)
_UNSUPPORTED_KPI_PATTERNS: tuple[tuple[str, str], ...] = (
    # Callback families — most specific first, catch-all last
    ("callback alert", "CALLBACK_ALERT"),
    ("open callback", "OPEN_CALLBACK_REMINDER"),
    ("callback status", "CALLBACK_STATUS"),
    ("callbacks uploaded", "CALLBACKS_UPLOADED"),
    # Service / shutdown families
    ("back in service", "BACK_IN_SERVICE"),
    ("possible shutdown", "DEVICE_SHUTDOWN"),
    ("no car running", "NO_CAR_RUNNING"),
    ("out-of-service report", "DEVICE_OUT_OF_SERVICE"),
    ("entrapment", "ENTRAPMENT_OR_OCCUPIED"),
    ("solutrak event", "SOLUTRAK_EVENT"),
    ("solutrak emergency event", "SOLUTRAK_EVENT"),
    # Report upload families
    ("activities uploaded", "ACTIVITIES_UPLOADED"),
    ("maintenance uploaded", "MAINTENANCE_UPLOADED"),
    ("maintenance report", "MAINTENANCE_REPORT"),
    # Consultant / report families
    ("consultant report", "CONSULTANT_REPORT"),
    ("completed government report", "GOVERNMENT_REPORT"),
    ("government report", "GOVERNMENT_REPORT"),
    # Directive families (unsupported variants)
    ("ahj directive", "AHJ_DIRECTIVE"),
    # System / platform
    ("service alert", "SERVICE_ALERT"),
    ("apps exception", "SYSTEM_NOTIFICATION"),
    ("exception on servicecaller", "SYSTEM_NOTIFICATION"),
    # License / permit
    ("expiring licen", "EXPIRING_LICENSE"),
    ("expiring permit", "EXPIRING_PERMIT"),
    ("license expiry", "LICENSE_EXPIRY"),
    ("licence expiry", "LICENSE_EXPIRY"),
    # KPI metric families
    ("uptime lower than expectation", "UPTIME_LOW"),
    ("mtbc too low", "MTBC_TOO_LOW"),
    ("callback ratio too high", "CALLBACK_RATIO_HIGH"),
    ("callbacks exceed expectation", "CALLBACKS_EXCEED"),
    ("all callbacks exceed", "CALLBACKS_EXCEED"),
    # Service restoration
    ("returned to normal service", "RETURNED_TO_NORMAL_SERVICE"),
    # Preventive maintenance - shutdown variant must precede the plain variant
    ("preventive maintenance [shutdown]", "SCHEDULED_PREVENTIVE_MAINTENANCE_SHUTDOWN"),
    ("preventive maintenance shutdown", "SCHEDULED_PREVENTIVE_MAINTENANCE_SHUTDOWN"),
    ("scheduled preventive maintenance", "SCHEDULED_PREVENTIVE_MAINTENANCE"),
    # Emergency entrapment events (non-SoluTrak "Emergency: Building(Device)" format)
    ("emergency:", "ENTRAPMENT_OR_OCCUPIED"),
    # Code Yellow elevator alerts
    ("code yellow", "CODE_YELLOW"),
    # SoluBoard connectivity alerts
    ("soluboard event", "SOLUBOARD_NOT_COMMUNICATING"),
    # Portfolio and performance reporting
    ("portfolio summary", "PORTFOLIO_SUMMARY"),
    ("performance target kpi", "PERFORMANCE_TARGET_KPI"),
    # Data uploads
    ("tssa data uploaded", "TSSA_DATA_UPLOADED"),
    # Budgeting
    ("budgeting reminder", "BUDGETING_REMINDER"),
    # System / platform
    ("exception on kpi", "SYSTEM_NOTIFICATION"),
    # Generic callback catch-all — MUST be last among callback patterns
    ("callback", "CALLBACK_STATUS"),
)
_NON_KPI_PATTERNS = NOISE_PATTERNS + (
    "read receipt",
    "meeting accepted",
    "meeting declined",
    "invoice",
    "statement",
    "newsletter",
    "undeliverable",
    "delivery status notification",
    "delivery failure",
    "mail delivery",
    "postmaster",
    "auto-reply",
    "auto reply",
    "out of office",
    "automatic reply",
    "ndr:",
    "system notification",
    "password reset",
    "login notification",
    "account notification",
    "subscription",
    "unsubscribe",
    # e-volve account / login setup notifications
    "e-volve account",
    "evolve account",
    "evolve login",
    "evolve login",
    "your e-volve",
    "your evolve",
    # Contact and CRM noise
    "new contact",
    # SoluTrak account noise (event alerts handled as SOLUTRAK_EVENT above)
    "solutrak: ",
    # Security notification system emails (not elevator KPI alerts)
    "critical security alert",
    "security alert",
)
_DEVICE_PATTERN = re.compile(r"\b[A-Z]-\d+\s*#\d+\b", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class BacklogRunOptions:
    """Safety and reporting switches for one backlog import run."""

    outbound_enabled: bool = False
    progress_interval: int = 50
    report_detail: str = "summary"


def load_backlog(
    source: str,
    path: Path,
    dry_run: bool,
    limit: Optional[int] = None,
    report_dir: Optional[Path] = None,
    progress_interval: int = 50,
    report_detail: str = "summary",
) -> Dict[str, Any]:
    """Load staged historical KPI emails into reports or SQLite.

    Backlog mode is intentionally narrower than live/demo processing: JSON
    source only, deterministic parsing only, no outbound messages, no
    follow-ups, and no AI calls.
    """
    if source != "json":
        raise ValueError(f"Unsupported backlog source: {source!r}. Only 'json' is supported.")
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")
    if progress_interval <= 0:
        raise ValueError("progress_interval must be > 0")
    if report_detail not in {"summary", "full"}:
        raise ValueError("report_detail must be 'summary' or 'full'")

    options = BacklogRunOptions(
        outbound_enabled=False,
        progress_interval=progress_interval,
        report_detail=report_detail,
    )

    raw_records = _load_json_source(path)
    if limit is not None:
        raw_records = raw_records[:limit]

    run_dir = Path(report_dir) if report_dir else _default_report_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    rejected_items: List[Dict[str, Any]] = []
    duplicate_items: List[Dict[str, Any]] = []
    review_items: List[Dict[str, Any]] = []
    unsupported_kpi_items: List[Dict[str, Any]] = []
    accepted_results: List[Dict[str, Any]] = []
    seen_message_ids: set[str] = set()
    planned_grouping_keys: set[str] = set()

    duplicates = 0
    expected_new_cases = 0
    expected_case_updates = 0
    expected_memory_observations = 0
    start_time = time.monotonic()
    total_records = len(raw_records)
    last_progress_processed = -1

    for processed, raw in enumerate(raw_records, start=1):
        try:
            normalized = _normalize_record(raw)
        except ValueError as exc:
            item = _result_stub(raw, action="rejected", reason=str(exc))
            results.append(item)
            rejected_items.append(item)
            if processed % options.progress_interval == 0:
                _print_progress(
                    processed,
                    total_records,
                    start_time,
                    len(accepted_results),
                    len(rejected_items),
                    len(review_items),
                    len(unsupported_kpi_items),
                )
                last_progress_processed = processed
            continue

        if normalized["message_id"] in seen_message_ids:
            duplicates += 1
            item = _decorate_for_report(
                normalized,
                {
                    "action": "duplicate",
                    "reason": "Duplicate message_id within the backlog source file.",
                },
            )
            results.append(item)
            duplicate_items.append(item)
            if processed % options.progress_interval == 0:
                _print_progress(
                    processed,
                    total_records,
                    start_time,
                    len(accepted_results),
                    len(rejected_items),
                    len(review_items),
                    len(unsupported_kpi_items),
                )
                last_progress_processed = processed
            continue
        seen_message_ids.add(normalized["message_id"])

        result = _process_record(normalized, dry_run=dry_run)
        results.append(result)

        if result["action"] == "accepted":
            accepted_results.append(result)
            expected_memory_observations += int(result.get("expected_memory_observations", 0))
            grouping_key = result.get("grouping_key")
            existing_case_id = result.get("existing_case_id")
            if dry_run and grouping_key:
                if existing_case_id or grouping_key in planned_grouping_keys:
                    expected_case_updates += 1
                else:
                    expected_new_cases += 1
                    planned_grouping_keys.add(grouping_key)
        elif result["action"] == "review":
            review_items.append(result)
        elif result["action"] == "recognized_unsupported_kpi":
            unsupported_kpi_items.append(result)
        elif result["action"] == "rejected":
            rejected_items.append(result)
        elif result["action"] == "duplicate":
            duplicate_items.append(result)
            duplicates += 1

        if processed % options.progress_interval == 0:
            _print_progress(
                processed,
                total_records,
                start_time,
                len(accepted_results),
                len(rejected_items),
                len(review_items),
                len(unsupported_kpi_items),
            )
            last_progress_processed = processed

    if total_records == 0 or last_progress_processed != total_records:
        _print_progress(
            total_records,
            total_records,
            start_time,
            len(accepted_results),
            len(rejected_items),
            len(review_items),
            len(unsupported_kpi_items),
        )

    recipient_summary = _collect_recipient_summary(results)
    ai_report = {"total_ai_calls": 0}
    accepted_kpi = len(accepted_results)
    rejected_count = len(rejected_items)
    review_count = len(review_items)
    new_cases = expected_new_cases if dry_run else len(
        [result for result in accepted_results if result.get("action_taken") == "created"]
    )
    case_updates = expected_case_updates if dry_run else len(
        [result for result in accepted_results if result.get("action_taken") == "updated"]
    )
    memory_observations_created = (
        0 if dry_run else sum(int(result.get("memory_observations_created", 0)) for result in accepted_results)
    )
    pattern_flags_created = (
        0 if dry_run else sum(int(result.get("pattern_flags_created", 0)) for result in accepted_results)
    )
    summary: Dict[str, Any] = {
        "mode": "dry_run" if dry_run else "commit",
        "generated_at": _generated_at_timestamp(),
        "source": source,
        "path": str(path),
        "source_path": str(path),
        "dry_run": dry_run,
        "emails_scanned": len(raw_records),
        "accepted_kpi": accepted_kpi,
        "recognized_unsupported_kpi": len(unsupported_kpi_items),
        "rejected": rejected_count,
        "review_candidates": review_count,
        "duplicate_inputs": duplicates,
        "new_cases_expected_or_created": new_cases,
        "case_updates_expected_or_done": case_updates,
        "expected_memory_observations": expected_memory_observations if dry_run else 0,
        "memory_observations_created": memory_observations_created,
        "pattern_flags_created": pattern_flags_created,
        "unique_recipients": recipient_summary["unique_recipients"],
        "recipient_summary": recipient_summary,
        "common_rejected_subjects": _top_rejected_subjects(rejected_items),
        "top_unknown_subject_patterns": _top_unknown_subject_patterns(review_items),
        "top_supported_extraction_failures": _top_supported_extraction_failures(review_items),
        "ai_calls": ai_report["total_ai_calls"],
        "outbound_emails": 0,
        "followups_scheduled": 0,
        "results": results,
        "rejected_items": rejected_items,
        "duplicate_items": duplicate_items,
        "review_items": review_items,
        "unsupported_kpi_items": unsupported_kpi_items,
        "unsupported_kpi_counts_by_family": _count_by_family(unsupported_kpi_items),
        "report_dir": str(run_dir),
        "report_paths": _report_paths(run_dir),
        # Backward-compatible aliases for existing callers/tests.
        "accepted_supported_kpi_emails": accepted_kpi if dry_run else 0,
        "imported_supported_kpi_emails": accepted_kpi if not dry_run else 0,
        "rejected_emails": rejected_count,
        "duplicate_input_emails": duplicates,
        "expected_new_cases": expected_new_cases if dry_run else 0,
        "expected_case_updates": expected_case_updates if dry_run else 0,
        "cases_created": new_cases if not dry_run else 0,
        "cases_updated": case_updates if not dry_run else 0,
        "unique_recipients_found": recipient_summary["unique_recipients"],
    }
    if dry_run:
        summary["dry_run_note"] = "No database changes were committed. This is a preview only."

    _write_reports(summary, run_dir, dry_run, report_detail=options.report_detail)
    _print_summary(summary)
    return summary


def _load_json_source(path: Path) -> List[Dict[str, Any]]:
    """Read and validate the top-level JSON backlog source shape."""
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"Backlog source file not found: {source_path}")

    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON backlog source: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError("Backlog JSON source must contain a top-level list of email records.")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError("Backlog JSON source must contain only object records.")
    return payload


def _normalize_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw JSON record into the importer's internal email shape."""
    subject = str(raw.get("subject", "")).strip()
    body = str(raw.get("body", "")).strip()
    if not subject:
        raise ValueError("Backlog record is missing a non-empty subject.")
    if not body:
        raise ValueError("Backlog record is missing a non-empty body.")

    received_at_raw = str(raw.get("received_at", "")).strip()
    normalized = {
        "message_id": str(raw.get("message_id", "")).strip() or _synthetic_message_id(subject, received_at_raw, body),
        "thread_id": _optional_text(raw.get("thread_id")),
        "subject": subject,
        "body": body,
        "from_addr": _optional_text(raw.get("from_addr")),
        "to_addrs": _normalize_addresses(raw.get("to_addrs") or raw.get("to_addr")),
        "cc_addrs": _normalize_addresses(raw.get("cc_addrs") or raw.get("cc_addr")),
        "bcc_addrs": _normalize_addresses(raw.get("bcc_addrs") or raw.get("bcc_addr")),
        "reply_to": _optional_text(raw.get("reply_to")),
        "received_at": received_at_raw or _generated_at_timestamp(),
    }
    return normalized


def _is_obvious_non_kpi(record: Dict[str, Any]) -> Tuple[bool, str]:
    """Return whether a record is obvious noise before KPI classification."""
    subject = str(record.get("subject", "")).strip()
    body = str(record.get("body", "")).strip()
    if not subject:
        return True, "Empty subject."
    if not body:
        return True, "Empty body."

    normalized_subject = subject.lower()
    for pattern in _NON_KPI_PATTERNS:
        if pattern in normalized_subject:
            if pattern == "solutrak: " and _match_subject_to_unsupported_kpi(subject) == "SOLUTRAK_EVENT":
                continue
            return True, f"Matched obvious non-KPI pattern: {pattern}."
    return False, ""


def _classify_for_backlog(record: Dict[str, Any]) -> Dict[str, Any]:
    """Classify a backlog record through the six-type subject gate."""
    subject_case_type = _match_subject_to_case_type(record["subject"])
    if subject_case_type is None:
        family = _match_subject_to_unsupported_kpi(record["subject"])
        if family:
            return {
                "source": "backlog_unsupported_kpi",
                "case_type": CASE_TYPE_UNKNOWN,
                "unsupported_family": family,
                "reason": f"Recognized unsupported KPI family: {family}.",
            }
        return {
            "source": "backlog_subject_gate",
            "case_type": CASE_TYPE_UNKNOWN,
            "reason": "Subject did not match any supported backlog KPI pattern.",
        }

    result = classify_email_deterministic_only(record["subject"], record["body"])
    if result["source"] == "deterministic" and result["case_type"] in _SUPPORTED_CASE_TYPES:
        return result
    if result["source"] == "noise":
        return result
    return {
        **result,
        "case_type": subject_case_type,
        "source": "backlog_subject_override",
        "reason": f"Subject matched {subject_case_type}; body classification was ambiguous.",
    }


def _match_subject_to_case_type(subject: str) -> Optional[str]:
    """Return supported case type if subject matches a known pattern, else None."""
    lowered = subject.lower()
    for pattern, case_type in _BACKLOG_SUBJECT_PATTERNS:
        if _subject_pattern_matches(pattern, subject, lowered):
            return case_type
    return None


def _subject_pattern_matches(pattern: _SubjectPattern, subject: str, lowered_subject: str) -> bool:
    """Match string or regex subject patterns without broadening short tokens."""
    if isinstance(pattern, str):
        return pattern in lowered_subject
    return bool(pattern.search(subject))


def _match_subject_to_unsupported_kpi(subject: str) -> Optional[str]:
    """Return recognized-but-unsupported KPI family name, or None."""
    lowered = subject.lower()
    for pattern, family in _UNSUPPORTED_KPI_PATTERNS:
        if pattern in lowered:
            return family
    return None


def _validate_body_signature(case_type: str, body: str) -> bool:
    """Return True when the body has minimal evidence for the matched case type."""
    lowered = body.lower()
    if case_type in {CASE_TYPE_CAT1_COMPLIANCE, CASE_TYPE_CAT5_COMPLIANCE}:
        return bool(_DEVICE_PATTERN.search(body)) or (
            ("test" in lowered or "reminder" in lowered) and "building" in lowered
        )
    if case_type == CASE_TYPE_DATA_ABSENCE:
        lowered_stripped = re.sub(r"&\w+;", " ", lowered)
        return any(
            phrase in lowered_stripped
            for phrase in (
                "missing",
                "not submitted",
                "absence",
                "elapsed",
                "not up to date",
                "never been submitted",
                "data has never",
                "last activity",
                "maintenance data",
            )
        )
    if case_type == CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL:
        return "hours" in lowered and any(
            phrase in lowered for phrase in ("required", "contract", "actual")
        )
    if case_type == CASE_TYPE_MAJOR_WORK_OVERDUE:
        return any(
            phrase in lowered
            for phrase in (
                "scheduled",
                "overdue",
                "scheduleddate",
                "major maintenance",
                "appear to be overdue",
            )
        )
    if case_type == CASE_TYPE_GOVERNMENT_DIRECTIVE:
        return any(phrase in lowered for phrase in ("directive", "duedate", "due date"))
    return False


def _process_record(record: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    """Process one normalized backlog record and optionally commit it."""
    normalized = _normalize_record(record)

    is_non_kpi, reason = _is_obvious_non_kpi(normalized)
    if is_non_kpi:
        return _decorate_for_report(normalized, {"action": "rejected", "reason": reason})

    if detect_injection(normalized["subject"]) or detect_injection(normalized["body"]):
        return _decorate_for_report(
            normalized,
            {
                "action": "review",
                "case_type": CASE_TYPE_UNKNOWN,
                "reason": "Backlog import review: prompt-injection pattern detected in the email content.",
            },
        )

    classification = _classify_for_backlog(normalized)
    if classification["source"] == "noise":
        return _decorate_for_report(normalized, {"action": "rejected", "reason": classification["reason"]})
    if classification.get("unsupported_family"):
        return _decorate_for_report(
            normalized,
            {
                "action": "recognized_unsupported_kpi",
                "unsupported_family": classification["unsupported_family"],
                "reason": classification["reason"],
                "classification": classification,
            },
        )

    case_type = classification["case_type"]
    if case_type == CASE_TYPE_UNKNOWN:
        return _decorate_for_report(
            normalized,
            {
                "action": "review",
                "case_type": CASE_TYPE_UNKNOWN,
                "reason": "Backlog import review: deterministic classification could not safely match a supported KPI.",
                "classification": classification,
            },
        )

    if not _validate_body_signature(case_type, normalized["body"]):
        return _decorate_for_report(
            normalized,
            {
                "action": "review",
                "case_type": case_type,
                "reason": "Backlog import review: subject matched a supported KPI, but the body signature was too weak.",
                "classification": classification,
            },
        )

    fields, missing_required = _extract_fields_deterministically(
        normalized["subject"],
        normalized["body"],
        case_type,
    )
    if missing_required:
        return _decorate_for_report(
            normalized,
            {
                "action": "review",
                "case_type": case_type,
                "fields": fields,
                "reason": (
                    "Backlog import review: subject matched a supported KPI, "
                    f"but required body fields were missing: {', '.join(missing_required)}."
                ),
                "classification": classification,
            },
        )

    grouping_key = extractor.generate_grouping_key(
        case_type=case_type,
        building=fields.get("building"),
        device=fields.get("device"),
        period=fields.get("period"),
    )
    if not grouping_key or not grouping_key.strip("|"):
        return _decorate_for_report(
            normalized,
            {
                "action": "review",
                "case_type": case_type,
                "fields": fields,
                "reason": "Backlog import review: grouping key could not be determined safely.",
                "classification": classification,
            },
        )

    existing_email = db.get_email_by_message_id(normalized["message_id"])
    existing_case = db.get_case_by_grouping_key(grouping_key)
    expected_observations = _estimate_observation_count(case_type, fields)

    if dry_run:
        expected_action = "update_case" if existing_case else "create_case"
        if existing_email:
            return _decorate_for_report(
                normalized,
                {
                    "action": "duplicate",
                    "case_type": case_type,
                    "grouping_key": grouping_key,
                    "existing_case_id": existing_case["case_id"] if existing_case else None,
                    "reason": "Duplicate email already exists in the database.",
                    "classification": classification,
                },
            )
        return _decorate_for_report(
            normalized,
            {
                "action": "accepted",
                "case_type": case_type,
                "grouping_key": grouping_key,
                "existing_case_id": existing_case["case_id"] if existing_case else None,
                "expected_action": expected_action,
                "expected_memory_observations": expected_observations,
                "fields": fields,
                "classification": classification,
                "reason": "Record is eligible for backlog import.",
            },
        )

    if existing_email:
        return _decorate_for_report(
            normalized,
            {
                "action": "duplicate",
                "case_type": case_type,
                "grouping_key": grouping_key,
                "existing_case_id": existing_case["case_id"] if existing_case else None,
                "reason": "Duplicate email already exists in the database.",
                "classification": classification,
            },
        )

    email_id = str(uuid.uuid4())
    db.insert_email(
        email_id=email_id,
        message_id=normalized["message_id"],
        thread_id=normalized.get("thread_id"),
        subject=normalized["subject"],
        from_addr=normalized.get("from_addr") or "",
        to_addr="; ".join(normalized.get("to_addrs", [])),
        received_at=normalized["received_at"],
        raw_body=normalized["body"],
        normalized_text=sanitize_email_content(normalized["body"]),
    )
    db.mark_email_processed(email_id)

    created = existing_case is None
    case_id = existing_case["case_id"] if existing_case else str(uuid.uuid4())
    if created:
        db.insert_case(
            case_id=case_id,
            case_type=case_type,
            grouping_key=grouping_key,
            building=fields.get("building"),
            device=fields.get("device"),
            contractor=fields.get("contractor"),
            due_date=fields.get("due_date"),
            period=fields.get("period"),
            priority="medium",
        )
        db.insert_case_event(
            event_id=str(uuid.uuid4()),
            case_id=case_id,
            event_type=EVENT_BACKLOG_CASE_CREATED,
            description="Historical backlog case created from a supported KPI email.",
            source_email_id=email_id,
        )
    else:
        updates = {
            key: value
            for key, value in {
                "building": fields.get("building"),
                "device": fields.get("device"),
                "contractor": fields.get("contractor"),
                "due_date": fields.get("due_date"),
                "period": fields.get("period"),
            }.items()
            if value
        }
        if updates:
            db.update_case(case_id, updates)
        db.insert_case_event(
            event_id=str(uuid.uuid4()),
            case_id=case_id,
            event_type=EVENT_BACKLOG_CASE_UPDATED,
            description="Historical backlog email matched an existing case and refreshed deterministic fields.",
            source_email_id=email_id,
        )

    for field_name, field_value in fields.items():
        if field_value in (None, ""):
            continue
        db.insert_extracted_field(
            field_id=str(uuid.uuid4()),
            case_id=case_id,
            email_id=email_id,
            field_name=field_name,
            field_value=str(field_value),
            confidence_score=1.0,
        )

    building_group_id = building_groups.attach_case_to_group(
        case_id=case_id,
        source="backlog_import",
        enqueue=False,
    )

    db.insert_case_event(
        event_id=str(uuid.uuid4()),
        case_id=case_id,
        event_type=EVENT_BACKLOG_EMAIL_IMPORTED,
        description=_email_import_description(normalized),
        source_email_id=email_id,
    )

    for entity_type, value in (
        ("building", fields.get("building")),
        ("device", fields.get("device")),
        ("contractor", fields.get("contractor")),
    ):
        if value:
            memory.upsert_entity(entity_type, value, source="backlog_import")

    before_observations = _count_rows("observations")
    before_pattern_flags = _count_rows("pattern_flags")

    record_issue_observation = getattr(memory, "record_issue_observation", None)
    if callable(record_issue_observation):
        record_issue_observation(
            case_id=case_id,
            case_type=case_type,
            building=fields.get("building"),
            device=fields.get("device"),
            contractor=fields.get("contractor"),
            email_id=email_id,
            observed_at=normalized["received_at"],
        )
    elif hasattr(memory, "record_case_observations"):
        memory.record_case_observations(
            case_id=case_id,
            email_id=email_id,
            case_type=case_type,
            fields=fields,
            source="backlog_import",
        )

    pattern_runner = getattr(memory, "run_pattern_detection", None)
    if callable(pattern_runner):
        pattern_runner(case_id)
    else:
        memory.detect_patterns_for_case(case_id)

    memory_observations_created = _count_rows("observations") - before_observations
    pattern_flags_created = _count_rows("pattern_flags") - before_pattern_flags

    db.insert_case_event(
        event_id=str(uuid.uuid4()),
        case_id=case_id,
        event_type=EVENT_BACKLOG_MEMORY_UPDATED,
        description=(
            "Backlog memory updated. "
            f"Observations added: {memory_observations_created}. "
            f"Pattern flags created: {pattern_flags_created}."
        ),
        source_email_id=email_id,
    )

    return _decorate_for_report(
        normalized,
        {
            "action": "accepted",
            "action_taken": "created" if created else "updated",
            "committed": True,
            "case_type": case_type,
            "case_id": case_id,
            "building_group_id": building_group_id,
            "email_id": email_id,
            "grouping_key": grouping_key,
            "fields": fields,
            "classification": classification,
            "existing_case_id": existing_case["case_id"] if existing_case else None,
            "expected_memory_observations": expected_observations,
            "memory_observations_created": memory_observations_created,
            "pattern_flags_created": pattern_flags_created,
            "reason": "Record imported successfully.",
        },
    )


def _collect_recipient_summary(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize original sender and recipient metadata for the backlog report."""
    unique_recipients: set[str] = set()
    domain_counter: Counter[str] = Counter()
    to_counter: Counter[str] = Counter()
    cc_counter: Counter[str] = Counter()
    case_type_counter: Counter[str] = Counter()
    missing_recipient_count = 0

    for record in records:
        recipient_groups = [
            record.get("to_addrs", []) or [],
            record.get("cc_addrs", []) or [],
            record.get("bcc_addrs", []) or [],
        ]
        all_recipients = list(_flatten(recipient_groups))
        if record.get("from_addr"):
            all_recipients.append(record["from_addr"])
        if record.get("reply_to"):
            all_recipients.append(record["reply_to"])

        if not all_recipients:
            missing_recipient_count += 1

        case_type = record.get("case_type")
        if case_type in _SUPPORTED_CASE_TYPES:
            case_type_counter[case_type] += 1

        for address in all_recipients:
            normalized = address.lower()
            unique_recipients.add(normalized)
            domain = _domain_for(address)
            if domain:
                domain_counter[domain] += 1

        for address in record.get("to_addrs", []) or []:
            to_counter[address.lower()] += 1
        for address in record.get("cc_addrs", []) or []:
            cc_counter[address.lower()] += 1

    return {
        "unique_recipients": len(unique_recipients),
        "missing_recipient_count": missing_recipient_count,
        "by_domain": {
            domain: count
            for domain, count in domain_counter.most_common()
        },
        "top_to_recipients": [
            {"address": address, "count": count}
            for address, count in to_counter.most_common(10)
        ],
        "top_cc_recipients": [
            {"address": address, "count": count}
            for address, count in cc_counter.most_common(10)
        ],
        "by_kpi_family": {
            case_type: case_type_counter.get(case_type, 0)
            for case_type in sorted(_SUPPORTED_CASE_TYPES)
        },
    }


def _extract_fields_deterministically(subject: str, body: str, case_type: str) -> Tuple[Dict[str, Any], List[str]]:
    """Extract fields through the public no-AI extractor path."""
    return extractor.extract_fields_deterministic_only(subject, body, case_type)


def _estimate_observation_count(case_type: str, fields: Dict[str, Any]) -> int:
    """Estimate memory observations that a commit should create for one record."""
    count = 2  # case_seen + issue_seen
    if fields.get("building"):
        count += 1
    if fields.get("device"):
        count += 1
    if fields.get("contractor"):
        count += 1
    if fields.get("mechanic") or fields.get("technician"):
        count += 1
    if fields.get("work_item"):
        count += 1
    if fields.get("scheduled_date"):
        count += 1
    if case_type in _SUPPORTED_CASE_TYPES:
        count += 1
    return count


def _write_reports(
    results: Dict[str, Any],
    report_dir: Path,
    dry_run: bool,
    report_detail: str = "summary",
) -> None:
    """Write JSON, Markdown, rejection, review, unsupported, and recipient reports."""
    del dry_run
    report_paths = _report_paths(report_dir)

    Path(report_paths["report_json"]).write_text(
        json.dumps(_report_json_payload(results, report_detail), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    Path(report_paths["report_md"]).write_text(
        _build_markdown_report(results, report_detail),
        encoding="utf-8",
    )
    Path(report_paths["rejected_json"]).write_text(
        json.dumps(_rejected_report_items(results["rejected_items"]), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    Path(report_paths["review_candidates_json"]).write_text(
        json.dumps(_review_report_items(results["review_items"]), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    Path(report_paths["unsupported_kpis_json"]).write_text(
        json.dumps(_unsupported_kpi_report_items(results["unsupported_kpi_items"]), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    Path(report_paths["recipient_summary_json"]).write_text(
        json.dumps(results["recipient_summary"], indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _build_markdown_report(summary: Dict[str, Any], report_detail: str = "summary") -> str:
    """Render the human-readable backlog import report."""
    mode_label = "DRY RUN" if summary["dry_run"] else "COMMIT"
    case_label = "Expected / Created" if summary["dry_run"] else "Created / Updated"
    top_rejected = summary["common_rejected_subjects"]
    unsupported_counts = summary["unsupported_kpi_counts_by_family"]
    top_review_reasons = _top_review_reasons(summary.get("review_items", []))
    lines = [
        "# Backlog Loading Mode Report",
        "",
        f"**Mode:** {mode_label}",
        f"**Generated:** {summary['generated_at']}",
        f"**Source:** `{summary['source_path']}`",
    ]
    if summary["dry_run"]:
        lines.extend(["", "**DRY RUN — No database changes committed.**"])
    lines.extend(
        [
            "",
            "## Counts",
            "",
            "| Metric | Count |",
            "| --- | ---: |",
            f"| Emails scanned | {summary['emails_scanned']} |",
            f"| Accepted (supported KPI) | {summary['accepted_kpi']} |",
            f"| Recognized unsupported KPI | {summary['recognized_unsupported_kpi']} |",
            f"| Safe rejected (non-KPI) | {summary['rejected']} |",
            f"| Review required | {summary['review_candidates']} |",
            f"| Duplicate inputs | {summary['duplicate_inputs']} |",
            "",
            "## Unsupported KPI Families",
            "",
            "| Family | Count |",
            "| --- | ---: |",
        ]
    )
    if unsupported_counts:
        for family, count in unsupported_counts.items():
            lines.append(f"| {family} | {count} |")
    else:
        lines.append("| None | 0 |")
    lines.extend(
        [
            "",
            "## Top Review Reasons",
            "",
        ]
    )
    if top_review_reasons:
        for item in top_review_reasons:
            lines.append(f"- `{item['reason']}` — {item['count']}")
    else:
        lines.append("- None.")
    if summary.get("top_unknown_subject_patterns"):
        lines += [
            "",
            "## Top Unknown Subject Patterns",
            "",
            "| Count | Subject Prefix |",
            "|------:|----------------|",
        ]
        for entry in summary["top_unknown_subject_patterns"][:15]:
            lines.append(f"| {entry['count']} | `{entry['subject_prefix']}` |")
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Metric | Count |",
            "| --- | ---: |",
            f"| New cases ({case_label}) | {summary['new_cases_expected_or_created']} |",
            f"| Case updates ({case_label}) | {summary['case_updates_expected_or_done']} |",
            f"| Memory observations created | {summary['memory_observations_created']} |",
            f"| Pattern flags created | {summary['pattern_flags_created']} |",
            "",
            "Review candidates are listed in `review_candidates.json` and are NOT in the live review queue.",
            "",
            "## Safety",
            "",
            (
                f"AI calls: {summary['ai_calls']} | "
                f"Outbound: {summary['outbound_emails']} | "
                f"Follow-ups: {summary['followups_scheduled']}"
            ),
            "",
            "## Top Rejected Subjects",
            "",
        ]
    )
    if top_rejected:
        for item in top_rejected:
            lines.append(f"- `{item['subject']}` — {item['count']} ({item['reason']})")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Files",
            "",
            "- [rejected.json](rejected.json)",
            "- [review_candidates.json](review_candidates.json)",
            "- [unsupported_kpis.json](unsupported_kpis.json)",
            "- [recipient_summary.json](recipient_summary.json)",
        ]
    )
    detail_items = _detail_report_items(summary, report_detail)
    detail_label = "All Processed Records" if report_detail == "full" else "Rejected and Review Records"
    lines.extend(
        [
            "",
            f"## {detail_label}",
            "",
        ]
    )
    if detail_items:
        for item in detail_items:
            subject = str(item.get("subject") or "(no subject)")
            action = str(item.get("action") or "unknown")
            reason = str(item.get("reason") or "")
            case_type = str(item.get("case_type") or "")
            suffix = f" — {reason}" if reason else ""
            type_text = f" `{case_type}`" if case_type else ""
            lines.append(f"- `{action}`{type_text}: {subject}{suffix}")
    else:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def _default_report_dir() -> Path:
    """Return a timestamped default directory under ``data/backlog_runs``."""
    timestamp = utc_compact_timestamp()
    return PROJECT_ROOT / "data" / "backlog_runs" / timestamp


def _report_paths(report_dir: Path) -> Dict[str, str]:
    """Return all report output paths for a backlog run directory."""
    return {
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "rejected_json": str(report_dir / "rejected.json"),
        "review_candidates_json": str(report_dir / "review_candidates.json"),
        "unsupported_kpis_json": str(report_dir / "unsupported_kpis.json"),
        "recipient_summary_json": str(report_dir / "recipient_summary.json"),
    }


def _generated_at_timestamp() -> str:
    """Return the report generation timestamp in display format."""
    return utc_display_timestamp()


def _synthetic_message_id(subject: str, received_at: str, body: str) -> str:
    """Stable synthetic ID — hashes full body; uses empty string if received_at absent."""
    digest = hashlib.sha256(f"{subject}|{received_at}|{body}".encode("utf-8")).hexdigest()
    return f"backlog:{digest}"


def _normalize_addresses(value: Any) -> List[str]:
    """Normalize one or more address strings into lowercase email addresses."""
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        items: Iterable[str] = [value]
    elif isinstance(value, list):
        items = [str(item) for item in value if item not in (None, "")]
    else:
        items = [str(value)]

    parsed = []
    for _name, address in getaddresses(items):
        cleaned = address.strip().lower()
        if cleaned:
            parsed.append(cleaned)
    if parsed:
        return parsed

    fallback = []
    for item in items:
        for part in re.split(r"[;,]", item):
            cleaned = part.strip().lower()
            if cleaned:
                fallback.append(cleaned)
    return fallback


def _optional_text(value: Any) -> Optional[str]:
    """Return stripped text for optional JSON fields, or None when empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _domain_for(address: str) -> Optional[str]:
    """Return the domain portion of an email address, if present."""
    if "@" not in address:
        return None
    return address.rsplit("@", 1)[1].lower()


def _body_preview(body: str, limit: int = 200) -> str:
    """Collapse body whitespace and return a short report preview."""
    collapsed = _WHITESPACE_RE.sub(" ", body).strip()
    return collapsed[:limit]


def _result_stub(raw: Dict[str, Any], action: str, reason: str) -> Dict[str, Any]:
    """Build a report item for records that failed before normalization."""
    subject = str(raw.get("subject", "")).strip()
    body = str(raw.get("body", "")).strip()
    return {
        "action": action,
        "reason": reason,
        "subject": subject,
        "from_addr": _optional_text(raw.get("from_addr")),
        "to_addrs": _normalize_addresses(raw.get("to_addrs") or raw.get("to_addr")),
        "cc_addrs": _normalize_addresses(raw.get("cc_addrs") or raw.get("cc_addr")),
        "bcc_addrs": _normalize_addresses(raw.get("bcc_addrs") or raw.get("bcc_addr")),
        "reply_to": _optional_text(raw.get("reply_to")),
        "received_at": _optional_text(raw.get("received_at")),
        "body_preview": _body_preview(body) if body else "",
    }


def _decorate_for_report(record: Dict[str, Any], details: Dict[str, Any]) -> Dict[str, Any]:
    """Merge normalized record metadata with outcome-specific report fields."""
    return {
        "message_id": record.get("message_id"),
        "subject": record.get("subject"),
        "from_addr": record.get("from_addr"),
        "to_addrs": list(record.get("to_addrs", []) or []),
        "cc_addrs": list(record.get("cc_addrs", []) or []),
        "bcc_addrs": list(record.get("bcc_addrs", []) or []),
        "reply_to": record.get("reply_to"),
        "received_at": record.get("received_at"),
        "body_preview": _body_preview(record.get("body", "")),
        **details,
    }


def _email_import_description(record: Dict[str, Any]) -> str:
    """Build an audit-event description preserving original recipient metadata."""
    to_addrs = ", ".join(record.get("to_addrs", [])) or "(none)"
    cc_addrs = ", ".join(record.get("cc_addrs", [])) or "(none)"
    reply_to = record.get("reply_to") or "(none)"
    return (
        "Historical backlog email imported. "
        f"From: {record.get('from_addr') or '(unknown)'}. "
        f"To: {to_addrs}. "
        f"Cc: {cc_addrs}. "
        f"Reply-To: {reply_to}."
    )


def _count_rows(table_name: str) -> int:
    """Return a row count for trusted table names used by backlog reporting."""
    conn = db.get_connection()
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    return int(row["count"]) if row else 0


def _report_json_payload(summary: Dict[str, Any], report_detail: str = "summary") -> Dict[str, Any]:
    payload = {
        "mode": summary["mode"],
        "generated_at": summary["generated_at"],
        "source_path": summary["source_path"],
        "report_detail": report_detail,
        "emails_scanned": summary["emails_scanned"],
        "accepted_kpi": summary["accepted_kpi"],
        "recognized_unsupported_kpi": summary["recognized_unsupported_kpi"],
        "rejected": summary["rejected"],
        "review_candidates": summary["review_candidates"],
        "duplicate_inputs": summary["duplicate_inputs"],
        "new_cases_expected_or_created": summary["new_cases_expected_or_created"],
        "case_updates_expected_or_done": summary["case_updates_expected_or_done"],
        "expected_memory_observations": summary.get("expected_memory_observations", 0),
        "memory_observations_created": summary["memory_observations_created"],
        "pattern_flags_created": summary["pattern_flags_created"],
        "ai_calls": summary["ai_calls"],
        "outbound_emails": summary["outbound_emails"],
        "followups_scheduled": summary["followups_scheduled"],
        "common_rejected_subjects": summary["common_rejected_subjects"],
        "unsupported_kpi_counts_by_family": summary["unsupported_kpi_counts_by_family"],
        "top_review_reasons": _top_review_reasons(summary.get("review_items", [])),
        "top_unknown_subject_patterns": summary.get("top_unknown_subject_patterns", []),
        "top_supported_extraction_failures": summary.get("top_supported_extraction_failures", []),
        "unique_recipients": summary["unique_recipients"],
        "detail_items": _detail_report_items(summary, report_detail),
    }
    if summary["dry_run"]:
        payload["dry_run_note"] = summary["dry_run_note"]
    return payload


def _detail_report_items(summary: Dict[str, Any], report_detail: str) -> List[Dict[str, Any]]:
    if report_detail == "full":
        return [_compact_detail_item(item) for item in summary.get("results", [])]
    return [
        _compact_detail_item(item)
        for item in (
            list(summary.get("rejected_items", []))
            + list(summary.get("review_items", []))
        )
    ]


def _compact_detail_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return a compact per-record report item suitable for summary/full modes."""
    compact: Dict[str, Any] = {
        "action": item.get("action"),
        "message_id": item.get("message_id"),
        "subject": item.get("subject"),
        "from_addr": item.get("from_addr"),
        "received_at": item.get("received_at"),
        "case_type": item.get("case_type"),
        "reason": item.get("reason"),
        "body_preview": item.get("body_preview", ""),
    }
    for key in (
        "case_id",
        "email_id",
        "grouping_key",
        "existing_case_id",
        "expected_action",
        "action_taken",
        "unsupported_family",
    ):
        if item.get(key) is not None:
            compact[key] = item.get(key)
    if item.get("fields"):
        compact["fields"] = item["fields"]
    return compact


def _rejected_report_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "subject": item.get("subject"),
            "from_addr": item.get("from_addr"),
            "received_at": item.get("received_at"),
            "rejection_reason": item.get("reason"),
            "body_preview": item.get("body_preview", ""),
        }
        for item in items
    ]


def _review_report_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "subject": item.get("subject"),
            "from_addr": item.get("from_addr"),
            "received_at": item.get("received_at"),
            "review_reason": item.get("reason"),
            "classified_as": item.get("case_type") or CASE_TYPE_UNKNOWN,
            "body_preview": item.get("body_preview", ""),
        }
        for item in items
    ]


def _unsupported_kpi_report_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "subject": item.get("subject"),
            "from_addr": item.get("from_addr"),
            "received_at": item.get("received_at"),
            "unsupported_family": item.get("unsupported_family"),
            "reason": item.get("reason"),
            "body_preview": item.get("body_preview", ""),
        }
        for item in items
    ]


def _top_rejected_subjects(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counter = Counter(
        (
            item.get("subject") or "(no subject)",
            item.get("reason") or "Rejected during backlog import.",
        )
        for item in items
    )
    return [
        {"subject": subject, "count": count, "reason": reason}
        for (subject, reason), count in counter.most_common(10)
    ]


def _count_by_family(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        family = item.get("unsupported_family") or CASE_TYPE_UNKNOWN
        counter[family] += 1
    return dict(counter.most_common())


def _top_review_reasons(items: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    counter: Counter[str] = Counter()
    for item in items:
        reason = item.get("reason", "unknown")
        counter[reason] += 1
    return [{"reason": reason, "count": count} for reason, count in counter.most_common(limit)]


def _top_unknown_subject_patterns(review_items: List[Dict[str, Any]], n: int = 20) -> List[Dict[str, Any]]:
    """Return top N subject prefixes from records with UNKNOWN classification."""
    unknown_items = [
        item for item in review_items
        if item.get("case_type", CASE_TYPE_UNKNOWN) == CASE_TYPE_UNKNOWN
    ]
    counter: Counter = Counter()
    for item in unknown_items:
        subj = re.sub(r"\s+", " ", str(item.get("subject", ""))).strip()
        prefix = subj[:70] if subj else "(empty)"
        counter[prefix] += 1
    return [{"subject_prefix": subj, "count": cnt} for subj, cnt in counter.most_common(n)]


def _top_supported_extraction_failures(review_items: List[Dict[str, Any]], n: int = 15) -> List[Dict[str, Any]]:
    """Return top N extraction failure reasons from review items that matched a supported KPI."""
    supported_fails = [
        item for item in review_items
        if item.get("case_type", CASE_TYPE_UNKNOWN) != CASE_TYPE_UNKNOWN
    ]
    counter: Counter = Counter()
    for item in supported_fails:
        reason = str(item.get("reason", ""))
        counter[reason] += 1
    return [{"reason": r, "count": c} for r, c in counter.most_common(n)]


def _report_dir_for_display(path: str) -> str:
    report_dir = Path(path)
    try:
        display_path = report_dir.relative_to(PROJECT_ROOT)
    except ValueError:
        display_path = report_dir
    return f"{display_path.as_posix().rstrip('/')}/"


def _print_progress(
    processed: int,
    total: int,
    start_time: float,
    accepted: int,
    rejected: int,
    review: int,
    unsupported: int,
) -> None:
    elapsed = int(time.monotonic() - start_time)
    print(
        f"[BACKLOG] Progress: {processed}/{total} processed | "
        f"accepted: {accepted} rejected: {rejected} review: {review} "
        f"unsupported: {unsupported} | elapsed: {elapsed}s"
    )


def _print_summary(summary: Dict[str, Any]) -> None:
    border = "[BACKLOG] ============================================================"
    mode_label = "DRY RUN" if summary["dry_run"] else "COMMIT"
    print(border)
    print(f"[BACKLOG] Backlog Loading Mode — {mode_label}")
    print(border)
    print(f"[BACKLOG]   Emails scanned:             {summary['emails_scanned']}")
    print(f"[BACKLOG]   Accepted (supported KPI):   {summary['accepted_kpi']}")
    print(f"[BACKLOG]   Recognized unsupported KPI: {summary['recognized_unsupported_kpi']}")
    print(f"[BACKLOG]   Safe rejected (non-KPI):    {summary['rejected']}")
    print(f"[BACKLOG]   Review required:            {summary['review_candidates']}")
    print(f"[BACKLOG]   Duplicate inputs:           {summary['duplicate_inputs']}")
    print(f"[BACKLOG]   New cases:             {summary['new_cases_expected_or_created']}")
    print(f"[BACKLOG]   Case updates:          {summary['case_updates_expected_or_done']}")
    print(f"[BACKLOG]   AI calls:              {summary['ai_calls']}")
    print(f"[BACKLOG]   Outbound emails:       {summary['outbound_emails']}")
    print(f"[BACKLOG]   Follow-ups scheduled:  {summary['followups_scheduled']}")
    print(border)
    print(f"[BACKLOG]   Reports written to: {_report_dir_for_display(summary['report_dir'])}")
    print(border)


def _flatten(values: Iterable[Iterable[str]]) -> Iterable[str]:
    for group in values:
        for item in group:
            yield item
