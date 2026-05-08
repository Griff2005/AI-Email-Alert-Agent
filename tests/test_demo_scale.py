import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from demo_fixtures import SAFE_DEMO_RECIPIENT, allowed_test_domain, generate_synthetic_dataset
from demo_scale_harness import (
    ScaleTestOptions,
    compare_extracted_fields,
    run_demo_scale_test,
)


class DemoScaleHarnessTests(unittest.TestCase):
    def test_generate_dataset_uses_safe_domains_and_requested_count(self):
        dataset = generate_synthetic_dataset(
            total_emails=24,
            client_count=4,
            building_count=6,
            devices_per_building=3,
            seed=42,
        )

        self.assertEqual(24, len(dataset.emails))
        self.assertGreater(len(dataset.reply_plans), 0)
        self.assertGreater(len(dataset.followup_case_keys), 0)

        for email in dataset.emails:
            self.assertTrue(allowed_test_domain(email.from_addr))
            self.assertTrue(allowed_test_domain(email.to_addr))
            self.assertEqual(email.expected_case_type, email.expected_metadata["case_type"])

    def test_offline_scale_run_creates_safe_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir) / "reports"
            options = ScaleTestOptions(
                emails=18,
                clients=4,
                buildings=8,
                devices_per_building=3,
                seed=7,
                offline=True,
                require_ai=False,
                report_dir=report_dir,
                verbose=False,
            )

            result = run_demo_scale_test(options)

            self.assertIn(result.overall_result, {"PASS", "PASS WITH WARNINGS"})
            self.assertEqual(0, result.safety["real_smtp_calls_attempted"])
            self.assertEqual(0, result.safety["real_imap_calls_attempted"])
            self.assertEqual(0, result.safety["actual_recipient_violations"])
            self.assertFalse(result.safety["production_database_used"])
            self.assertTrue(result.paths["report_json"].exists())
            self.assertTrue(result.paths["report_markdown"].exists())
            self.assertTrue(result.paths["database"].exists())
            self.assertEqual(result.paths["database"], Path(result.safety["test_database_path"]))
            self.assertTrue(payload := json.loads(result.paths["report_json"].read_text(encoding="utf-8")))
            self.assertTrue(payload["safety"]["test_database_retained"])
            self.assertIn("run_dir", payload["paths"])
            self.assertIn("database", payload["paths"])
            self.assertIn("extraction", payload)
            self.assertIn("structured_field_failures", payload["extraction"])
            self.assertIn("semantic_description_mismatches", payload["extraction"])
            self.assertIn("manual_reviews", payload)

            self.assertEqual(SAFE_DEMO_RECIPIENT, payload["safety"]["safe_demo_recipient"])
            self.assertGreater(payload["processing"]["emails_processed"], 0)
            self.assertGreater(payload["processing"]["outbound_drafts_or_fake_sends_created"], 0)
            self.assertIn("classification", payload["quality_checks"])

    def test_compare_extracted_fields_allows_semantic_description_variation(self):
        data_absence = compare_extracted_fields(
            case_type="DATA_ABSENCE",
            actual_fields={
                "building": "123 Example Road, Example City",
                "contractor": "Example Elevator Company",
                "description": "Data Absence Alert - Maintenance data has never been submitted",
                "last_activity_date": "2025-11-01",
                "elapsed_days": "187",
            },
            expected_fields={
                "building": "123 Example Road, Example City",
                "contractor": "Example Elevator Company",
                "description": "Maintenance data has never been submitted",
                "last_activity_date": "2025-11-01",
                "elapsed_days": "187",
            },
        )
        self.assertEqual([], data_absence["failures"])
        self.assertGreaterEqual(data_absence["semantic_description_mismatches"], 1)

        shortfall = compare_extracted_fields(
            case_type="MAINTENANCE_HOURS_SHORTFALL",
            actual_fields={
                "building": "789 Demo Street, Example City",
                "contractor": "Demo Vertical Transport",
                "description": None,
                "hours_required": "3.00",
                "hours_actual": "0.25",
                "period": "April 2026",
            },
            expected_fields={
                "building": "789 Demo Street, Example City",
                "contractor": "Demo Vertical Transport",
                "description": "Maintenance Hours Less Than Required",
                "hours_required": "3.00",
                "hours_actual": "0.25",
                "period": "April 2026",
            },
        )
        self.assertEqual([], shortfall["failures"])
        self.assertGreaterEqual(shortfall["optional_description_missing"], 1)
        self.assertGreaterEqual(shortfall["warnings"], 1)

    def test_memory_connection_audit_is_reported_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir) / "reports"
            options = ScaleTestOptions(
                emails=24,
                clients=4,
                buildings=8,
                devices_per_building=3,
                seed=11,
                offline=True,
                validate_memory_connections=True,
                report_dir=report_dir,
                verbose=False,
            )

            result = run_demo_scale_test(options)

            self.assertIn(result.overall_result, {"PASS", "PASS WITH WARNINGS"})
            payload = json.loads(result.paths["report_json"].read_text(encoding="utf-8"))
            audit = payload["memory_connection_audit"]
            self.assertTrue(audit["enabled"])
            self.assertIn(audit["status"], {"PASS", "PASS WITH WARNINGS"})
            self.assertIn("validation_rows", audit)
            self.assertIn("matched_expected_flags", audit)
            self.assertIn("missing_expected_flags", audit)
            self.assertIn("unexpected_pattern_flags", audit)
            self.assertIn("evidence_mismatch_count", audit)
            self.assertEqual(0, audit["mechanic_flags_expected"])


if __name__ == "__main__":
    unittest.main()
