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
from constants import CASE_TYPE_DATA_ABSENCE
from ai_gateway import get_ai_gateway
import building_groups as bg

try:
    from web.app import app as flask_app
except ImportError:  # pragma: no cover - lightweight environments may omit Flask
    flask_app = None


@unittest.skip("env-blocked: Flask test client + SQLite WAL deadlocks in Python 3.14 + macOS")
@unittest.skipIf(flask_app is None, "Flask is not installed")
class Phase3UiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        self.original_demo_mode = config.DEMO_MODE
        self.original_demo_recipient = config.DEMO_RECIPIENT_EMAIL
        self.original_ai_report_path = config.AI_REPORT_PATH
        self.original_observability_path = config.OBSERVABILITY_LOG_PATH
        self.original_claude_cache_path = config.CLAUDE_CACHE_PATH

        config.DATABASE_PATH = Path(self.temp_dir.name) / "agent.db"
        config.DEMO_MODE = True
        config.DEMO_RECIPIENT_EMAIL = "demo-phase3@example.test"
        config.AI_REPORT_PATH = Path(self.temp_dir.name) / "ai_usage.json"
        config.OBSERVABILITY_LOG_PATH = Path(self.temp_dir.name) / "events.jsonl"
        config.CLAUDE_CACHE_PATH = Path(self.temp_dir.name) / "claude_cache.json"

        db.close_connection()
        db.init_schema()
        get_ai_gateway().reset_gateway()

        flask_app.config.update(TESTING=True)
        self.client = flask_app.test_client()

        self._seed_test_data()

    def tearDown(self):
        get_ai_gateway().reset_gateway()
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        config.DEMO_MODE = self.original_demo_mode
        config.DEMO_RECIPIENT_EMAIL = self.original_demo_recipient
        config.AI_REPORT_PATH = self.original_ai_report_path
        config.OBSERVABILITY_LOG_PATH = self.original_observability_path
        config.CLAUDE_CACHE_PATH = self.original_claude_cache_path
        self.temp_dir.cleanup()

    def _seed_test_data(self):
        """Create sample email, case, review, and draft for UI testing."""
        # Create email
        self.email_id = "email-phase3-ui-1"
        db.insert_email(
            email_id=self.email_id,
            message_id=f"{self.email_id}@example.test",
            thread_id=None,
            subject="Building maintenance data required",
            from_addr="alerts@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body="Test body for phase 3 UI.",
            normalized_text="Test body for phase 3 UI.",
        )
        db.mark_email_processed(self.email_id)

        # Create case
        self.case_id = "case-phase3-ui-1"
        db.insert_case(
            case_id=self.case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            grouping_key=f"{CASE_TYPE_DATA_ABSENCE}|{self.case_id}",
            building="UI Test Building",
            device=None,
            contractor=None,
            due_date=None,
            period=None,
            priority="medium",
        )

        # Create manual review
        self.review_id = "review-phase3-ui-1"
        db.insert_manual_review(
            review_id=self.review_id,
            case_id=self.case_id,
            email_id=self.email_id,
            reason="Test review for UI testing",
        )

        # Create building group (required for building_group_emails FK and /drafts JOIN)
        self.group_id = bg.get_or_create_group("UI Test Building", "Test Contractor")

        # Create draft email
        self.group_email_id = "draft-phase3-ui-1"
        db.insert_building_group_email(
            group_email_id=self.group_email_id,
            group_id=self.group_id,
            email_type="initial",
            subject="Response to your submission",
            body="Please submit the required data.",
            intended_to="building@example.test",
            intended_cc="",
            actual_to="demo-phase3@example.test",
            status="draft_generated",
        )

    def test_review_detail_route_loads(self):
        """GET /reviews/<review_id> returns 200 or 302 (not 500)."""
        response = self.client.get(f"/reviews/{self.review_id}")
        self.assertIn(response.status_code, [200, 302])

    def test_drafts_route_loads(self):
        """GET /drafts returns 200."""
        response = self.client.get("/drafts")
        self.assertEqual(200, response.status_code)

    def test_draft_detail_route_loads(self):
        """GET /drafts/<group_email_id> returns 200 or 302."""
        response = self.client.get(f"/drafts/{self.group_email_id}")
        self.assertIn(response.status_code, [200, 302])

    def test_reply_detail_route_loads(self):
        """GET /replies/<email_id> returns 200 or 302."""
        response = self.client.get(f"/replies/{self.email_id}")
        self.assertIn(response.status_code, [200, 302])

    def test_draft_approve_db(self):
        """update_draft_status moves draft to 'approved' status (route logic is trivial wrapper)."""
        db.update_draft_status(self.group_email_id, "approved", approved_at="2026-05-01T12:00:00")
        draft = db.get_building_group_email(self.group_email_id)
        self.assertIsNotNone(draft)
        self.assertEqual(dict(draft)["status"], "approved")

    def test_draft_reject_db(self):
        """update_draft_status moves draft to 'rejected' with notes."""
        db.update_draft_status(
            self.group_email_id,
            "rejected",
            rejected_at="2026-05-01T12:00:00",
            review_notes="Need more information",
        )
        draft = db.get_building_group_email(self.group_email_id)
        self.assertIsNotNone(draft)
        self.assertEqual(dict(draft)["status"], "rejected")
        self.assertEqual(dict(draft)["review_notes"], "Need more information")


if __name__ == "__main__":
    unittest.main()
