"""
demo_scale_harness.py - Offline safety/demo validator.

This module answers one question: is the MVP demo path safe and basically
working? It runs the real pipeline against deterministic synthetic KPI alerts
using an isolated SQLite database, hard-blocked SMTP/IMAP, AI disabled, and a
concise JSON/Markdown report.
"""

from __future__ import annotations

import imaplib
import json
import re
import smtplib
from collections import Counter, defaultdict
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from ai_gateway import AiUsageConfig, get_ai_gateway
import case_manager
from content_safety import sanitize_email_content
import database as db
import email_sender
import followup
from config import PROJECT_ROOT, config
from demo_fixtures import (
    SAFE_DEMO_RECIPIENT,
    SyntheticDataset,
    allowed_test_domain,
    generate_synthetic_dataset,
)
from runtime_options import RuntimeOptions, runtime_options
from time_utils import utc_compact_timestamp, utc_now_naive


@dataclass(frozen=True)
class ScaleTestOptions:
    """Options for the offline demo safety harness."""

    emails: int = 50
    seed: int = 42
    offline: bool = True
    enable_followups: bool = False
    disable_outbound_generation: bool = False
    report_dir: Path = PROJECT_ROOT / "data" / "test_runs"
    verbose: bool = False


@dataclass
class ScaleTestResult:
    """Structured result and report paths from the offline demo harness."""

    overall_result: str
    dataset: Dict[str, Any]
    processing: Dict[str, Any]
    extraction: Dict[str, Any]
    ai_usage: Dict[str, Any]
    manual_reviews: Dict[str, Any]
    safety: Dict[str, Any]
    quality_checks: Dict[str, str]
    memory: Dict[str, Any]
    warnings: List[str]
    failures: List[str]
    paths: Dict[str, Path]
    mode: Dict[str, Any]


@dataclass
class _SafetyMonitor:
    """Counters proving the harness did not touch real network recipients."""

    real_smtp_calls_attempted: int = 0
    real_imap_calls_attempted: int = 0
    actual_recipient_violations: int = 0
    disallowed_domain_violations: int = 0


