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


class TestTriageBuckets(BacklogLoaderTestCase):
    def test_callback_alert_is_recognized_unsupported_kpi(self):
        record = {
            "message_id": "cb-001@test.example",
            "subject": "Callback Alert - 123 Test Street, Toronto",
            "from_addr": "alerts@test.example",
            "to_addrs": ["ops@test.example"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "reply_to": "",
            "received_at": "2026-01-10T09:00:00",
            "body": "Client: Test Client\nBuilding: 123 Test Street\nCallback alert: device requires service.",
        }

        result = self._run_loader([record], dry_run=True)

        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])
        self.assertEqual(0, result["accepted_kpi"])

    def test_back_in_service_is_recognized_unsupported_kpi(self):
        record = {
            "message_id": "bis-001@test.example",
            "subject": "Back in Service - 456 Demo Avenue, Toronto",
            "from_addr": "alerts@test.example",
            "to_addrs": ["ops@test.example"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "reply_to": "",
            "received_at": "2026-01-11T09:00:00",
            "body": "Client: Test Client\nBuilding: 456 Demo Avenue\nDevice is back in service.",
        }

        result = self._run_loader([record], dry_run=True)

        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_data_absence_evolve_format_accepted(self):
        record = {
            "message_id": "da-evolve-001@test.example",
            "subject": "Data Absence: Maintenance Data is not up to date - 55 Bloor Street, Toronto",
            "from_addr": "kpi-alerts@test.example",
            "to_addrs": ["ops@test.example"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "reply_to": "",
            "received_at": "2026-01-12T09:00:00",
            "body": (
                "Client: Test Client\n"
                "Contractor: Test Maintenance Co\n"
                "Last Activity Date:&nbsp;06-Jun-2019\n"
                "Elapsed Days:&nbsp;179\n"
                "Data Status: Maintenance data is not up to date."
            ),
        }

        result = self._run_loader([record], dry_run=True)

        self.assertEqual(1, result["accepted_kpi"])
        self.assertEqual(0, result["review_candidates"])
        self.assertEqual(1, result["new_cases_expected_or_created"])

    def test_major_work_overdue_evolve_format_accepted(self):
        record = {
            "message_id": "mwo-evolve-001@test.example",
            "subject": "Major Scheduled Work is Overdue - 99 Demo Road, Toronto",
            "from_addr": "projects@test.example",
            "to_addrs": ["facilities@test.example"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "reply_to": "",
            "received_at": "2026-01-15T10:00:00",
            "body": (
                "The scheduled major maintenance at the above noted site appear to be overdue.\n"
                "Device\n"
                "ScheduledDate\n"
                "Description\n"
                "1 #36578\n"
                "Scheduled Maintenance\n"
                "04-Aug-2019\n"
                "Replace governor rope set."
            ),
        }

        result = self._run_loader([record], dry_run=True)

        self.assertEqual(1, result["accepted_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_automatic_reply_is_safe_rejected(self):
        record = {
            "message_id": "oof-002@test.example",
            "subject": "Automatic Reply: Re: CAT1 Tests Reminder",
            "from_addr": "person@test.example",
            "to_addrs": ["kpi@test.example"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "reply_to": "",
            "received_at": "2026-01-16T09:00:00",
            "body": "I am out of the office.",
        }

        result = self._run_loader([record], dry_run=True)

        self.assertEqual(1, result["rejected"])
        self.assertEqual(0, result["review_candidates"])

    def test_ai_calls_zero_after_triage_changes(self):
        records = [
            {
                "message_id": "cb-002@test.example",
                "subject": "Callback Alert - Building A",
                "from_addr": "a@test.example",
                "to_addrs": ["b@test.example"],
                "cc_addrs": [],
                "bcc_addrs": [],
                "reply_to": "",
                "received_at": "2026-01-17T09:00:00",
                "body": "callback alert details here",
            },
            {
                "message_id": "bis-002@test.example",
                "subject": "Back in Service Notification",
                "from_addr": "a@test.example",
                "to_addrs": ["b@test.example"],
                "cc_addrs": [],
                "bcc_addrs": [],
                "reply_to": "",
                "received_at": "2026-01-18T09:00:00",
                "body": "device back in service confirmed",
            },
        ]

        result = self._run_loader(records, dry_run=True)

        self.assertEqual(0, result["ai_calls"])
        self.assertEqual(0, result["outbound_emails"])
        self.assertEqual(0, result["followups_scheduled"])
        self.assertEqual(2, result["recognized_unsupported_kpi"])

    def test_unsupported_kpis_json_written(self):
        record = {
            "message_id": "cb-003@test.example",
            "subject": "Callback Alert",
            "from_addr": "a@test.example",
            "to_addrs": ["b@test.example"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "reply_to": "",
            "received_at": "2026-01-19T09:00:00",
            "body": "Callback alert details.",
        }

        self._run_loader([record], dry_run=True)

        self.assertTrue(self._report_path("unsupported_kpis.json").exists())

    def test_report_json_has_recognized_unsupported_kpi_count(self):
        record = {
            "message_id": "cb-004@test.example",
            "subject": "Callback Alert",
            "from_addr": "a@test.example",
            "to_addrs": ["b@test.example"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "reply_to": "",
            "received_at": "2026-01-20T09:00:00",
            "body": "Callback alert details.",
        }

        self._run_loader([record], dry_run=True)

        report = self._read_json(self._report_path("report.json"))
        self.assertIn("recognized_unsupported_kpi", report)
        self.assertEqual(1, report["recognized_unsupported_kpi"])


class TestReduceReview(BacklogLoaderTestCase):

    def _make_record(self, subject: str, body: str, msg_id: str) -> dict:
        return {
            "message_id": msg_id,
            "subject": subject,
            "from_addr": "no-reply@solucore.com",
            "to_addrs": ["recipient@example.com"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "reply_to": "",
            "received_at": "2026-01-10T09:00:00",
            "body": body,
        }

    def test_open_callback_reminder_recognized_unsupported(self):
        record = self._make_record(
            "Open Callback(s) Reminder - York Mills Centre(36 York Mills), Toronto",
            "This is a reminder that the following callbacks are open for more than 24 hours.",
            "ocr-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_open_callback_reminder_slash_bilingual_recognized_unsupported(self):
        record = self._make_record(
            "Open Callback Reminder / Appel de service ouvert - Colliers International",
            "This is a reminder about an open callback.",
            "ocr-002@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])

    def test_plain_callback_recognized_unsupported(self):
        record = self._make_record(
            "Callback N22 #33995 (North_Low_Rise) - 483 Bay St, Toronto",
            "Please be advised of the following: Elevator Bank: North_Low_Rise Device: N22 #33995.",
            "cb-plain-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_possible_shutdown_recognized_unsupported(self):
        record = self._make_record(
            "Possible Shutdown Alert - JLL - 11 King St W, Toronto (2 #14165)",
            "This device may be shut down. Please investigate.",
            "psd-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])

    def test_apps_exception_recognized_unsupported(self):
        record = self._make_record(
            "APPS EXCEPTION - OtisServiceCaller",
            "An application exception occurred in OtisServiceCaller. Please review logs.",
            "appsex-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])

    def test_activities_uploaded_recognized_unsupported(self):
        record = self._make_record(
            "Activities Uploaded (#U834C128)",
            "Service activities have been uploaded to the system.",
            "act-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])

    def test_no_car_running_recognized_unsupported(self):
        record = self._make_record(
            "*** EMERGENCY - NO CAR RUNNING IN THE BANK *** Callback / *** URGENCE ***",
            "Emergency: no car running in the bank. Immediate attention required.",
            "ncr-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])

    def test_evolve_account_setup_safe_rejected(self):
        record = self._make_record(
            "Your e-volve account has been set up",
            "Welcome to e-volve. Your account has been created.",
            "acct-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["rejected"])
        self.assertEqual(0, result["review_candidates"])

    def test_evolve_login_safe_rejected(self):
        record = self._make_record(
            "Your eVolve Login",
            "Here are your login credentials for eVolve.",
            "login-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["rejected"])

    def test_new_contact_safe_rejected(self):
        record = self._make_record(
            "New Contact",
            "A new contact has been added to the system.",
            "nc-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["rejected"])

    def test_data_absence_two_column_contractor_extracted(self):
        """Real e-volve DATA_ABSENCE format extracts contractor from two-column table."""
        record = self._make_record(
            "Data Absence: Maintenance Data is not up to date - 55 Bloor Street, Toronto",
            (
                "Dear recipient:\r\n"
                " Per your request, this e-mail was sent to inform you that this is the"
                " last maintenance data received from the following contractor.\r\n"
                " \r\n \r\n \r\n"
                " Contractor \r\n Details \r\n \r\n \r\n"
                " Schindler Elevator Corporation \r\n"
                " Last Activity Date:&nbsp;06-Jun-2019 \r\n"
                "Elapsed Days:&nbsp;179 \r\n"
                " \r\n Regards, \r\ne_volve TM Webmaster"
            ),
            "da-extract-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["accepted_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_data_absence_inline_contractor_extracted(self):
        """Flattened e-volve DATA_ABSENCE body still extracts contractor."""
        record = self._make_record(
            "Data Absence: Maintenance Data is not up to date - 375 University Avenue,\r\n Toronto",
            (
                "Dear recipient: Per your request, this e-mail was sent to inform you that "
                "this is the last maintenance data received from the following contractor. "
                "Contractor Details ThyssenKrupp Elevator (Canada) Limited "
                "Last Activity Date:&nbsp;04-Apr-2017 Elapsed Days:&nbsp;1063 Regards, "
                "e_volve TM Webmaster"
            ),
            "da-extract-003@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["accepted_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_data_absence_multiline_subject_building_extracted(self):
        """Subject with embedded \\r\\n still yields correct building."""
        record = self._make_record(
            "Data Absence: Maintenance Data is not up to date - 55 Bloor Street,\r\n Toronto",
            (
                "Dear recipient:\r\n"
                " Contractor \r\n Details \r\n \r\n"
                " Schindler Elevator Corporation \r\n"
                " Last Activity Date:&nbsp;01-Jan-2020 \r\n"
                "Elapsed Days:&nbsp;90 \r\n Regards"
            ),
            "da-extract-002@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["accepted_kpi"])

    def test_government_directive_building_device_duedate_extracted(self):
        """Real e-volve GOVERNMENT_DIRECTIVE format extracts all required fields."""
        record = self._make_record(
            "Outstanding Government Directive - 45 St. Clair Avenue, Toronto",
            (
                "Dear recipient: This e-mail was sent to inform you that the government "
                "directives at the above noted site appear to be outstanding. "
                "ThyssenKrupp Elevator (Canada) Limited "
                "Device DueDate Description "
                "1 #19363 28-Nov-2019 8.6.1 (CAD)\r\n"
                "The overdue scheduled maintenance task for brakes shall be performed."
            ),
            "gd-extract-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["accepted_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_government_directive_multiline_subject_building_extracted(self):
        """Subject with \\r\\n still yields correct building for GOVERNMENT_DIRECTIVE."""
        record = self._make_record(
            "Outstanding Government Directive - Lansing Square (2550 Victoria\r\n Park), North York",
            (
                "Dear recipient: Government directives are outstanding. "
                "ThyssenKrupp Elevator (Canada) Limited "
                "Device DueDate Description "
                "3 #60599 02-Apr-2020 8.6.1 (CAD)\r\n"
                "The overdue scheduled maintenance task for governors shall be performed."
            ),
            "gd-extract-002@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["accepted_kpi"])

    def test_report_json_has_top_unknown_subject_patterns(self):
        """report.json includes top_unknown_subject_patterns key."""
        record = self._make_record(
            "Completely Unknown Subject Type XYZ",
            "This is a completely unknown email type.",
            "unk-report-001@test.example",
        )
        self._run_loader([record], dry_run=True)
        report = self._read_json(self._report_path("report.json"))
        self.assertIn("top_unknown_subject_patterns", report)

    def test_ai_calls_remain_zero(self):
        records = [
            self._make_record(
                "Open Callback(s) Reminder - Test Building",
                "Open callback reminder.",
                f"ai-zero-{i:03d}@test.example",
            )
            for i in range(5)
        ]
        result = self._run_loader(records, dry_run=True)
        self.assertEqual(0, result["ai_calls"])

    def test_outbound_remains_zero(self):
        records = [
            self._make_record(
                "Possible Shutdown Alert - Test Building",
                "Shutdown alert details.",
                f"out-zero-{i:03d}@test.example",
            )
            for i in range(3)
        ]
        result = self._run_loader(records, dry_run=True)
        self.assertEqual(0, result["outbound_emails"])

    def test_followups_remain_zero(self):
        records = [
            self._make_record(
                "Activities Uploaded (#U001)",
                "Activities have been uploaded.",
                f"fu-zero-{i:03d}@test.example",
            )
            for i in range(3)
        ]
        result = self._run_loader(records, dry_run=True)
        self.assertEqual(0, result["followups_scheduled"])


class TestFinalCleanup(BacklogLoaderTestCase):

    def _make_record(self, subject: str, body: str, msg_id: str) -> dict:
        return {
            "message_id": msg_id,
            "subject": subject,
            "from_addr": "no-reply@solucore.com",
            "to_addrs": ["recipient@example.com"],
            "cc_addrs": [],
            "bcc_addrs": [],
            "reply_to": "",
            "received_at": "2026-01-15T09:00:00",
            "body": body,
        }

    def test_code_yellow_recognized_unsupported(self):
        record = self._make_record(
            "Code Yellow S #37908 (36 York Mills) - York Mills Centre, Toronto",
            "Code Yellow alert for elevator S #37908 at York Mills Centre. Please investigate.",
            "cy-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_code_yellow_bilingual_recognized_unsupported(self):
        record = self._make_record(
            "Code Yellow / Code voiture jaune - SE 2 (C5) #61436 - 40 King St West",
            "Code Yellow alert. Elevator requires attention.",
            "cy-002@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_returned_to_normal_service_recognized_unsupported(self):
        record = self._make_record(
            "40 King St West - Freight(TE1, TE2) Returned to Normal Service",
            "The elevator has returned to normal service. No further action required.",
            "rns-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_returned_to_normal_bilingual_recognized_unsupported(self):
        record = self._make_record(
            "Returned to Normal Service / Remis en service normal) - 300 Consilium Place",
            "Service has been restored. The device is now operational.",
            "rns-002@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_critical_security_alert_safe_rejected(self):
        record = self._make_record(
            "Critical security alert",
            ".awl a {color: #FFFFFF; text-decoration: none;} .abml a {color: #000000;} Someone signed in to your account.",
            "sec-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["rejected"])
        self.assertEqual(0, result["review_candidates"])
        self.assertEqual(0, result["recognized_unsupported_kpi"])

    def test_soluboard_not_communicating_recognized_unsupported(self):
        record = self._make_record(
            "SoluBoard Event: Not Communicating (WECHC_FountainbleauTower)",
            "SoluBoard device is not communicating. Please check connectivity.",
            "sb-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_portfolio_summary_recognized_unsupported(self):
        record = self._make_record(
            "e-volve Portfolio Summary Period: 01/Jun/2019 to 30/Nov/2019",
            "Your portfolio summary for the period 01/Jun/2019 to 30/Nov/2019 is attached.",
            "ps-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_scheduled_preventive_maintenance_recognized_unsupported(self):
        record = self._make_record(
            "Scheduled Preventive Maintenance / Maintenance préventive programmée - Building",
            "Scheduled preventive maintenance is planned for this device.",
            "spm-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_emergency_scheduled_preventive_maintenance_shutdown_recognized_unsupported(self):
        record = self._make_record(
            "*** EMERGENCY *** Scheduled Preventive Maintenance [Shutdown] on 100 Consilium",
            "Emergency scheduled preventive maintenance shutdown is in progress.",
            "espm-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_performance_target_kpi_recognized_unsupported(self):
        record = self._make_record(
            "Performance Target KPI Q3 (AB) / Ojectif de performance ICP trimestre-3",
            "Performance target KPI report for Q3. Please review the attached data.",
            "pt-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_tssa_data_uploaded_recognized_unsupported(self):
        record = self._make_record(
            "TSSA Data Uploaded (#U934C68)",
            "Aravinth Ponnambalam\nNortham Realty Advisors Ltd.\n//storage/file.csv",
            "tssa-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_exception_on_kpi_recognized_unsupported(self):
        record = self._make_record(
            "Exception on KPI_ProcessDaily_Email",
            "An exception occurred during KPI daily processing. Please review logs.",
            "kpiex-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_budgeting_reminder_recognized_unsupported(self):
        record = self._make_record(
            "Budgeting Reminder - Suggested Upgrade Items",
            "This is a reminder about suggested upgrade items for your budget planning.",
            "bud-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_government_report_recognized_unsupported(self):
        record = self._make_record(
            "Government Report for 2 #39844 (2 Bloor West)",
            "Please be advised that the Government Inspection Report has been uploaded.",
            "govrpt-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_completed_government_report_recognized_unsupported(self):
        record = self._make_record(
            "Completed Government Report for 12 #86695 (4701 Tahoe Blvd)",
            "The Government Inspection Report upload has completed successfully.",
            "govrpt-002@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_emergency_entrapment_recognized_unsupported(self):
        record = self._make_record(
            "Emergency: 860 Mercer(Windsor_860Mercer_Car_B)",
            "There is a possible emergency happening.\r\n\r\nBuilding: 860 Mercer\r\nDevice: Windsor_860Mercer_Car_B\r\nDescription: Possible Entrapment.",
            "em-001@test.example",
        )
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_ai_calls_remain_zero(self):
        records = [
            self._make_record(
                f"Code Yellow elevator-{i} - Test Building",
                "Code Yellow alert for elevator.",
                f"cy-zero-{i:03d}@test.example",
            )
            for i in range(5)
        ]
        result = self._run_loader(records, dry_run=True)
        self.assertEqual(0, result["ai_calls"])

    def test_outbound_remains_zero(self):
        records = [
            self._make_record(
                "Returned to Normal Service - Building",
                "Device is back in normal service.",
                f"out-zero-{i:03d}@test.example",
            )
            for i in range(3)
        ]
        result = self._run_loader(records, dry_run=True)
        self.assertEqual(0, result["outbound_emails"])

    def test_followups_remain_zero(self):
        records = [
            self._make_record(
                "SoluBoard Event: Not Communicating (Test Device)",
                "SoluBoard connectivity issue detected.",
                f"fu-zero-{i:03d}@test.example",
            )
            for i in range(3)
        ]
        result = self._run_loader(records, dry_run=True)
        self.assertEqual(0, result["followups_scheduled"])


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
        self.assertIsInstance(backlog_loader.SUPPORTED_CASE_TYPES_SET, frozenset)
        self.assertEqual(frozenset(SUPPORTED_CASE_TYPES), backlog_loader.SUPPORTED_CASE_TYPES_SET)

    def test_match_subject_to_case_type(self):
        self.assertEqual(
            "DATA_ABSENCE",
            backlog_loader._match_subject_to_case_type("Maintenance data has never been submitted"),
        )
        self.assertIsNone(backlog_loader._match_subject_to_case_type("Weekly KPI Digest"))

    def test_cat_subject_matching_does_not_overmatch_longer_tokens(self):
        self.assertEqual(
            "CAT1_COMPLIANCE",
            backlog_loader._match_subject_to_case_type("CAT 1 Tests Reminder"),
        )
        self.assertEqual(
            "CAT5_COMPLIANCE",
            backlog_loader._match_subject_to_case_type("CAT5 Tests Reminder"),
        )
        self.assertIsNone(backlog_loader._match_subject_to_case_type("CAT10 Tests Reminder"))
        self.assertIsNone(backlog_loader._match_subject_to_case_type("CAT50 Tests Reminder"))

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
