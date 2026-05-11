"""
backlog_loader.py - Standalone importer for staged historical KPI emails.

Supports JSON source only. Deterministic only. No outbound. No follow-ups.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from email.utils import getaddresses
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from classifier import _NOISE_PATTERNS, _deterministic_classification
from claude_client import detect_injection, sanitize_email_content
from config import PROJECT_ROOT
import database as db
import extractor
import memory

_SUPPORTED_CASE_TYPES: frozenset[str] = frozenset({
    "CAT1_COMPLIANCE",
    "CAT5_COMPLIANCE",
    "DATA_ABSENCE",
    "MAINTENANCE_HOURS_SHORTFALL",
    "MAJOR_WORK_OVERDUE",
    "GOVERNMENT_DIRECTIVE",
})
_BACKLOG_SUBJECT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("cat1", "CAT1_COMPLIANCE"),
    ("cat5", "CAT5_COMPLIANCE"),
    ("data absence", "DATA_ABSENCE"),
    ("maintenance data is not up to date", "DATA_ABSENCE"),
    ("maintenance data has never been submitted", "DATA_ABSENCE"),
    ("maintenance hours less than required", "MAINTENANCE_HOURS_SHORTFALL"),
    ("maintenance hours shortfall", "MAINTENANCE_HOURS_SHORTFALL"),
    ("major scheduled work is overdue", "MAJOR_WORK_OVERDUE"),
    ("scheduled work is overdue", "MAJOR_WORK_OVERDUE"),
    ("outstanding government directive", "GOVERNMENT_DIRECTIVE"),
    ("government directive", "GOVERNMENT_DIRECTIVE"),
)
_NON_KPI_PATTERNS = _NOISE_PATTERNS + (
    "read receipt",
    "meeting accepted",
    "meeting declined",
    "invoice",
    "statement",
    "newsletter",
)
_DEVICE_PATTERN = re.compile(r"\b[A-Z]-\d+\s*#\d+\b", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def load_backlog(
    source: str,
    path: Path,
    dry_run: bool,
    limit: Optional[int] = None,
    report_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    if source != "json":
        raise ValueError(f"Unsupported backlog source: {source!r}. Only 'json' is supported.")
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")

    raw_records = _load_json_source(path)
    if limit is not None:
        raw_records = raw_records[:limit]

    run_dir = Path(report_dir) if report_dir else _default_report_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    rejected_items: List[Dict[str, Any]] = []
    duplicate_items: List[Dict[str, Any]] = []
    review_items: List[Dict[str, Any]] = []
    accepted_results: List[Dict[str, Any]] = []
    seen_message_ids: set[str] = set()
    planned_grouping_keys: set[str] = set()

    duplicates = 0
    expected_new_cases = 0
    expected_case_updates = 0
    expected_memory_observations = 0

    for raw in raw_records:
        try:
            normalized = _normalize_record(raw)
        except ValueError as exc:
            item = _result_stub(raw, action="rejected", reason=str(exc))
            results.append(item)
            rejected_items.append(item)
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
        elif result["action"] == "rejected":
            rejected_items.append(result)
        elif result["action"] == "duplicate":
            duplicate_items.append(result)
            duplicates += 1

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
        "ai_calls": ai_report["total_ai_calls"],
        "outbound_emails": 0,
        "followups_scheduled": 0,
        "results": results,
        "rejected_items": rejected_items,
        "duplicate_items": duplicate_items,
        "review_items": review_items,
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

    _write_reports(summary, run_dir, dry_run)
    _print_summary(summary)
    return summary


def _load_json_source(path: Path) -> List[Dict[str, Any]]:
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
    subject = str(record.get("subject", "")).strip()
    body = str(record.get("body", "")).strip()
    if not subject:
        return True, "Empty subject."
    if not body:
        return True, "Empty body."

    normalized_subject = subject.lower()
    for pattern in _NON_KPI_PATTERNS:
        if pattern in normalized_subject:
            return True, f"Matched obvious non-KPI pattern: {pattern}."
    return False, ""


def _classify_for_backlog(record: Dict[str, Any]) -> Dict[str, Any]:
    subject_case_type = _match_subject_to_case_type(record["subject"])
    if subject_case_type is None:
        return {
            "source": "backlog_subject_gate",
            "case_type": "UNKNOWN",
            "reason": "Subject did not match any supported backlog KPI pattern.",
        }

    result = _deterministic_classification(record["subject"], record["body"])
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
        if pattern in lowered:
            return case_type
    return None


def _validate_body_signature(case_type: str, body: str) -> bool:
    lowered = body.lower()
    if case_type in {"CAT1_COMPLIANCE", "CAT5_COMPLIANCE"}:
        return bool(_DEVICE_PATTERN.search(body)) or (
            ("test" in lowered or "reminder" in lowered) and "building" in lowered
        )
    if case_type == "DATA_ABSENCE":
        return "data" in lowered and any(
            phrase in lowered for phrase in ("missing", "not submitted", "absence", "elapsed", "not up to date")
        )
    if case_type == "MAINTENANCE_HOURS_SHORTFALL":
        return "hours" in lowered and any(
            phrase in lowered for phrase in ("required", "contract", "actual")
        )
    if case_type == "MAJOR_WORK_OVERDUE":
        return any(phrase in lowered for phrase in ("scheduled", "overdue", "scheduleddate"))
    if case_type == "GOVERNMENT_DIRECTIVE":
        return any(phrase in lowered for phrase in ("directive", "duedate", "due date"))
    return False


def _process_record(record: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    normalized = _normalize_record(record)

    is_non_kpi, reason = _is_obvious_non_kpi(normalized)
    if is_non_kpi:
        return _decorate_for_report(normalized, {"action": "rejected", "reason": reason})

    if detect_injection(normalized["subject"]) or detect_injection(normalized["body"]):
        return _decorate_for_report(
            normalized,
            {
                "action": "review",
                "case_type": "UNKNOWN",
                "reason": "Backlog import review: prompt-injection pattern detected in the email content.",
            },
        )

    classification = _classify_for_backlog(normalized)
    if classification["source"] == "noise":
        return _decorate_for_report(normalized, {"action": "rejected", "reason": classification["reason"]})

    case_type = classification["case_type"]
    if case_type == "UNKNOWN":
        return _decorate_for_report(
            normalized,
            {
                "action": "review",
                "case_type": "UNKNOWN",
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
            event_type="backlog_case_created",
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
            event_type="backlog_case_updated",
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

    db.insert_case_event(
        event_id=str(uuid.uuid4()),
        case_id=case_id,
        event_type="backlog_email_imported",
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
        event_type="backlog_memory_updated",
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
    fields = extractor._empty_fields()
    fields.update(extractor._extract_common_fields(body))
    case_specific_fields = extractor._extract_case_specific_fields(subject, body, case_type, fields)
    for field_name, value in case_specific_fields.items():
        if value is not None:
            fields[field_name] = value

    missing_required = [
        field_name
        for field_name in extractor._REQUIRED_FIELDS.get(case_type, ())
        if not fields.get(field_name)
    ]
    return fields, missing_required


def _estimate_observation_count(case_type: str, fields: Dict[str, Any]) -> int:
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


def _write_reports(results: Dict[str, Any], report_dir: Path, dry_run: bool) -> None:
    del dry_run
    report_paths = _report_paths(report_dir)

    Path(report_paths["report_json"]).write_text(
        json.dumps(_report_json_payload(results), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    Path(report_paths["report_md"]).write_text(
        _build_markdown_report(results),
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
    Path(report_paths["recipient_summary_json"]).write_text(
        json.dumps(results["recipient_summary"], indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _build_markdown_report(summary: Dict[str, Any]) -> str:
    mode_label = "DRY RUN" if summary["dry_run"] else "COMMIT"
    case_label = "Expected / Created" if summary["dry_run"] else "Created / Updated"
    top_rejected = summary["common_rejected_subjects"]
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
            f"| Accepted (KPI) | {summary['accepted_kpi']} |",
            f"| Rejected | {summary['rejected']} |",
            f"| Review candidates | {summary['review_candidates']} |",
            f"| Duplicate inputs | {summary['duplicate_inputs']} |",
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
            f"AI calls: {summary['ai_calls']} | Outbound: {summary['outbound_emails']} | Follow-ups: {summary['followups_scheduled']}",
            "",
            "## Top Rejected Subjects",
            "",
        ]
    )
    if top_rejected:
        for item in top_rejected:
            lines.append(
                f"- `{item['subject']}` — {item['count']} ({item['reason']})"
            )
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Files",
            "",
            "- [rejected.json](rejected.json)",
            "- [review_candidates.json](review_candidates.json)",
            "- [recipient_summary.json](recipient_summary.json)",
        ]
    )
    return "\n".join(lines) + "\n"


def _default_report_dir() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return PROJECT_ROOT / "data" / "backlog_runs" / timestamp


def _report_paths(report_dir: Path) -> Dict[str, str]:
    return {
        "report_json": str(report_dir / "report.json"),
        "report_md": str(report_dir / "report.md"),
        "rejected_json": str(report_dir / "rejected.json"),
        "review_candidates_json": str(report_dir / "review_candidates.json"),
        "recipient_summary_json": str(report_dir / "recipient_summary.json"),
    }


def _generated_at_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _synthetic_message_id(subject: str, received_at: str, body: str) -> str:
    """Stable synthetic ID — hashes full body; uses empty string if received_at absent."""
    digest = hashlib.sha256(f"{subject}|{received_at}|{body}".encode("utf-8")).hexdigest()
    return f"backlog:{digest}"


def _normalize_addresses(value: Any) -> List[str]:
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
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _domain_for(address: str) -> Optional[str]:
    if "@" not in address:
        return None
    return address.rsplit("@", 1)[1].lower()


def _body_preview(body: str, limit: int = 200) -> str:
    collapsed = _WHITESPACE_RE.sub(" ", body).strip()
    return collapsed[:limit]


def _result_stub(raw: Dict[str, Any], action: str, reason: str) -> Dict[str, Any]:
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
    conn = db.get_connection()
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    return int(row["count"]) if row else 0


def _report_json_payload(summary: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "mode": summary["mode"],
        "generated_at": summary["generated_at"],
        "source_path": summary["source_path"],
        "emails_scanned": summary["emails_scanned"],
        "accepted_kpi": summary["accepted_kpi"],
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
        "unique_recipients": summary["unique_recipients"],
    }
    if summary["dry_run"]:
        payload["dry_run_note"] = summary["dry_run_note"]
    return payload


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
            "classified_as": item.get("case_type") or "UNKNOWN",
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


def _report_dir_for_display(path: str) -> str:
    report_dir = Path(path)
    try:
        display_path = report_dir.relative_to(PROJECT_ROOT)
    except ValueError:
        display_path = report_dir
    return f"{display_path.as_posix().rstrip('/')}/"


def _print_summary(summary: Dict[str, Any]) -> None:
    border = "[BACKLOG] ============================================================"
    mode_label = "DRY RUN" if summary["dry_run"] else "COMMIT"
    print(border)
    print(f"[BACKLOG] Backlog Loading Mode — {mode_label}")
    print(border)
    print(f"[BACKLOG]   Emails scanned:        {summary['emails_scanned']}")
    print(f"[BACKLOG]   Accepted (KPI):        {summary['accepted_kpi']}")
    print(f"[BACKLOG]   Rejected:              {summary['rejected']}")
    print(f"[BACKLOG]   Review candidates:     {summary['review_candidates']}")
    print(f"[BACKLOG]   Duplicate inputs:      {summary['duplicate_inputs']}")
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
