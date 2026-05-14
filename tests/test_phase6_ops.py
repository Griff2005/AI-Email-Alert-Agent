import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import agent
import ai_gateway
import backlog_loader
import database as db
from config import config
from runtime_options import RuntimeOptions, runtime_options


def _make_kpi_email(case_type="DATA_ABSENCE", idx=1):
    subjects = {
        "CAT1_COMPLIANCE": "CAT1 Tests Reminder",
        "DATA_ABSENCE": "Maintenance data has never been submitted",
        "MAINTENANCE_HOURS_SHORTFALL": "Maintenance Hours Less Than Required",
    }
    bodies = {
        "CAT1_COMPLIANCE": (
            f"Client: Test Client {idx}\n"
            f"Building: Test Building {idx}\n"
            f"Device: B-1 #70000{idx}\n"
            "Contractor: Test Co\n"
            "CAT1 Tests Reminder: CAT1 compliance tests are due."
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
    }
    return {
        "message_id": f"phase6-{case_type.lower()}-{idx}@test.example",
        "subject": subjects[case_type],
        "from_addr": "kpi-alerts@test.example",
        "to_addrs": [f"client{idx}@test.example"],
        "cc_addrs": [],
        "bcc_addrs": [],
        "reply_to": "kpi-alerts@test.example",
        "received_at": f"2026-01-{10 + idx:02d}T09:00:00",
        "body": bodies[case_type],
    }


class Phase6OpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "phase6_test_agent.db"
        self.report_dir = self.tmp_dir / "reports"
        self.source_path = self.tmp_dir / "backlog.json"

        self._original_db_path = config.DATABASE_PATH
        self._original_cache_path = config.CLAUDE_CACHE_PATH
        self._original_ai_report_path = config.AI_REPORT_PATH
        self._original_observability_path = config.OBSERVABILITY_LOG_PATH
        self._original_demo_mode = config.DEMO_MODE
        self._original_demo_recipient = config.DEMO_RECIPIENT_EMAIL

        config.DATABASE_PATH = self.db_path
        config.CLAUDE_CACHE_PATH = self.tmp_dir / "claude_cache.json"
        config.AI_REPORT_PATH = self.tmp_dir / "ai_usage.json"
        config.OBSERVABILITY_LOG_PATH = self.tmp_dir / "events.jsonl"
        config.DEMO_MODE = True
        config.DEMO_RECIPIENT_EMAIL = "demo-recipient@example.test"

        db.close_connection()
        db.init_schema()
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions())

    def tearDown(self):
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions())
        db.close_connection()
        config.DATABASE_PATH = self._original_db_path
        config.CLAUDE_CACHE_PATH = self._original_cache_path
        config.AI_REPORT_PATH = self._original_ai_report_path
        config.OBSERVABILITY_LOG_PATH = self._original_observability_path
        config.DEMO_MODE = self._original_demo_mode
        config.DEMO_RECIPIENT_EMAIL = self._original_demo_recipient
        shutil.rmtree(self.tmp_dir)

    def _write_source(self, records):
        self.source_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        return self.source_path

    def _run_loader(self, records, *, dry_run=True, report_dir=None, **kwargs):
        self._write_source(records)
        return backlog_loader.load_backlog(
            source="json",
            path=self.source_path,
            dry_run=dry_run,
            report_dir=report_dir or self.report_dir,
            **kwargs,
        )

    def _count_rows(self, table_name):
        row = db.get_connection().execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
        return int(row["count"])

    def test_progress_output_printed(self):
        records = [_make_kpi_email("DATA_ABSENCE", idx) for idx in range(1, 6)]

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self._run_loader(records, dry_run=True, progress_interval=2)

        output = stdout.getvalue()
        self.assertIn("[BACKLOG] Progress: 2/5 processed", output)
        self.assertIn("[BACKLOG] Progress: 5/5 processed", output)

    def test_resume_skips_duplicates(self):
        records = [_make_kpi_email("DATA_ABSENCE", idx) for idx in range(1, 3)]

        first = self._run_loader(records, dry_run=False)
        second = self._run_loader(records, dry_run=False)

        self.assertEqual(2, first["imported_supported_kpi_emails"])
        self.assertEqual(0, second["imported_supported_kpi_emails"])
        self.assertEqual(2, second["duplicate_input_emails"])

    def test_report_detail_full_is_larger(self):
        records = [
            _make_kpi_email("DATA_ABSENCE", 1),
            _make_kpi_email("CAT1_COMPLIANCE", 2),
            {
                **_make_kpi_email("DATA_ABSENCE", 3),
                "message_id": "phase6-review@test.example",
                "subject": "Weekly KPI Digest",
            },
        ]
        summary_dir = self.tmp_dir / "summary_report"
        full_dir = self.tmp_dir / "full_report"

        self._run_loader(records, dry_run=True, report_dir=summary_dir, report_detail="summary")
        self._run_loader(records, dry_run=True, report_dir=full_dir, report_detail="full")

        summary_report = json.loads((summary_dir / "report.json").read_text(encoding="utf-8"))
        full_report = json.loads((full_dir / "report.json").read_text(encoding="utf-8"))
        self.assertLess(len(summary_report["detail_items"]), len(full_report["detail_items"]))

    def test_reset_demo_db_refuses_without_yes(self):
        args = SimpleNamespace(yes=False, database=self.tmp_dir / "demo_reset.db")

        with self.assertRaises(SystemExit) as raised:
            agent.cmd_reset_demo_db(args)

        self.assertEqual(1, raised.exception.code)

    def test_reset_demo_db_refuses_unsafe_path(self):
        args = SimpleNamespace(yes=True, database=PROJECT_ROOT / "data" / "agent.db")

        with self.assertRaises(SystemExit) as raised:
            agent.cmd_reset_demo_db(args)

        self.assertEqual(1, raised.exception.code)

    def test_build_demo_scenario_is_deterministic(self):
        args = SimpleNamespace()

        with contextlib.redirect_stdout(io.StringIO()):
            agent.cmd_build_demo_scenario(args)
            first_case_ids = sorted(row["case_id"] for row in db.get_all_cases())
            agent.cmd_build_demo_scenario(args)
            second_case_ids = sorted(row["case_id"] for row in db.get_all_cases())

        self.assertEqual(first_case_ids, second_case_ids)
        self.assertEqual(6, self._count_rows("building_issue_groups"))
        self.assertEqual(6, self._count_rows("building_group_emails"))
        self.assertEqual(1, self._count_rows("connection_hypotheses"))
        self.assertEqual(2, self._count_rows("pattern_flags"))

    def test_safety_check_passes(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            result = agent.cmd_safety_check(SimpleNamespace())

        self.assertEqual(0, result)
        self.assertIn("[SAFETY] 5/5 checks passed", stdout.getvalue())

    def test_replay_routes_ambiguous_extraction_to_review_without_ai(self):
        replay_path = self.tmp_dir / "demo_replay.json"
        replay_path.write_text(
            json.dumps(
                [
                    {
                        "id": "replay-ambiguous-1",
                        "subject": "Maintenance Data is not up to date",
                        "from": "alerts@example.test",
                        "to": "ops@example.test",
                        "date": "2026-05-14T09:00:00",
                        "body": "Maintenance data is not up to date, but this replay email omits required fields.",
                    }
                ],
                indent=2,
            ),
            encoding="utf-8",
        )

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = agent.cmd_replay(SimpleNamespace(path=replay_path))

        self.assertEqual(1, result["review_flagged"])
        self.assertIn("review_flagged", stdout.getvalue())

    def test_build_demo_scenario_refuses_when_demo_mode_false(self):
        config.DEMO_MODE = False
        with self.assertRaises(SystemExit) as raised:
            agent.cmd_build_demo_scenario(SimpleNamespace())
        self.assertEqual(1, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
