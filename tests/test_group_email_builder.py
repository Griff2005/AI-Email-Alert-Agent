import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import backlog_loader
import database as db
from building_groups import attach_case_to_group
from config import config
from constants import (
    CASE_TYPE_CAT1_COMPLIANCE,
    CASE_TYPE_DATA_ABSENCE,
    CASE_TYPE_GOVERNMENT_DIRECTIVE,
)
from group_email_builder import (
    approve_group_email_draft,
    build_consolidated_email,
    create_group_email_draft,
    reject_group_email_draft,
    validate_draft_quality,
)


class GroupEmailBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "agent.db"
        self.backlog_path = self.tmp_dir / "backlog.json"
        self.report_dir = self.tmp_dir / "reports"
        self.original_database_path = config.DATABASE_PATH
        self.original_demo_mode = config.DEMO_MODE
        self.original_demo_recipient = config.DEMO_RECIPIENT_EMAIL

        config.DATABASE_PATH = self.db_path
        config.DEMO_MODE = True
        config.DEMO_RECIPIENT_EMAIL = "demo-group-drafts@example.test"

        db.close_connection()
        db.init_schema()
        self.group_id = self._seed_group_with_cases()

    def tearDown(self):
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        config.DEMO_MODE = self.original_demo_mode
        config.DEMO_RECIPIENT_EMAIL = self.original_demo_recipient
        shutil.rmtree(self.tmp_dir)

    def _count_rows(self, table_name: str) -> int:
        conn = db.get_connection()
        return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()["count"])

    def _insert_case(
        self,
        case_id: str,
        case_type: str,
        *,
        building: str = "Group Draft Building",
        contractor: str = "Group Draft Contractor",
        device: str | None = None,
        due_date: str | None = None,
        period: str | None = None,
        status: str = "open",
    ) -> str:
        email_id = f"email-{case_id}"
        db.insert_email(
            email_id=email_id,
            message_id=f"{email_id}@example.test",
            thread_id=None,
            subject=f"{case_type} alert",
            from_addr="alerts@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body="Group draft test body.",
            normalized_text="Group draft test body.",
        )
        db.mark_email_processed(email_id)
        db.insert_case(
            case_id=case_id,
            case_type=case_type,
            grouping_key=f"{case_type}|{case_id}",
            building=building,
            device=device,
            contractor=contractor,
            due_date=due_date,
            period=period,
            priority="medium",
        )
        if status != "open":
            db.update_case(case_id, {"status": status})
        for field_name, field_value in (
            ("building", building),
            ("contractor", contractor),
            ("device", device),
            ("due_date", due_date),
            ("period", period),
        ):
            if field_value:
                db.insert_extracted_field(
                    field_id=f"field-{field_name}-{case_id}",
                    case_id=case_id,
                    email_id=email_id,
                    field_name=field_name,
                    field_value=field_value,
                    confidence_score=1.0,
                )
        return email_id

    def _seed_group_with_cases(self) -> str:
        self._insert_case(
            "case-draft-data",
            CASE_TYPE_DATA_ABSENCE,
            period="April 2026",
        )
        self._insert_case(
            "case-draft-cat1",
            CASE_TYPE_CAT1_COMPLIANCE,
            device="Elevator 1",
            due_date="2026-06-30",
        )
        self._insert_case(
            "case-draft-closed",
            CASE_TYPE_GOVERNMENT_DIRECTIVE,
            due_date="2026-05-30",
            status="closed",
        )
        group_id = attach_case_to_group("case-draft-data")
        attach_case_to_group("case-draft-cat1")
        attach_case_to_group("case-draft-closed")
        return group_id

    def test_build_consolidated_email_includes_all_open_child_cases(self):
        draft = build_consolidated_email(self.group_id)

        self.assertIn("case-draft-data", draft["body"])
        self.assertIn(CASE_TYPE_DATA_ABSENCE, draft["body"])
        self.assertIn("case-draft-cat1", draft["body"])
        self.assertIn(CASE_TYPE_CAT1_COMPLIANCE, draft["body"])
        self.assertNotIn("case-draft-closed", draft["body"])

    def test_build_consolidated_email_includes_required_response_instructions(self):
        draft = build_consolidated_email(self.group_id)

        self.assertIn("Required response", draft["body"])
        self.assertIn("maintenance data has been uploaded", draft["body"])
        self.assertIn("scheduled or completed date", draft["body"])

    def test_build_consolidated_email_excludes_ai_hypothesis_markers(self):
        draft = build_consolidated_email(self.group_id)

        forbidden = ("AI hypothesis", "internal note", "prompt injection", "reasoning:")
        for marker in forbidden:
            self.assertNotIn(marker.lower(), draft["body"].lower())

    def test_build_consolidated_email_demo_mode_actual_to_is_demo_recipient(self):
        draft = build_consolidated_email(self.group_id)

        self.assertEqual("demo-group-drafts@example.test", draft["actual_to"])
        self.assertNotEqual("", draft["intended_to"])

    def test_validate_draft_quality_fails_missing_subject(self):
        draft = build_consolidated_email(self.group_id)
        draft["subject"] = ""

        with self.assertRaises(ValueError):
            validate_draft_quality(draft)

    def test_validate_draft_quality_fails_missing_required_instructions(self):
        draft = build_consolidated_email(self.group_id)
        draft["body"] = "Hello,\n\nGroup Draft Building\nGroup Draft Contractor\n"

        with self.assertRaises(ValueError):
            validate_draft_quality(draft)

    def test_create_group_email_draft_writes_db_row(self):
        group_email_id = create_group_email_draft(self.group_id)

        row = db.get_building_group_email(group_email_id)
        self.assertIsNotNone(row)
        self.assertEqual(self.group_id, row["group_id"])
        self.assertEqual("draft_generated", row["status"])
        self.assertEqual("demo-group-drafts@example.test", row["actual_to"])
        quality = json.loads(row["quality_check_json"])
        self.assertTrue(quality["passed"])

    def test_create_group_email_draft_blocked_by_open_blocking_review(self):
        db.insert_manual_review(
            review_id="review-blocking-group-draft",
            case_id="case-draft-data",
            email_id=None,
            reason="Blocking review for draft test.",
        )

        with self.assertRaises(ValueError):
            create_group_email_draft(self.group_id)

    def test_approve_draft_moves_to_approved_without_sending(self):
        group_email_id = create_group_email_draft(self.group_id)

        updated = approve_group_email_draft(group_email_id, notes="Approved for review test.")

        self.assertEqual("approved", updated["status"])
        self.assertIsNone(updated["sent_at"])

    def test_reject_draft_moves_to_rejected(self):
        group_email_id = create_group_email_draft(self.group_id)

        updated = reject_group_email_draft(group_email_id, notes="Needs edits.")

        self.assertEqual("rejected", updated["status"])
        self.assertIsNotNone(updated["rejected_at"])
        self.assertEqual("Needs edits.", updated["review_notes"])

    def test_draft_excluded_from_backlog_import(self):
        db.close_connection()
        config.DATABASE_PATH = self.tmp_dir / "backlog-agent.db"
        db.init_schema()
        self.backlog_path.write_text(
            json.dumps(
                [
                    {
                        "message_id": "backlog-group-draft-safety@example.test",
                        "subject": "Data Absence: Maintenance data has never been submitted",
                        "from_addr": "kpi-alerts@example.test",
                        "to_addrs": ["client@example.test"],
                        "cc_addrs": [],
                        "bcc_addrs": [],
                        "reply_to": "kpi-alerts@example.test",
                        "received_at": "2026-05-02T09:00:00",
                        "body": (
                            "Client: Test Client\n"
                            "Building: Backlog Draft Building\n"
                            "Contractor: Backlog Draft Contractor\n"
                            "Data Absence Alert: Maintenance data has never been submitted. "
                            "Elapsed: 30 days."
                        ),
                    }
                ],
                indent=2,
            ),
            encoding="utf-8",
        )

        backlog_loader.load_backlog(
            source="json",
            path=self.backlog_path,
            dry_run=False,
            report_dir=self.report_dir,
        )

        self.assertEqual(0, self._count_rows("building_group_emails"))
        self.assertEqual(0, self._count_rows("communication_queue"))


if __name__ == "__main__":
    unittest.main()