def compare_extracted_fields(
    case_type: str,
    actual_fields: Dict[str, Optional[str]],
    expected_fields: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    """Compare expected synthetic fields with fields stored by the pipeline."""
    del case_type
    failures: List[str] = []
    checked_fields: List[str] = []
    for field_name, expected in expected_fields.items():
        if expected is None:
            continue
        checked_fields.append(field_name)
        actual = actual_fields.get(field_name)
        if not _field_matches(field_name, actual, expected):
            failures.append(f"{field_name}: expected {expected!r}, got {actual!r}")
    return {
        "checked_fields": checked_fields,
        "failures": failures,
        "warnings": 0,
        "extraction_failures": len(failures),
    }


def run_demo_scale_test(options: ScaleTestOptions) -> ScaleTestResult:
    """Run the offline demo validator and write concise reports."""
    if not options.offline:
        raise ValueError("The demo harness is offline-only. Do not use it for live AI or network tests.")

    run_dir = _new_run_dir(options.report_dir)
    database_path = run_dir / "test_agent.db"
    cache_path = run_dir / "claude_cache.json"
    log_path = run_dir / "harness.log"
    paths = {
        "run_dir": run_dir,
        "database": database_path,
        "harness_log": log_path,
        "ai_usage_report": run_dir / "ai_usage_report.json",
        "ai_usage_report_csv": run_dir / "ai_usage_report.csv",
        "report_json": run_dir / "report.json",
        "report_markdown": run_dir / "report.md",
    }

    dataset = generate_synthetic_dataset(total_emails=options.emails, seed=options.seed)
    warnings: List[str] = []
    failures: List[str] = []
    monitor = _SafetyMonitor()

    with ExitStack() as stack:
        _apply_config_overrides(stack, database_path=database_path, cache_path=cache_path)
        _install_network_blocks(stack, monitor)
        _install_sender_guard(stack, monitor)
        previous_runtime_options = runtime_options.get()
        stack.callback(lambda: runtime_options.configure(previous_runtime_options))
        stack.callback(lambda: get_ai_gateway().reset())
        _configure_offline_runtime(options, paths)

        with log_path.open("w", encoding="utf-8") as log_handle:
            if options.verbose:
                execution = _run_pipeline(dataset, options, monitor, warnings, failures)
            else:
                with redirect_stdout(log_handle), redirect_stderr(log_handle):
                    execution = _run_pipeline(dataset, options, monitor, warnings, failures)

        get_ai_gateway().set_run_metadata(
            command="test-demo-scale",
            emails_processed=execution["processing"].get("emails_processed", 0),
            cases_created=execution["processing"].get("cases_created", 0),
            cases_updated=execution["processing"].get("duplicates_grouped", 0),
            seed=options.seed,
        )
        get_ai_gateway().write_report(paths["ai_usage_report"], paths["ai_usage_report_csv"])

        result = _build_result(
            dataset=dataset,
            options=options,
            execution=execution,
            monitor=monitor,
            warnings=warnings,
            failures=failures,
            paths=paths,
        )
        _write_reports(result)

    db.close_connection()
    return result


def _run_pipeline(
    dataset: SyntheticDataset,
    options: ScaleTestOptions,
    monitor: _SafetyMonitor,
    warnings: List[str],
    failures: List[str],
) -> Dict[str, Any]:
    db.close_connection()
    db.init_schema()
    config.validate()

    seen_by_group: Dict[str, int] = defaultdict(int)
    case_ids_by_key: Dict[str, str] = {}
    action_counts: Counter = Counter()
    extraction_failures = 0
    extraction_checked = 0
    prompt_injection_failures = 0

    for fixture in dataset.emails:
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
        action_counts[result["action"]] += 1
        seen_by_group[fixture.expected_grouping_key] += 1

        if result["case_type"] != fixture.expected_case_type:
            failures.append(
                f"Classification mismatch for {fixture.message_id}: "
                f"expected {fixture.expected_case_type}, got {result['case_type']}."
            )

        expected_action = "created" if seen_by_group[fixture.expected_grouping_key] == 1 else "updated"
        if result["action"] != expected_action:
            failures.append(
                f"Grouping mismatch for {fixture.message_id}: expected {expected_action}, got {result['action']}."
            )

        case_id = result.get("case_id")
        if case_id:
            case_ids_by_key.setdefault(fixture.expected_grouping_key, case_id)
            comparison = compare_extracted_fields(
                case_type=fixture.expected_case_type,
                actual_fields=_case_fields(case_id),
                expected_fields=fixture.expected_fields,
            )
            extraction_checked += len(comparison["checked_fields"])
            extraction_failures += comparison["extraction_failures"]
            failures.extend(f"{fixture.message_id}: {item}" for item in comparison["failures"])

        if "prompt_injection" in fixture.tags and not result.get("injection_detected"):
            prompt_injection_failures += 1
            failures.append(f"Prompt injection was not detected for {fixture.message_id}.")

    reply_summary = _process_replies(dataset, case_ids_by_key, failures)
    followup_summary = _process_followups(dataset, case_ids_by_key, options, failures)
    ui_summary = _run_ui_smoke(case_ids_by_key)
    failures.extend(ui_summary["errors"])
    if ui_summary.get("skipped"):
        warnings.append(ui_summary["reason"])
    memory_summary = _memory_summary()

    processing = _processing_summary(
        dataset=dataset,
        action_counts=action_counts,
        case_ids_by_key=case_ids_by_key,
        reply_summary=reply_summary,
        followup_summary=followup_summary,
    )
    safety_failures = _final_safety_checks(monitor)
    if safety_failures:
        failures.extend(safety_failures)

    if memory_summary["active_pattern_flags"] == 0:
        warnings.append("No active memory pattern flags were created during the run.")

    quality_checks = {
        "classification": "PASS" if not any("Classification mismatch" in item for item in failures) else "FAIL",
        "extraction": "PASS" if extraction_failures == 0 else "FAIL",
        "duplicate_grouping": "PASS" if processing["duplicates_grouped"] > 0 else "FAIL",
        "reply_handling": "PASS" if reply_summary["failures"] == 0 else "FAIL",
        "prompt_injection": "PASS" if prompt_injection_failures == 0 and reply_summary["prompt_injection_failures"] == 0 else "FAIL",
        "outbound_recipient_override": "PASS" if monitor.actual_recipient_violations == 0 else "FAIL",
        "memory_pattern_creation": "PASS" if memory_summary["active_pattern_flags"] > 0 else "WARN",
        "flask_ui_smoke": "SKIPPED" if ui_summary.get("skipped") else ("PASS" if ui_summary["valid"] else "FAIL"),
        "followup_handling": followup_summary["status"],
    }

    return {
        "processing": processing,
        "extraction": {
            "checked_fields": extraction_checked,
            "failures": extraction_failures,
        },
        "reply_summary": reply_summary,
        "followup_summary": followup_summary,
        "ui_summary": ui_summary,
        "memory": memory_summary,
        "quality_checks": quality_checks,
    }


def _process_replies(
    dataset: SyntheticDataset,
    case_ids_by_key: Dict[str, str],
    failures: List[str],
) -> Dict[str, Any]:
    processed = 0
    flagged = 0
    satisfied = 0
    reply_failures = 0
    injection_failures = 0

    for plan in dataset.reply_plans:
        case_id = case_ids_by_key.get(plan.case_key)
        if not case_id:
            continue
        before = dict(db.get_case_by_id(case_id))
        result = case_manager.process_reply(case_id=case_id, reply_text=plan.reply_text, verbose=False)
        after = dict(db.get_case_by_id(case_id))
        processed += 1
        flagged += int(bool(result["flagged_for_review"]))
        satisfied += int(bool(result["satisfies_action"]))

        if before.get("status") == "closed" or after.get("status") == "closed":
            reply_failures += 1
            failures.append(f"Reply handling closed case {case_id}; cases must not auto-close.")
        if result["flagged_for_review"] != plan.should_flag_review:
            reply_failures += 1
            failures.append(
                f"Reply {plan.reply_type} for {case_id} review flag mismatch: "
                f"expected {plan.should_flag_review}, got {result['flagged_for_review']}."
            )
        if result["satisfies_action"] != plan.should_satisfy_action:
            reply_failures += 1
            failures.append(
                f"Reply {plan.reply_type} for {case_id} completion mismatch: "
                f"expected {plan.should_satisfy_action}, got {result['satisfies_action']}."
            )
        if plan.reply_type == "prompt_injection_reply" and not result["flagged_for_review"]:
            injection_failures += 1

    return {
        "processed": processed,
        "flagged_for_review": flagged,
        "satisfies_action": satisfied,
        "failures": reply_failures,
        "prompt_injection_failures": injection_failures,
    }


def _process_followups(
    dataset: SyntheticDataset,
    case_ids_by_key: Dict[str, str],
    options: ScaleTestOptions,
    failures: List[str],
) -> Dict[str, Any]:
    if not options.enable_followups:
        return {"status": "SKIPPED", "enabled": False, "cases_touched": 0, "errors": []}

    case_ids = [case_ids_by_key[key] for key in dataset.followup_case_keys if key in case_ids_by_key]
    if not case_ids:
        failures.append("Follow-up simulation had no cases to touch.")
        return {"status": "FAIL", "enabled": True, "cases_touched": 0, "errors": ["No cases available."]}

    past_deadline = (utc_now_naive() - timedelta(days=1)).isoformat()
    conn = db.get_connection()
    for case_id in case_ids:
        conn.execute(
            "UPDATE followups SET deadline = ?, status = 'pending' WHERE case_id = ?",
            (past_deadline, case_id),
        )
    conn.commit()

    summary = followup.check_and_process_followups()
    errors = list(summary.get("errors", []))
    if not summary.get("valid", False):
        failures.extend(errors)
    return {
        "status": "PASS" if summary.get("valid", False) else "FAIL",
        "enabled": True,
        "cases_touched": int(summary.get("cases_touched", 0)),
        "errors": errors,
    }


def _run_ui_smoke(case_ids_by_key: Dict[str, str]) -> Dict[str, Any]:
    try:
        from web.app import create_app
    except ImportError as exc:
        return {
            "valid": True,
            "skipped": True,
            "reason": f"Flask UI smoke skipped because Flask is unavailable: {exc}",
            "errors": [],
            "checks": [],
        }

    app = create_app()
    app.testing = True
    client = app.test_client()
    targets = ["/", "/emails", "/cases", "/reviews", "/events", "/patterns"]
    first_case_id = next(iter(case_ids_by_key.values()), None)
    if first_case_id:
        targets.append(f"/cases/{first_case_id}")

    # Add first email detail to smoke targets
    conn = db.get_connection()
    first_email = conn.execute("SELECT email_id FROM emails LIMIT 1").fetchone()
    if first_email:
        targets.append(f"/emails/{first_email['email_id']}")

    errors: List[str] = []
    checks: List[Dict[str, Any]] = []
    for route in targets:
        response = client.get(route)
        checks.append({"route": route, "status_code": response.status_code})
        if response.status_code != 200:
            errors.append(f"UI route {route} returned {response.status_code}.")
    return {"valid": not errors, "skipped": False, "errors": errors, "checks": checks}


def _processing_summary(
    dataset: SyntheticDataset,
    action_counts: Counter,
    case_ids_by_key: Dict[str, str],
    reply_summary: Dict[str, Any],
    followup_summary: Dict[str, Any],
) -> Dict[str, Any]:
    conn = db.get_connection()
    outbound_count = int(conn.execute("SELECT COUNT(*) AS count FROM outbound_messages").fetchone()["count"])
    review_count = int(conn.execute("SELECT COUNT(*) AS count FROM manual_reviews").fetchone()["count"])
    duplicate_count = int(action_counts.get("updated", 0))
    return {
        "emails_processed": len(dataset.emails),
        "cases_created": int(action_counts.get("created", 0)),
        "duplicates_grouped": duplicate_count,
        "review_cases_created": int(action_counts.get("review_flagged", 0)),
        "distinct_cases": len(case_ids_by_key),
        "outbound_drafts_created": outbound_count,
        "replies_processed": reply_summary["processed"],
        "followups_triggered": followup_summary["cases_touched"],
        "manual_reviews_created": review_count,
    }


def _manual_review_summary() -> Dict[str, Any]:
    conn = db.get_connection()
    rows = conn.execute(
        """
        SELECT reason, COUNT(*) AS count
        FROM manual_reviews
        GROUP BY reason
        ORDER BY count DESC, reason ASC
        """
    ).fetchall()
    return {
        "total": sum(int(row["count"]) for row in rows),
        "open": int(conn.execute("SELECT COUNT(*) AS count FROM manual_reviews WHERE resolved = 0").fetchone()["count"]),
        "reasons": [{"reason": row["reason"], "count": int(row["count"])} for row in rows],
    }


def _memory_summary() -> Dict[str, Any]:
    conn = db.get_connection()
    pattern_rows = conn.execute(
        "SELECT pattern_type, COUNT(*) AS count FROM pattern_flags WHERE status = 'active' GROUP BY pattern_type"
    ).fetchall()
    observation_count = int(conn.execute("SELECT COUNT(*) AS count FROM observations").fetchone()["count"])
    related_case_count = int(conn.execute("SELECT COUNT(*) AS count FROM case_links").fetchone()["count"])
    pattern_counts = {row["pattern_type"]: int(row["count"]) for row in pattern_rows}
    return {
        "active_pattern_flags": sum(pattern_counts.values()),
        "pattern_counts": pattern_counts,
        "observations": observation_count,
        "related_case_links": related_case_count,
    }


def _final_safety_checks(monitor: _SafetyMonitor) -> List[str]:
    safety_failures: List[str] = []
    if monitor.real_smtp_calls_attempted:
        safety_failures.append("Harness attempted real SMTP.")
    if monitor.real_imap_calls_attempted:
        safety_failures.append("Harness attempted real IMAP.")
    if monitor.actual_recipient_violations:
        safety_failures.append("Outbound actual recipient was not the demo recipient.")

    unsafe_rows = db.get_connection().execute(
        "SELECT msg_id, actual_to FROM outbound_messages WHERE actual_to != ?",
        (SAFE_DEMO_RECIPIENT,),
    ).fetchall()
    for row in unsafe_rows:
        safety_failures.append(f"Unsafe outbound recipient for {row['msg_id']}: {row['actual_to']}")
    return safety_failures


def _build_result(
    dataset: SyntheticDataset,
    options: ScaleTestOptions,
    execution: Dict[str, Any],
    monitor: _SafetyMonitor,
    warnings: List[str],
    failures: List[str],
    paths: Dict[str, Path],
) -> ScaleTestResult:
    case_distribution = Counter(fixture.expected_case_type for fixture in dataset.emails)
    safety = {
        "real_smtp_calls_attempted": monitor.real_smtp_calls_attempted,
        "real_imap_calls_attempted": monitor.real_imap_calls_attempted,
        "actual_recipient_violations": monitor.actual_recipient_violations,
        "disallowed_domain_violations": monitor.disallowed_domain_violations,
        "production_database_used": paths["database"].resolve() == (PROJECT_ROOT / "data" / "agent.db").resolve(),
        "safe_demo_recipient": SAFE_DEMO_RECIPIENT,
        "test_database_retained": paths["database"].exists(),
        "test_database_path": str(paths["database"]),
    }
    if safety["production_database_used"]:
        failures.append("Harness used the production/demo database path.")

    ai_usage = get_ai_gateway().build_report()
    if ai_usage.get("total_ai_calls", 0) != 0 or ai_usage.get("live_ai_calls", 0) != 0:
        failures.append("Harness made AI calls even though it is offline-only.")

    overall_result = "PASS"
    if failures:
        overall_result = "FAIL"
    elif warnings:
        overall_result = "PASS WITH WARNINGS"

    return ScaleTestResult(
        overall_result=overall_result,
        dataset={
            "seed": options.seed,
            "requested_emails": options.emails,
            "generated_emails": len(dataset.emails),
            "distinct_case_keys": dataset.metadata["distinct_case_keys"],
            "duplicate_emails": dataset.metadata["duplicate_emails"],
            "case_type_distribution": dict(sorted(case_distribution.items())),
        },
        processing=execution["processing"],
        extraction=execution["extraction"],
        ai_usage=ai_usage,
        manual_reviews=_manual_review_summary(),
        safety=safety,
        quality_checks=execution["quality_checks"],
        memory=execution["memory"],
        warnings=warnings,
        failures=failures,
        paths=paths,
        mode={
            "offline": True,
            "ai_enabled": False,
            "followups_enabled": options.enable_followups,
        },
    )


def _write_reports(result: ScaleTestResult) -> None:
    payload = {
        "overall_result": result.overall_result,
        "mode": result.mode,
        "dataset": result.dataset,
        "processing": result.processing,
        "extraction": result.extraction,
        "ai_usage": {
            key: value
            for key, value in result.ai_usage.items()
            if key != "records"
        },
        "manual_reviews": result.manual_reviews,
        "safety": result.safety,
        "quality_checks": result.quality_checks,
        "memory": result.memory,
        "warnings": result.warnings,
        "failures": result.failures,
        "paths": {key: str(value) for key, value in result.paths.items()},
    }
    result.paths["report_json"].write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    result.paths["report_markdown"].write_text(_render_markdown_report(result), encoding="utf-8")


def _render_markdown_report(result: ScaleTestResult) -> str:
    lines = [
        "# Demo Harness Report",
        "",
        f"Overall result: **{result.overall_result}**",
        "",
        "## Paths",
        f"- Run directory: `{result.paths['run_dir']}`",
        f"- Database: `{result.paths['database']}`",
        f"- JSON report: `{result.paths['report_json']}`",
        f"- Harness log: `{result.paths['harness_log']}`",
        "",
        "## Processing",
    ]
    for key, value in result.processing.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## Safety"])
    for key, value in result.safety.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## Quality Checks"])
    for key, value in result.quality_checks.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    lines.extend(["", "## Memory"])
    for key, value in result.memory.items():
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
        f"[TEST-DEMO-SCALE] Duplicates grouped: {result.processing.get('duplicates_grouped', 0)}",
        f"[TEST-DEMO-SCALE] Replies processed: {result.processing.get('replies_processed', 0)}",
        f"[TEST-DEMO-SCALE] Outbound drafts: {result.processing.get('outbound_drafts_created', 0)}",
        f"[TEST-DEMO-SCALE] Manual reviews: {result.processing.get('manual_reviews_created', 0)}",
        f"[TEST-DEMO-SCALE] Follow-ups triggered: {result.processing.get('followups_triggered', 0)}",
        f"[TEST-DEMO-SCALE] AI enabled: {result.ai_usage.get('ai_enabled', False)}",
        f"[TEST-DEMO-SCALE] Total AI calls: {result.ai_usage.get('total_ai_calls', 0)}",
        f"[TEST-DEMO-SCALE] SMTP attempts blocked: {result.safety['real_smtp_calls_attempted']}",
        f"[TEST-DEMO-SCALE] IMAP attempts blocked: {result.safety['real_imap_calls_attempted']}",
        f"[TEST-DEMO-SCALE] Database: {result.paths['database']}",
        f"[TEST-DEMO-SCALE] Report: {result.paths['report_markdown']}",
        f"[TEST-DEMO-SCALE] Report JSON: {result.paths['report_json']}",
    ]
    if result.warnings:
        lines.append(f"[TEST-DEMO-SCALE] Warnings: {len(result.warnings)}")
    if result.failures:
        lines.append(f"[TEST-DEMO-SCALE] Failures: {len(result.failures)}")
    return "\n".join(lines)


