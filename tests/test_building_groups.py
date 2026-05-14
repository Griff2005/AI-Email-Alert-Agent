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

import ai_gateway
import backlog_loader
import database as db
from building_groups import (
    attach_case_to_group,
    build_grouping_key,
    list_building_groups,
    normalize_group_value,
    rebuild_all_groups,
)
from config import config
from constants import CASE_TYPE_DATA_ABSENCE, CASE_TYPE_UNKNOWN


class BuildingGroupsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "agent.db"
        self.report_dir = self.tmp_dir / "reports"
        self.source_path = self.tmp_dir / "backlog.json"
        self.original_database_path = config.DATABASE_PATH
        self.original_ai_report_path = config.AI_REPORT_PATH
        self.original_cache_path = config.CLAUDE_CACHE_PATH

        config.DATABASE_PATH = self.db_path
        config.AI_REPORT_PATH = self.tmp_dir / "ai_usage_report.json"
        config.CLAUDE_CACHE_PATH = self.tmp_dir / "claude_cache.json"

        db.close_connection()
        db.init_schema()
        ai_gateway.reset_gateway()

    def tearDown(self):
        ai_gateway.reset_gateway()
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        config.AI_REPORT_PATH = self.original_ai_report_path
        config.CLAUDE_CACHE_PATH = self.original_cache_path
        shutil.rmtree(self.tmp_dir)

    def _count_rows(self, table_name, where=None, params=()):
        conn = db.get_connection()
        sql = f"SELECT COUNT(*) AS count FROM {table_name}"
        if where:
            sql += f" WHERE {where}"
        return conn.execute(sql, params).fetchone()["count"]

    def _manual_reviews_for_case(self, case_id):
        conn = db.get_connection()
        return conn.execute(
            """
            SELECT *
            FROM manual_reviews
            WHERE case_id = ?
            ORDER BY flagged_at ASC
            """,
            (case_id,),
        ).fetchall()

    def _insert_case(
        self,
        case_id,
        *,
        case_type=CASE_TYPE_DATA_ABSENCE,
        building="123 Example Road",
        contractor="Example Elevator Company",
        status="open",
    ):
        email_id = f"email-{case_id}"
        db.insert_email(
            email_id=email_id,
            message_id=f"{email_id}@example.test",
            thread_id=None,
            subject="Maintenance data has never been submitted",
            from_addr="alerts@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body="Building and contractor test body.",
            normalized_text="Building and contractor test body.",
        )
        db.mark_email_processed(email_id)
        db.insert_case(
            case_id=case_id,
            case_type=case_type,
            grouping_key=f"{case_type}|{case_id}",
            building=building,
            device=None,
            contractor=contractor,
            due_date=None,
            period=None,
            priority="medium",
        )
        if status != "open":
            db.update_case(case_id, {"status": status})
        if building is not None:
            db.insert_extracted_field(
                field_id=f"field-building-{case_id}",
                case_id=case_id,
                email_id=email_id,
                field_name="building",
                field_value=building,
                confidence_score=1.0,
            )
        if contractor is not None:
            db.insert_extracted_field(
                field_id=f"field-contractor-{case_id}",
                case_id=case_id,
                email_id=email_id,
                field_name="contractor",
                field_value=contractor,
                confidence_score=1.0,
            )

    def _backlog_record(self):
        return {
            "message_id": "backlog-data-absence-1@example.test",
            "subject": "Data Absence: Maintenance data has never been submitted",
            "from_addr": "kpi-alerts@example.test",
            "to_addrs": ["client@example.test"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "reply_to": "kpi-alerts@example.test",
            "received_at": "2026-05-02T09:00:00",
            "body": (
                "Client: Test Client\n"
                "Building: Backlog Building\n"
                "Contractor: Backlog Elevator Company\n"
                "Data Absence Alert: Maintenance data has never been submitted. "
                "Elapsed: 30 days."
            ),
        }

    def _run_backlog_import(self):
        self.source_path.write_text(json.dumps([self._backlog_record()], indent=2), encoding="utf-8")
        return backlog_loader.load_backlog(
            source="json",
            path=self.source_path,
            dry_run=False,
            report_dir=self.report_dir,
        )

    def test_group_created_for_case_with_building_and_contractor(self):
        self._insert_case("case-group-1")

        group_id = attach_case_to_group("case-group-1")

        conn = db.get_connection()
        group = conn.execute("SELECT * FROM building_issue_groups").fetchone()
        link = conn.execute("SELECT * FROM building_issue_group_cases").fetchone()
        self.assertIsNotNone(group_id)
        self.assertEqual(group_id, group["group_id"])
        self.assertEqual("123 Example Road", group["building"])
        self.assertEqual("Example Elevator Company", group["contractor"])
        self.assertEqual("case-group-1", link["case_id"])
        self.assertEqual("live_pipeline", link["source"])

    def test_same_building_contractor_reuses_group(self):
        self._insert_case(
            "case-group-1",
            building="  123   Example Road ",
            contractor="Example Elevator Company",
        )
        self._insert_case(
            "case-group-2",
            building="123 example road",
            contractor=" example   elevator company ",
        )

        first_group_id = attach_case_to_group("case-group-1")
        second_group_id = attach_case_to_group("case-group-2")

        self.assertEqual(first_group_id, second_group_id)
        self.assertEqual(1, self._count_rows("building_issue_groups"))
        self.assertEqual(2, self._count_rows("building_issue_group_cases"))

    def test_normalization_collapses_whitespace_and_case(self):
        self.assertEqual("123 main street", normalize_group_value("  123   MAIN\tStreet!!  "))
        self.assertEqual(
            "building_group::123 main street::acme elevator inc",
            build_grouping_key(" 123 MAIN Street!!", " ACME   Elevator, Inc. "),
        )

    def test_missing_building_does_not_group(self):
        self._insert_case("case-no-building", building=None, contractor="Example Elevator Company")

        group_id = attach_case_to_group("case-no-building")

        self.assertIsNone(group_id)
        self.assertEqual(0, self._count_rows("building_issue_groups"))
        self.assertEqual(0, self._count_rows("building_issue_group_cases"))

    def test_missing_contractor_does_not_group(self):
        self._insert_case("case-no-contractor", building="123 Example Road", contractor=None)

        group_id = attach_case_to_group("case-no-contractor")

        self.assertIsNone(group_id)
        self.assertEqual(0, self._count_rows("building_issue_groups"))
        self.assertEqual(0, self._count_rows("building_issue_group_cases"))

    def test_missing_building_creates_manual_review(self):
        self._insert_case("case-no-building-review", building=None, contractor="Example Elevator Company")

        group_id = attach_case_to_group("case-no-building-review")

        reviews = self._manual_reviews_for_case("case-no-building-review")
        self.assertIsNone(group_id)
        self.assertEqual(1, len(reviews))
        self.assertEqual(0, reviews[0]["resolved"])
        self.assertIn("building", reviews[0]["reason"].lower())
        self.assertIn("grouping", reviews[0]["reason"].lower())

    def test_missing_contractor_creates_manual_review(self):
        self._insert_case("case-no-contractor-review", building="123 Example Road", contractor=None)

        group_id = attach_case_to_group("case-no-contractor-review")

        reviews = self._manual_reviews_for_case("case-no-contractor-review")
        self.assertIsNone(group_id)
        self.assertEqual(1, len(reviews))
        self.assertEqual(0, reviews[0]["resolved"])
        self.assertIn("contractor", reviews[0]["reason"].lower())
        self.assertIn("grouping", reviews[0]["reason"].lower())

    def test_missing_grouping_data_backlog_does_not_create_review(self):
        self._insert_case("case-backlog-missing-building", building=None, contractor="Example Elevator Company")

        group_id = attach_case_to_group(
            "case-backlog-missing-building",
            source="backlog_import",
            enqueue=False,
        )

        self.assertIsNone(group_id)
        self.assertEqual(0, len(self._manual_reviews_for_case("case-backlog-missing-building")))

    def test_missing_grouping_review_not_duplicated(self):
        self._insert_case("case-no-building-duplicate", building=None, contractor="Example Elevator Company")

        attach_case_to_group("case-no-building-duplicate")
        attach_case_to_group("case-no-building-duplicate")

        reviews = self._manual_reviews_for_case("case-no-building-duplicate")
        self.assertEqual(1, len(reviews))

    def test_rebuild_all_groups_is_idempotent(self):
        self._insert_case("case-rebuild-1")
        self._insert_case("case-rebuild-2", building="123 example road", contractor="Example Elevator Company")

        first = rebuild_all_groups()
        second = rebuild_all_groups()

        self.assertEqual(2, first["attached"])
        self.assertEqual(0, second["attached"])
        self.assertEqual(1, self._count_rows("building_issue_groups"))
        self.assertEqual(2, self._count_rows("building_issue_group_cases"))

    def test_rebuild_missing_grouping_data_creates_manual_review(self):
        self._insert_case("case-rebuild-missing-building", building=None, contractor="Example Elevator Company")

        summary = rebuild_all_groups()

        reviews = self._manual_reviews_for_case("case-rebuild-missing-building")
        self.assertEqual(1, summary["skipped_missing_building"])
        self.assertEqual(1, len(reviews))
        self.assertIn("building", reviews[0]["reason"].lower())
        self.assertIn("grouping", reviews[0]["reason"].lower())

    def test_backlog_imported_case_can_be_grouped(self):
        result = self._run_backlog_import()

        groups = list_building_groups()
        conn = db.get_connection()
        link = conn.execute("SELECT * FROM building_issue_group_cases").fetchone()

        self.assertEqual(1, result["accepted_kpi"])
        self.assertEqual(1, len(groups))
        self.assertEqual("Backlog Building", groups[0]["building"])
        self.assertEqual("backlog_import", link["source"])

    def test_backlog_group_attach_creates_zero_outbound_rows(self):
        self._run_backlog_import()

        self.assertEqual(0, self._count_rows("outbound_messages"))

    def test_backlog_group_attach_creates_zero_draft_rows(self):
        self._run_backlog_import()

        self.assertEqual(0, self._count_rows("outbound_messages", "status = ?", ("draft",)))

    def test_case_has_at_most_one_active_group_link(self):
        self._insert_case(
            "case-moves-groups",
            building="123 Example Road",
            contractor="Example Elevator Company",
        )
        first_group_id = attach_case_to_group("case-moves-groups")
        db.insert_extracted_field(
            field_id="field-contractor-case-moves-groups-updated",
            case_id="case-moves-groups",
            email_id="email-case-moves-groups",
            field_name="contractor",
            field_value="Different Elevator Company",
            confidence_score=1.0,
        )

        second_group_id = attach_case_to_group("case-moves-groups")

        conn = db.get_connection()
        active_links = conn.execute(
            """
            SELECT *
            FROM building_issue_group_cases
            WHERE case_id = ? AND status = 'active'
            """,
            ("case-moves-groups",),
        ).fetchall()
        self.assertNotEqual(first_group_id, second_group_id)
        self.assertEqual(1, len(active_links))
        self.assertEqual(second_group_id, active_links[0]["group_id"])

    def test_stale_group_link_is_removed_not_deleted(self):
        self._insert_case(
            "case-stale-link",
            building="123 Example Road",
            contractor="Example Elevator Company",
        )
        first_group_id = attach_case_to_group("case-stale-link")
        db.insert_extracted_field(
            field_id="field-contractor-case-stale-link-updated",
            case_id="case-stale-link",
            email_id="email-case-stale-link",
            field_name="contractor",
            field_value="Different Elevator Company",
            confidence_score=1.0,
        )

        second_group_id = attach_case_to_group("case-stale-link")

        conn = db.get_connection()
        old_link = conn.execute(
            """
            SELECT *
            FROM building_issue_group_cases
            WHERE group_id = ? AND case_id = ?
            """,
            (first_group_id, "case-stale-link"),
        ).fetchone()
        self.assertIsNotNone(old_link)
        self.assertNotEqual(first_group_id, second_group_id)
        self.assertEqual("removed", old_link["status"])
        self.assertEqual(2, self._count_rows("building_issue_group_cases", "case_id = ?", ("case-stale-link",)))

    def test_unsupported_case_type_does_not_group(self):
        self._insert_case(
            "case-unknown",
            case_type=CASE_TYPE_UNKNOWN,
            building="123 Example Road",
            contractor="Example Elevator Company",
        )

        self.assertIsNone(attach_case_to_group("case-unknown"))
        self.assertEqual(0, self._count_rows("building_issue_groups"))


if __name__ == "__main__":
    unittest.main()
