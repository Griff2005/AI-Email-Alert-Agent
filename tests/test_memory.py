import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import database as db
import email_sender
import memory
from config import config


class MemoryTests(unittest.TestCase):
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
        grouping_key=None,
        building=None,
        device=None,
        contractor=None,
        created_at=None,
    ):
        db.insert_case(
            case_id=case_id,
            case_type=case_type,
            grouping_key=grouping_key or f"{case_type}|{case_id}",
            building=building,
            device=device,
            contractor=contractor,
            due_date=None,
            period=None,
            priority="medium",
        )
        if created_at:
            conn = db.get_connection()
            conn.execute(
                "UPDATE cases SET created_at = ?, updated_at = ? WHERE case_id = ?",
                (created_at, created_at, case_id),
            )
            conn.commit()

    def _insert_email(self, email_id, received_at="2026-05-01T10:00:00"):
        db.insert_email(
            email_id=email_id,
            message_id=f"message-{email_id}",
            thread_id=None,
            subject="Test subject",
            from_addr="alerts@example.com",
            to_addr="agent@example.com",
            received_at=received_at,
            raw_body="Test body",
            normalized_text="Test body",
        )

    def test_normalize_text_dedupes_equivalent_names(self):
        entity_one = memory.upsert_entity("contractor", "  Example Elevator Co.  ")
        entity_two = memory.upsert_entity("contractor", "example elevator co.")
        self.assertEqual(entity_one, entity_two)

    def test_record_case_observations_records_core_entities(self):
        self._insert_case(
            case_id="case-1",
            case_type="MAJOR_WORK_OVERDUE",
            building="Alpha Tower",
            device="Car 1",
            contractor="Example Elevator Co.",
        )
        self._insert_email("email-1")

        memory.record_case_observations(
            case_id="case-1",
            email_id="email-1",
            case_type="MAJOR_WORK_OVERDUE",
            fields={
                "building": "Alpha Tower",
                "device": "Car 1",
                "contractor": "Example Elevator Co.",
            },
        )

        rows = db.get_connection().execute(
            """
            SELECT observation_type, entity_type, entity_value
            FROM observations
            WHERE case_id = ?
            ORDER BY observation_type ASC
            """,
            ("case-1",),
        ).fetchall()
        seen = {(row["observation_type"], row["entity_type"], row["entity_value"]) for row in rows}

        self.assertIn(("building_seen", "building", "Alpha Tower"), seen)
        self.assertIn(("device_seen", "device", "Car 1"), seen)
        self.assertIn(("contractor_seen", "contractor", "Example Elevator Co."), seen)
        self.assertIn(("issue_seen", "issue_type", "MAJOR_WORK_OVERDUE"), seen)

    def test_record_case_observations_does_not_invent_mechanics(self):
        self._insert_case(case_id="case-2", case_type="DATA_ABSENCE")
        self._insert_email("email-2")

        memory.record_case_observations(
            case_id="case-2",
            email_id="email-2",
            case_type="DATA_ABSENCE",
            fields={"building": "Beta Plaza"},
        )

        mechanic_rows = db.get_connection().execute(
            "SELECT * FROM observations WHERE case_id = ? AND observation_type = 'mechanic_seen'",
            ("case-2",),
        ).fetchall()
        self.assertEqual([], list(mechanic_rows))

    def test_repeated_building_issue_flag_after_three_recent_cases(self):
        timestamps = [
            "2026-05-01T09:00:00",
            "2026-05-10T09:00:00",
            "2026-05-20T09:00:00",
        ]
        for index, observed_at in enumerate(timestamps, start=1):
            case_id = f"building-case-{index}"
            email_id = f"building-email-{index}"
            self._insert_case(
                case_id=case_id,
                case_type="DATA_ABSENCE",
                building="Recurring Building",
                grouping_key=f"data_absence|recurring-building|{index}",
                created_at=observed_at,
            )
            self._insert_email(email_id, received_at=observed_at)
            memory.record_case_observations(
                case_id=case_id,
                email_id=email_id,
                case_type="DATA_ABSENCE",
                fields={"building": "Recurring Building"},
            )

        created = memory.detect_patterns_for_case("building-case-3")
        pattern_types = {item["pattern_type"] for item in created}
        self.assertIn("repeated_building_issue", pattern_types)

    def test_repeated_device_issue_flag_after_two_recent_cases(self):
        timestamps = [
            "2026-05-01T09:00:00",
            "2026-06-01T09:00:00",
        ]
        for index, observed_at in enumerate(timestamps, start=1):
            case_id = f"device-case-{index}"
            email_id = f"device-email-{index}"
            self._insert_case(
                case_id=case_id,
                case_type="CAT1_COMPLIANCE",
                building="Gamma Centre",
                device="B-4 #731842",
                grouping_key=f"cat1|gamma-centre|b-4-731842|{index}",
                created_at=observed_at,
            )
            self._insert_email(email_id, received_at=observed_at)
            memory.record_case_observations(
                case_id=case_id,
                email_id=email_id,
                case_type="CAT1_COMPLIANCE",
                fields={"building": "Gamma Centre", "device": "B-4 #731842"},
            )

        created = memory.detect_patterns_for_case("device-case-2")
        pattern_types = {item["pattern_type"] for item in created}
        self.assertIn("repeated_device_issue", pattern_types)

    def test_repeated_no_response_flag_after_multiple_followups(self):
        self._insert_case(
            case_id="case-no-response",
            case_type="MAINTENANCE_HOURS_SHORTFALL",
            contractor="Responder Elevator",
        )
        db.upsert_followup(
            followup_id="followup-1",
            case_id="case-no-response",
            deadline="2026-05-01T09:00:00",
        )
        db.increment_followup_count("case-no-response")
        db.increment_followup_count("case-no-response")

        memory.record_case_observations(
            case_id="case-no-response",
            email_id=None,
            case_type="MAINTENANCE_HOURS_SHORTFALL",
            fields={"contractor": "Responder Elevator"},
            source="system",
        )
        memory.record_no_response("case-no-response")

        created = memory.detect_patterns_for_case("case-no-response")
        pattern_types = {item["pattern_type"] for item in created}
        self.assertIn("repeated_no_response", pattern_types)

    def test_detect_patterns_is_idempotent_for_same_evidence(self):
        timestamps = [
            "2026-05-01T09:00:00",
            "2026-05-10T09:00:00",
            "2026-05-20T09:00:00",
        ]
        for index, observed_at in enumerate(timestamps, start=1):
            case_id = f"idempotent-case-{index}"
            email_id = f"idempotent-email-{index}"
            self._insert_case(
                case_id=case_id,
                case_type="DATA_ABSENCE",
                building="Stable Building",
                grouping_key=f"data_absence|stable-building|{index}",
                created_at=observed_at,
            )
            self._insert_email(email_id, received_at=observed_at)
            memory.record_case_observations(
                case_id=case_id,
                email_id=email_id,
                case_type="DATA_ABSENCE",
                fields={"building": "Stable Building"},
            )

        memory.detect_patterns_for_case("idempotent-case-3")
        memory.detect_patterns_for_case("idempotent-case-3")

        count = db.get_connection().execute(
            """
            SELECT COUNT(*) AS count
            FROM pattern_flags
            WHERE case_id = ?
              AND pattern_type = 'repeated_building_issue'
              AND status = 'active'
            """,
            ("idempotent-case-3",),
        ).fetchone()["count"]
        self.assertEqual(1, count)

    def test_demo_safety_routes_email_to_demo_recipient(self):
        self._insert_case(case_id="case-email")

        msg_id = email_sender.create_draft(
            case_id="case-email",
            subject="Follow-up Required",
            body="Please review this case.",
            intended_to="real-contractor@example.com",
        )

        row = db.get_connection().execute(
            "SELECT intended_to, actual_to, subject, body FROM outbound_messages WHERE msg_id = ?",
            (msg_id,),
        ).fetchone()
        self.assertEqual("real-contractor@example.com", row["intended_to"])
        self.assertEqual("demo-review@example.com", row["actual_to"])
        self.assertTrue(row["subject"].startswith("[DEMO] "))
        self.assertIn("demo review only", row["body"].lower())


if __name__ == "__main__":
    unittest.main()
