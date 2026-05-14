# Codex Task: Phase 3 — Tests

You are implementing Phase 3 of Backlog Loading Mode for the Email Alert Triage Agent.

**FIRST: Read `Backlog Loading Mode Design Spec.md`** — it is the source of truth.

Phases 1 and 2 have already implemented `src/backlog_loader.py` and `data/backlog_sample.json`.

Your working directory is the project root.

---

## What you must build

### File: `tests/test_backlog_loader.py`

A focused test module covering the core backlog loader behavior.

#### Test requirements

**Test class: `TestDryRunMode`**
- `test_dry_run_does_not_modify_db`: Run load_backlog in dry_run mode on a temp JSON file with valid KPI emails. Assert that the database has no new rows in emails, cases, or case_events tables after the run. Use a temporary in-memory or temp-file SQLite DB.
- `test_dry_run_report_written`: After dry_run, assert that a report directory was created and report.json and report.md exist.
- `test_dry_run_report_has_dry_run_note`: Assert report.json contains `"mode": "dry_run"`.

**Test class: `TestCommitMode`**
- `test_commit_imports_accepted_kpi_emails`: Run load_backlog in commit mode with at least one email for each supported case type. Assert emails table has the expected rows, cases table has expected rows, case_events has backlog events.
- `test_commit_creates_cases`: Assert that at least one new case exists in DB after commit for a valid KPI email.
- `test_commit_no_outbound_messages`: After commit, assert outbound_messages table is empty.
- `test_commit_no_followups`: After commit, assert followups table is empty.
- `test_commit_no_closed_cases`: After commit, assert no cases have status="closed".

**Test class: `TestFiltering`**
- `test_non_kpi_rejected`: Pass obviously non-KPI records (out of office, invoice). Assert they appear in rejected list and are not imported.
- `test_unsupported_kpi_not_forced_into_case`: Pass an email with "compliance" in subject but no matching KPI body pattern. Assert it does NOT create a case. It should appear in review_candidates.
- `test_empty_subject_rejected`: Pass a record with empty subject. Assert it is rejected.
- `test_empty_body_rejected`: Pass a record with empty body. Assert it is rejected.

**Test class: `TestDeduplication`**
- `test_duplicate_input_in_json`: Include two records with the same message_id in the input JSON. Assert only one is imported, and duplicate count is 1.
- `test_duplicate_db_email_skipped`: Commit a record, then commit the same file again. Assert the second run sees the email as already present (duplicate count >= 1) and does not create a second row.
- `test_duplicate_case_grouping`: Include two emails for the same building/device/case_type. Assert they share the same case (one create + one update, not two new cases).

**Test class: `TestRecipientSummary`**
- `test_recipient_summary_structure`: After a run, assert recipient_summary.json has the required fields: unique_recipients, missing_recipient_count, by_domain, top_to_recipients, top_cc_recipients.
- `test_missing_recipients_counted`: Pass records with no to_addrs. Assert missing_recipient_count > 0.

**Test class: `TestReports`**
- `test_all_report_files_written`: After commit run, assert report.json, report.md, rejected.json, review_candidates.json, and recipient_summary.json all exist.
- `test_ai_calls_zero_in_report`: Assert report.json `ai_calls` == 0.
- `test_outbound_zero_in_report`: Assert report.json `outbound_emails` == 0.
- `test_followups_zero_in_report`: Assert report.json `followups_scheduled` == 0.

---

## Setup pattern (important)

Use a temporary database path so tests do not touch `data/agent.db`:

```python
import tempfile
import os
from pathlib import Path
from unittest import TestCase

class TestBacklogLoader(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.tmp_dir) / "test_agent.db"
        # Patch config.DATABASE_PATH to use temp db
        from config import config
        self._original_db_path = config.DATABASE_PATH
        config.DATABASE_PATH = self.db_path
        # Also reset thread-local db connection so it picks up new path
        import database as db
        db.close_connection()
        db.init_schema()

    def tearDown(self):
        from config import config
        config.DATABASE_PATH = self._original_db_path
        import database as db
        db.close_connection()
```

Also use a temporary report dir for each test:
```python
self.report_dir = Path(self.tmp_dir) / "reports"
```

---

## Sample data helpers

Create a `_make_kpi_email(case_type, idx=1) -> dict` helper in the test file that returns a minimal valid email record for each case type:

```python
def _make_kpi_email(case_type, idx=1):
    bodies = {
        "CAT1_COMPLIANCE": f"Client: Test Client {idx}\nBuilding: Test Building {idx}\nDevice: B-1 #70000{idx}\nContractor: Test Co\nCAT1 Tests Reminder: CAT1 compliance tests are due.",
        "CAT5_COMPLIANCE": f"Client: Test Client {idx}\nBuilding: Test Building {idx}\nDevice: B-2 #70000{idx}\nContractor: Test Co\nCAT5 Tests Reminder: CAT5 compliance tests are due.",
        "DATA_ABSENCE": f"Client: Test Client {idx}\nBuilding: Test Building {idx}\nContractor: Test Co\nData Absence Alert: Maintenance data has never been submitted. Elapsed: 30 days.",
        "MAINTENANCE_HOURS_SHORTFALL": f"Client: Test Client {idx}\nBuilding: Test Building {idx}\nContractor: Test Co\nReporting Period: Jan 2026\nContract Hours: 40\nActual Hours: 20\nMaintenance hours less than required.",
        "MAJOR_WORK_OVERDUE": f"Client: Test Client {idx}\nBuilding: Test Building {idx}\nDevice: B-1 #70000{idx}\nContractor: Test Co\nScheduledDate: 2026-01-10\nScheduled work is overdue.",
        "GOVERNMENT_DIRECTIVE": f"Client: Test Client {idx}\nBuilding: Test Building {idx}\nDevice: B-1 #70000{idx}\nContractor: Test Co\nDueDate: 2026-03-01\nOutstanding Government Directive: Action required.",
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
        "from_addr": f"kpi-alerts@test.example",
        "to_addrs": [f"client{idx}@test.example"],
        "cc_addrs": [],
        "bcc_addrs": [],
        "reply_to": "kpi-alerts@test.example",
        "received_at": f"2026-01-{10+idx:02d}T09:00:00",
        "body": bodies[case_type],
    }
```

---

## Running tests

Use `.venv/bin/python` if it exists, otherwise `python3`.

Run after writing:
```bash
.venv/bin/python -m unittest tests.test_backlog_loader -v
```
(or `python3 -m unittest tests.test_backlog_loader -v` if .venv unavailable)

Report the result.

---

## Hard constraints

- Tests must use temporary DB paths, never `data/agent.db`
- No real SMTP, no real IMAP, no AI calls in tests
- Tests must be fast and deterministic
- Do NOT import from `case_manager`, `email_sender`, `email_reader`, or `followup`

---

## At the end, report

- Files modified (list them)
- Tests added (list them by class/method)
- Commands run and pass/fail result
- Any issues found with Phase 1/2 implementation that needed fixing (report only, do not fix unless trivial)
