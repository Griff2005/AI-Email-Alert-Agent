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
    CASE_TYPE_CAT5_COMPLIANCE,
    EVENT_BACKLOG_CASE_CREATED,
    EVENT_BACKLOG_CASE_UPDATED,
    EVENT_BACKLOG_EMAIL_IMPORTED,
    EVENT_CASE_CREATED,
)


class DatabaseReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.temp_dir.name) / "agent.db"
        db.close_connection()
        db.init_schema()

    def tearDown(self):
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def _insert_case_with_email_event(self, email_id: str, case_id: str, event_type: str) -> None:
        db.insert_email(
            email_id=email_id,
            message_id=f"{email_id}@example.test",
            thread_id=None,
            subject="CAT1 Tests Reminder",
            from_addr="alerts@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body="Building: 123 Example Road",
            normalized_text="Building: 123 Example Road",
        )
        db.mark_email_processed(email_id)
        db.insert_case(
            case_id=case_id,
            case_type=CASE_TYPE_CAT1_COMPLIANCE,
            grouping_key=f"{CASE_TYPE_CAT1_COMPLIANCE}|{case_id}",
            building="123 Example Road",
            device="B-4 #731842",
            contractor="Example Elevator Company",
            due_date=None,
            period=None,
            priority="high",
        )
        db.insert_case_event(
            event_id=f"event-{email_id}",
            case_id=case_id,
            event_type=event_type,
            description=f"{event_type} for reporting test.",
            source_email_id=email_id,
        )

    def test_email_pipeline_counts_normal_and_backlog_created_events(self):
        self._insert_case_with_email_event("email-normal-created", "case-normal-created", EVENT_CASE_CREATED)
        self._insert_case_with_email_event("email-backlog-created", "case-backlog-created", EVENT_BACKLOG_CASE_CREATED)

        summary = db.get_email_pipeline_summary()

        self.assertEqual(2, summary["created_cases"])

    def test_email_backlog_classifies_backlog_created_as_created(self):
        self._insert_case_with_email_event("email-backlog-created", "case-backlog-created", EVENT_BACKLOG_CASE_CREATED)

        rows = {row["email_id"]: row for row in db.get_email_backlog(limit=10)}

        self.assertEqual("created", rows["email-backlog-created"]["action"])

    def test_email_backlog_classifies_backlog_update_events_as_updated(self):
        self._insert_case_with_email_event("email-backlog-updated", "case-backlog-updated", EVENT_BACKLOG_CASE_UPDATED)
        self._insert_case_with_email_event("email-backlog-imported", "case-backlog-imported", EVENT_BACKLOG_EMAIL_IMPORTED)

        rows = {row["email_id"]: row for row in db.get_email_backlog(limit=10)}

        self.assertEqual("updated", rows["email-backlog-updated"]["action"])
        self.assertEqual("updated", rows["email-backlog-imported"]["action"])

    def test_update_case_rejects_unsupported_fields(self):
        db.insert_case(
            case_id="case-update-allowlist",
            case_type=CASE_TYPE_CAT5_COMPLIANCE,
            grouping_key="CAT5|case-update-allowlist",
            building="456 Sample Street",
            device="C-2 #990012",
            contractor="Example Elevator Company",
            due_date=None,
            period=None,
            priority="high",
        )

        with self.assertRaisesRegex(ValueError, "Unsupported case update field"):
            db.update_case("case-update-allowlist", {"case_type": "DATA_ABSENCE"})

    def test_update_case_allows_known_mutable_fields(self):
        db.insert_case(
            case_id="case-update-valid",
            case_type=CASE_TYPE_CAT5_COMPLIANCE,
            grouping_key="CAT5|case-update-valid",
            building="456 Sample Street",
            device="C-2 #990012",
            contractor="Example Elevator Company",
            due_date=None,
            period=None,
            priority="high",
        )

        db.update_case(
            "case-update-valid",
            {
                "status": "closed",
                "owner": "reviewer@example.test",
                "priority": "medium",
                "building": "789 Updated Road",
            },
        )

        row = db.get_case_by_id("case-update-valid")
        self.assertEqual("closed", row["status"])
        self.assertEqual("reviewer@example.test", row["owner"])
        self.assertEqual("medium", row["priority"])
        self.assertEqual("789 Updated Road", row["building"])


if __name__ == "__main__":
    unittest.main()
