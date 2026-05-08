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
    _run_ui_smoke,
    compare_extracted_fields,
    run_demo_scale_test,
)


class DemoHarnessTests(unittest.TestCase):
    def test_generate_dataset_uses_safe_domains_and_requested_count(self):
        dataset = generate_synthetic_dataset(total_emails=24, seed=42)

        self.assertEqual(24, len(dataset.emails))
        self.assertGreater(len(dataset.reply_plans), 0)
        self.assertGreater(len(dataset.followup_case_keys), 0)
        self.assertEqual(6, len(dataset.metadata["case_types"]))
        self.assertGreater(dataset.metadata["duplicate_emails"], 0)

        for email in dataset.emails:
            self.assertTrue(allowed_test_domain(email.from_addr))
            self.assertTrue(allowed_test_domain(email.to_addr))
            self.assertEqual(email.expected_case_type, email.expected_metadata["case_type"])

    def test_offline_harness_run_creates_safe_concise_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir) / "reports"
            result = run_demo_scale_test(
                ScaleTestOptions(
                    emails=25,
                    seed=42,
                    report_dir=report_dir,
                    verbose=False,
                )
            )

            self.assertIn(result.overall_result, {"PASS", "PASS WITH WARNINGS"})
            self.assertEqual(0, result.safety["real_smtp_calls_attempted"])
            self.assertEqual(0, result.safety["real_imap_calls_attempted"])
            self.assertEqual(0, result.safety["actual_recipient_violations"])
            self.assertFalse(result.safety["production_database_used"])
            self.assertTrue(result.paths["report_json"].exists())
            self.assertTrue(result.paths["report_markdown"].exists())
            self.assertTrue(result.paths["database"].exists())

            payload = json.loads(result.paths["report_json"].read_text(encoding="utf-8"))
            self.assertEqual(SAFE_DEMO_RECIPIENT, payload["safety"]["safe_demo_recipient"])
            self.assertEqual(25, payload["processing"]["emails_processed"])
            self.assertGreater(payload["processing"]["duplicates_grouped"], 0)
            self.assertGreater(payload["processing"]["outbound_drafts_created"], 0)
            self.assertGreater(payload["processing"]["manual_reviews_created"], 0)
            self.assertEqual(0, payload["ai_usage"]["total_ai_calls"])
            self.assertNotIn("memory_connection_audit", payload)

    def test_compare_extracted_fields_reports_simple_mismatches(self):
        comparison = compare_extracted_fields(
            case_type="DATA_ABSENCE",
            actual_fields={
                "building": "123 Example Road, Example City",
                "contractor": "Example Elevator Company",
            },
            expected_fields={
                "building": "123 Example Road, Example City",
                "contractor": "Different Elevator Company",
            },
        )

        self.assertEqual(1, comparison["extraction_failures"])
        self.assertIn("contractor", comparison["failures"][0])

    def test_ui_smoke_skips_when_flask_is_not_installed(self):
        # This repository can run deterministic CLI validation in lightweight
        # Python environments before dependencies are installed.
        import builtins
        from unittest.mock import patch

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "web.app":
                raise ImportError("No module named 'flask'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = _run_ui_smoke({"case-key": "case-id"})

        self.assertTrue(result["valid"])
        self.assertTrue(result["skipped"])
        self.assertIn("Flask UI smoke skipped", result["reason"])


if __name__ == "__main__":
    unittest.main()
