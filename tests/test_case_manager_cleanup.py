import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import ai_gateway
import database as db
from case_manager import process_email
from config import config
from constants import EVENT_EMAIL_DRY_RUN, EVENT_EMAIL_SENT, STATUS_DRAFT
from runtime_options import RuntimeOptions, runtime_options


class CaseManagerCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        self.original_demo_mode = config.DEMO_MODE
        self.original_demo_recipient = config.DEMO_RECIPIENT_EMAIL
        config.DATABASE_PATH = Path(self.temp_dir.name) / "agent.db"
        config.DEMO_MODE = True
        config.DEMO_RECIPIENT_EMAIL = "demo-case-manager@example.test"
        db.close_connection()
        db.init_schema()
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions(disable_outbound_generation=True, followups_enabled=False))

    def tearDown(self):
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions())
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        config.DEMO_MODE = self.original_demo_mode
        config.DEMO_RECIPIENT_EMAIL = self.original_demo_recipient
        self.temp_dir.cleanup()

    def _insert_email(self, email_id: str, subject: str, body: str) -> None:
        db.insert_email(
            email_id=email_id,
            message_id=f"{email_id}@example.test",
            thread_id=None,
            subject=subject,
            from_addr="alerts@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-06T08:15:00",
            raw_body=body,
            normalized_text=body,
        )

    def test_reprocessing_same_review_email_reuses_existing_review_case(self):
        email_id = "review-email-1"
        subject = "Maintenance Data is not up to date"
        body = (
            "Data Absence Alert\n\n"
            "Maintenance data is significantly out of date for the following device.\n\n"
            "Building: 321 Placeholder Boulevard\n"
            "Last Activity: 15-Oct-2025\n"
            "Elapsed: 203 days since last maintenance record\n"
        )
        db.insert_email(
            email_id=email_id,
            message_id=email_id,
            thread_id=None,
            subject=subject,
            from_addr="alerts@example.com",
            to_addr="agent@example.com",
            received_at="2026-05-06T08:15:00",
            raw_body=body,
            normalized_text=body,
        )

        first = process_email(email_id=email_id, subject=subject, body=body, verbose=False)
        second = process_email(email_id=email_id, subject=subject, body=body, verbose=False)

        self.assertEqual("review_flagged", first["action"])
        self.assertEqual("review_flagged", second["action"])
        self.assertEqual(first["case_id"], second["case_id"])
        open_reviews = db.get_open_manual_reviews()
        self.assertEqual(1, len(open_reviews))

    def test_process_email_creates_draft_without_dry_run_send_by_default(self):
        runtime_options.configure(
            RuntimeOptions(
                disable_outbound_generation=False,
                followups_enabled=False,
                template_outbound_only=True,
            )
        )
        email_id = "draft-email-1"
        subject = "CAT1 Tests Reminder"
        body = (
            "Client: Example Client\n"
            "Building: 123 Example Road\n"
            "Device: B-4 #731842\n"
            "Contractor: Example Elevator Company\n"
            "CAT1 Tests Reminder: CAT1 compliance tests are due."
        )
        self._insert_email(email_id, subject, body)

        result = process_email(email_id=email_id, subject=subject, body=body, verbose=False)

        messages = db.get_messages_for_case(result["case_id"])
        self.assertEqual(1, len(messages))
        self.assertEqual(STATUS_DRAFT, messages[0]["status"])
        self.assertEqual("contractor@solucore-production.com", messages[0]["intended_to"])
        self.assertEqual(config.DEMO_RECIPIENT_EMAIL, messages[0]["actual_to"])
        event_types = {event["event_type"] for event in db.get_events_for_case(result["case_id"])}
        self.assertNotIn(EVENT_EMAIL_DRY_RUN, event_types)
        self.assertNotIn(EVENT_EMAIL_SENT, event_types)

    def test_process_email_respects_disabled_outbound_generation(self):
        runtime_options.configure(
            RuntimeOptions(
                disable_outbound_generation=True,
                followups_enabled=False,
                template_outbound_only=True,
            )
        )
        email_id = "no-draft-email-1"
        subject = "CAT5 Tests Reminder"
        body = (
            "Client: Example Client\n"
            "Building: 456 Sample Street\n"
            "Device: C-2 #990012\n"
            "Contractor: Example Elevator Company\n"
            "CAT5 Tests Reminder: CAT5 compliance tests are due."
        )
        self._insert_email(email_id, subject, body)

        result = process_email(email_id=email_id, subject=subject, body=body, verbose=False)

        self.assertEqual("created", result["action"])
        self.assertEqual([], db.get_messages_for_case(result["case_id"]))


if __name__ == "__main__":
    unittest.main()
