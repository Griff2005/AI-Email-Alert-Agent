"""
demo_scale_harness.py — Safe large-scale demo harness for the triage agent.

Runs the real case pipeline against synthetic KPI alerts while isolating the
database, hard-blocking network email access, optionally replacing Claude with
deterministic offline responses, and producing JSON/Markdown reports.
"""

from __future__ import annotations

import json
import re
import shutil
import smtplib
import imaplib
from collections import Counter, defaultdict
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

from ai_gateway import AiUsageConfig, get_ai_gateway
import case_manager
import claude_client
import database as db
import email_sender
import followup
import memory
from config import PROJECT_ROOT, config
from demo_fixtures import (
    ALLOWED_TEST_DOMAINS,
    SAFE_DEMO_RECIPIENT,
    SyntheticDataset,
    allowed_test_domain,
    generate_synthetic_dataset,
    infer_case_type,
    parse_expected_fields,
)
from runtime_options import RuntimeOptions, runtime_options


@dataclass(frozen=True)
class ScaleTestOptions:
    emails: int = 150
    clients: int = 8
    buildings: int = 25
    devices_per_building: int = 4
    seed: int = 42
    offline: bool = True
    enable_ai: bool = False
    require_ai: bool = False
    keep_db: bool = True
    validate_memory_connections: bool = False
    include_mechanics: bool = False
    allow_uncapped_ai: bool = False
    max_ai_calls: Optional[int] = 0
    max_ai_calls_per_email: int = 0
    max_ai_calls_per_case: int = 0
    ai_budget_mode: str = "manual_review"
    disable_outbound_generation: bool = False
    template_outbound_only: bool = True
    ai_outbound_enabled: bool = False
    disable_followups: bool = True
    max_followups: int = 3
    max_followup_runs: int = 0
    report_dir: Path = PROJECT_ROOT / "data" / "test_runs"
    verbose: bool = False


@dataclass
class ScaleTestResult:
    overall_result: str
    dataset: Dict[str, Any]
    processing: Dict[str, Any]
    extraction: Dict[str, Any]
    ai_usage: Dict[str, Any]
    manual_reviews: Dict[str, Any]
    safety: Dict[str, Any]
    quality_checks: Dict[str, str]
    memory_readiness: Dict[str, Any]
    memory_connection_audit: Dict[str, Any]
    warnings: List[str]
    failures: List[str]
    paths: Dict[str, Path]
    mode: Dict[str, Any]


@dataclass
class _SafetyMonitor:
    real_smtp_calls_attempted: int = 0
    real_imap_calls_attempted: int = 0
    actual_recipient_violations: int = 0
    disallowed_domain_violations: int = 0
    intended_recipient_rewrites: int = 0


@dataclass
class _CheckTally:
    classification_total: int = 0
    classification_failures: int = 0
    extraction_total: int = 0
    extraction_failures: int = 0
    grouping_total: int = 0
    grouping_failures: int = 0
    reply_total: int = 0
    reply_failures: int = 0
    followup_total: int = 0
    followup_failures: int = 0
    injection_total: int = 0
    injection_failures: int = 0
    ui_total: int = 0
    ui_failures: int = 0


_DESCRIPTION_OPTIONAL_CASE_TYPES = {"CAT1_COMPLIANCE", "CAT5_COMPLIANCE"}
_GENERIC_DESCRIPTION_TERMS = {
    "DATA_ABSENCE": {"data", "absence", "submitted", "missing", "stale", "up to date"},
    "MAINTENANCE_HOURS_SHORTFALL": {"maintenance", "hours", "shortfall", "less than required", "below required", "contract hours", "actual hours"},
    "MAJOR_WORK_OVERDUE": {"replace", "repair", "door operator", "component", "scheduled work", "overdue", "corrective work"},
    "GOVERNMENT_DIRECTIVE": {"submit confirmation", "corrective action", "completed", "directive", "due", "required action"},
}


def _new_extraction_audit() -> Dict[str, Any]:
    return {
        "structured_field_failures": 0,
        "semantic_description_mismatches": 0,
        "optional_description_missing": 0,
        "extraction_warnings": 0,
        "extraction_failures": 0,
        "validation_rows": [],
    }


def _normalize_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return " ".join(str(value).strip().lower().split())


def _field_matches(field_name: str, actual: Optional[str], expected: Optional[str]) -> bool:
    if expected is None:
        return True
    if actual is None:
        return False
    if field_name in {"hours_required", "hours_actual"}:
        try:
            return abs(float(actual) - float(expected)) < 1e-9
        except ValueError:
            return _normalize_text(actual) == _normalize_text(expected)
    if field_name == "elapsed_days":
        try:
            return int(float(actual)) == int(float(expected))
        except ValueError:
            return _normalize_text(actual) == _normalize_text(expected)
    return _normalize_text(actual) == _normalize_text(expected)


def _core_fields_for_case_type(case_type: str) -> List[str]:
    if case_type == "DATA_ABSENCE":
        return ["building", "contractor"]
    if case_type == "MAINTENANCE_HOURS_SHORTFALL":
        return ["building", "contractor", "period", "hours_required", "hours_actual"]
    if case_type == "MAJOR_WORK_OVERDUE":
        return ["building", "contractor", "device", "scheduled_date"]
    if case_type == "GOVERNMENT_DIRECTIVE":
        return ["building", "contractor", "device", "due_date"]
    return ["building", "device", "contractor"]


def _description_tokens(description: Optional[str]) -> set:
    if not description:
        return set()
    tokens = re.findall(r"[a-z0-9#]+", description.lower())
    return {
        token
        for token in tokens
        if token not in {"the", "this", "that", "with", "and", "for", "are", "has", "have", "been", "than", "less"}
    }


def _description_semantically_matches(
    case_type: str,
    actual_description: Optional[str],
    expected_description: Optional[str],
    actual_fields: Dict[str, Optional[str]],
    expected_fields: Dict[str, Optional[str]],
) -> Tuple[bool, str]:
    normalized_actual = _normalize_text(actual_description) or ""
    normalized_expected = _normalize_text(expected_description) or ""

    if not expected_description and not actual_description:
        return True, "Description not expected."
    if normalized_actual and normalized_actual == normalized_expected:
        return True, "Description matched after normalization."

    generic_terms = _GENERIC_DESCRIPTION_TERMS.get(case_type, set())
    actual_tokens = _description_tokens(actual_description)
    expected_tokens = _description_tokens(expected_description)
    combined_terms = {term for term in generic_terms if term in normalized_actual}
    token_overlap = expected_tokens.intersection(actual_tokens)

    if case_type == "DATA_ABSENCE":
        has_indicator = any(
            phrase in normalized_actual
            for phrase in ("not submitted", "never submitted", "not up to date", "missing", "stale", "data absence")
        ) or len(token_overlap.intersection({"data", "submitted", "missing", "stale", "absence"})) >= 2
        if has_indicator:
            return True, "Description semantically matches the data-absence issue."
    elif case_type == "MAINTENANCE_HOURS_SHORTFALL":
        has_indicator = any(phrase in normalized_actual for phrase in ("less than required", "below required", "contract hours", "actual hours"))
        has_indicator = has_indicator or len(token_overlap.intersection({"maintenance", "hours", "shortfall", "required", "actual", "contract"})) >= 2
        if has_indicator:
            return True, "Description semantically matches the maintenance-hours shortfall."
    elif case_type == "MAJOR_WORK_OVERDUE":
        if combined_terms or len(token_overlap) >= 2:
            return True, "Description semantically matches the major work item."
    elif case_type == "GOVERNMENT_DIRECTIVE":
        directive_text = actual_fields.get("directive_tasks") or actual_description or ""
        normalized_directive = _normalize_text(directive_text) or ""
        has_indicator = any(phrase in normalized_directive for phrase in ("submit confirmation", "corrective action", "required action", "directive", "completed"))
        if has_indicator or len(token_overlap) >= 2:
            return True, "Description semantically matches the directive action."
    else:
        return True, "Description is optional for this case type."

    core_fields_ok = all(
        _field_matches(field_name, actual_fields.get(field_name), expected_fields.get(field_name))
        for field_name in _core_fields_for_case_type(case_type)
        if expected_fields.get(field_name) is not None
    )
    if core_fields_ok:
        return True, "Core structured fields are correct; description variation is tolerated."
    return False, "Description did not semantically match and core structured fields are incomplete."