def _configure_offline_runtime(options: ScaleTestOptions, paths: Dict[str, Path]) -> None:
    runtime_options.configure(
        RuntimeOptions(
            ai_enabled=False,
            allow_uncapped_ai=False,
            max_ai_calls=0,
            ai_report_path=paths["ai_usage_report"],
            disable_outbound_generation=options.disable_outbound_generation,
            template_outbound_only=True,
            ai_outbound_enabled=False,
            followups_enabled=options.enable_followups,
            max_followups=3,
            max_followup_runs=10 if options.enable_followups else 0,
        )
    )
    gateway = get_ai_gateway()
    gateway.reset()
    gateway.configure(
        AiUsageConfig(
            enabled=False,
            allow_uncapped_ai=False,
            max_calls=0,
            budget_mode="manual_review",
            report_path=paths["ai_usage_report"],
            csv_report_path=paths["ai_usage_report_csv"],
            cache_path=config.CLAUDE_CACHE_PATH,
            model_name=config.CLAUDE_MODEL,
            config_version="demo-harness-v1",
        )
    )


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
    originals = {key: (getattr(config_obj, key), getattr(config_cls, key)) for key in overrides}
    for key, value in overrides.items():
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
        raise AssertionError("SMTP use is blocked by the demo harness.")

    def blocked_imap(*args: Any, **kwargs: Any) -> Any:
        monitor.real_imap_calls_attempted += 1
        raise AssertionError("IMAP use is blocked by the demo harness.")

    stack.enter_context(patch.object(smtplib, "SMTP", side_effect=blocked_smtp))
    stack.enter_context(patch.object(smtplib, "SMTP_SSL", side_effect=blocked_smtp))
    stack.enter_context(patch.object(imaplib, "IMAP4_SSL", side_effect=blocked_imap))


