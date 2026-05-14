import json
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
import ai_gateway
from ai_gateway import get_ai_gateway
import building_groups as bg

try:
    from web.app import app as flask_app
except ImportError:  # pragma: no cover - lightweight environments may omit Flask
    flask_app = None


@unittest.skip("env-blocked: Flask test client + SQLite WAL deadlocks in Python 3.14 + macOS")
@unittest.skipIf(flask_app is None, "Flask is not installed")
class Phase5UiTests(unittest.TestCase):
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
        config.DEMO_RECIPIENT_EMAIL = "demo-phase5@example.test"
        config.AI_REPORT_PATH = Path(self.temp_dir.name) / "ai_usage.json"
        config.OBSERVABILITY_LOG_PATH = Path(self.temp_dir.name) / "events.jsonl"
        config.CLAUDE_CACHE_PATH = Path(self.temp_dir.name) / "claude_cache.json"

        db.close_connection()
        db.init_schema()
        ai_gateway.reset_gateway()

        flask_app.config.update(TESTING=True)
        self.client = flask_app.test_client()

        self._seed_test_data()

    def tearDown(self):
        ai_gateway.reset_gateway()
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        config.DEMO_MODE = self.original_demo_mode
        config.DEMO_RECIPIENT_EMAIL = self.original_demo_recipient
        config.AI_REPORT_PATH = self.original_ai_report_path
        config.OBSERVABILITY_LOG_PATH = self.original_observability_path
        config.CLAUDE_CACHE_PATH = self.original_claude_cache_path
        self.temp_dir.cleanup()

    def _seed_test_data(self):
        self.email_id = "email-phase5-ui-1"
        db.insert_email(
            email_id=self.email_id,
            message_id=f"{self.email_id}@example.test",
            thread_id=None,
            subject="Building maintenance data required",
            from_addr="alerts@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body="Test body for phase 5 UI.",
            normalized_text="Test body for phase 5 UI.",
        )
        db.mark_email_processed(self.email_id)

        self.reply_email_id = "reply-phase5-ui-1"
        db.insert_email(
            email_id=self.reply_email_id,
            message_id=f"{self.reply_email_id}@example.test",
            thread_id="thread-phase5-ui-1",
            subject="Re: Building maintenance data required",
            from_addr="contractor@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-02T10:00:00",
            raw_body="Reply body for phase 5 UI.",
            normalized_text="Reply body for phase 5 UI.",
        )

        self.case_id = "case-phase5-ui-1"
        db.insert_case(
            case_id=self.case_id,
            case_type=CASE_TYPE_DATA_ABSENCE,
            grouping_key=f"{CASE_TYPE_DATA_ABSENCE}|{self.case_id}",
            building="Phase 5 Building",
            device="ELV-5",
            contractor="Phase 5 Contractor",
            due_date=None,
            period=None,
            priority="medium",
        )

        self.review_id = "review-phase5-ui-1"
        db.insert_manual_review(
            review_id=self.review_id,
            case_id=self.case_id,
            email_id=self.email_id,
            reason="Test review for phase 5 UI",
        )

        self.group_id = bg.get_or_create_group("Phase 5 Building", "Phase 5 Contractor")

        self.group_email_id = "draft-phase5-ui-1"
        db.insert_building_group_email(
            group_email_id=self.group_email_id,
            group_id=self.group_id,
            email_type="initial",
            subject="Response to your submission",
            body="Please submit the required data.",
            intended_to="building@example.test",
            intended_cc="",
            actual_to="demo-phase5@example.test",
            status="draft_generated",
        )

        self.hypothesis_id = "hyp-phase5-ui-1"
        db.insert_connection_hypothesis(
            hypothesis_id=self.hypothesis_id,
            hypothesis_type="device_recurrence",
            summary="Possible recurring device issue for human review.",
            confidence="medium",
            risk_level="review",
            evidence_json=json.dumps(
                {
                    "case_ids": [self.case_id],
                    "building": "Phase 5 Building",
                    "contractor": "Phase 5 Contractor",
                    "device": "ELV-5",
                    "description": "Supported case evidence only.",
                }
            ),
            reasoning="The supported case history suggests a possible recurrence.",
            recommended_human_review="Review the linked case and source evidence.",
        )
        db.insert_connection_hypothesis_case(self.hypothesis_id, self.case_id)

        db.insert_discovery_run(
            run_id="run-phase5-ui-1",
            scope="patterns",
            status="completed",
            max_ai_calls=5,
            ai_calls_used=1,
            packets_created=1,
            packets_analyzed=1,
            hypotheses_created=1,
            completed_at="2026-05-02T11:00:00",
        )

    def test_needs_attention_route_loads(self):
        response = self.client.get("/needs-attention")
        self.assertEqual(200, response.status_code)

    def test_replies_route_loads(self):
        response = self.client.get("/replies")
        self.assertEqual(200, response.status_code)

    def test_connection_hypotheses_route_loads(self):
        response = self.client.get("/connection-hypotheses")
        self.assertEqual(200, response.status_code)

    def test_connection_hypothesis_detail_route_loads(self):
        response = self.client.get(f"/connection-hypotheses/{self.hypothesis_id}")
        self.assertIn(response.status_code, [200, 302])

    def test_observability_route_loads(self):
        response = self.client.get("/observability")
        self.assertEqual(200, response.status_code)

    def test_settings_route_loads(self):
        response = self.client.get("/settings")
        self.assertEqual(200, response.status_code)

    def test_jobs_route_loads(self):
        response = self.client.get("/jobs")
        self.assertEqual(200, response.status_code)

    def test_demo_banner_present_on_dashboard(self):
        response = self.client.get("/")
        self.assertIn(b"DEMO MODE", response.data)
        self.assertIn(b"all outbound email redirected to demo-phase5@example.test", response.data)

    def test_settings_does_not_expose_password_strings(self):
        response = self.client.get("/settings")
        self.assertNotIn(b"password", response.data.lower())

    def test_hypothesis_detail_shows_proposed_label(self):
        response = self.client.get(f"/connection-hypotheses/{self.hypothesis_id}")
        self.assertIn(b"proposed", response.data.lower())


if __name__ == "__main__":
    unittest.main()
