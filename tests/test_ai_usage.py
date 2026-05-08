import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import ai_gateway
import database as db
import followup
from classifier import classify_email
from config import config
from demo_scale_harness import ScaleTestOptions, run_demo_scale_test
from extractor import extract_fields_with_meta, generate_email_body
from runtime_options import RuntimeOptions, runtime_options


class AiUsageGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        self.original_cache_path = config.CLAUDE_CACHE_PATH
        self.original_report_path = config.AI_REPORT_PATH

        config.DATABASE_PATH = Path(self.temp_dir.name) / "agent.db"
        config.CLAUDE_CACHE_PATH = Path(self.temp_dir.name) / "claude_cache.json"
        config.AI_REPORT_PATH = Path(self.temp_dir.name) / "ai_usage_report.json"

        db.close_connection()
        db.init_schema()
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions())

    def tearDown(self):
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions())
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        config.CLAUDE_CACHE_PATH = self.original_cache_path
        config.AI_REPORT_PATH = self.original_report_path
        self.temp_dir.cleanup()

    def test_gateway_blocks_calls_when_disabled(self):
        gateway = ai_gateway.get_ai_gateway()
        gateway.configure(
            ai_gateway.AiUsageConfig(
                enabled=False,
                max_calls=0,
                budget_mode="manual_review",
                report_path=config.AI_REPORT_PATH,
            )
        )

        outcome = gateway.call_json(
            prompt='{"task":"classify"}',
            purpose="classification",
            prompt_type="unit_test",
            caller="tests.test_ai_usage",
            email_id="email-1",
        )

        self.assertEqual("blocked", outcome.status)
        self.assertIsNone(outcome.payload)
        report = gateway.build_report()
        self.assertEqual(0, report["total_ai_calls"])
        self.assertEqual(1, report["total_ai_calls_blocked"])

    def test_gateway_uses_cache_before_transport(self):
        seen_prompts = []

        def fake_json_transport(prompt: str, model: str) -> dict:
            seen_prompts.append((prompt, model))
            return {"case_type": "DATA_ABSENCE", "confidence": 0.8, "reasoning": "cached"}

        gateway = ai_gateway.get_ai_gateway()
        gateway.set_test_transports(json_transport=fake_json_transport)
        gateway.configure(
            ai_gateway.AiUsageConfig(
                enabled=True,
                max_calls=3,
                budget_mode="fail",
                report_path=config.AI_REPORT_PATH,
            )
        )

        first = gateway.call_json(
            prompt='{"task":"classify","body":"hello"}',
            purpose="classification",
            prompt_type="unit_test",
            caller="tests.test_ai_usage",
            email_id="email-1",
            use_cache=True,
        )
        second = gateway.call_json(
            prompt='{"task":"classify","body":"hello"}',
            purpose="classification",
            prompt_type="unit_test",
            caller="tests.test_ai_usage",
            email_id="email-1",
            use_cache=True,
        )

        self.assertEqual("allowed", first.status)
        self.assertEqual("cached", second.status)
        self.assertEqual(1, len(seen_prompts))
        report = gateway.build_report()
        self.assertEqual(1, report["total_ai_calls"])
        self.assertEqual(1, report["cache_hits"])
        self.assertEqual(1, report["cache_misses"])

    def test_deterministic_classification_uses_zero_ai_for_known_kpi_email(self):
        gateway = ai_gateway.get_ai_gateway()
        gateway.configure(
            ai_gateway.AiUsageConfig(
                enabled=True,
                max_calls=5,
                budget_mode="fail",
                report_path=config.AI_REPORT_PATH,
            )
        )

        result = classify_email(
            subject="CAT1 Tests Reminder",
            body=(
                "CAT1 Reminder - Daily Alert\n\n"
                "Building: 123 Example Road\n"
                "Device: B-4 #731842\n"
                "Contractor: Example Elevator Company\n"
            ),
        )

        self.assertEqual("CAT1_COMPLIANCE", result["case_type"])
        self.assertEqual("deterministic", result["source"])
        self.assertEqual(0, gateway.build_report()["total_ai_calls"])

    def test_ambiguous_classification_goes_to_manual_review_when_ai_is_disabled(self):
        gateway = ai_gateway.get_ai_gateway()
        gateway.configure(
            ai_gateway.AiUsageConfig(
                enabled=False,
                max_calls=0,
                budget_mode="manual_review",
                report_path=config.AI_REPORT_PATH,
            )
        )

        result = classify_email(
            subject="Question about this item",
            body="Please look into this and let me know what happened.",
        )

        self.assertEqual("UNKNOWN", result["case_type"])
        self.assertEqual("manual_review", result["source"])
        self.assertEqual(0, gateway.build_report()["total_ai_calls"])

    def test_ambiguous_classification_uses_ai_only_when_enabled_and_budget_allows(self):
        gateway = ai_gateway.get_ai_gateway()
        gateway.set_test_transports(
            json_transport=lambda prompt, model: {
                "case_type": "DATA_ABSENCE",
                "confidence": 0.76,
                "reasoning": f"mocked via {model}",
            }
        )
        gateway.configure(
            ai_gateway.AiUsageConfig(
                enabled=True,
                max_calls=1,
                budget_mode="fail",
                report_path=config.AI_REPORT_PATH,
                cache_path=config.CLAUDE_CACHE_PATH,
            )
        )

        result = classify_email(
            subject="Question about a maintenance record",
            body="The note is unclear and needs interpretation.",
        )

        self.assertEqual("DATA_ABSENCE", result["case_type"])
        self.assertEqual("ai", result["source"])
        self.assertEqual(1, gateway.build_report()["total_ai_calls"])

    def test_known_template_extraction_uses_zero_ai(self):
        gateway = ai_gateway.get_ai_gateway()
        gateway.configure(
            ai_gateway.AiUsageConfig(
                enabled=True,
                max_calls=5,
                budget_mode="fail",
                report_path=config.AI_REPORT_PATH,
            )
        )

        fields, meta = extract_fields_with_meta(
            subject="Maintenance Hours Less Than Required",
            body=(
                "Client: Example Client 003\n"
                "Building: 789 Demo Street, Example City\n"
                "Contractor: Demo Vertical Transport\n"
                "Reporting Period: April 2026\n\n"
                "Device | Contract Hours | Actual Hours\n"
                "Car 1 #810056 | 1.50 | 0.00\n"
                "Car 2 #810057 | 1.50 | 0.25\n"
            ),
            case_type="MAINTENANCE_HOURS_SHORTFALL",
        )

        self.assertEqual("deterministic", meta["source"])
        self.assertEqual("3.00", fields["hours_required"])
        self.assertEqual("0.25", fields["hours_actual"])
        self.assertEqual([], meta["missing_required_fields"])
        self.assertEqual(0, gateway.build_report()["total_ai_calls"])

    def test_missing_optional_fields_do_not_trigger_ai_extraction(self):
        gateway = ai_gateway.get_ai_gateway()
        gateway.configure(
            ai_gateway.AiUsageConfig(
                enabled=True,
                max_calls=5,
                budget_mode="fail",
                report_path=config.AI_REPORT_PATH,
            )
        )

        fields, meta = extract_fields_with_meta(
            subject="Maintenance Data is not up to date",
            body=(
                "Data Absence Alert\n\n"
                "Building: 456 Sample Avenue, Example City\n"
                "Contractor: Sample Lift Services\n"
                "Data Status: Maintenance data has never been submitted\n"
                "Last Activity Date: 2025-11-01\n"
                "Elapsed Days: 187\n"
            ),
            case_type="DATA_ABSENCE",
        )

        self.assertEqual("deterministic", meta["source"])
        self.assertIsNone(fields["device"])
        self.assertEqual([], meta["missing_required_fields"])
        self.assertEqual(0, gateway.build_report()["total_ai_calls"])

    def test_missing_required_fields_trigger_manual_review_when_ai_disabled(self):
        gateway = ai_gateway.get_ai_gateway()
        gateway.configure(
            ai_gateway.AiUsageConfig(
                enabled=False,
                max_calls=0,
                budget_mode="manual_review",
                report_path=config.AI_REPORT_PATH,
            )
        )

        fields, meta = extract_fields_with_meta(
            subject="Scheduled Work is Overdue",
            body=(
                "Client: Example Client 004\n"
                "Building: 100 Example Road, Example City\n"
                "Contractor: Placeholder Elevator Group\n"
            ),
            case_type="MAJOR_WORK_OVERDUE",
        )

        self.assertEqual("manual_review", meta["source"])
        self.assertIn("device", meta["missing_required_fields"])
        self.assertIn("scheduled_date", meta["missing_required_fields"])
        self.assertEqual(0, gateway.build_report()["total_ai_calls"])
        self.assertEqual("100 Example Road, Example City", fields["building"])

    def test_template_outbound_generation_uses_zero_ai(self):
        runtime_options.configure(
            RuntimeOptions(
                ai_enabled=False,
                template_outbound_only=True,
                ai_outbound_enabled=False,
            )
        )

        gateway = ai_gateway.get_ai_gateway()
        gateway.configure(
            ai_gateway.AiUsageConfig(
                enabled=False,
                max_calls=0,
                budget_mode="manual_review",
                report_path=config.AI_REPORT_PATH,
            )
        )

        body = generate_email_body(
            case_type="DATA_ABSENCE",
            fields={
                "building": "456 Sample Avenue, Example City",
                "contractor": "Sample Lift Services",
                "description": "Maintenance data has never been submitted",
            },
            case_id="case-123",
            memory_context=None,
        )

        self.assertIn("456 Sample Avenue, Example City", body)
        self.assertIn("Sample Lift Services", body)
        self.assertEqual(0, gateway.build_report()["total_ai_calls"])

    def test_followups_can_be_disabled(self):
        runtime_options.configure(RuntimeOptions(followups_enabled=False))
        summary = followup.check_and_process_followups()
        self.assertTrue(summary["disabled"])

    def test_followup_idempotency_prevents_duplicate_generation(self):
        runtime_options.configure(
            RuntimeOptions(
                ai_enabled=False,
                followups_enabled=True,
                disable_outbound_generation=False,
                template_outbound_only=True,
                max_followups=3,
                max_followup_runs=10,
            )
        )
        db.insert_case(
            case_id="case-followup-1",
            case_type="DATA_ABSENCE",
            grouping_key="data_absence|123 example road|",
            building="123 Example Road, Example City",
            device=None,
            contractor="Example Elevator Company",
            due_date=None,
            period=None,
            priority="medium",
        )
        db.upsert_followup(
            followup_id="followup-1",
            case_id="case-followup-1",
            deadline="2026-05-01T00:00:00",
        )

        first = followup.check_and_process_followups()
        second = followup.check_and_process_followups()

        self.assertTrue(first["valid"])
        self.assertEqual(1, first["cases_touched"])
        self.assertEqual(0, second["cases_touched"])
        follow_row = db.get_followup_for_case("case-followup-1")
        self.assertEqual(1, int(follow_row["follow_count"]))
        action_rows = db.get_followup_actions_for_case("case-followup-1")
        self.assertEqual(1, len(action_rows))

    def test_followup_count_only_increments_after_draft_success(self):
        runtime_options.configure(
            RuntimeOptions(
                ai_enabled=False,
                followups_enabled=True,
                disable_outbound_generation=False,
                template_outbound_only=True,
                max_followups=3,
                max_followup_runs=10,
            )
        )
        db.insert_case(
            case_id="case-followup-2",
            case_type="DATA_ABSENCE",
            grouping_key="data_absence|456 sample avenue|",
            building="456 Sample Avenue, Example City",
            device=None,
            contractor="Sample Lift Services",
            due_date=None,
            period=None,
            priority="medium",
        )
        db.upsert_followup(
            followup_id="followup-2",
            case_id="case-followup-2",
            deadline="2026-05-01T00:00:00",
        )

        with patch("email_sender.create_draft", side_effect=RuntimeError("draft failure")):
            summary = followup.check_and_process_followups()

        self.assertFalse(summary["valid"])
        follow_row = db.get_followup_for_case("case-followup-2")
        self.assertEqual(0, int(follow_row["follow_count"]))

    def test_default_harness_run_uses_zero_ai_and_writes_usage_report(self):
        report_dir = Path(self.temp_dir.name) / "reports"
        result = run_demo_scale_test(
            ScaleTestOptions(
                emails=25,
                seed=42,
                report_dir=report_dir,
                verbose=False,
            )
        )

        self.assertIn(result.overall_result, {"PASS", "PASS WITH WARNINGS"})
        report_path = result.paths["run_dir"] / "ai_usage_report.json"
        self.assertTrue(report_path.exists())
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertFalse(payload["ai_enabled"])
        self.assertEqual(0, payload["total_ai_calls"])
        self.assertEqual(0, payload["live_ai_calls"])


class DirectAiUsageGuardTests(unittest.TestCase):
    def test_only_gateway_uses_direct_ai_transport(self):
        allowed_files = {
            PROJECT_ROOT / "src" / "ai_gateway.py",
            PROJECT_ROOT / "src" / "claude_client.py",
        }
        forbidden_hits = []
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path in allowed_files:
                continue
            if "call_claude(" in text or "call_claude_json(" in text:
                forbidden_hits.append(str(path.relative_to(PROJECT_ROOT)))

        self.assertEqual([], forbidden_hits)


if __name__ == "__main__":
    unittest.main()