def compare_extracted_fields(
    case_type: str,
    actual_fields: Dict[str, Optional[str]],
    expected_fields: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    """Deterministically compare extracted fields with flexible description validation."""
    failures: List[str] = []
    warning_messages: List[str] = []
    validation_rows: List[Dict[str, Any]] = []
    structured_field_failures = 0
    semantic_description_mismatches = 0
    optional_description_missing = 0

    for field_name, expected in expected_fields.items():
        if field_name in {"description", "directive_tasks"} or expected is None:
            continue
        actual = actual_fields.get(field_name)
        matched = _field_matches(field_name, actual, expected)
        validation_rows.append(
            {
                "field_name": field_name,
                "expected": expected,
                "actual": actual,
                "result": "PASS" if matched else "FAIL",
                "reason": "Structured field matched." if matched else "Structured field mismatch.",
            }
        )
        if not matched:
            structured_field_failures += 1
            failures.append(f"extraction mismatch for {field_name}: expected {expected!r}, got {actual!r}")

    if expected_fields.get("directive_tasks") is not None:
        actual_directive = actual_fields.get("directive_tasks")
        expected_directive = expected_fields.get("directive_tasks")
        matched = _field_matches("directive_tasks", actual_directive, expected_directive)
        if not matched and case_type == "GOVERNMENT_DIRECTIVE":
            matched, reason = _description_semantically_matches(
                case_type=case_type,
                actual_description=actual_directive or actual_fields.get("description"),
                expected_description=expected_directive,
                actual_fields=actual_fields,
                expected_fields=expected_fields,
            )
            if matched:
                semantic_description_mismatches += 1
                warning_messages.append("Directive tasks varied semantically but core directive fields are correct.")
            validation_rows.append(
                {
                    "field_name": "directive_tasks",
                    "expected": expected_directive,
                    "actual": actual_directive,
                    "result": "PASS" if matched else "FAIL",
                    "reason": reason if matched else "Directive tasks mismatch.",
                }
            )
            if not matched:
                structured_field_failures += 1
                failures.append(
                    f"extraction mismatch for directive_tasks: expected {expected_directive!r}, got {actual_directive!r}"
                )
        else:
            validation_rows.append(
                {
                    "field_name": "directive_tasks",
                    "expected": expected_directive,
                    "actual": actual_directive,
                    "result": "PASS" if matched else "FAIL",
                    "reason": "Directive tasks matched." if matched else "Directive tasks mismatch.",
                }
            )
            if not matched:
                structured_field_failures += 1
                failures.append(
                    f"extraction mismatch for directive_tasks: expected {expected_directive!r}, got {actual_directive!r}"
                )

    expected_description = expected_fields.get("description")
    actual_description = actual_fields.get("description")
    if case_type in _DESCRIPTION_OPTIONAL_CASE_TYPES:
        validation_rows.append(
            {
                "field_name": "description",
                "expected": expected_description,
                "actual": actual_description,
                "result": "PASS",
                "reason": "Description is optional for CAT compliance cases.",
            }
        )
    elif expected_description is not None:
        if not actual_description:
            matched, reason = _description_semantically_matches(
                case_type=case_type,
                actual_description=actual_description,
                expected_description=expected_description,
                actual_fields=actual_fields,
                expected_fields=expected_fields,
            )
            validation_rows.append(
                {
                    "field_name": "description",
                    "expected": expected_description,
                    "actual": actual_description,
                    "result": "PASS" if matched else "FAIL",
                    "reason": reason,
                }
            )
            if matched:
                optional_description_missing += 1
                warning_messages.append(f"Description missing for {case_type}, but structured fields were sufficient.")
            else:
                structured_field_failures += 1
                failures.append(f"description missing and structured fallback was insufficient for {case_type}")
        else:
            normalized_match = _field_matches("description", actual_description, expected_description)
            if normalized_match:
                validation_rows.append(
                    {
                        "field_name": "description",
                        "expected": expected_description,
                        "actual": actual_description,
                        "result": "PASS",
                        "reason": "Description matched after normalization.",
                    }
                )
            else:
                matched, reason = _description_semantically_matches(
                    case_type=case_type,
                    actual_description=actual_description,
                    expected_description=expected_description,
                    actual_fields=actual_fields,
                    expected_fields=expected_fields,
                )
                validation_rows.append(
                    {
                        "field_name": "description",
                        "expected": expected_description,
                        "actual": actual_description,
                        "result": "PASS" if matched else "FAIL",
                        "reason": reason,
                    }
                )
                if matched:
                    semantic_description_mismatches += 1
                    warning_messages.append(f"Description wording varied for {case_type} but remained semantically correct.")
                else:
                    structured_field_failures += 1
                    failures.append(
                        f"description mismatch for {case_type}: expected semantic match to {expected_description!r}, got {actual_description!r}"
                    )

    return {
        "failures": failures,
        "warnings": len(warning_messages),
        "warning_messages": warning_messages,
        "structured_field_failures": structured_field_failures,
        "semantic_description_mismatches": semantic_description_mismatches,
        "optional_description_missing": optional_description_missing,
        "extraction_warnings": len(warning_messages),
        "extraction_failures": len(failures),
        "validation_rows": validation_rows,
    }


def run_demo_scale_test(options: ScaleTestOptions) -> ScaleTestResult:
    """Run the scale harness and write reports to the configured report dir."""
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = options.report_dir / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    database_path = run_dir / "test_agent.db"
    cache_path = run_dir / "claude_cache.json"
    log_path = run_dir / "harness.log"

    dataset = generate_synthetic_dataset(
        total_emails=options.emails,
        client_count=options.clients,
        building_count=options.buildings,
        devices_per_building=options.devices_per_building,
        seed=options.seed,
        include_mechanics=options.include_mechanics,
    )

    warnings: List[str] = []
    failures: List[str] = []
    quality = _CheckTally()
    monitor = _SafetyMonitor()
    extraction_audit = _new_extraction_audit()
    processing: Dict[str, Any] = {}
    manual_reviews: Dict[str, Any] = {"total": 0, "open": 0, "manual_review_reason_breakdown": {}, "exact_reason_rows": []}
    memory_connection_audit: Dict[str, Any] = {
        "enabled": options.validate_memory_connections,
        "status": "Not requested.",
        "expected_pattern_flags": [],
        "actual_pattern_flags": [],
        "matched_expected_flags": [],
        "missing_expected_flags": [],
        "unexpected_pattern_flags": [],
        "false_positive_links": 0,
        "evidence_mismatch_count": 0,
        "duplicate_pattern_flags": 0,
        "mechanic_flags_expected": 0,
        "mechanic_flags_actual": 0,
        "validation_rows": [],
    }
    mode = {
        "requested_offline": options.offline,
        "requested_enable_ai": options.enable_ai,
        "requested_require_ai": options.require_ai,
        "used_offline_shim": False,
        "used_ai": False,
        "ai_probe_successful": False,
        "ai_dependent_checks_skipped": False,
    }

    with ExitStack() as stack:
        _apply_config_overrides(
            stack=stack,
            database_path=database_path,
            cache_path=cache_path,
        )
        _install_network_blocks(stack=stack, monitor=monitor)
        _install_sender_guards(stack=stack, monitor=monitor)
        runtime_options.configure(
            RuntimeOptions(
                ai_enabled=options.enable_ai,
                allow_uncapped_ai=options.allow_uncapped_ai,
                max_ai_calls=options.max_ai_calls,
                max_ai_calls_per_email=options.max_ai_calls_per_email,
                max_ai_calls_per_case=options.max_ai_calls_per_case,
                ai_budget_mode=options.ai_budget_mode,
                ai_report_path=run_dir / "ai_usage_report.json",
                disable_outbound_generation=options.disable_outbound_generation,
                template_outbound_only=options.template_outbound_only,
                ai_outbound_enabled=options.ai_outbound_enabled,
                followups_enabled=not options.disable_followups,
                max_followups=options.max_followups,
                max_followup_runs=options.max_followup_runs,
            )
        )
        gateway = get_ai_gateway()
        gateway.reset()
        gateway.configure(
            AiUsageConfig(
                enabled=options.enable_ai,
                allow_uncapped_ai=options.allow_uncapped_ai,
                max_calls=options.max_ai_calls,
                max_calls_per_email=options.max_ai_calls_per_email,
                max_calls_per_case=options.max_ai_calls_per_case,
                budget_mode=options.ai_budget_mode,
                report_path=run_dir / "ai_usage_report.json",
                csv_report_path=run_dir / "ai_usage_report.csv",
                cache_path=cache_path,
                model_name=config.CLAUDE_MODEL,
                config_version="demo-scale-harness-v2",
            )
        )
        gateway.set_run_metadata(
            requested_emails=options.emails,
            clients=options.clients,
            buildings=options.buildings,
            devices_per_building=options.devices_per_building,
            seed=options.seed,
            offline=options.offline,
            enable_ai=options.enable_ai,
        )

        if options.enable_ai:
            if options.offline:
                _install_offline_gateway_transport()
                mode["used_offline_shim"] = True
                mode["used_ai"] = True
                mode["ai_probe_successful"] = True
            else:
                ai_available, ai_error = _claude_cli_available()
                if ai_available:
                    mode["used_ai"] = True
                    mode["ai_probe_successful"] = True
                elif options.require_ai:
                    failures.append(f"Claude CLI required but unavailable: {ai_error}")
                else:
                    warnings.append(f"Claude unavailable, falling back to zero-AI mode: {ai_error}")
                    mode["ai_dependent_checks_skipped"] = True
                    runtime_options.configure(
                        RuntimeOptions(
                            ai_enabled=False,
                            allow_uncapped_ai=False,
                            max_ai_calls=0,
                            max_ai_calls_per_email=0,
                            max_ai_calls_per_case=0,
                            ai_budget_mode=options.ai_budget_mode,
                            ai_report_path=run_dir / "ai_usage_report.json",
                            disable_outbound_generation=options.disable_outbound_generation,
                            template_outbound_only=options.template_outbound_only,
                            ai_outbound_enabled=False,
                            followups_enabled=not options.disable_followups,
                            max_followups=options.max_followups,
                            max_followup_runs=options.max_followup_runs,
                        )
                    )
                    gateway.configure(
                        AiUsageConfig(
                            enabled=False,
                            allow_uncapped_ai=False,
                            max_calls=0,
                            max_calls_per_email=0,
                            max_calls_per_case=0,
                            budget_mode=options.ai_budget_mode,
                            report_path=run_dir / "ai_usage_report.json",
                            csv_report_path=run_dir / "ai_usage_report.csv",
                            cache_path=cache_path,
                            model_name=config.CLAUDE_MODEL,
                            config_version="demo-scale-harness-v2",
                        )
                    )

        if failures:
            get_ai_gateway().write_report(run_dir / "ai_usage_report.json", run_dir / "ai_usage_report.csv")
            result = _build_result(
                dataset=dataset,
                processing={},
                extraction=extraction_audit,
                ai_usage=get_ai_gateway().build_report(),
                manual_reviews=manual_reviews,
                monitor=monitor,
                quality=quality,
                memory_connection_audit=memory_connection_audit,
                warnings=warnings,
                failures=failures,
                paths={
                    "run_dir": run_dir,
                    "database": database_path,
                    "harness_log": log_path,
                    "ai_usage_report": run_dir / "ai_usage_report.json",
                    "ai_usage_report_csv": run_dir / "ai_usage_report.csv",
                    "report_json": run_dir / "report.json",
                    "report_markdown": run_dir / "report.md",
                },
                mode=mode,
            )
            _write_reports(result)
            return result

        expected_seen: Dict[str, int] = defaultdict(int)
        case_ids_by_key: Dict[str, str] = {}
        process_results: List[Dict[str, Any]] = []
        reply_results: List[Dict[str, Any]] = []
        followup_summary: Dict[str, Any] = {"valid": False, "errors": ["Follow-up phase not run."], "cases_touched": 0}
        ui_summary: Dict[str, Any] = {"valid": False, "errors": ["UI phase not run."], "checks": []}

        log_handle = open(log_path, "w", encoding="utf-8")
        stack.callback(log_handle.close)
        if options.verbose:
            output_context = nullcontext()
        else:
            output_context = ExitStack()
            output_context.enter_context(redirect_stdout(log_handle))
            output_context.enter_context(redirect_stderr(log_handle))

        with output_context:
            db.close_connection()
            db.init_schema()
            config.validate()

            for index, fixture in enumerate(dataset.emails, start=1):
                _validate_safe_address(fixture.from_addr, monitor, failures, "inbound from")
                _validate_safe_address(fixture.to_addr, monitor, failures, "inbound to")
                email_id = _insert_email_fixture(fixture)
                result = case_manager.process_email(
                    email_id=email_id,
                    subject=fixture.subject,
                    body=fixture.body,
                    from_addr=fixture.from_addr,
                    received_at=fixture.received_at,
                    verbose=options.verbose,
                )
                process_results.append(
                    {
                        "fixture": fixture,
                        "result": result,
                        "email_id": email_id,
                    }
                )
                expected_seen[fixture.expected_grouping_family] += 1
                if result.get("case_id"):
                    case_ids_by_key.setdefault(fixture.expected_grouping_family, result["case_id"])

                quality.classification_total += 1
                if result["case_type"] != fixture.expected_case_type:
                    quality.classification_failures += 1
                    failures.append(
                        f"Classification mismatch for {fixture.message_id}: "
                        f"expected {fixture.expected_case_type}, got {result['case_type']}."
                    )

                quality.grouping_total += 1
                expected_action = "created" if expected_seen[fixture.expected_grouping_family] == 1 else "updated"
                if result["action"] != expected_action:
                    quality.grouping_failures += 1
                    failures.append(
                        f"Grouping mismatch for {fixture.message_id}: expected action {expected_action}, got {result['action']}."
                    )

                if result.get("case_id"):
                    quality.extraction_total += 1
                    expected_fields = fixture.expected_core_fields
                    if result["action"] != "created":
                        expected_fields = {
                            key: value
                            for key, value in expected_fields.items()
                            if key in {"building", "device", "contractor", "due_date", "period"}
                        }
                    actual_fields = _case_fields_for_validation(result["case_id"])
                    field_comparison = compare_extracted_fields(
                        case_type=result["case_type"],
                        actual_fields=actual_fields,
                        expected_fields=expected_fields,
                    )
                    _record_extraction_comparison(
                        audit=extraction_audit,
                        fixture=fixture,
                        case_id=result["case_id"],
                        comparison=field_comparison,
                    )
                    if field_comparison["extraction_failures"]:
                        quality.extraction_failures += 1
                        failures.extend(
                            f"{fixture.message_id}: {message}" for message in field_comparison["failures"]
                        )

                if "prompt_injection_attempt" in fixture.synthetic_scenario_tags:
                    quality.injection_total += 1
                    if not result.get("injection_detected"):
                        quality.injection_failures += 1
                        failures.append(f"Prompt injection was not flagged for {fixture.message_id}.")

            for plan in dataset.reply_plans:
                case_id = case_ids_by_key.get(plan.case_key)
                if not case_id:
                    warnings.append(f"Reply plan skipped because case key was not created: {plan.case_key}")
                    continue
                before_case = db.get_case_by_id(case_id)
                result = case_manager.process_reply(case_id=case_id, reply_text=plan.reply_text, verbose=options.verbose)
                after_case = db.get_case_by_id(case_id)
                reply_results.append({"plan": plan, "result": result, "case_id": case_id})

                quality.reply_total += 1
                reply_failures = _validate_reply_result(
                    case_id=case_id,
                    reply_plan=plan,
                    result=result,
                    before_case=dict(before_case) if before_case else {},
                    after_case=dict(after_case) if after_case else {},
                )
                if reply_failures:
                    quality.reply_failures += 1
                    failures.extend(reply_failures)
                if plan.reply_type == "prompt_injection_reply":
                    quality.injection_total += 1

            if options.disable_followups:
                followup_summary = {
                    "disabled": True,
                    "valid": True,
                    "errors": [],
                    "cases_touched": 0,
                    "followup_rows": [],
                }
            else:
                followup_case_ids = [case_ids_by_key[key] for key in dataset.followup_case_keys if key in case_ids_by_key]
                followup_summary = _simulate_followups(followup_case_ids)
                quality.followup_total = followup_summary["cases_touched"]
                quality.followup_failures = 0 if followup_summary["valid"] else 1
                if not followup_summary["valid"]:
                    failures.extend(followup_summary["errors"])

            injection_reply_failures = _validate_prompt_injection_replies(reply_results)
            if injection_reply_failures:
                quality.injection_failures += 1
                failures.extend(injection_reply_failures)

            ui_summary = _run_ui_smoke(case_ids_by_key)
            quality.ui_total = len(ui_summary["checks"])
            quality.ui_failures = 0 if ui_summary["valid"] else 1
            if not ui_summary["valid"]:
                failures.extend(ui_summary["errors"])

        if monitor.intended_recipient_rewrites:
            warnings.append(
                f"Rewrote {monitor.intended_recipient_rewrites} non-test intended recipient values to safe placeholder domains."
            )

        processing = _collect_processing_summary(
            dataset=dataset,
            process_results=process_results,
            reply_results=reply_results,
            followup_summary=followup_summary,
            case_ids_by_key=case_ids_by_key,
        )
        manual_reviews = _manual_review_summary()
        if options.validate_memory_connections:
            memory_connection_audit = _run_memory_connection_audit(
                dataset=dataset,
                case_ids_by_key=case_ids_by_key,
                processing=processing,
                include_mechanics=options.include_mechanics,
            )

    db.close_connection()
    if extraction_audit["extraction_warnings"]:
        warnings.append(
            "Extraction warnings recorded: "
            f"{extraction_audit['semantic_description_mismatches']} semantic description variations, "
            f"{extraction_audit['optional_description_missing']} optional descriptions missing."
        )
    if options.validate_memory_connections:
        audit_status = memory_connection_audit.get("status")
        if audit_status == "FAIL":
            failures.append(
                "Memory connection audit failed: "
                f"{len(memory_connection_audit.get('missing_expected_flags', []))} missing expected flags, "
                f"{len(memory_connection_audit.get('unexpected_pattern_flags', []))} unexpected flags, "
                f"{memory_connection_audit.get('evidence_mismatch_count', 0)} evidence mismatches."
            )
        elif audit_status == "PASS WITH WARNINGS":
            warnings.append(
                "Memory connection audit completed with warnings: "
                f"{memory_connection_audit.get('false_positive_links', 0)} false-positive links, "
                f"{memory_connection_audit.get('duplicate_pattern_flags', 0)} duplicate pattern flags."
            )
    if not database_path.exists():
        failures.append(f"Retained test database is missing at end of run: {database_path}")
    get_ai_gateway().set_run_metadata(
        emails_processed=processing.get("emails_processed", 0),
        cases_created=processing.get("cases_created", 0),
        existing_cases_updated=processing.get("existing_cases_updated", 0),
    )
    get_ai_gateway().write_report(run_dir / "ai_usage_report.json", run_dir / "ai_usage_report.csv")

    result = _build_result(
        dataset=dataset,
        processing=processing,
        extraction=extraction_audit,
        ai_usage=get_ai_gateway().build_report(),
        manual_reviews=manual_reviews,
        monitor=monitor,
        quality=quality,
        memory_connection_audit=memory_connection_audit,
        warnings=warnings,
        failures=failures,
        paths={
            "run_dir": run_dir,
            "database": database_path,
            "harness_log": log_path,
            "ai_usage_report": run_dir / "ai_usage_report.json",
            "ai_usage_report_csv": run_dir / "ai_usage_report.csv",
            "report_json": run_dir / "report.json",
            "report_markdown": run_dir / "report.md",
        },
        mode=mode,
    )
    _write_reports(result)
    db.close_connection()
    return result


def _apply_config_overrides(stack: ExitStack, database_path: Path, cache_path: Path) -> None:
    config_obj = config
    config_cls = type(config)
    overrides = {
        "DATABASE_PATH": database_path,
        "CLAUDE_CACHE_PATH": cache_path,
        "DEMO_RECIPIENT_EMAIL": SAFE_DEMO_RECIPIENT,
        "DEMO_MODE": True,
        "IMAP_HOST": "imap.placeholder.com",
        "IMAP_PORT": 993,
        "SMTP_HOST": "smtp.placeholder.com",
        "SMTP_PORT": 587,
        "AGENT_EMAIL": "agent@example.com",
        "AGENT_EMAIL_PASSWORD": "PLACEHOLDER",
    }
    originals: Dict[str, Tuple[Any, Any]] = {}
    for key, value in overrides.items():
        originals[key] = (getattr(config_obj, key), getattr(config_cls, key))
        setattr(config_obj, key, value)
        setattr(config_cls, key, value)

    def restore() -> None:
        db.close_connection()
        for key, (instance_value, class_value) in originals.items():
            setattr(config_obj, key, instance_value)
            setattr(config_cls, key, class_value)

    stack.callback(restore)


def _install_network_blocks(stack: ExitStack, monitor: _SafetyMonitor) -> None:
    def blocked_smtp(*args: Any, **kwargs: Any) -> Any:
        monitor.real_smtp_calls_attempted += 1
        raise AssertionError("SMTP use is blocked by the demo scale harness.")

    def blocked_imap(*args: Any, **kwargs: Any) -> Any:
        monitor.real_imap_calls_attempted += 1
        raise AssertionError("IMAP use is blocked by the demo scale harness.")

    stack.enter_context(patch.object(smtplib, "SMTP", side_effect=blocked_smtp))
    stack.enter_context(patch.object(smtplib, "SMTP_SSL", side_effect=blocked_smtp))
    stack.enter_context(patch.object(imaplib, "IMAP4_SSL", side_effect=blocked_imap))


def _normalize_safe_recipient(value: str, monitor: _SafetyMonitor) -> str:
    if not value or "@" not in value:
        return value
    local, domain = value.rsplit("@", 1)
    if domain.lower() in ALLOWED_TEST_DOMAINS:
        return value
    monitor.intended_recipient_rewrites += 1
    safe_local = re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-") or "recipient"
    return f"{safe_local}@example.com"


def _install_sender_guards(stack: ExitStack, monitor: _SafetyMonitor) -> None:
    real_create_draft = email_sender.create_draft

    def safe_create_draft(
        case_id: str,
        subject: str,
        body: str,
        intended_to: str,
        intended_cc: str = "",
    ) -> str:
        intended_to = _normalize_safe_recipient(intended_to, monitor)
        intended_cc = ",".join(
            _normalize_safe_recipient(part.strip(), monitor)
            for part in intended_cc.split(",")
            if part.strip()
        )
        msg_id = real_create_draft(case_id, subject, body, intended_to, intended_cc)
        row = db.get_connection().execute(
            "SELECT actual_to FROM outbound_messages WHERE msg_id = ?",
            (msg_id,),
        ).fetchone()
        actual_to = str(row["actual_to"]) if row else ""
        if actual_to != SAFE_DEMO_RECIPIENT:
            monitor.actual_recipient_violations += 1
            raise AssertionError(
                f"Unsafe outbound actual recipient detected: expected {SAFE_DEMO_RECIPIENT}, got {actual_to}."
            )
        return msg_id

    stack.enter_context(patch.object(email_sender, "create_draft", side_effect=safe_create_draft))


class _DeterministicClaudeShim:
    def complete_json(self, prompt: str, model_name: str) -> Dict[str, Any]:
        del model_name
        if "TASK: Classify this email into exactly one" in prompt:
            return self._classify(prompt)
        if "TASK: Extract structured fields from this" in prompt:
            return self._extract(prompt)
        if "ANALYSIS TASK:" in prompt and "reply email" in prompt:
            return self._analyze_reply(prompt)
        raise ValueError("Unsupported Claude JSON prompt in offline mode.")

    def complete_text(self, prompt: str, model_name: str) -> str:
        del model_name
        if "professional follow-up email" not in prompt.lower():
            return "OK"
        case_type = self._capture_case_type(prompt)
        building = self._capture_prompt_field(prompt, "building")
        contractor = self._capture_prompt_field(prompt, "contractor")
        device = self._capture_prompt_field(prompt, "device")
        detail = building or device or "the referenced device"
        contractor_phrase = contractor or "your team"
        return (
            f"This is a follow-up regarding {case_type.replace('_', ' ').title()} at {detail}.\n\n"
            f"Please provide a written update and next action plan within 5 business days. "
            f"If work is already scheduled or completed, reply with the date and supporting detail so {contractor_phrase} can be reviewed."
        )

    def _classify(self, prompt: str) -> Dict[str, Any]:
        subject = self._capture_subject(prompt)
        body = self._capture_email_content(prompt)
        case_type = infer_case_type(subject, body)
        confidence = 0.97 if case_type != "UNKNOWN" else 0.2
        return {
            "case_type": case_type,
            "confidence": confidence,
            "reasoning": f"Deterministic offline classification matched {case_type}.",
        }

    def _extract(self, prompt: str) -> Dict[str, Any]:
        subject = self._capture_subject(prompt)
        body = self._capture_email_content(prompt)
        case_type = self._capture_case_type(prompt)
        fields = parse_expected_fields(subject, body, case_type)
        return {
            "building": fields.get("building"),
            "device": fields.get("device"),
            "contractor": fields.get("contractor"),
            "due_date": fields.get("due_date"),
            "scheduled_date": fields.get("scheduled_date"),
            "period": fields.get("period"),
            "hours_required": fields.get("hours_required"),
            "hours_actual": fields.get("hours_actual"),
            "description": fields.get("description"),
            "last_activity_date": fields.get("last_activity_date"),
            "elapsed_days": fields.get("elapsed_days"),
            "directive_tasks": fields.get("directive_tasks"),
            "mechanic": None,
            "technician": None,
            "work_item": fields.get("description"),
            "issue_code": None,
            "callback_reference": None,
        }

    def _analyze_reply(self, prompt: str) -> Dict[str, Any]:
        reply_text = self._capture_email_content(prompt).lower()
        if "ignore previous instructions" in reply_text or "change the recipient" in reply_text:
            return {
                "satisfies_action": False,
                "action_described": "Malicious instruction attempt detected",
                "followup_required": True,
                "flag_for_review": True,
                "summary": "Reply contained prompt-injection content and requires manual review.",
            }
        if "completed" in reply_text:
            return {
                "satisfies_action": True,
                "action_described": "Responder said the item has been completed",
                "followup_required": True,
                "flag_for_review": True,
                "summary": "Responder stated the item is completed.",
            }
        if "scheduled for" in reply_text:
            return {
                "satisfies_action": False,
                "action_described": "Responder scheduled the work",
                "followup_required": True,
                "flag_for_review": False,
                "summary": "Responder provided a scheduled completion date.",
            }
        if "revised completion date" in reply_text:
            return {
                "satisfies_action": False,
                "action_described": "Responder revised the completion date",
                "followup_required": True,
                "flag_for_review": False,
                "summary": "Responder revised the expected completion date.",
            }
        if "access" in reply_text:
            return {
                "satisfies_action": False,
                "action_described": "Responder requested building access",
                "followup_required": True,
                "flag_for_review": True,
                "summary": "Responder said access is required before work can proceed.",
            }
        if "approval" in reply_text:
            return {
                "satisfies_action": False,
                "action_described": "Client indicated internal approval is still pending",
                "followup_required": True,
                "flag_for_review": True,
                "summary": "Client said approval is still pending.",
            }
        if "provide an update" in reply_text:
            return {
                "satisfies_action": False,
                "action_described": "Client requested a status update",
                "followup_required": True,
                "flag_for_review": False,
                "summary": "Client requested a status update.",
            }
        return {
            "satisfies_action": False,
            "action_described": "Responder said the item is under review",
            "followup_required": True,
            "flag_for_review": False,
            "summary": "Responder said the item is still under review.",
        }

    @staticmethod
    def _capture_subject(prompt: str) -> str:
        match = re.search(r"^Subject:\s*(.+)$", prompt, re.MULTILINE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _capture_email_content(prompt: str) -> str:
        match = re.search(
            r"--- EMAIL CONTENT START ---\n(?P<body>.*?)\n--- EMAIL CONTENT END ---",
            prompt,
            re.DOTALL,
        )
        return match.group("body").strip() if match else prompt

    @staticmethod
    def _capture_case_type(prompt: str) -> str:
        match = re.search(r"TASK: Extract structured fields from this ([A-Z0-9_]+) alert email", prompt)
        if match:
            return match.group(1)
        match = re.search(r"Case Type:\s*([A-Z0-9_]+)", prompt)
        if match:
            return match.group(1)
        return "UNKNOWN"

    @staticmethod
    def _capture_prompt_field(prompt: str, field_name: str) -> Optional[str]:
        match = re.search(rf"^\s*{re.escape(field_name)}:\s*(.+)$", prompt, re.MULTILINE | re.IGNORECASE)
        if not match:
            return None
        value = match.group(1).strip()
        return None if value.lower() == "n/a" else value


def _install_offline_gateway_transport() -> None:
    shim = _DeterministicClaudeShim()
    get_ai_gateway().set_test_transports(
        json_transport=shim.complete_json,
        text_transport=shim.complete_text,
        transport_mode="mocked",
    )


def _claude_cli_available() -> Tuple[bool, str]:
    if shutil.which("claude") is None:
        return False, "claude binary not found on PATH"
    return True, ""


def _insert_email_fixture(fixture: Any) -> str:
    email_id = fixture.message_id.replace("@", "-").replace(".", "-")
    normalized = claude_client.sanitize_email_content(fixture.body)
    db.insert_email(
        email_id=email_id,
        message_id=fixture.message_id,
        thread_id=fixture.thread_id,
        subject=fixture.subject,
        from_addr=fixture.from_addr,
        to_addr=fixture.to_addr,
        received_at=fixture.received_at,
        raw_body=fixture.body,
        normalized_text=normalized,
    )
    return email_id


def _validate_safe_address(value: str, monitor: _SafetyMonitor, failures: List[str], label: str) -> None:
    if allowed_test_domain(value):
        return
    monitor.disallowed_domain_violations += 1
    failures.append(f"Disallowed domain detected for {label}: {value}")


def _field_value_for_case(case_id: str, field_name: str) -> Optional[str]:
    case_row = db.get_case_by_id(case_id)
    if case_row and field_name in {"building", "device", "contractor", "due_date", "period", "case_type"}:
        value = case_row[field_name]
        return str(value) if value is not None else None
    row = db.get_connection().execute(
        """
        SELECT field_value
        FROM extracted_fields
        WHERE case_id = ? AND field_name = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (case_id, field_name),
    ).fetchone()
    if not row:
        return None
    value = row["field_value"]
    return str(value) if value is not None else None


def _case_fields_for_validation(case_id: str) -> Dict[str, Optional[str]]:
    fields = {
        "building": _field_value_for_case(case_id, "building"),
        "device": _field_value_for_case(case_id, "device"),
        "contractor": _field_value_for_case(case_id, "contractor"),
        "due_date": _field_value_for_case(case_id, "due_date"),
        "scheduled_date": _field_value_for_case(case_id, "scheduled_date"),
        "period": _field_value_for_case(case_id, "period"),
        "hours_required": _field_value_for_case(case_id, "hours_required"),
        "hours_actual": _field_value_for_case(case_id, "hours_actual"),
        "description": _field_value_for_case(case_id, "description"),
        "last_activity_date": _field_value_for_case(case_id, "last_activity_date"),
        "elapsed_days": _field_value_for_case(case_id, "elapsed_days"),
        "directive_tasks": _field_value_for_case(case_id, "directive_tasks"),
        "mechanic": _field_value_for_case(case_id, "mechanic"),
        "technician": _field_value_for_case(case_id, "technician"),
        "work_item": _field_value_for_case(case_id, "work_item"),
        "issue_code": _field_value_for_case(case_id, "issue_code"),
        "callback_reference": _field_value_for_case(case_id, "callback_reference"),
    }
    case_row = db.get_case_by_id(case_id)
    if case_row:
        fields["case_type"] = str(case_row["case_type"])
    return fields


def _record_extraction_comparison(
    audit: Dict[str, Any],
    fixture: Any,
    case_id: str,
    comparison: Dict[str, Any],
) -> None:
    audit["structured_field_failures"] += comparison["structured_field_failures"]
    audit["semantic_description_mismatches"] += comparison["semantic_description_mismatches"]
    audit["optional_description_missing"] += comparison["optional_description_missing"]
    audit["extraction_warnings"] += comparison["extraction_warnings"]
    audit["extraction_failures"] += comparison["extraction_failures"]

    interesting_rows = [
        row
        for row in comparison["validation_rows"]
        if row["result"] != "PASS"
        or "varied" in row["reason"].lower()
        or "missing" in row["reason"].lower()
        or "tolerated" in row["reason"].lower()
    ]
    if not interesting_rows:
        return
    audit["validation_rows"].append(
        {
            "message_id": fixture.message_id,
            "case_id": case_id,
            "case_type": fixture.expected_case_type,
            "warnings": comparison["warning_messages"],
            "failures": comparison["failures"],
            "field_results": interesting_rows,
        }
    )


def _manual_review_bucket(reason: str) -> str:
    normalized = (reason or "").lower()
    if "prompt injection" in normalized:
        return "prompt_injection"
    if normalized.startswith("pattern review:"):
        return "pattern_review"
    if normalized.startswith("escalated:") or "follow-ups sent with no resolution" in normalized:
        return "followup_escalation"
    if "reply flagged" in normalized:
        return "reply_requires_review"
    if "confidence" in normalized or "unknown" in normalized:
        return "classification_review"
    if "approval" in normalized:
        return "approval_pending"
    if "access" in normalized:
        return "access_needed"
    if "completed" in normalized or "completion" in normalized:
        return "completion_claim"
    return "other"


def _manual_review_summary() -> Dict[str, Any]:
    conn = db.get_connection()
    total = int(conn.execute("SELECT COUNT(*) AS count FROM manual_reviews").fetchone()["count"])
    open_count = int(conn.execute("SELECT COUNT(*) AS count FROM manual_reviews WHERE resolved = 0").fetchone()["count"])
    reason_rows = [
        {"reason": str(row["reason"] or ""), "count": int(row["count"])}
        for row in conn.execute(
            """
            SELECT reason, COUNT(*) AS count
            FROM manual_reviews
            GROUP BY reason
            ORDER BY count DESC, reason ASC
            """
        ).fetchall()
    ]
    breakdown = Counter()
    for row in reason_rows:
        breakdown[_manual_review_bucket(row["reason"])] += row["count"]
    return {
        "total": total,
        "open": open_count,
        "manual_review_reason_breakdown": dict(sorted(breakdown.items())),
        "exact_reason_rows": reason_rows,
    }


def _normalized_entity(value: Optional[str]) -> str:
    return memory.normalize_text(value or "")


def _flag_descriptor(row: Dict[str, Any]) -> Dict[str, Any]:
    evidence: Dict[str, Any] = {}
    if row.get("evidence_json"):
        try:
            evidence = json.loads(str(row["evidence_json"]))
        except json.JSONDecodeError:
            evidence = {"_invalid_json": str(row["evidence_json"])}
    entity_type = evidence.get("entity_type")
    entity_value = evidence.get("entity_value")
    return {
        "flag_id": int(row["id"]),
        "case_id": row.get("case_id"),
        "pattern_type": row["pattern_type"],
        "severity": row["severity"],
        "summary": row["summary"],
        "entity_type": entity_type,
        "entity_value": entity_value,
        "entity_key": (row["pattern_type"], entity_type or "", _normalized_entity(entity_value)),
        "supporting_case_ids": [str(value) for value in evidence.get("supporting_case_ids", [])],
        "supporting_observation_ids": [int(value) for value in evidence.get("supporting_observation_ids", []) if value is not None],
        "evidence": evidence,
    }


def _matching_link_count(link_rows: List[Dict[str, Any]], case_ids: List[str], link_types: set) -> int:
    case_id_set = set(case_ids)
    count = 0
    for row in link_rows:
        if row["link_type"] not in link_types:
            continue
        if row["source_case_id"] in case_id_set and row["target_case_id"] in case_id_set:
            count += 1
    return count


def _validate_flag_evidence(
    descriptor: Dict[str, Any],
    cases_by_id: Dict[str, Dict[str, Any]],
    observations_by_id: Dict[int, Dict[str, Any]],
) -> Tuple[List[str], List[str]]:
    evidence = descriptor["evidence"]
    reasons: List[str] = []
    mismatches: List[str] = []
    required_keys = {
        "rule",
        "entity_type",
        "entity_value",
        "time_window_days",
        "threshold",
        "observed_count",
        "supporting_case_ids",
        "supporting_observation_ids",
    }
    missing_keys = sorted(key for key in required_keys if key not in evidence)
    if missing_keys:
        mismatches.append(f"missing evidence keys: {', '.join(missing_keys)}")
        return reasons, mismatches

    entity_type = descriptor["entity_type"]
    entity_value = descriptor["entity_value"]
    entity_norm = _normalized_entity(entity_value)
    if not entity_type or not entity_norm:
        mismatches.append("missing entity_type/entity_value in evidence_json")
        return reasons, mismatches

    supporting_case_ids = descriptor["supporting_case_ids"]
    supporting_observation_ids = descriptor["supporting_observation_ids"]
    if not supporting_case_ids and not supporting_observation_ids:
        mismatches.append("evidence_json did not include supporting case or observation ids")
        return reasons, mismatches

    def case_matches(case_row: Dict[str, Any]) -> bool:
        if entity_type == "building":
            return _normalized_entity(case_row.get("building")) == entity_norm
        if entity_type == "device":
            return _normalized_entity(case_row.get("device")) == entity_norm
        if entity_type == "contractor":
            return _normalized_entity(case_row.get("contractor")) == entity_norm
        if entity_type == "case":
            return str(case_row.get("case_id")) == str(entity_value)
        return True

    for case_id in supporting_case_ids:
        case_row = cases_by_id.get(case_id)
        if not case_row:
            mismatches.append(f"supporting case does not exist: {case_id}")
            continue
        if not case_matches(case_row):
            mismatches.append(
                f"supporting case {case_id} does not match {entity_type}={entity_value!r}"
            )

    for observation_id in supporting_observation_ids:
        observation = observations_by_id.get(observation_id)
        if not observation:
            mismatches.append(f"supporting observation does not exist: {observation_id}")
            continue
        if observation.get("case_id") and observation["case_id"] not in supporting_case_ids:
            reasons.append(
                f"supporting observation {observation_id} refers to case {observation['case_id']}"
            )
        if entity_type == "building":
            observation_entity = observation.get("entity_value") or observation.get("building")
        elif entity_type == "device":
            observation_entity = observation.get("entity_value") or observation.get("device")
        elif entity_type == "contractor":
            observation_entity = observation.get("entity_value") or observation.get("contractor")
        elif entity_type == "mechanic":
            observation_entity = observation.get("entity_value")
        else:
            observation_entity = observation.get("entity_value")
        if entity_type in {"building", "device", "contractor", "mechanic"} and _normalized_entity(observation_entity) != entity_norm:
            mismatches.append(
                f"supporting observation {observation_id} does not match {entity_type}={entity_value!r}"
            )

    return reasons, mismatches


def _run_memory_connection_audit(
    dataset: SyntheticDataset,
    case_ids_by_key: Dict[str, str],
    processing: Dict[str, Any],
    include_mechanics: bool,
) -> Dict[str, Any]:
    conn = db.get_connection()
    case_expectations = list(dataset.metadata.get("case_expectations", []))
    controls = dict(dataset.metadata.get("memory_controls", {}))
    cases_by_id = {str(row["case_id"]): dict(row) for row in db.get_all_cases()}
    observations = [
        dict(row)
        for row in conn.execute(
            """
            SELECT o.*, c.building, c.device, c.contractor
            FROM observations o
            LEFT JOIN cases c ON c.case_id = o.case_id
            ORDER BY o.id ASC
            """
        ).fetchall()
    ]
    observations_by_id = {int(row["id"]): row for row in observations}
    link_rows = [dict(row) for row in conn.execute("SELECT * FROM case_links").fetchall()]
    entity_rows = [dict(row) for row in conn.execute("SELECT * FROM entities").fetchall()]
    active_flags = [_flag_descriptor(dict(row)) for row in db.get_active_pattern_flags()]

    validation_rows: List[Dict[str, Any]] = []
    expected_flags: List[Dict[str, Any]] = []
    matched_expected_flags: List[Dict[str, Any]] = []
    missing_expected_flags: List[Dict[str, Any]] = []
    unexpected_pattern_flags: List[Dict[str, Any]] = []
    evidence_mismatch_count = 0
    false_positive_links = 0
    warning_count = 0
    hard_failures = 0

    def add_expected(pattern_type: str, entity_type: str, entity_value: str, support_case_keys: List[str], reason: str) -> None:
        support_case_ids = [case_ids_by_key[key] for key in support_case_keys if key in case_ids_by_key]
        expected_flags.append(
            {
                "pattern_type": pattern_type,
                "entity_type": entity_type,
                "entity_value": entity_value,
                "entity_key": (pattern_type, entity_type, _normalized_entity(entity_value)),
                "supporting_case_keys": sorted(set(support_case_keys)),
                "supporting_case_ids": sorted(set(support_case_ids)),
                "reason": reason,
            }
        )

    def expectations_for_group(
        pattern_type: str,
        entity_type: str,
        expectations: List[Dict[str, Any]],
        entity_field: str,
        threshold: int,
        reason_label: str,
    ) -> None:
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for expectation in expectations:
            entity_value = expectation.get(entity_field)
            if entity_value:
                groups[_normalized_entity(str(entity_value))].append(expectation)
        for group in groups.values():
            if len(group) < threshold:
                continue
            add_expected(
                pattern_type=pattern_type,
                entity_type=entity_type,
                entity_value=str(group[0][entity_field]),
                support_case_keys=[str(item["case_key"]) for item in group],
                reason=f"{reason_label} threshold met by {len(group)} cases.",
            )

    expectations_for_group(
        pattern_type="repeated_building_issue",
        entity_type="building",
        expectations=[item for item in case_expectations if "recurring_building_issue" in item.get("tags", [])],
        entity_field="building",
        threshold=3,
        reason_label="Recurring building",
    )
    expectations_for_group(
        pattern_type="repeated_device_issue",
        entity_type="device",
        expectations=[item for item in case_expectations if "recurring_device_issue" in item.get("tags", [])],
        entity_field="device",
        threshold=2,
        reason_label="Recurring device",
    )
    expectations_for_group(
        pattern_type="repeated_data_absence",
        entity_type="building",
        expectations=[item for item in case_expectations if item.get("case_type") == "DATA_ABSENCE"],
        entity_field="building",
        threshold=2,
        reason_label="Repeated data absence",
    )
    expectations_for_group(
        pattern_type="repeated_maintenance_shortfall",
        entity_type="building",
        expectations=[item for item in case_expectations if item.get("case_type") == "MAINTENANCE_HOURS_SHORTFALL"],
        entity_field="building",
        threshold=2,
        reason_label="Repeated maintenance shortfall",
    )

    contractor_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for expectation in case_expectations:
        contractor = expectation.get("contractor")
        if contractor:
            contractor_groups[_normalized_entity(str(contractor))].append(expectation)
    for group in contractor_groups.values():
        if any("low_risk_contractor_control" in item.get("tags", []) for item in group):
            continue
        case_count = len(group)
        high_priority_count = sum(
            item.get("case_type") in {"MAJOR_WORK_OVERDUE", "GOVERNMENT_DIRECTIVE", "MAINTENANCE_HOURS_SHORTFALL"}
            for item in group
        )
        if case_count >= 3 or high_priority_count >= 2:
            add_expected(
                pattern_type="repeated_contractor_issue",
                entity_type="contractor",
                entity_value=str(group[0]["contractor"]),
                support_case_keys=[str(item["case_key"]) for item in group],
                reason=f"Recurring contractor threshold met by {case_count} cases and {high_priority_count} high-priority cases.",
            )

    shortfall_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for expectation in case_expectations:
        if expectation.get("case_type") != "MAINTENANCE_HOURS_SHORTFALL" or not expectation.get("contractor"):
            continue
        shortfall_groups[_normalized_entity(str(expectation["contractor"]))].append(expectation)
    for group in shortfall_groups.values():
        contractor_buildings = {str(item.get("building") or "") for item in group if item.get("building")}
        if len(contractor_buildings) >= 3:
            add_expected(
                pattern_type="repeated_maintenance_shortfall",
                entity_type="contractor",
                entity_value=str(group[0]["contractor"]),
                support_case_keys=[str(item["case_key"]) for item in group],
                reason=f"Maintenance shortfall contractor threshold met across {len(contractor_buildings)} buildings.",
            )

    followup_rows = [dict(row) for row in conn.execute("SELECT case_id, follow_count FROM followups").fetchall()]
    no_response_expected: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for row in followup_rows:
        if int(row["follow_count"]) < 2:
            continue
        case_row = cases_by_id.get(str(row["case_id"]))
        if not case_row:
            continue
        entity_type = "contractor" if case_row.get("contractor") else "case"
        entity_value = str(case_row.get("contractor") or case_row["case_id"])
        no_response_expected[(entity_type, entity_value)].append(str(row["case_id"]))
    for (entity_type, entity_value), support_case_ids in no_response_expected.items():
        expected_flags.append(
            {
                "pattern_type": "repeated_no_response",
                "entity_type": entity_type,
                "entity_value": entity_value,
                "entity_key": ("repeated_no_response", entity_type, _normalized_entity(entity_value)),
                "supporting_case_keys": [],
                "supporting_case_ids": sorted(set(support_case_ids)),
                "reason": f"No-response follow-up threshold met across {len(set(support_case_ids))} case(s).",
            }
        )

    mechanic_flags_expected = 0
    if include_mechanics:
        mechanic_device_map: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
        for reply_plan in dataset.reply_plans:
            case_id = case_ids_by_key.get(reply_plan.case_key)
            if not case_id:
                continue
            case_row = cases_by_id.get(case_id)
            if not case_row or not case_row.get("device"):
                continue
            for match in re.finditer(r"(?:Mechanic|Technician)\s*:\s*([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3})", reply_plan.reply_text):
                mechanic_device_map[str(case_row["device"])][match.group(1).strip()].add(case_id)
            for match in re.finditer(r"Assigned mechanic\s+([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3})", reply_plan.reply_text):
                mechanic_device_map[str(case_row["device"])][match.group(1).strip()].add(case_id)
        for device, mechanic_cases in mechanic_device_map.items():
            recurring = {name: case_ids for name, case_ids in mechanic_cases.items() if len(case_ids) >= 2}
            if recurring:
                mechanic_name, case_ids = max(recurring.items(), key=lambda item: len(item[1]))
                expected_flags.append(
                    {
                        "pattern_type": "mechanic_recurrence",
                        "entity_type": "mechanic",
                        "entity_value": mechanic_name,
                        "entity_key": ("mechanic_recurrence", "mechanic", _normalized_entity(mechanic_name)),
                        "supporting_case_keys": [],
                        "supporting_case_ids": sorted(case_ids),
                        "reason": f"Mechanic recurrence detected for {mechanic_name} on device {device}.",
                    }
                )
                mechanic_flags_expected += 1
            if len(mechanic_cases) >= 2:
                expected_flags.append(
                    {
                        "pattern_type": "mechanic_rotation",
                        "entity_type": "device",
                        "entity_value": device,
                        "entity_key": ("mechanic_rotation", "device", _normalized_entity(device)),
                        "supporting_case_keys": [],
                        "supporting_case_ids": sorted({case_id for case_ids in mechanic_cases.values() for case_id in case_ids}),
                        "reason": f"Mechanic rotation detected for device {device}.",
                    }
                )
                mechanic_flags_expected += 1

    actual_by_key: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for descriptor in active_flags:
        actual_by_key[descriptor["entity_key"]].append(descriptor)

    support_link_types = {
        "repeated_building_issue": {"same_building", "repeated_issue"},
        "repeated_device_issue": {"same_device", "repeated_issue"},
        "repeated_contractor_issue": {"same_contractor"},
        "repeated_data_absence": {"same_building", "same_device", "repeated_issue"},
        "repeated_major_work_overdue": {"same_building", "same_contractor", "related_work"},
        "repeated_maintenance_shortfall": {"same_building", "same_contractor"},
        "mechanic_rotation": {"same_device"},
        "mechanic_recurrence": {"same_device"},
    }

    for expected in expected_flags:
        matches = actual_by_key.get(expected["entity_key"], [])
        if matches:
            matched_expected_flags.append(
                {
                    **expected,
                    "matched_case_ids": sorted({descriptor["case_id"] for descriptor in matches if descriptor["case_id"]}),
                    "matched_flag_ids": sorted(descriptor["flag_id"] for descriptor in matches),
                }
            )
            entity_exists = any(
                row["entity_type"] == expected["entity_type"]
                and _normalized_entity(str(row["canonical_name"])) == _normalized_entity(expected["entity_value"])
                for row in entity_rows
            ) or expected["entity_type"] == "case"
            support_observations = [
                row
                for row in observations
                if row.get("case_id") in expected["supporting_case_ids"]
                and (
                    expected["entity_type"] == "case"
                    or _normalized_entity(str(row.get("entity_value") or row.get("building") or row.get("device") or row.get("contractor") or "")) == _normalized_entity(expected["entity_value"])
                )
            ]
            link_count = _matching_link_count(
                link_rows=link_rows,
                case_ids=expected["supporting_case_ids"],
                link_types=support_link_types.get(expected["pattern_type"], set()),
            )
            validation_result = "PASS"
            reason = expected["reason"]
            if not entity_exists:
                validation_result = "FAIL"
                reason = f"{reason} Missing matching entity record."
                hard_failures += 1
            elif not support_observations:
                validation_result = "FAIL"
                reason = f"{reason} Missing supporting observations."
                hard_failures += 1
            elif support_link_types.get(expected["pattern_type"]) and len(expected["supporting_case_ids"]) >= 2 and link_count == 0:
                validation_result = "FAIL"
                reason = f"{reason} Missing supporting case links."
                hard_failures += 1
            validation_rows.append(
                {
                    "pattern_type": expected["pattern_type"],
                    "entity_type": expected["entity_type"],
                    "entity_value": expected["entity_value"],
                    "severity": max((descriptor["severity"] for descriptor in matches), default="expected"),
                    "supporting_case_ids": expected["supporting_case_ids"],
                    "supporting_observation_ids": [int(row["id"]) for row in support_observations[:10]],
                    "expected_by_fixture": True,
                    "validation_result": validation_result,
                    "reason": reason,
                }
            )
        else:
            missing_expected_flags.append(expected)
            validation_rows.append(
                {
                    "pattern_type": expected["pattern_type"],
                    "entity_type": expected["entity_type"],
                    "entity_value": expected["entity_value"],
                    "severity": "expected",
                    "supporting_case_ids": expected["supporting_case_ids"],
                    "supporting_observation_ids": [],
                    "expected_by_fixture": True,
                    "validation_result": "FAIL",
                    "reason": f"Expected pattern flag was not created. {expected['reason']}",
                }
            )
            hard_failures += 1

    ambiguous_case_ids = [case_ids_by_key[key] for key in controls.get("ambiguous_device_identity_case_keys", []) if key in case_ids_by_key]
    ambiguous_device_values = {
        str(cases_by_id[case_id]["device"])
        for case_id in ambiguous_case_ids
        if case_id in cases_by_id and cases_by_id[case_id].get("device")
    }
    ambiguous_link_count = 0
    if len(ambiguous_case_ids) >= 2:
        ambiguous_link_count = _matching_link_count(
            link_rows=link_rows,
            case_ids=ambiguous_case_ids,
            link_types={"same_device", "repeated_issue"},
        )
    if ambiguous_link_count:
        false_positive_links += ambiguous_link_count
        warning_count += 1
        validation_rows.append(
            {
                "pattern_type": "repeated_device_issue",
                "entity_type": "device",
                "entity_value": next(iter(ambiguous_device_values), ""),
                "severity": "warning",
                "supporting_case_ids": ambiguous_case_ids,
                "supporting_observation_ids": [],
                "expected_by_fixture": False,
                "validation_result": "WARNING",
                "reason": "Same device string across different buildings was linked. Device identity lacks building context.",
            }
        )

    threshold_control_case_ids = [case_ids_by_key[key] for key in controls.get("building_below_threshold_case_keys", []) if key in case_ids_by_key]
    threshold_buildings = {
        str(cases_by_id[case_id]["building"])
        for case_id in threshold_control_case_ids
        if case_id in cases_by_id and cases_by_id[case_id].get("building")
    }
    for building in threshold_buildings:
        key = ("repeated_building_issue", "building", _normalized_entity(building))
        if actual_by_key.get(key):
            hard_failures += 1
            unexpected_pattern_flags.extend(actual_by_key[key])
            validation_rows.append(
                {
                    "pattern_type": "repeated_building_issue",
                    "entity_type": "building",
                    "entity_value": building,
                    "severity": "fail",
                    "supporting_case_ids": threshold_control_case_ids,
                    "supporting_observation_ids": [],
                    "expected_by_fixture": False,
                    "validation_result": "FAIL",
                    "reason": "Below-threshold building control incorrectly created a repeated_building_issue flag.",
                }
            )

    low_risk_case_ids = [case_ids_by_key[key] for key in controls.get("low_risk_contractor_case_keys", []) if key in case_ids_by_key]
    low_risk_contractors = {
        str(cases_by_id[case_id]["contractor"])
        for case_id in low_risk_case_ids
        if case_id in cases_by_id and cases_by_id[case_id].get("contractor")
    }
    for contractor in low_risk_contractors:
        key = ("repeated_contractor_issue", "contractor", _normalized_entity(contractor))
        if actual_by_key.get(key):
            hard_failures += 1
            unexpected_pattern_flags.extend(actual_by_key[key])
            validation_rows.append(
                {
                    "pattern_type": "repeated_contractor_issue",
                    "entity_type": "contractor",
                    "entity_value": contractor,
                    "severity": "fail",
                    "supporting_case_ids": low_risk_case_ids,
                    "supporting_observation_ids": [],
                    "expected_by_fixture": False,
                    "validation_result": "FAIL",
                    "reason": "Low-risk contractor control incorrectly created a repeated_contractor_issue flag.",
                }
            )

    alias_case_ids = [case_ids_by_key[key] for key in controls.get("building_alias_case_keys", []) if key in case_ids_by_key]
    if len(alias_case_ids) >= 2:
        alias_links = _matching_link_count(
            link_rows=link_rows,
            case_ids=alias_case_ids,
            link_types={"same_building", "repeated_issue"},
        )
        validation_rows.append(
            {
                "pattern_type": "building_alias_normalization",
                "entity_type": "building",
                "entity_value": "123 Example Road / 123 Example Rd",
                "severity": "warning" if alias_links == 0 else "info",
                "supporting_case_ids": alias_case_ids,
                "supporting_observation_ids": [],
                "expected_by_fixture": False,
                "validation_result": "WARNING" if alias_links == 0 else "PASS",
                "reason": "Building alias normalization not detected; similar building strings remained separate."
                if alias_links == 0
                else "Building alias normalization connected the similar building strings.",
            }
        )
        if alias_links == 0:
            warning_count += 1

    prompt_case_ids = [
        case_ids_by_key[item["case_key"]]
        for item in case_expectations
        if "prompt_injection_attempt" in item.get("tags", []) and item["case_key"] in case_ids_by_key
    ]
    for case_id in prompt_case_ids:
        review_count = int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM manual_reviews WHERE case_id = ?",
                (case_id,),
            ).fetchone()["count"]
        )
        validation_result = "PASS" if review_count > 0 else "FAIL"
        if validation_result == "FAIL":
            hard_failures += 1
        validation_rows.append(
            {
                "pattern_type": "prompt_injection_attempt",
                "entity_type": "case",
                "entity_value": case_id,
                "severity": "review",
                "supporting_case_ids": [case_id],
                "supporting_observation_ids": [],
                "expected_by_fixture": True,
                "validation_result": validation_result,
                "reason": "Prompt-injection scenario created a manual review."
                if review_count > 0
                else "Prompt-injection scenario did not create a manual review.",
            }
        )

    duplicate_expected = processing.get("duplicate_scenarios_generated", 0)
    duplicate_grouped = processing.get("duplicate_alerts_grouped", 0)
    duplicate_validation = "PASS" if duplicate_grouped >= duplicate_expected else "FAIL"
    if duplicate_validation == "FAIL":
        hard_failures += 1
    validation_rows.append(
        {
            "pattern_type": "duplicate_alert_grouping",
            "entity_type": "dataset",
            "entity_value": str(duplicate_expected),
            "severity": "info",
            "supporting_case_ids": [],
            "supporting_observation_ids": [],
            "expected_by_fixture": True,
            "validation_result": duplicate_validation,
            "reason": f"Grouped {duplicate_grouped} duplicate alerts out of {duplicate_expected} generated duplicate scenarios.",
        }
    )

    matched_keys = {flag["entity_key"] for flag in matched_expected_flags}
    allowed_warning_keys = {
        ("repeated_device_issue", "device", _normalized_entity(value))
        for value in ambiguous_device_values
    }
    allowed_auxiliary_types = {
        "repeated_building_issue",
        "repeated_device_issue",
        "repeated_major_work_overdue",
    }
    for descriptor in active_flags:
        _, evidence_mismatches = _validate_flag_evidence(
            descriptor=descriptor,
            cases_by_id=cases_by_id,
            observations_by_id=observations_by_id,
        )
        if evidence_mismatches:
            evidence_mismatch_count += len(evidence_mismatches)
            auxiliary_only = descriptor["pattern_type"] in allowed_auxiliary_types
            hard_evidence_markers = (
                "missing evidence keys",
                "did not include supporting case or observation ids",
                "supporting case does not exist",
                "supporting observation does not exist",
            )
            hard_evidence_failure = any(
                any(marker in mismatch for marker in hard_evidence_markers)
                for mismatch in evidence_mismatches
            )
            if auxiliary_only or not hard_evidence_failure:
                warning_count += 1
            else:
                hard_failures += 1
            validation_rows.append(
                {
                    "pattern_type": descriptor["pattern_type"],
                    "entity_type": descriptor["entity_type"],
                    "entity_value": descriptor["entity_value"],
                    "severity": descriptor["severity"],
                    "supporting_case_ids": descriptor["supporting_case_ids"],
                    "supporting_observation_ids": descriptor["supporting_observation_ids"],
                    "expected_by_fixture": descriptor["entity_key"] in matched_keys,
                    "validation_result": "WARNING" if auxiliary_only or not hard_evidence_failure else "FAIL",
                    "reason": "; ".join(evidence_mismatches),
                }
            )
        if descriptor["entity_key"] in matched_keys:
            continue
        if descriptor["entity_key"] in allowed_warning_keys:
            warning_count += 1
            continue
        if descriptor["pattern_type"] in allowed_auxiliary_types:
            continue
        unexpected_pattern_flags.append(
            {
                "flag_id": descriptor["flag_id"],
                "case_id": descriptor["case_id"],
                "pattern_type": descriptor["pattern_type"],
                "entity_type": descriptor["entity_type"],
                "entity_value": descriptor["entity_value"],
                "severity": descriptor["severity"],
            }
        )
        hard_failures += 1
        validation_rows.append(
            {
                "pattern_type": descriptor["pattern_type"],
                "entity_type": descriptor["entity_type"],
                "entity_value": descriptor["entity_value"],
                "severity": descriptor["severity"],
                "supporting_case_ids": descriptor["supporting_case_ids"],
                "supporting_observation_ids": descriptor["supporting_observation_ids"],
                "expected_by_fixture": False,
                "validation_result": "FAIL",
                "reason": "Unexpected active pattern flag was present without matching fixture expectations.",
            }
        )

    duplicate_groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for descriptor in active_flags:
        duplicate_key = (
            descriptor["case_id"],
            descriptor["pattern_type"],
            descriptor["entity_type"],
            _normalized_entity(descriptor["entity_value"]),
            tuple(sorted(descriptor["supporting_case_ids"])),
            tuple(sorted(descriptor["supporting_observation_ids"])),
        )
        duplicate_groups[duplicate_key].append(descriptor)
    duplicate_pattern_flags = sum(max(0, len(group) - 1) for group in duplicate_groups.values())
    if duplicate_pattern_flags:
        validation_rows.append(
            {
                "pattern_type": "duplicate_pattern_flags",
                "entity_type": "pattern_flag",
                "entity_value": str(duplicate_pattern_flags),
                "severity": "warning",
                "supporting_case_ids": [],
                "supporting_observation_ids": [],
                "expected_by_fixture": False,
                "validation_result": "WARNING" if duplicate_pattern_flags <= 3 else "FAIL",
                "reason": f"Detected {duplicate_pattern_flags} duplicate active pattern flags.",
            }
        )
        if duplicate_pattern_flags <= 3:
            warning_count += 1
        else:
            hard_failures += 1

    mechanic_flags_actual = sum(1 for descriptor in active_flags if descriptor["pattern_type"] in memory.MECHANIC_PATTERNS)
    if not include_mechanics and mechanic_flags_actual:
        hard_failures += 1
        validation_rows.append(
            {
                "pattern_type": "mechanic_flags_without_input",
                "entity_type": "mechanic",
                "entity_value": str(mechanic_flags_actual),
                "severity": "fail",
                "supporting_case_ids": [],
                "supporting_observation_ids": [],
                "expected_by_fixture": False,
                "validation_result": "FAIL",
                "reason": "Mechanic-related flags were created without explicit mechanic input data.",
            }
        )

    if hard_failures:
        status = "FAIL"
    elif warning_count:
        status = "PASS WITH WARNINGS"
    else:
        status = "PASS"

    return {
        "enabled": True,
        "status": status,
        "expected_pattern_flags": expected_flags,
        "actual_pattern_flags": [
            {
                "flag_id": descriptor["flag_id"],
                "case_id": descriptor["case_id"],
                "pattern_type": descriptor["pattern_type"],
                "entity_type": descriptor["entity_type"],
                "entity_value": descriptor["entity_value"],
                "severity": descriptor["severity"],
                "supporting_case_ids": descriptor["supporting_case_ids"],
                "supporting_observation_ids": descriptor["supporting_observation_ids"],
            }
            for descriptor in active_flags
        ],
        "matched_expected_flags": matched_expected_flags,
        "missing_expected_flags": missing_expected_flags,
        "unexpected_pattern_flags": unexpected_pattern_flags,
        "false_positive_links": false_positive_links,
        "evidence_mismatch_count": evidence_mismatch_count,
        "duplicate_pattern_flags": duplicate_pattern_flags,
        "mechanic_flags_expected": mechanic_flags_expected,
        "mechanic_flags_actual": mechanic_flags_actual,
        "validation_rows": validation_rows,
    }


def _validate_reply_result(
    case_id: str,
    reply_plan: Any,
    result: Dict[str, Any],
    before_case: Dict[str, Any],
    after_case: Dict[str, Any],
) -> List[str]:
    failures: List[str] = []
    if before_case.get("status") == "closed" or after_case.get("status") == "closed":
        failures.append(f"Reply handling closed case {case_id}, which is not allowed.")
    if reply_plan.reply_type == "contractor_completed" and not result["flagged_for_review"]:
        failures.append(f"Completed reply for {case_id} was not flagged for manual review.")
    if reply_plan.reply_type == "contractor_access_needed" and not result["flagged_for_review"]:
        failures.append(f"Access-needed reply for {case_id} was not flagged for manual review.")
    if reply_plan.reply_type == "prompt_injection_reply" and not result["flagged_for_review"]:
        failures.append(f"Prompt-injection reply for {case_id} was not flagged for manual review.")
    return failures


def _simulate_followups(case_ids: List[str]) -> Dict[str, Any]:
    if not case_ids:
        return {"valid": False, "errors": ["No cases available for follow-up simulation."], "cases_touched": 0}

    conn = db.get_connection()
    past_deadline = (datetime.utcnow() - timedelta(days=2)).isoformat()
    for case_id in case_ids:
        conn.execute(
            "UPDATE followups SET deadline = ?, status = 'pending' WHERE case_id = ?",
            (past_deadline, case_id),
        )
    conn.commit()
    followup.check_and_process_followups()

    for _ in range(2):
        conn = db.get_connection()
        for case_id in case_ids[:2]:
            conn.execute(
                "UPDATE followups SET deadline = ?, status = 'pending' WHERE case_id = ?",
                (past_deadline, case_id),
            )
        conn.commit()
        followup.check_and_process_followups()

    rows = conn.execute(
        """
        SELECT case_id, follow_count
        FROM followups
        WHERE case_id IN ({placeholders})
        """.format(placeholders=", ".join("?" for _ in case_ids)),
        tuple(case_ids),
    ).fetchall()
    errors: List[str] = []
    for row in rows:
        if row["follow_count"] <= 0:
            errors.append(f"Follow-up count did not increment for case {row['case_id']}.")
    return {
        "valid": not errors,
        "errors": errors,
        "cases_touched": len(case_ids),
        "followup_rows": [dict(row) for row in rows],
    }


def _validate_prompt_injection_replies(reply_results: List[Dict[str, Any]]) -> List[str]:
    failures: List[str] = []
    for item in reply_results:
        plan = item["plan"]
        case_id = item["case_id"]
        if plan.reply_type != "prompt_injection_reply":
            continue
        reviews = db.get_connection().execute(
            "SELECT COUNT(*) AS count FROM manual_reviews WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if not reviews or int(reviews["count"]) <= 0:
            failures.append(f"Prompt-injection reply did not create manual review for case {case_id}.")
    return failures


def _run_ui_smoke(case_ids_by_key: Dict[str, str]) -> Dict[str, Any]:
    try:
        from web.app import create_app
    except ImportError as exc:
        return {
            "valid": True,
            "errors": [],
            "checks": [],
            "skipped": True,
            "reason": f"Flask UI smoke skipped because Flask is unavailable: {exc}",
        }

    app = create_app()
    app.testing = True
    client = app.test_client()
    checks = []
    errors: List[str] = []
    targets = [
        ("/cases", "cases"),
        ("/events", "events"),
        ("/reviews", "reviews"),
    ]
    first_case_id = next(iter(case_ids_by_key.values()), None)
    if first_case_id:
        targets.append((f"/cases/{first_case_id}", "case_detail"))

    for route, label in targets:
        response = client.get(route)
        body = response.get_data(as_text=True)
        checks.append({"route": route, "status_code": response.status_code})
        if response.status_code != 200:
            errors.append(f"UI route {route} returned {response.status_code} instead of 200.")
            continue
        if label == "cases" and "Example" not in body:
            errors.append("UI route /cases rendered without visible test case content.")
        if label == "case_detail":
            case_row = db.get_case_by_id(first_case_id)
            building = case_row["building"] if case_row else None
            if building and building not in body and first_case_id not in body:
                errors.append("UI case detail page did not render the expected case information.")
    return {"valid": not errors, "errors": errors, "checks": checks}


def _collect_processing_summary(
    dataset: SyntheticDataset,
    process_results: List[Dict[str, Any]],
    reply_results: List[Dict[str, Any]],
    followup_summary: Dict[str, Any],
    case_ids_by_key: Dict[str, str],
) -> Dict[str, Any]:
    actions = Counter(item["result"]["action"] for item in process_results)
    duplicate_grouped = sum(
        1
        for item in process_results
        if "duplicate_alert" in item["fixture"].synthetic_scenario_tags and item["result"]["action"] == "updated"
    )
    outbound_count = db.get_connection().execute(
        "SELECT COUNT(*) AS count FROM outbound_messages"
    ).fetchone()["count"]
    followup_events = db.get_connection().execute(
        "SELECT COUNT(*) AS count FROM case_events WHERE event_type = 'followup_triggered'"
    ).fetchone()["count"]
    escalation_events = db.get_connection().execute(
        "SELECT COUNT(*) AS count FROM case_events WHERE event_type = 'escalated'"
    ).fetchone()["count"]
    reviews = db.get_connection().execute(
        "SELECT COUNT(*) AS count FROM manual_reviews"
    ).fetchone()["count"]
    prompt_reviews = db.get_connection().execute(
        "SELECT COUNT(*) AS count FROM manual_reviews WHERE reason LIKE '%prompt injection%'"
    ).fetchone()["count"]
    reply_injection_flags = sum(
        1
        for item in reply_results
        if item["plan"].reply_type == "prompt_injection_reply" and item["result"]["flagged_for_review"]
    )
    reply_type_details: Dict[str, Dict[str, int]] = defaultdict(lambda: {"processed": 0, "flagged_for_review": 0, "satisfies_action": 0})
    for item in reply_results:
        reply_type = item["plan"].reply_type
        reply_type_details[reply_type]["processed"] += 1
        if item["result"].get("flagged_for_review"):
            reply_type_details[reply_type]["flagged_for_review"] += 1
        if item["result"].get("satisfies_action"):
            reply_type_details[reply_type]["satisfies_action"] += 1

    return {
        "requested_emails": dataset.metadata.get("requested_emails", len(dataset.emails)),
        "generated_emails": len(dataset.emails),
        "duplicate_scenarios_generated": sum(
            1 for fixture in dataset.emails if "duplicate_alert" in fixture.synthetic_scenario_tags
        ),
        "distinct_cases_expected": len({fixture.expected_grouping_family for fixture in dataset.emails}),
        "distinct_cases_created": len(case_ids_by_key),
        "reply_types_generated": list(dataset.metadata.get("reply_types_generated", [])),
        "reply_types_validated": sorted(reply_type_details.keys()),
        "emails_processed": len(process_results),
        "cases_created": actions.get("created", 0),
        "existing_cases_updated": actions.get("updated", 0),
        "duplicate_alerts_grouped": duplicate_grouped,
        "outbound_drafts_or_fake_sends_created": int(outbound_count),
        "replies_processed": len(reply_results),
        "followups_triggered": int(followup_events),
        "normal_followups_triggered": max(0, int(followup_events) - int(escalation_events)),
        "escalation_followups_triggered": int(escalation_events),
        "reply_handling_details": dict(sorted(reply_type_details.items())),
        "manual_reviews_created": int(reviews),
        "prompt_injection_items_flagged": int(prompt_reviews) + reply_injection_flags,
        "followup_cases_touched": followup_summary.get("cases_touched", 0),
    }


def _quality_summary(quality: _CheckTally, mode: Dict[str, Any]) -> Dict[str, str]:
    def status(total: int, failures: int, skip_if_ai: bool = False) -> str:
        if skip_if_ai and mode["ai_dependent_checks_skipped"] and not mode["requested_offline"]:
            return "SKIPPED"
        if total == 0:
            return "SKIPPED"
        return "PASS" if failures == 0 else "FAIL"

    return {
        "classification": status(quality.classification_total, quality.classification_failures, skip_if_ai=True),
        "extraction": status(quality.extraction_total, quality.extraction_failures, skip_if_ai=True),
        "grouping": status(quality.grouping_total, quality.grouping_failures),
        "reply_handling": status(quality.reply_total, quality.reply_failures, skip_if_ai=True),
        "followup_handling": status(quality.followup_total, quality.followup_failures),
        "prompt_injection_handling": status(quality.injection_total, quality.injection_failures),
        "flask_ui_smoke": status(quality.ui_total, quality.ui_failures),
    }


def _memory_summary() -> Dict[str, Any]:
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT pattern_type, COUNT(*) AS count FROM pattern_flags WHERE status = 'active' GROUP BY pattern_type"
    ).fetchall()
    counts = {row["pattern_type"]: int(row["count"]) for row in rows}
    if not counts:
        return {"status": "Advanced memory not detected, pattern tests skipped."}
    return {
        "status": "Advanced memory detected.",
        "pattern_flags_created": sum(counts.values()),
        "repeated_building_flags": counts.get("repeated_building_issue", 0),
        "repeated_device_flags": counts.get("repeated_device_issue", 0),
        "repeated_contractor_flags": counts.get("repeated_contractor_issue", 0),
        "repeated_no_response_flags": counts.get("repeated_no_response", 0),
        "mechanic_related_flags": sum(
            counts.get(name, 0) for name in ("mechanic_recurrence", "mechanic_rotation")
        ),
    }


def _build_result(
    dataset: SyntheticDataset,
    processing: Dict[str, Any],
    extraction: Dict[str, Any],
    ai_usage: Dict[str, Any],
    manual_reviews: Dict[str, Any],
    monitor: _SafetyMonitor,
    quality: _CheckTally,
    memory_connection_audit: Dict[str, Any],
    warnings: List[str],
    failures: List[str],
    paths: Dict[str, Path],
    mode: Dict[str, Any],
) -> ScaleTestResult:
    case_type_distribution = Counter(email.expected_case_type for email in dataset.emails)
    tag_distribution = Counter()
    for email in dataset.emails:
        tag_distribution.update(email.synthetic_scenario_tags)

    dataset_summary = {
        "seed_used": dataset.metadata["seed"],
        "requested_emails": processing.get("requested_emails", len(dataset.emails)),
        "generated_emails": len(dataset.emails),
        "emails_generated": len(dataset.emails),
        "replies_generated": len(dataset.reply_plans),
        "duplicate_scenarios_generated": processing.get("duplicate_scenarios_generated", 0),
        "distinct_cases_expected": processing.get("distinct_cases_expected", 0),
        "distinct_cases_created": processing.get("distinct_cases_created", 0),
        "reply_types_generated": processing.get("reply_types_generated", []),
        "reply_types_validated": processing.get("reply_types_validated", []),
        "clients": len(dataset.metadata["clients"]),
        "buildings": len(dataset.metadata["buildings"]),
        "devices": dataset.metadata["device_count"],
        "contractors": len(dataset.metadata["contractors"]),
        "case_type_distribution": dict(sorted(case_type_distribution.items())),
        "scenario_tag_distribution": dict(sorted(tag_distribution.items())),
    }

    safety = {
        "real_smtp_calls_attempted": monitor.real_smtp_calls_attempted,
        "real_imap_calls_attempted": monitor.real_imap_calls_attempted,
        "actual_recipient_violations": monitor.actual_recipient_violations,
        "disallowed_domain_violations": monitor.disallowed_domain_violations,
        "intended_recipient_rewrites": monitor.intended_recipient_rewrites,
        "production_database_used": paths["database"].resolve() == (PROJECT_ROOT / "data" / "agent.db").resolve(),
        "safe_demo_recipient": SAFE_DEMO_RECIPIENT,
        "test_database_path": str(paths["database"]),
        "test_database_retained": paths["database"].exists(),
    }

    quality_checks = _quality_summary(quality, mode)
    memory_readiness = _memory_summary() if processing else {"status": "Not executed."}

    overall_result = "PASS"
    if failures:
        overall_result = "FAIL"
    elif (
        warnings
        or extraction.get("extraction_warnings", 0)
        or memory_connection_audit.get("status") == "PASS WITH WARNINGS"
        or any(value == "SKIPPED" for value in quality_checks.values())
    ):
        overall_result = "PASS WITH WARNINGS"

    return ScaleTestResult(
        overall_result=overall_result,
        dataset=dataset_summary,
        processing=processing,
        extraction=extraction,
        ai_usage=ai_usage,
        manual_reviews=manual_reviews,
        safety=safety,
        quality_checks=quality_checks,
        memory_readiness=memory_readiness,
        memory_connection_audit=memory_connection_audit,
        warnings=warnings,
        failures=failures,
        paths=paths,
        mode=mode,
    )


def _write_reports(result: ScaleTestResult) -> None:
    report_payload = {
        "overall_result": result.overall_result,
        "mode": result.mode,
        "dataset": result.dataset,
        "processing": result.processing,
        "extraction": result.extraction,
        "ai_usage": result.ai_usage,
        "manual_reviews": result.manual_reviews,
        "safety": result.safety,
        "quality_checks": result.quality_checks,
        "memory_readiness": result.memory_readiness,
        "memory_connection_audit": result.memory_connection_audit,
        "warnings": result.warnings,
        "failures": result.failures,
        "paths": {key: str(value) for key, value in result.paths.items()},
    }
    result.paths["report_json"].write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
    result.paths["report_markdown"].write_text(_render_markdown_report(result), encoding="utf-8")


def _render_markdown_report(result: ScaleTestResult) -> str:
    lines = [
        "# Demo Scale Test Report",
        "",
        f"Overall result: **{result.overall_result}**",
        "",
        "## Paths",
        f"- Run Dir: {result.paths['run_dir']}",
        f"- Database: {result.paths['database']}",
        f"- Report Json: {result.paths['report_json']}",
        f"- Report Markdown: {result.paths['report_markdown']}",
        f"- Harness Log: {result.paths['harness_log']}",
        "",
        "## Dataset summary",
    ]
    for key, value in result.dataset.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## Processing summary"])
    for key, value in result.processing.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## Extraction"])
    for key, value in result.extraction.items():
        if key == "validation_rows":
            continue
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## AI Usage"])
    for key, value in result.ai_usage.items():
        if key == "records":
            lines.append(f"- Records: {len(value)}")
        else:
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## Manual Reviews"])
    for key, value in result.manual_reviews.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## Safety summary"])
    for key, value in result.safety.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## Quality checks"])
    for key, value in result.quality_checks.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## Memory readiness"])
    for key, value in result.memory_readiness.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## Memory Connection Audit"])
    for key, value in result.memory_connection_audit.items():
        if key in {
            "expected_pattern_flags",
            "actual_pattern_flags",
            "matched_expected_flags",
            "missing_expected_flags",
            "unexpected_pattern_flags",
            "validation_rows",
        }:
            lines.append(f"- {key.replace('_', ' ').title()}: {len(value)}")
        else:
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    if result.warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in result.warnings)
    if result.failures:
        lines.extend(["", "## Failures"])
        lines.extend(f"- {failure}" for failure in result.failures)
    return "\n".join(lines) + "\n"


def format_result_summary(result: ScaleTestResult) -> str:
    """Return a compact CLI summary for the completed harness run."""
    lines = [
        f"[TEST-DEMO-SCALE] Result: {result.overall_result}",
        f"[TEST-DEMO-SCALE] Emails processed: {result.processing.get('emails_processed', 0)}",
        f"[TEST-DEMO-SCALE] Cases created: {result.processing.get('cases_created', 0)}",
        f"[TEST-DEMO-SCALE] Cases updated: {result.processing.get('existing_cases_updated', 0)}",
        f"[TEST-DEMO-SCALE] Replies processed: {result.processing.get('replies_processed', 0)}",
        f"[TEST-DEMO-SCALE] Follow-ups triggered: {result.processing.get('followups_triggered', 0)}",
        f"[TEST-DEMO-SCALE] AI enabled: {result.ai_usage.get('ai_enabled', False)}",
        f"[TEST-DEMO-SCALE] Total AI calls: {result.ai_usage.get('total_ai_calls', 0)}",
        f"[TEST-DEMO-SCALE] Blocked AI calls: {result.ai_usage.get('total_ai_calls_blocked', 0)}",
        f"[TEST-DEMO-SCALE] Memory audit: {result.memory_connection_audit.get('status', 'Not requested.')}",
        f"[TEST-DEMO-SCALE] SMTP attempts blocked: {result.safety['real_smtp_calls_attempted']}",
        f"[TEST-DEMO-SCALE] IMAP attempts blocked: {result.safety['real_imap_calls_attempted']}",
        f"[TEST-DEMO-SCALE] Test run retained: {result.paths['run_dir']}",
        f"[TEST-DEMO-SCALE] Database: {result.paths['database']}",
        f"[TEST-DEMO-SCALE] AI Usage Report: {result.paths['ai_usage_report']}",
        f"[TEST-DEMO-SCALE] Report: {result.paths['report_markdown']}",
        f"[TEST-DEMO-SCALE] Report JSON: {result.paths['report_json']}",
        f"[TEST-DEMO-SCALE] Harness log: {result.paths['harness_log']}",
    ]
    if result.warnings:
        lines.append(f"[TEST-DEMO-SCALE] Warnings: {len(result.warnings)}")
    if result.failures:
        lines.append(f"[TEST-DEMO-SCALE] Failures: {len(result.failures)}")
    return "\n".join(lines)
