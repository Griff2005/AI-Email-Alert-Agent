import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import ai_gateway
from classifier import classify_email_deterministic_only
from constants import CASE_TYPE_CAT1_COMPLIANCE, CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL
from extractor import extract_fields_deterministic_only


class DeterministicCleanupTests(unittest.TestCase):
    def tearDown(self):
        ai_gateway.reset_gateway()

    def test_deterministic_classifier_path_does_not_record_ai_usage(self):
        result = classify_email_deterministic_only(
            subject="CAT1 Tests Reminder",
            body="Building: 123 Example Road\nDevice: B-4 #731842",
        )

        self.assertEqual(CASE_TYPE_CAT1_COMPLIANCE, result["case_type"])
        self.assertEqual("deterministic", result["source"])
        self.assertEqual(0, ai_gateway.get_ai_gateway().build_report()["total_ai_calls"])

    def test_deterministic_extractor_path_returns_missing_required_fields(self):
        fields, missing = extract_fields_deterministic_only(
            subject="Maintenance Hours Less Than Required",
            body=(
                "Building: 123 Example Road\n"
                "Contractor: Example Elevator Company\n"
                "Reporting Period: 2026-01\n"
                "Device | Required | Actual\n"
                "B-4 #731842 | 8.0 | 4.0\n"
            ),
            case_type=CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL,
        )

        self.assertEqual([], missing)
        self.assertEqual("123 Example Road", fields["building"])
        self.assertEqual("Example Elevator Company", fields["contractor"])
        self.assertEqual("2026-01", fields["period"])
        self.assertEqual("8.00", fields["hours_required"])
        self.assertEqual("4.00", fields["hours_actual"])
        self.assertEqual(0, ai_gateway.get_ai_gateway().build_report()["total_ai_calls"])


if __name__ == "__main__":
    unittest.main()
