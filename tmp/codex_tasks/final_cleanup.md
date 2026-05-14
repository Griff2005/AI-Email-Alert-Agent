# Codex Task: Final Deterministic Cleanup — Reduce Review Round 3

You are a careful Python developer. Working directory: `/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent`

---

## Absolute constraints

- Do NOT call AI, enable AI, or import ai_gateway
- Do NOT add outbound messages, follow-ups, or auto-closure
- Do NOT create cases for unsupported KPI families
- Do NOT expand active case creation beyond the six supported case types
- Do NOT change the database schema
- Preserve all existing tests — they must all still pass
- Run tests with `.venv/bin/python -m unittest discover -v`

---

## Use `.venv/bin/python`

```bash
ls .venv/bin/python && echo "ok"
```

---

## Background

Latest dry-run result (50,585 emails):
- Accepted KPI: 13,301
- Recognized unsupported KPI: 31,938
- Safe rejected: 1,700
- Review required: 3,621
- AI calls: 0

Review breakdown:
- 3,544 UNKNOWN (no supported KPI match)
- 67 MAINTENANCE_HOURS_SHORTFALL missing: building, contractor, period, hours_required, hours_actual
- 10 weak body signature

Top unknown subject patterns (from report.json):
- 508  "Code Yellow / Code voiture jaune"
- 375  "e-volve Portfolio Summary Period: ..."
- 95   "Scheduled Preventive Maintenance / Maintenance préventive programmée"
- 71   "Returned to Normal Service / Remis en service normal)"
- 64   "SoluBoard Event: Not Communicating"
- 57   "Performance Target KPI Q3/Q4"
- 27   "*** EMERGENCY *** Scheduled Preventive Maintenance [Shutdown]"
- 28   "Emergency: BuildingName(DeviceID)"
- 25   "Exception on KPI_ProcessDaily"
- 23   "Critical security alert"
- 15   "Budgeting Reminder - Suggested Upgrade Items"
- 78   "TSSA Data Uploaded (#U...)"

---

## Change 1 — Add new entries to `_UNSUPPORTED_KPI_PATTERNS` in `src/backlog_loader.py`

Insert the following entries into `_UNSUPPORTED_KPI_PATTERNS`. Order matters — more specific patterns must come before broader ones.

Add BEFORE the existing `("callback", "CALLBACK_STATUS")` catch-all line:

```python
    # Service restoration
    ("returned to normal service", "RETURNED_TO_NORMAL_SERVICE"),
    # Preventive maintenance — shutdown variant must precede the plain variant
    ("preventive maintenance [shutdown]", "SCHEDULED_PREVENTIVE_MAINTENANCE_SHUTDOWN"),
    ("preventive maintenance shutdown", "SCHEDULED_PREVENTIVE_MAINTENANCE_SHUTDOWN"),
    ("scheduled preventive maintenance", "SCHEDULED_PREVENTIVE_MAINTENANCE"),
    # Emergency entrapment events (non-SoluTrak "Emergency: Building(Device)" format)
    ("emergency:", "ENTRAPMENT_OR_OCCUPIED"),
    # Code Yellow elevator alerts
    ("code yellow", "CODE_YELLOW"),
    # SoluBoard connectivity alerts
    ("soluboard event", "SOLUBOARD_NOT_COMMUNICATING"),
    # Portfolio and performance reporting
    ("portfolio summary", "PORTFOLIO_SUMMARY"),
    ("performance target kpi", "PERFORMANCE_TARGET_KPI"),
    # Data uploads
    ("tssa data uploaded", "TSSA_DATA_UPLOADED"),
    # Budgeting
    ("budgeting reminder", "BUDGETING_REMINDER"),
    # System / platform
    ("exception on kpi", "SYSTEM_NOTIFICATION"),
```

---

## Change 2 — Add `"critical security alert"` to `_NON_KPI_PATTERNS` in `src/backlog_loader.py`

"Critical security alert" emails have bodies consisting entirely of CSS/HTML from Google/email security notification systems. They are not KPI alerts.

Append to `_NON_KPI_PATTERNS`:

```python
    # Security notification system emails (not elevator KPI alerts)
    "critical security alert",
    "security alert",
```

---

## Change 3 — Verify MAINTENANCE_HOURS_SHORTFALL failures (investigate only; fix only if pattern is clear)

There are 67 MAINTENANCE_HOURS_SHORTFALL review candidates failing with all of:
`building, contractor, period, hours_required, hours_actual` missing.

The real e-volve body format for these is (after HTML entity stripping):

```
 Contractor
 Details

 ThyssenKrupp Elevator (Canada) Limited
   Device: 1 #64549880
Contract Hours: 6.00
Actual Hours: 0.00
```

The subject format is: `"Maintenance Hours Less Than Required - 2 Anndale Drive, Toronto"`

Note: `period` is NOT present in these emails. The required fields for MAINTENANCE_HOURS_SHORTFALL include `period`, so even if building/contractor/hours are extracted, `period` will still be missing and these records will remain in review. Do NOT remove `period` from required fields — it is needed for deduplication in the main pipeline.

**Action:** Do NOT change the extractor for MAINTENANCE_HOURS_SHORTFALL. These 67 records correctly remain in review because `period` is genuinely absent. Confirm this by reading `_REQUIRED_FIELDS` in `src/extractor.py` — do not modify it.

**No code change needed for Change 3.**

---

## Change 4 — Add tests in `tests/test_backlog_loader.py`

Add a new test class `TestFinalCleanup` after the existing `TestReduceReview` class.

```python
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
```

---

## Verification steps (run in this order)

```bash
# 1. Compile check
.venv/bin/python -m compileall src

# 2. Full test suite — all existing + new tests must pass
.venv/bin/python -m unittest discover -v

# 3. Dry-run on the real backlog
.venv/bin/python src/agent.py load-backlog --source json --path data/my_backlog.json --dry-run
```

Target after changes:
- `review_required` should drop from 3,621 to under 2,300
- `recognized_unsupported_kpi` should increase by ~1,300+
- `rejected` should increase by ~23 (critical security alerts)
- `ai_calls` must remain 0
- `outbound_emails` must remain 0
- `followups_scheduled` must remain 0

---

## Report format

```
Change N: [DONE | SKIPPED | FAILED]
File: <filename>
Summary: <1-2 sentences>

Tests: [PASS | FAIL]
Ran: N tests, N failures

Dry-run result: <accepted> accepted, <unsupported> unsupported, <rejected> rejected, <review> review

FINAL: [PASS | FAIL]
```
