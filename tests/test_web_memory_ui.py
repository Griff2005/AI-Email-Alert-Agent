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

try:
    from web.app import app as flask_app
except ImportError:  # pragma: no cover - lightweight environments may omit Flask
    flask_app = None


@unittest.skipIf(flask_app is None, "Flask is not installed")
class WebMemoryUiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        self.original_demo_mode = config.DEMO_MODE
        self.original_demo_recipient = config.DEMO_RECIPIENT_EMAIL

        config.DATABASE_PATH = Path(self.temp_dir.name) / "agent.db"
        config.DEMO_MODE = True
        config.DEMO_RECIPIENT_EMAIL = "demo-review@example.com"

        db.close_connection()
        db.init_schema()
        self._seed_memory_demo_data()
        flask_app.config.update(TESTING=True)
        self.client = flask_app.test_client()

    def tearDown(self):
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        config.DEMO_MODE = self.original_demo_mode
        config.DEMO_RECIPIENT_EMAIL = self.original_demo_recipient
        self.temp_dir.cleanup()

    def _insert_case(
        self,
        case_id,
        case_type="DATA_ABSENCE",
        building="123 Example Road",
        device="ELV-2",
        contractor="Example Elevator Co.",
        created_at="2026-05-01T10:00:00",
    ):
        db.insert_case(
            case_id=case_id,
            case_type=case_type,
            grouping_key=f"{case_type}|{case_id}",
            building=building,
            device=device,
            contractor=contractor,
            due_date=None,
            period=None,
            priority="medium",
        )
        conn = db.get_connection()
        conn.execute(
            "UPDATE cases SET created_at = ?, updated_at = ? WHERE case_id = ?",
            (created_at, created_at, case_id),
        )
        conn.commit()

    def _seed_memory_demo_data(self):
        self._insert_case("case-main-1234", created_at="2026-05-03T10:00:00")
        self._insert_case("case-related-1", created_at="2026-04-24T10:00:00")
        self._insert_case("case-related-2", device="ELV-7", created_at="2026-04-18T10:00:00")

        db.insert_case_link_record(
            source_case_id="case-main-1234",
            target_case_id="case-related-1",
            link_type="same_building",
            reason="Shared building: 123 Example Road",
        )
        db.insert_case_link_record(
            source_case_id="case-main-1234",
            target_case_id="case-related-2",
            link_type="repeated_issue",
            reason="Shared case type with overlapping location.",
        )

        observation_id = db.insert_observation_record(
            case_id="case-main-1234",
            email_id=None,
            entity_id=None,
            observation_type="building_seen",
            entity_type="building",
            entity_value="123 Example Road",
            value_text="Building appeared in a KPI alert.",
            value_json=None,
            observed_at="2026-05-03T10:00:00",
            source="inbound_email",
            confidence=1.0,
        )

        evidence = {
            "rule": "building recurrence threshold",
            "entity_type": "building",
            "entity_value": "123 Example Road",
            "observed_count": 3,
            "threshold": 3,
            "time_window_days": 60,
            "supporting_case_ids": ["case-main-1234", "case-related-1", "case-related-2"],
            "supporting_observation_ids": [observation_id],
        }
        db.upsert_pattern_flag_record(
            case_id="case-main-1234",
            pattern_type="repeated_building_issue",
            severity="medium",
            summary="Recurring issue detected for 123 Example Road.",
            evidence_json=json.dumps(evidence),
        )

        db.insert_manual_review(
            review_id="review-pattern-1",
            case_id="case-main-1234",
            email_id=None,
            reason="Pattern review: Recurring issue detected for 123 Example Road. (severity: medium).",
        )

    def test_cases_list_shows_memory_signal_counts(self):
        response = self.client.get("/cases")

        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("Pattern Signals", html)
        self.assertIn("Related", html)
        self.assertIn("Reviews", html)
        self.assertIn("Repeated Building", html)

    def test_case_detail_renders_memory_intelligence_cards_and_evidence(self):
        response = self.client.get("/cases/case-main-1234")

        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("Memory / Intelligence", html)
        self.assertIn("Pattern Signals", html)
        self.assertIn("Entity Connections", html)
        self.assertIn("Related Cases", html)
        self.assertIn("Recent Observations", html)
        self.assertIn("Supporting Evidence", html)
        self.assertIn("3 cases observed for building 123 Example Road over 60 days", html)
        self.assertIn("Mechanic/technician data has not appeared", html)

    def test_patterns_page_renders_overview_and_evidence(self):
        response = self.client.get("/patterns")

        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("Intelligence Overview", html)
        self.assertIn("Active pattern flags", html)
        self.assertIn("Buildings with repeated issues", html)
        self.assertIn("Supporting Evidence", html)
        self.assertIn("building recurrence threshold", html)
        self.assertIn("case-related-1", html)


if __name__ == "__main__":
    unittest.main()
