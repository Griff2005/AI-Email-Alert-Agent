import json
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import ai_gateway
import agent
import database as db
import observability
from config import config
from constants import CASE_TYPE_DATA_ABSENCE, EVENT_BACKLOG_CASE_CREATED, EVENT_CASE_CREATED
from runtime_options import RuntimeOptions, runtime_options

try:
    from web.app import app as flask_app
except ImportError:  # pragma: no cover - lightweight environments may omit Flask
    flask_app = None


class ObservabilitySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        self.original_demo_mode = config.DEMO_MODE
        self.original_demo_recipient = config.DEMO_RECIPIENT_EMAIL

        config.DATABASE_PATH = Path(self.temp_dir.name) / "agent.db"
        config.DEMO_MODE = True
        config.DEMO_RECIPIENT_EMAIL = "demo-observe@example.com"

        db.close_connection()
        db.init_schema()
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions())

    def tearDown(self):
        ai_gateway.reset_gateway()
        runtime_options.configure(RuntimeOptions())
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        config.DEMO_MODE = self.original_demo_mode
        config.DEMO_RECIPIENT_EMAIL = self.original_demo_recipient
        self.temp_dir.cleanup()

    def _seed_observed_records(self):
        db.insert_email(
            email_id="email-observe-1",
            message_id="email-observe-1@example.test",
            thread_id=None,
            subject="Maintenance Data is not up to date",
            from_addr="alerts@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body="Building: 123 Example Road",
            normalized_text="Building: 123 Example Road",
        )
        db.mark_email_processed("email-observe-1")
        db.insert_case(
            case_id="case-observe-1",
            case_type=CASE_TYPE_DATA_ABSENCE,
            grouping_key="DATA_ABSENCE|123 Example Road",
            building="123 Example Road",
            device=None,
            contractor="Example Elevator Co.",
            due_date=None,
            period=None,
            priority="critical",
        )
        db.insert_case_event(
            event_id="event-observe-1",
            case_id="case-observe-1",
            event_type=EVENT_CASE_CREATED,
            description="Case created from observed email.",
            source_email_id="email-observe-1",
        )
        db.insert_manual_review(
            review_id="review-observe-1",
            case_id="case-observe-1",
            email_id="email-observe-1",
            reason="Missing required extracted fields.",
        )
        db.insert_outbound_message(
            msg_id="msg-observe-draft",
            case_id="case-observe-1",
            intended_to="contractor@example.test",
            intended_cc="",
            actual_to="demo-observe@example.com",
            subject="[DEMO] Follow up",
            body="Demo draft.",
            status="draft",
        )
        db.insert_outbound_message(
            msg_id="msg-observe-violation",
            case_id="case-observe-1",
            intended_to="contractor@example.test",
            intended_cc="",
            actual_to="contractor@example.test",
            subject="[DEMO] Follow up",
            body="This should be counted as unsafe in demo mode.",
            status="sent",
        )
        db.upsert_followup(
            followup_id="follow-observe-1",
            case_id="case-observe-1",
            deadline="2026-01-01T00:00:00",
        )

    def test_metrics_snapshot_includes_operational_safety_and_ai_sections(self):
        self._seed_observed_records()

        snapshot = observability.build_metrics_snapshot()

        self.assertIn("generated_at", snapshot)
        self.assertEqual(1, snapshot["email_pipeline"]["total"])
        self.assertEqual(1, snapshot["email_pipeline"]["processed"])
        self.assertEqual(1, snapshot["cases"]["by_status"]["open"])
        self.assertEqual(1, snapshot["cases"]["by_type"][CASE_TYPE_DATA_ABSENCE])
        self.assertEqual(1, snapshot["manual_reviews"]["open"])
        self.assertEqual(1, snapshot["manual_reviews"]["by_reason"]["Missing required extracted fields."])
        self.assertEqual(1, snapshot["events"]["by_type"][EVENT_CASE_CREATED])
        self.assertEqual(1, snapshot["outbound"]["by_status"]["draft"])
        self.assertEqual(1, snapshot["followups"]["by_status"]["pending"])
        self.assertFalse(snapshot["runtime"]["ai_enabled"])
        self.assertFalse(snapshot["safety"]["smtp_configured"])
        self.assertFalse(snapshot["safety"]["imap_configured"])
        self.assertEqual(1, snapshot["safety"]["outbound_recipient_violations"])
        self.assertEqual(0, snapshot["ai_usage"]["total_ai_calls"])
        self.assertNotIn("records", snapshot["ai_usage"])

    def test_metrics_snapshot_on_empty_initialized_database_is_compact(self):
        snapshot = observability.build_metrics_snapshot()

        self.assertEqual(0, snapshot["email_pipeline"]["total"])
        self.assertEqual({}, snapshot["cases"]["by_status"])
        self.assertEqual(0, snapshot["manual_reviews"]["open"])
        self.assertIn("outbound_recipient_violations", snapshot["safety"])
        self.assertNotIn("records", snapshot["ai_usage"])

    def test_metrics_snapshot_omits_full_ai_records(self):
        gateway = ai_gateway.get_ai_gateway()
        gateway.configure(
            ai_gateway.AiUsageConfig(
                enabled=False,
                max_calls=0,
                budget_mode="manual_review",
                report_path=Path(self.temp_dir.name) / "ai_usage.json",
            )
        )
        gateway.record_skip(
            purpose="classification",
            prompt_type="unit_test",
            caller="tests.test_observability",
            reason="deterministic test skip",
        )

        snapshot = observability.build_metrics_snapshot()

        self.assertEqual(1, snapshot["ai_usage"]["total_ai_calls_skipped"])
        self.assertNotIn("records", snapshot["ai_usage"])

    def test_metrics_snapshot_counts_backlog_event_types(self):
        db.insert_email(
            email_id="email-backlog-observe",
            message_id="email-backlog-observe@example.test",
            thread_id=None,
            subject="CAT1 Tests Reminder",
            from_addr="alerts@example.test",
            to_addr="agent@example.test",
            received_at="2026-05-01T10:00:00",
            raw_body="Building: 123 Example Road",
            normalized_text="Building: 123 Example Road",
        )
        db.mark_email_processed("email-backlog-observe")
        db.insert_case(
            case_id="case-backlog-observe",
            case_type="CAT1_COMPLIANCE",
            grouping_key="CAT1|case-backlog-observe",
            building="123 Example Road",
            device="B-4 #731842",
            contractor="Example Elevator Company",
            due_date=None,
            period=None,
            priority="high",
        )
        db.insert_case_event(
            event_id="event-backlog-observe",
            case_id="case-backlog-observe",
            event_type=EVENT_BACKLOG_CASE_CREATED,
            description="Backlog case created.",
            source_email_id="email-backlog-observe",
        )

        snapshot = observability.build_metrics_snapshot()

        self.assertEqual(1, snapshot["events"]["by_type"][EVENT_BACKLOG_CASE_CREATED])
        self.assertEqual(1, snapshot["email_pipeline"]["created_cases"])

    def test_structured_event_log_writes_json_line(self):
        log_path = Path(self.temp_dir.name) / "observability" / "events.jsonl"

        event = observability.append_structured_event(
            component="unit_test",
            event_name="snapshot_generated",
            log_path=log_path,
            run_id="run-123",
            status="ok",
            email_id="email-observe-1",
            latency_ms=12,
        )

        self.assertEqual("unit_test", event["component"])
        self.assertEqual("snapshot_generated", event["event_name"])
        self.assertEqual("ok", event["status"])
        self.assertEqual("run-123", event["run_id"])
        payload = json.loads(log_path.read_text(encoding="utf-8").strip())
        self.assertEqual(event, payload)

    def test_write_metrics_snapshot_creates_json_report(self):
        self._seed_observed_records()
        report_path = Path(self.temp_dir.name) / "observability" / "snapshot.json"

        written_path = observability.write_metrics_snapshot(report_path)

        self.assertEqual(report_path, written_path)
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(1, payload["dashboard"]["total_cases"])
        self.assertEqual(1, payload["safety"]["outbound_recipient_violations"])

    def test_cli_observability_report_prints_json_snapshot(self):
        args = type("Args", (), {"output": None})()

        stdout = StringIO()
        with redirect_stdout(stdout):
            agent.cmd_observability_report(args)

        output = stdout.getvalue().strip()
        self.assertTrue(output.startswith("{"))
        payload = json.loads(output)
        self.assertIn("email_pipeline", payload)
        self.assertIn("safety", payload)

    def test_cli_observability_report_writes_structured_event_for_output(self):
        output_path = Path(self.temp_dir.name) / "observability" / "snapshot.json"
        log_path = Path(self.temp_dir.name) / "observability" / "events.jsonl"
        original_log_path = config.OBSERVABILITY_LOG_PATH
        config.OBSERVABILITY_LOG_PATH = log_path
        args = type("Args", (), {"output": output_path})()
        try:
            with redirect_stdout(StringIO()):
                agent.cmd_observability_report(args)
        finally:
            config.OBSERVABILITY_LOG_PATH = original_log_path

        self.assertTrue(output_path.exists())
        event = json.loads(log_path.read_text(encoding="utf-8").strip())
        self.assertEqual("observability_report_written", event["event_name"])


@unittest.skipIf(flask_app is None, "Flask is not installed")
class ObservabilityWebTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = config.DATABASE_PATH
        config.DATABASE_PATH = Path(self.temp_dir.name) / "agent.db"
        db.close_connection()
        db.init_schema()
        flask_app.config.update(TESTING=True)
        self.client = flask_app.test_client()

    def tearDown(self):
        db.close_connection()
        config.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def test_observability_json_route_returns_snapshot(self):
        response = self.client.get("/observability.json")

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertIn("generated_at", payload)
        self.assertIn("email_pipeline", payload)
        self.assertIn("safety", payload)


if __name__ == "__main__":
    unittest.main()