def _install_sender_guard(stack: ExitStack, monitor: _SafetyMonitor) -> None:
    real_create_draft = email_sender.create_draft

    def guarded_create_draft(
        case_id: str,
        subject: str,
        body: str,
        intended_to: str,
        intended_cc: str = "",
    ) -> str:
        msg_id = real_create_draft(
            case_id=case_id,
            subject=subject,
            body=body,
            intended_to=_safe_intended_recipient(intended_to),
            intended_cc=", ".join(
                _safe_intended_recipient(value.strip())
                for value in intended_cc.split(",")
                if value.strip()
            ),
        )
        row = db.get_connection().execute(
            "SELECT actual_to FROM outbound_messages WHERE msg_id = ?",
            (msg_id,),
        ).fetchone()
        if not row or row["actual_to"] != SAFE_DEMO_RECIPIENT:
            monitor.actual_recipient_violations += 1
            raise AssertionError("Demo recipient override failed.")
        return msg_id

    stack.enter_context(patch.object(email_sender, "create_draft", side_effect=guarded_create_draft))


def _safe_intended_recipient(value: str) -> str:
    if not value or "@" not in value:
        return value
    local, domain = value.rsplit("@", 1)
    if domain.lower() in {"example.com", "example.test", "localhost"}:
        return value
    safe_local = re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-") or "recipient"
    return f"{safe_local}@example.com"


