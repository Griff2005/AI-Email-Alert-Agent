"""Tests for missing-data review database helpers."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import ai_gateway
import database as db
from config import config
from constants import CASE_TYPE_DATA_ABSENCE
from response_requirements import get_required_response_items


class MissingDataReviewTests(unittest.TestCase):
    """Exercise read-only missing-data helper behavior."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._orig_db_path = config.DATABASE_PATH
        self._orig_cache_path = config.CLAUDE_CACHE_PATH
        self._orig_report_path = config.AI_REPORT_PATH
        self._orig_observability_path = config.OBSERVABILITY_LOG_PATH
        config.DATABASE_PATH = Path(self.temp_dir.name) / "test_agent.db"
        config.CLAUDE_CACHE_PATH = Path(self.temp_dir.name) / "claude_cache.json"
        config.AI_REPORT_PATH = Path(self.temp_dir.name) / "ai_usage.json"
        config.OBSERVABILITY_LOG_PATH = Path(self.temp_dir.name) / "events.jsonl"
        db.close_connection()
        db.init_schema()
        ai_gateway.reset_gateway()

    def tearDown(self):
        ai_gateway.reset_gateway()
        db.close_connection()
        config.DATABASE_PATH = self._orig_db_path
        config.CLAUDE_CACHE_PATH = self._orig_cache_path
        config.AI_REPORT_PATH = self._orig_report_path
        config.OBSERVABILITY_LOG_PATH = self._orig_observability_path
        self.temp_dir.cleanup()

    def _insert_email(self, email_id="email-1"):
        db.insert_email(
            email_id=email_id,
            message_id=f"{email_id}@example.test",
            thread_id=None,
            subject=f"Subject {email_id}",
            from_addr="sender@example.test",
            to_addr="triage@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body=f"Raw body {email_id}",
            normalized_text=f"Normalized body {email_id}",
        )

    def _insert_case(self, case_id="case-1", **overrides):
        values = {
            "case_id": case_id,
            "case_type": CASE_TYPE_DATA_ABSENCE,
            "grouping_key": f"{CASE_TYPE_DATA_ABSENCE}|{case_id}",
            "building": "100 Example Road",
            "device": "ELV-1",
            "contractor": "Example Elevator",
            "due_date": "2026-06-01",
            "period": "2026-05",
            "priority": "medium",
        }
        values.update(overrides)
        db.insert_case(**values)

    def _mark_required_evidence_provided(self, case_id):
        for item in get_required_response_items(CASE_TYPE_DATA_ABSENCE):
            db.upsert_case_data_requirement(
                requirement_id=f"req-{case_id}-{item['key']}",
                case_id=case_id,
                requirement_key=item["key"],
                label=item["label"],
                status="provided",
                required=1,
            )

    def test_source_email_lookup_via_events(self):
        self._insert_email("email-event")
        self._insert_case("case-event")
        db.insert_case_event(
            event_id="event-source-email",
            case_id="case-event",
            event_type="case_created",
            description="Created from email.",
            source_email_id="email-event",
        )

        rows = db.get_source_emails_for_case("case-event")

        self.assertEqual([row["email_id"] for row in rows], ["email-event"])

    def test_source_email_lookup_via_extracted_fields(self):
        self._insert_email("email-field")
        self._insert_case("case-field")
        db.insert_extracted_field(
            field_id="field-source-email",
            case_id="case-field",
            email_id="email-field",
            field_name="building",
            field_value="100 Example Road",
            confidence_score=1.0,
        )

        rows = db.get_source_emails_for_case("case-field")

        self.assertEqual([row["email_id"] for row in rows], ["email-field"])

    def test_source_email_lookup_deduplicates(self):
        self._insert_email("email-dupe")
        self._insert_case("case-dupe")
        db.insert_case_event(
            event_id="event-dupe",
            case_id="case-dupe",
            event_type="case_created",
            description="Created from email.",
            source_email_id="email-dupe",
        )
        db.insert_extracted_field(
            field_id="field-dupe",
            case_id="case-dupe",
            email_id="email-dupe",
            field_name="building",
            field_value="100 Example Road",
            confidence_score=1.0,
        )

        rows = db.get_source_emails_for_case("case-dupe")

        self.assertEqual([row["email_id"] for row in rows], ["email-dupe"])

    def test_email_cc_bcc_reply_to_persisted(self):
        db.insert_email(
            email_id="email-metadata",
            message_id="email-metadata@example.test",
            thread_id=None,
            subject="Email metadata",
            from_addr="sender@example.test",
            to_addr="triage@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body="Raw body",
            normalized_text="Normalized body",
            cc_addrs="cc@test.com",
            bcc_addrs="bcc@test.com",
            reply_to="reply@test.com",
        )

        row = db.get_email_by_id("email-metadata")

        self.assertEqual(row["cc_addrs"], "cc@test.com")
        self.assertEqual(row["bcc_addrs"], "bcc@test.com")
        self.assertEqual(row["reply_to"], "reply@test.com")

    def test_latest_field_values_last_value_wins(self):
        self._insert_email("email-latest")
        self._insert_case("case-latest")
        db.insert_extracted_field(
            field_id="field-old",
            case_id="case-latest",
            email_id="email-latest",
            field_name="contractor",
            field_value="Old Contractor",
            confidence_score=0.8,
        )
        db.insert_extracted_field(
            field_id="field-new",
            case_id="case-latest",
            email_id="email-latest",
            field_name="contractor",
            field_value="New Contractor",
            confidence_score=0.9,
        )

        values = db.get_latest_field_values_for_case("case-latest")

        self.assertEqual(values["contractor"], "New Contractor")

    def test_missing_data_cases_finds_blank_contractor(self):
        self._insert_case("case-missing-contractor", contractor=None)

        rows = db.list_missing_data_cases()
        row = next(
            item for item in rows
            if item["case_id"] == "case-missing-contractor"
        )

        self.assertIn("contractor", row["missing_fields"])

    def test_missing_data_cases_excludes_filled_cases(self):
        self._insert_email("email-filled")
        self._insert_case("case-filled")
        self._mark_required_evidence_provided("case-filled")
        db.insert_extracted_field(
            field_id="field-client",
            case_id="case-filled",
            email_id="email-filled",
            field_name="client",
            field_value="Example Client",
            confidence_score=1.0,
        )

        rows = db.list_missing_data_cases()

        self.assertNotIn("case-filled", {row["case_id"] for row in rows})

    def test_missing_data_detail_returns_none_for_unknown(self):
        self.assertIsNone(db.get_missing_data_case_detail("nonexistent-id"))

    def test_missing_data_detail_structure(self):
        self._insert_email("email-detail")
        self._insert_case("case-detail", contractor=None)
        db.insert_case_event(
            event_id="event-detail",
            case_id="case-detail",
            event_type="case_created",
            description="Created from email.",
            source_email_id="email-detail",
        )

        detail = db.get_missing_data_case_detail("case-detail")

        self.assertIsInstance(detail, dict)
        self.assertIn("case", detail)
        self.assertIn("source_emails", detail)
        self.assertIn("field_values", detail)
        self.assertIn("missing_fields", detail)
        self.assertEqual(detail["source_emails"][0]["email_id"], "email-detail")


if __name__ == "__main__":
    unittest.main()
