import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import database as db
from config import config
from constants import (
    CASE_TYPE_CAT1_COMPLIANCE,
    CASE_TYPE_DATA_ABSENCE,
    CASE_TYPE_GOVERNMENT_DIRECTIVE,
)
from response_requirements import (
    get_required_response_items,
    validate_required_response_in_email,
)


class ResponseRequirementsTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.original_database_path = config.DATABASE_PATH
        config.DATABASE_PATH = self.tmp_dir / "agent.db"
        db.close_connection()
        db.init_schema()

    def tearDown(self):
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        shutil.rmtree(self.tmp_dir)

    def _insert_case(self, case_id: str, case_type: str) -> None:
        db.insert_case(
            case_id=case_id,
            case_type=case_type,
            grouping_key=f"{case_type}|{case_id}",
            building="Response Test Building",
            device=None,
            contractor="Response Test Contractor",
            due_date=None,
            period=None,
            priority="medium",
        )

    def test_get_required_response_items_data_absence_has_all_keys(self):
        items = get_required_response_items(CASE_TYPE_DATA_ABSENCE)

        self.assertEqual(
            [
                "upload_confirmation",
                "maintenance_activity_date",
                "data_delay_reason",
                "system_access_blocker",
                "correction_date",
            ],
            [item["key"] for item in items],
        )

    def test_get_required_response_items_cat1_compliance_has_all_keys(self):
        items = get_required_response_items(CASE_TYPE_CAT1_COMPLIANCE)

        self.assertEqual(
            [
                "test_status",
                "scheduled_or_completed_date",
                "contractor_confirmation",
                "documentation",
                "reason_if_delayed",
            ],
            [item["key"] for item in items],
        )

    def test_get_required_response_items_government_directive_has_all_keys(self):
        items = get_required_response_items(CASE_TYPE_GOVERNMENT_DIRECTIVE)

        self.assertEqual(
            [
                "compliance_status",
                "action_taken_or_planned",
                "expected_completion_date",
                "evidence_or_documentation",
                "extension_or_blocker",
            ],
            [item["key"] for item in items],
        )

    def test_validate_required_response_detects_missing_instruction(self):
        self._insert_case("case-missing-instructions", CASE_TYPE_DATA_ABSENCE)

        missing = validate_required_response_in_email(
            ["case-missing-instructions"],
            "Please provide confirmation whether maintenance data has been uploaded.",
        )

        self.assertIn("maintenance_activity_date", missing)
        self.assertIn("data_delay_reason", missing)
        self.assertIn("system_access_blocker", missing)
        self.assertIn("correction_date", missing)


if __name__ == "__main__":
    unittest.main()
