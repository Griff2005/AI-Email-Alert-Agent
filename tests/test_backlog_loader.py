import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import ai_gateway
import backlog_loader
import database as db
from config import config

SUPPORTED_CASE_TYPES = (
    "CAT1_COMPLIANCE",
    "CAT5_COMPLIANCE",
    "DATA_ABSENCE",
    "MAINTENANCE_HOURS_SHORTFALL",
    "MAJOR_WORK_OVERDUE",
    "GOVERNMENT_DIRECTIVE",
)


def _make_kpi_email(case_type, idx=1):
    bodies = {
        "CAT1_COMPLIANCE": (
            f"Client: Test Client {idx}\n"
            f"Building: Test Building {idx}\n"
            f"Device: B-1 #70000{idx}\n"
            "Contractor: Test Co\n"
            "CAT1 Tests Reminder: CAT1 compliance tests are due."
        ),
        "CAT5_COMPLIANCE": (
            f"Client: Test Client {idx}\n"
            f"Building: Test Building {idx}\n"
            f"Device: B-2 #70000{idx}\n"
            "Contractor: Test Co\n"
            "CAT5 Tests Reminder: CAT5 compliance tests are due."
        ),
        "DATA_ABSENCE": (
            f"Client: Test Client {idx}\n"
            f"Building: Test Building {idx}\n"
            "Contractor: Test Co\n"
            "Data Absence Alert: Maintenance data has never been submitted. Elapsed: 30 days."
        ),
        "MAINTENANCE_HOURS_SHORTFALL": (
            f"Client: Test Client {idx}\n"
            f"Building: Test Building {idx}\n"
            "Contractor: Test Co\n"
            "Reporting Period: Jan 2026\n"
            "Contract Hours: 40\n"
            "Actual Hours: 20\n"
            "Device | Required | Actual\n"
            f"B-3 #70000{idx} | 40 | 20\n"
            "Maintenance hours less than required."
        ),
        "MAJOR_WORK_OVERDUE": (
            f"Client: Test Client {idx}\n"
            f"Building: Test Building {idx}\n"
            f"Device: B-1 #70000{idx}\n"
            "Contractor: Test Co\n"
            "ScheduledDate: 2026-01-10\n"
            "Scheduled work is overdue."
        ),
        "GOVERNMENT_DIRECTIVE": (
            f"Client: Test Client {idx}\n"
            f"Building: Test Building {idx}\n"
            f"Device: B-1 #70000{idx}\n"
            "Contractor: Test Co\n"
            "DueDate: 2026-03-01\n"
            "Outstanding Government Directive: Action required."
        ),
    }
    subjects = {
        "CAT1_COMPLIANCE": "CAT1 Tests Reminder",
        "CAT5_COMPLIANCE": "CAT5 Tests Reminder",
        "DATA_ABSENCE": "Maintenance data has never been submitted",
        "MAINTENANCE_HOURS_SHORTFALL": "Maintenance Hours Less Than Required",
        "MAJOR_WORK_OVERDUE": "Scheduled Work is Overdue",
        "GOVERNMENT_DIRECTIVE": "Outstanding Government Directive",
    }
    return {
        "message_id": f"test-{case_type.lower()}-{idx}@test.example",
        "subject": subjects[case_type],
        "from_addr": "kpi-alerts@test.example",
        "to_addrs": [f"client{idx}@test.example"],
        "cc_addrs": [],
        "bcc_addrs": [],
        "reply_to": "kpi-alerts@test.example",
        "received_at": f"2026-01-{10 + idx:02d}T09:00:00",
        "body": bodies[case_type],
    }


class BacklogLoaderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "test_agent.db"
        self.report_dir = self.tmp_dir / "reports"
        self.source_path = self.tmp_dir / "backlog.json"

        self._original_db_path = config.DATABASE_PATH
        self._original_ai_report_path = config.AI_REPORT_PATH
        self._original_cache_path = config.CLAUDE_CACHE_PATH

        config.DATABASE_PATH = self.db_path
        config.AI_REPORT_PATH = self.tmp_dir / "ai_usage_report.json"
        config.CLAUDE_CACHE_PATH = self.tmp_dir / "claude_cache.json"

        db.close_connection()
        db.init_schema()
        ai_gateway.reset_gateway()

    def tearDown(self):
        ai_gateway.reset_gateway()
        db.close_connection()
        config.DATABASE_PATH = self._original_db_path
        config.AI_REPORT_PATH = self._original_ai_report_path
        config.CLAUDE_CACHE_PATH = self._original_cache_path
        shutil.rmtree(self.tmp_dir)

    def _write_source(self, records):
        self.source_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        return self.source_path

    def _run_loader(self, records, *, dry_run, report_dir=None, limit=None):
        ai_gateway.reset_gateway()
        self._write_source(records)
        return backlog_loader.load_backlog(
            source="json",
            path=self.source_path,
            dry_run=dry_run,
            limit=limit,
            report_dir=report_dir or self.report_dir,
        )

    def _count_rows(self, table_name, where=None, params=()):
        conn = db.get_connection()
        sql = f"SELECT COUNT(*) AS count FROM {table_name}"
        if where:
            sql += f" WHERE {where}"
        return conn.execute(sql, params).fetchone()["count"]

    def _read_json(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def _report_path(self, name):
        return self.report_dir / name


class TestDryRunMode(BacklogLoaderTestCase):
    def test_dry_run_does_not_modify_db(self):
        records = [
            _make_kpi_email("CAT1_COMPLIANCE", 1),
            _make_kpi_email("CAT5_COMPLIANCE", 2),
        ]

        result = self._run_loader(records, dry_run=True)

        self.assertEqual(2, result["accepted_kpi"])
        self.assertEqual(0, self._count_rows("emails"))
        self.assertEqual(0, self._count_rows("cases"))
        self.assertEqual(0, self._count_rows("case_events"))

    def test_dry_run_report_written(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=True)

        self.assertTrue(self.report_dir.exists())
        self.assertTrue(self._report_path("report.json").exists())
        self.assertTrue(self._report_path("report.md").exists())

    def test_dry_run_report_has_dry_run_note(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=True)

        report = self._read_json(self._report_path("report.json"))
        self.assertEqual("dry_run", report["mode"])
        self.assertIn("dry_run_note", report)


class TestCommitMode(BacklogLoaderTestCase):
    def test_commit_imports_accepted_kpi_emails(self):
        records = [
            _make_kpi_email(case_type, idx)
            for idx, case_type in enumerate(SUPPORTED_CASE_TYPES, start=1)
        ]

        result = self._run_loader(records, dry_run=False)
        conn = db.get_connection()
        case_types = {
            row["case_type"]
            for row in conn.execute("SELECT case_type FROM cases").fetchall()
        }
        event_types = {
            row["event_type"]
            for row in conn.execute("SELECT event_type FROM case_events").fetchall()
        }

        self.assertEqual(len(SUPPORTED_CASE_TYPES), result["accepted_kpi"])
        self.assertEqual(len(SUPPORTED_CASE_TYPES), self._count_rows("emails"))
        self.assertEqual(len(SUPPORTED_CASE_TYPES), self._count_rows("cases"))
        self.assertEqual(set(SUPPORTED_CASE_TYPES), case_types)
        self.assertGreaterEqual(self._count_rows("case_events"), len(SUPPORTED_CASE_TYPES) * 3)
        self.assertIn("backlog_case_created", event_types)
        self.assertIn("backlog_email_imported", event_types)
        self.assertIn("backlog_memory_updated", event_types)

    def test_commit_creates_cases(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=False)

        self.assertGreaterEqual(self._count_rows("cases"), 1)

    def test_commit_no_outbound_messages(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=False)

        self.assertEqual(0, self._count_rows("outbound_messages"))

    def test_commit_no_followups(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=False)

        self.assertEqual(0, self._count_rows("followups"))

    def test_commit_no_closed_cases(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=False)

        self.assertEqual(0, self._count_rows("cases", "status = ?", ("closed",)))


class TestFiltering(BacklogLoaderTestCase):
    def test_non_kpi_rejected(self):
        records = [
            {
                "message_id": "out-of-office@test.example",
                "subject": "Out of Office Automatic Reply",
                "from_addr": "person@test.example",
                "to_addrs": ["client@test.example"],
                "cc_addrs": [],
                "bcc_addrs": [],
                "reply_to": "",
                "received_at": "2026-02-01T09:00:00",
                "body": "I am away from the office.",
            },
            {
                "message_id": "invoice@test.example",
                "subject": "Invoice 1001",
                "from_addr": "billing@test.example",
                "to_addrs": ["client@test.example"],
                "cc_addrs": [],
                "bcc_addrs": [],
                "reply_to": "",
                "received_at": "2026-02-01T10:00:00",
                "body": "Attached is your monthly invoice.",
            },
        ]

        result = self._run_loader(records, dry_run=False)
        rejected_subjects = {
            item["subject"]
            for item in result["rejected_items"]
        }

        self.assertEqual(2, result["rejected"])
        self.assertEqual(
            {"Out of Office Automatic Reply", "Invoice 1001"},
            rejected_subjects,
        )
        self.assertEqual(0, self._count_rows("emails"))
        self.assertEqual(0, self._count_rows("cases"))

    def test_unsupported_kpi_not_forced_into_case(self):
        records = [
            {
                "message_id": "unsupported-compliance@test.example",
                "subject": "Compliance Review Needed",
                "from_addr": "kpi-alerts@test.example",
                "to_addrs": ["client@test.example"],
                "cc_addrs": [],
                "bcc_addrs": [],
                "reply_to": "kpi-alerts@test.example",
                "received_at": "2026-02-03T09:00:00",
                "body": (
                    "Client: Test Client\n"
                    "Building: Test Building\n"
                    "This compliance notice needs attention but does not match a supported KPI body pattern."
                ),
            }
        ]

        result = self._run_loader(records, dry_run=False)

        self.assertEqual(1, result["review_candidates"])
        self.assertEqual("Compliance Review Needed", result["review_items"][0]["subject"])
        self.assertEqual(0, self._count_rows("cases"))

    def test_supported_body_phrase_without_supported_subject_goes_to_review(self):
        record = _make_kpi_email("DATA_ABSENCE", 1)
        record["subject"] = "Weekly KPI Digest"

        result = self._run_loader([record], dry_run=True)

        self.assertEqual(0, result["accepted_kpi"])
        self.assertEqual(1, result["review_candidates"])
        self.assertEqual("review", result["results"][0]["action"])
        self.assertEqual("backlog_subject_gate", result["results"][0]["classification"]["source"])

    def test_empty_subject_rejected(self):
        record = _make_kpi_email("CAT1_COMPLIANCE", 1)
        record["subject"] = ""

        result = self._run_loader([record], dry_run=True)

        self.assertEqual(1, result["rejected"])
        self.assertEqual("rejected", result["results"][0]["action"])
        self.assertEqual(0, self._count_rows("emails"))

    def test_empty_body_rejected(self):
        record = _make_kpi_email("CAT1_COMPLIANCE", 1)
        record["body"] = ""

        result = self._run_loader([record], dry_run=True)

        self.assertEqual(1, result["rejected"])
        self.assertEqual("rejected", result["results"][0]["action"])
        self.assertEqual(0, self._count_rows("emails"))


class TestDeduplication(BacklogLoaderTestCase):
    def test_duplicate_input_in_json(self):
        record = _make_kpi_email("CAT1_COMPLIANCE", 1)
        duplicate = dict(record)

        result = self._run_loader([record, duplicate], dry_run=False)

        self.assertEqual(1, result["duplicate_inputs"])
        self.assertEqual(1, self._count_rows("emails"))
        self.assertEqual(1, self._count_rows("cases"))

    def test_duplicate_db_email_skipped(self):
        record = _make_kpi_email("CAT1_COMPLIANCE", 1)

        self._run_loader([record], dry_run=False, report_dir=self.tmp_dir / "reports-first")
        second_result = self._run_loader([record], dry_run=False, report_dir=self.tmp_dir / "reports-second")

        self.assertGreaterEqual(second_result["duplicate_inputs"], 1)
        self.assertEqual(1, self._count_rows("emails"))
        self.assertEqual(1, self._count_rows("cases"))

    def test_duplicate_case_grouping(self):
        first = _make_kpi_email("CAT1_COMPLIANCE", 1)
        second = dict(first)
        second["message_id"] = "test-cat1-duplicate-group@test.example"
        second["subject"] = "CAT1 Reminder"
        second["received_at"] = "2026-01-21T09:00:00"
        second["body"] = first["body"].replace("Test Client 1", "Test Client 2")
        second["to_addrs"] = ["client2@test.example"]

        result = self._run_loader([first, second], dry_run=False)
        event_types = {
            row["event_type"]
            for row in db.get_connection().execute("SELECT event_type FROM case_events").fetchall()
        }

        self.assertEqual(1, result["new_cases_expected_or_created"])
        self.assertEqual(1, result["case_updates_expected_or_done"])
        self.assertEqual(2, self._count_rows("emails"))
        self.assertEqual(1, self._count_rows("cases"))
        self.assertIn("backlog_case_created", event_types)
        self.assertIn("backlog_case_updated", event_types)

    def test_missing_received_at_does_not_change_synthetic_message_id(self):
        raw = {
            "subject": "CAT1 Tests Reminder",
            "body": "Building: Test Building\nDevice: B-1 #700001\nCAT1 test reminder.",
        }

        with mock.patch.object(backlog_loader, "_generated_at_timestamp", return_value="2026-01-01T00:00:00Z"):
            first = backlog_loader._normalize_record(raw)
        with mock.patch.object(backlog_loader, "_generated_at_timestamp", return_value="2026-01-02T00:00:00Z"):
            second = backlog_loader._normalize_record(raw)

        self.assertEqual(first["message_id"], second["message_id"])
        self.assertEqual("2026-01-01T00:00:00Z", first["received_at"])
        self.assertEqual("2026-01-02T00:00:00Z", second["received_at"])

    def test_synthetic_message_id_hashes_full_body(self):
        shared_prefix = "X" * 200
        first = backlog_loader._synthetic_message_id("subject", "", shared_prefix + "first")
        second = backlog_loader._synthetic_message_id("subject", "", shared_prefix + "second")

        self.assertNotEqual(first, second)


class TestClassificationHelpers(unittest.TestCase):
    def test_supported_case_types_are_hardcoded(self):
        self.assertIsInstance(backlog_loader._SUPPORTED_CASE_TYPES, frozenset)
        self.assertEqual(frozenset(SUPPORTED_CASE_TYPES), backlog_loader._SUPPORTED_CASE_TYPES)

    def test_match_subject_to_case_type(self):
        self.assertEqual(
            "DATA_ABSENCE",
            backlog_loader._match_subject_to_case_type("Maintenance data has never been submitted"),
        )
        self.assertIsNone(backlog_loader._match_subject_to_case_type("Weekly KPI Digest"))

    def test_classify_for_backlog_can_override_ambiguous_body_from_subject(self):
        result = backlog_loader._classify_for_backlog(
            {
                "subject": "Data Absence",
                "body": (
                    "Building: Test Building\n"
                    "Contractor: Test Co\n"
                    "Maintenance data missing. Elapsed: 30 days."
                ),
            }
        )

        self.assertEqual("DATA_ABSENCE", result["case_type"])
        self.assertEqual("backlog_subject_override", result["source"])


class TestRecipientSummary(BacklogLoaderTestCase):
    def test_recipient_summary_structure(self):
        record = _make_kpi_email("CAT1_COMPLIANCE", 1)
        record["cc_addrs"] = ["manager@test.example"]

        self._run_loader([record], dry_run=True)
        summary = self._read_json(self._report_path("recipient_summary.json"))

        self.assertIn("unique_recipients", summary)
        self.assertIn("missing_recipient_count", summary)
        self.assertIn("by_domain", summary)
        self.assertIn("top_to_recipients", summary)
        self.assertIn("top_cc_recipients", summary)

    def test_missing_recipients_counted(self):
        record = _make_kpi_email("CAT1_COMPLIANCE", 1)
        record["from_addr"] = ""
        record["to_addrs"] = []
        record["cc_addrs"] = []
        record["bcc_addrs"] = []
        record["reply_to"] = ""

        self._run_loader([record], dry_run=True)
        summary = self._read_json(self._report_path("recipient_summary.json"))

        self.assertGreater(summary["missing_recipient_count"], 0)


class TestReports(BacklogLoaderTestCase):
    def test_all_report_files_written(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=False)

        self.assertTrue(self._report_path("report.json").exists())
        self.assertTrue(self._report_path("report.md").exists())
        self.assertTrue(self._report_path("rejected.json").exists())
        self.assertTrue(self._report_path("review_candidates.json").exists())
        self.assertTrue(self._report_path("recipient_summary.json").exists())

    def test_ai_calls_zero_in_report(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=False)

        report = self._read_json(self._report_path("report.json"))
        self.assertEqual(0, report["ai_calls"])

    def test_outbound_zero_in_report(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=False)

        report = self._read_json(self._report_path("report.json"))
        self.assertEqual(0, report["outbound_emails"])

    def test_followups_zero_in_report(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=False)

        report = self._read_json(self._report_path("report.json"))
        self.assertEqual(0, report["followups_scheduled"])

    def test_dry_run_report_includes_expected_memory_observations_only(self):
        result = self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=True)

        report = self._read_json(self._report_path("report.json"))
        self.assertEqual(result["expected_memory_observations"], report["expected_memory_observations"])
        self.assertNotIn("manual_reviews_created", report)
        self.assertNotIn("manual_reviews_created", result)

    def test_commit_marks_backlog_emails_processed(self):
        self._run_loader([_make_kpi_email("CAT1_COMPLIANCE", 1)], dry_run=False)

        row = db.get_connection().execute("SELECT processed FROM emails").fetchone()
        self.assertEqual(1, row["processed"])

    def test_report_notes_review_candidates_are_file_only(self):
        record = _make_kpi_email("DATA_ABSENCE", 1)
        record["subject"] = "Weekly KPI Digest"

        self._run_loader([record], dry_run=True)

        report_md = self._report_path("report.md").read_text(encoding="utf-8")
        self.assertIn("review_candidates.json", report_md)
        self.assertIn("NOT in the live review queue", report_md)


if __name__ == "__main__":
    unittest.main()
