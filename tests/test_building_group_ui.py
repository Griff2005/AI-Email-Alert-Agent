import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import database as db
from building_groups import attach_case_to_group
from config import config
from constants import CASE_TYPE_DATA_ABSENCE

try:
    from web.app import app as flask_app
except ImportError:  # pragma: no cover - lightweight environments may omit Flask
    flask_app = None


@unittest.skipIf(flask_app is None, "Flask is not installed")
class BuildingGroupUiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        self.original_demo_mode = config.DEMO_MODE
        self.original_demo_recipient = config.DEMO_RECIPIENT_EMAIL

        config.DATABASE_PATH = Path(self.temp_dir.name) / "agent.db"
        config.DEMO_MODE = True
        config.DEMO_RECIPIENT_EMAIL = "demo-building-groups@example.test"

        db.close_connection()
        db.init_schema()
        self.group_id = self._seed_group()
        flask_app.config.update(TESTING=True)
        self.client = flask_app.test_client()

    def tearDown(self):
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        config.DEMO_MODE = self.original_demo_mode
        config.DEMO_RECIPIENT_EMAIL = self.original_demo_recipient
        self.temp_dir.cleanup()

    def _seed_group(self):
        email_id = "email-ui-group-1"
        case_id = "case-ui-group-1"
        db.insert_email(
            email_id=email_id,
            message_id=f"{email_id}@example.test",
            thread_id=None,
            subject="Maintenance data has never been submitted",
            from_addr="alerts@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body="Building group UI test body.",
            normalized_text="Building group UI test body.",
        )
        db.mark_email_processed(email_id)
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            grouping_key=f"{CASE_TYPE_DATA_ABSENCE}|{case_id}",
            building="UI Test Building",
            device=None,
            contractor="UI Contractor",
            due_date=None,
            period=None,
            priority="medium",
        )
        db.insert_extracted_field(
            field_id="field-ui-building",
            case_id=case_id,
            email_id=email_id,
            field_name="building",
            field_value="UI Test Building",
            confidence_score=1.0,
        )
        db.insert_extracted_field(
            field_id="field-ui-contractor",
            case_id=case_id,
            email_id=email_id,
            field_name="contractor",
            field_value="UI Contractor",
            confidence_score=1.0,
        )
        return attach_case_to_group(case_id)

    def test_building_groups_route_loads(self):
        response = self.client.get("/building-groups")

        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("Building Issue Groups", html)
        self.assertIn("UI Test Building", html)
        self.assertIn("UI Contractor", html)

    def test_building_group_detail_route_loads(self):
        response = self.client.get(f"/building-groups/{self.group_id}")

        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("UI Test Building", html)
        self.assertIn("case-ui-group-1", html)
        self.assertIn(CASE_TYPE_DATA_ABSENCE, html)


if __name__ == "__main__":
    unittest.main()