def _validate_safe_address(value: str, monitor: _SafetyMonitor, failures: List[str], label: str) -> None:
    if allowed_test_domain(value):
        return
    monitor.disallowed_domain_violations += 1
    failures.append(f"Disallowed domain detected for {label}: {value}")


def _insert_email_fixture(fixture: Any) -> str:
    email_id = fixture.message_id.replace("@", "-").replace(".", "-")
    db.insert_email(
        email_id=email_id,
        message_id=fixture.message_id,
        thread_id=fixture.thread_id,
        subject=fixture.subject,
        from_addr=fixture.from_addr,
        to_addr=fixture.to_addr,
        received_at=fixture.received_at,
        raw_body=fixture.body,
        normalized_text=sanitize_email_content(fixture.body),
    )
    return email_id


def _case_fields(case_id: str) -> Dict[str, Optional[str]]:
    fields = {
        "building": None,
        "device": None,
        "contractor": None,
        "due_date": None,
        "period": None,
    }
    case = db.get_case_by_id(case_id)
    if case:
        for key in fields:
            fields[key] = str(case[key]) if case[key] is not None else None
    for row in db.get_fields_for_case(case_id):
        if row["field_value"] is not None:
            fields[row["field_name"]] = str(row["field_value"])
    return fields


def _field_matches(field_name: str, actual: Optional[str], expected: Optional[str]) -> bool:
    if expected is None:
        return True
    if actual is None:
        return False
    if field_name in {"hours_required", "hours_actual"}:
        try:
            return abs(float(actual) - float(expected)) < 1e-9
        except ValueError:
            return _normalize(actual) == _normalize(expected)
    return _normalize(actual) == _normalize(expected)


def _normalize(value: Optional[str]) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _new_run_dir(report_dir: Path) -> Path:
    timestamp = utc_compact_timestamp()
    report_dir.mkdir(parents=True, exist_ok=True)
    candidate = report_dir / timestamp
    counter = 1
    while candidate.exists():
        candidate = report_dir / f"{timestamp}-{counter}"
        counter += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate
