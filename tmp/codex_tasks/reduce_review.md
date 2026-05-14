# Codex Task: Reduce Backlog Review Candidates — Round 2

You are a careful Python developer. Working directory: `/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent`

---

## Absolute constraints

- Do NOT call AI, enable AI, or import ai_gateway
- Do NOT add outbound messages, follow-ups, or auto-closure
- Do NOT run `--enable-ai`, `--live-ai`, `--require-ai`
- Do NOT create cases for unsupported KPI families
- Do NOT expand active case creation beyond the six supported case types
- Do NOT change the database schema
- Preserve all existing tests — they must still pass after your changes
- Run all tests with `.venv/bin/python -m unittest discover -v`

---

## Use `.venv/bin/python`

```bash
ls .venv/bin/python && echo "ok"
```

---

## Background

Latest dry-run on 50,585 emails:
- Accepted KPI: 6,323
- Recognized unsupported KPI: 14,211
- Safe rejected: 391
- Review required: 29,635
- AI calls: 0

Breakdown of the 29,635 review candidates by subject pattern:
- 9,603  "Open Callback(s) Reminder" / "Open Callback Reminder" — NOT caught by existing patterns
- 3,847  DATA_ABSENCE — subject matched, but contractor field fails extraction
- 3,141  GOVERNMENT_DIRECTIVE — subject matched, but device/due_date/building fail extraction
- 3,059  Plain "Callback …" (no "alert" / "open") — NOT caught by any unsupported KPI pattern
- 1,633  "Possible Shutdown Alert" — NOT caught
- 1,104  "Your e-volve account…" / "Your eVolve Login" — not safe-rejected
- 742    "APPS EXCEPTION" / "Exception on ServiceCaller" — NOT caught
- 713    "Activities Uploaded" — NOT caught
- 822    "SoluTrak:" emails (various) — not fully caught
- 504    "*** EMERGENCY - NO CAR RUNNING IN THE BANK ***" — NOT caught
- 332    "Outstanding AHJ Directive" — NOT caught
- 240    "Maintenance Uploaded" — NOT caught
- 205    "New Contact" — not safe-rejected
- 117    "Callbacks Uploaded" — NOT caught
- 79     "Out-of-Service Report" — NOT caught
- ~3,300 Other unrecognized subjects

---

## Change 1 — Expand `_UNSUPPORTED_KPI_PATTERNS` in `src/backlog_loader.py`

The current `_UNSUPPORTED_KPI_PATTERNS` tuple is missing many recognized-but-unsupported families.

Replace the ENTIRE `_UNSUPPORTED_KPI_PATTERNS` constant with the following (order matters — most specific first):

```python
_UNSUPPORTED_KPI_PATTERNS: tuple[tuple[str, str], ...] = (
    # Callback families — most specific first, catch-all last
    ("callback alert", "CALLBACK_ALERT"),
    ("open callback", "OPEN_CALLBACK_REMINDER"),
    ("callback status", "CALLBACK_STATUS"),
    ("callbacks uploaded", "CALLBACKS_UPLOADED"),
    # Service / shutdown families
    ("back in service", "BACK_IN_SERVICE"),
    ("possible shutdown", "DEVICE_SHUTDOWN"),
    ("no car running", "NO_CAR_RUNNING"),
    ("out-of-service report", "DEVICE_OUT_OF_SERVICE"),
    ("entrapment", "ENTRAPMENT_OR_OCCUPIED"),
    ("solutrak event", "SOLUTRAK_EVENT"),
    ("solutrak emergency event", "SOLUTRAK_EVENT"),
    # Report upload families
    ("activities uploaded", "ACTIVITIES_UPLOADED"),
    ("maintenance uploaded", "MAINTENANCE_UPLOADED"),
    ("maintenance report", "MAINTENANCE_REPORT"),
    # Consultant / report families
    ("consultant report", "CONSULTANT_REPORT"),
    # Directive families (unsupported variants)
    ("ahj directive", "AHJ_DIRECTIVE"),
    # System / platform
    ("service alert", "SERVICE_ALERT"),
    ("apps exception", "SYSTEM_NOTIFICATION"),
    ("exception on servicecaller", "SYSTEM_NOTIFICATION"),
    # License / permit
    ("expiring licen", "EXPIRING_LICENSE"),
    ("expiring permit", "EXPIRING_PERMIT"),
    ("license expiry", "LICENSE_EXPIRY"),
    ("licence expiry", "LICENSE_EXPIRY"),
    # KPI metric families
    ("uptime lower than expectation", "UPTIME_LOW"),
    ("mtbc too low", "MTBC_TOO_LOW"),
    ("callback ratio too high", "CALLBACK_RATIO_HIGH"),
    ("callbacks exceed expectation", "CALLBACKS_EXCEED"),
    ("all callbacks exceed", "CALLBACKS_EXCEED"),
    # Generic callback catch-all — MUST be last among callback patterns
    ("callback", "CALLBACK_STATUS"),
)
```

---

## Change 2 — Expand `_NON_KPI_PATTERNS` in `src/backlog_loader.py`

Add safe-rejection patterns for system account notifications and obvious non-KPI subjects. These should be APPENDED to the existing tuple — do NOT remove any existing entries.

Add these entries to `_NON_KPI_PATTERNS` (after the existing entries):

```python
    # e-volve account / login setup notifications
    "e-volve account",
    "evolve account",
    "evolve login",
    "evolve login",
    "your e-volve",
    "your evolve",
    # Contact and CRM noise
    "new contact",
    # SoluTrak account noise (event alerts handled as SOLUTRAK_EVENT above)
    "solutrak: ",
```

---

## Change 3 — Fix DATA_ABSENCE contractor extraction in `src/extractor.py`

### Problem

DATA_ABSENCE emails from the real e-volve system use a two-column HTML table format in the body:

```
 Contractor \r\n Details \r\n \r\n \r\n Schindler Elevator Corporation \r\n Last Activity Date:&nbsp;06-Jun-2019 \r\nElapsed Days:&nbsp;179
```

After stripping HTML entities this becomes (line by line):
```
 Contractor
 Details


 Schindler Elevator Corporation
 Last Activity Date: 06-Jun-2019
Elapsed Days: 179
```

The current `_capture_line(body, "Contractor")` and `_capture_line(body, "Details")` use regex `^label:\s*(.+)$` which requires a colon. Neither "Contractor" nor "Details" has a colon in this format, so extraction returns None and contractor is missing.

Also, subjects have embedded `\r\n` (e.g. `"Data Absence: Maintenance Data is not up to date - 55 Bloor Street,\r\n Toronto"`) which means `subject.rsplit(" - ", 1)[-1].strip()` returns a building string containing `\r\n`. Fix this too.

### Fix A — Add `_extract_data_absence_contractor()` helper

Add this function to `src/extractor.py` (near the other private helpers, before `_extract_case_specific_fields`):

```python
def _extract_data_absence_contractor(body: str) -> Optional[str]:
    """Extract contractor name from e-volve two-column table format.

    Handles the format where Contractor and Details are separate column
    headers, and the contractor name appears on a subsequent line.
    """
    clean = _strip_html_entities(body)
    # Standard colon-based label (backward compat with sample data)
    standard = _capture_line(clean, "Contractor")
    if standard:
        return standard
    # e-volve two-column format: "Details" header line, then contractor name
    m = re.search(
        r'(?:^|\n)\s*Details\s*\r?\n(?:[ \t]*\r?\n)*[ \t]*([A-Za-z][^\r\n:]{4,80})',
        clean,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip() or None
    return None
```

### Fix B — Update `_extract_case_specific_fields` for DATA_ABSENCE

In the `DATA_ABSENCE` branch of `_extract_case_specific_fields`, replace the existing building and contractor extraction logic with:

```python
    if case_type == "DATA_ABSENCE":
        clean_body = _strip_html_entities(body)
        if not fields.get("building") and " - " in subject:
            # Normalize embedded newlines in subject before splitting
            norm_subject = re.sub(r"\s+", " ", subject)
            extracted["building"] = norm_subject.rsplit(" - ", 1)[-1].strip()
        if not fields.get("contractor"):
            extracted["contractor"] = _extract_data_absence_contractor(body)
        for field_name, labels in _LABELS.items():
            if field_name in {"building", "contractor"}:
                continue  # already handled above
            if fields.get(field_name) or extracted.get(field_name):
                continue
            for label in labels:
                value = _capture_line(clean_body, label)
                if not value:
                    continue
                normalized = _strip_html_entities(value).strip()
                if field_name in {"due_date", "scheduled_date", "last_activity_date"}:
                    normalized = _normalize_date(normalized)
                if field_name == "elapsed_days":
                    normalized = _capture_digits(normalized) or normalized
                extracted[field_name] = normalized
                break
        if not fields.get("description") and not extracted.get("description"):
            extracted["description"] = "Maintenance data has never been submitted"
        return extracted
```

---

## Change 4 — Fix GOVERNMENT_DIRECTIVE extraction in `src/extractor.py`

### Problem

Real e-volve GOVERNMENT_DIRECTIVE emails use this body format (space-separated, all on one line):

```
ThyssenKrupp Elevator (Canada) Limited Device DueDate Description 3 #60599 02-Apr-2020 8.6.1 (CAD)\r\n
```

The current `_capture_directive_row` looks for pipe-separated values (`"|" in line`). This never matches the real format, so device, due_date, and description are all missing. Building is also not extracted from the subject.

Subject format: `"Outstanding Government Directive - 45 St. Clair Avenue, Toronto"`
After final " - ": building = "45 St. Clair Avenue, Toronto"

### Fix A — Update `_capture_directive_row` in `src/extractor.py`

Replace the existing `_capture_directive_row` function entirely:

```python
def _capture_directive_row(body: str) -> Optional[Dict[str, str]]:
    """Extract device, due_date, description from a government directive email body.

    Supports two formats:
    1. Pipe-separated: "device / report_date | due_date | description"
    2. e-volve space-separated: "... Device DueDate Description <device> <date> <desc>"
    """
    # Format 1: pipe-separated (original sample data)
    for line in body.splitlines():
        if "|" not in line or "/" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3 or " / " not in parts[0]:
            continue
        device, _report_date = [item.strip() for item in parts[0].split(" / ", 1)]
        return {
            "device": device,
            "due_date": parts[1],
            "description": parts[2],
        }

    # Format 2: e-volve space-separated inline table
    # Header: "Device DueDate Description" or "Device/Report Date DueDate Description"
    # Data row immediately follows on same physical line:
    # "<device tokens> <DD-Mon-YYYY> <description>"
    m = re.search(
        r"Device(?:/Report\s+Date)?\s+DueDate\s+Description\s+"
        r"([A-Za-z0-9#()\s/\-]{1,40}?)\s+"
        r"(\d{2}-[A-Za-z]+-\d{4})\s+"
        r"(.+?)(?:\r?\n|$)",
        body,
        re.DOTALL,
    )
    if m:
        return {
            "device": m.group(1).strip(),
            "due_date": m.group(2).strip(),
            "description": m.group(3).strip(),
        }
    return None
```

### Fix B — Add building extraction to GOVERNMENT_DIRECTIVE in `_extract_case_specific_fields`

In the `GOVERNMENT_DIRECTIVE` branch, add building-from-subject extraction BEFORE the directive row call:

```python
    if case_type == "GOVERNMENT_DIRECTIVE":
        if not fields.get("building") and " - " in subject:
            norm_subject = re.sub(r"\s+", " ", subject)
            extracted["building"] = norm_subject.rsplit(" - ", 1)[-1].strip()
        directive = _capture_directive_row(body)
        if directive:
            extracted["device"] = directive["device"]
            extracted["due_date"] = _normalize_date(directive["due_date"])
            extracted["description"] = directive["description"]
            extracted["directive_tasks"] = directive["description"]
        return extracted
```

---

## Change 5 — Improve reporting in `src/backlog_loader.py`

### 5a — Add `top_unknown_subject_patterns` to summary and report.json

Add a helper function near `_top_review_reasons`:

```python
def _top_unknown_subject_patterns(review_items: List[Dict[str, Any]], n: int = 20) -> List[Dict[str, Any]]:
    """Return top N subject prefixes from records with UNKNOWN classification."""
    unknown_items = [
        item for item in review_items
        if item.get("case_type", "UNKNOWN") == "UNKNOWN"
    ]
    counter: Counter = Counter()
    for item in unknown_items:
        subj = re.sub(r"\s+", " ", str(item.get("subject", ""))).strip()
        prefix = subj[:70] if subj else "(empty)"
        counter[prefix] += 1
    return [{"subject_prefix": subj, "count": cnt} for subj, cnt in counter.most_common(n)]
```

In `load_backlog()`, add to the `summary` dict:

```python
"top_unknown_subject_patterns": _top_unknown_subject_patterns(review_items),
```

Add to `_report_json_payload()`:

```python
"top_unknown_subject_patterns": summary.get("top_unknown_subject_patterns", []),
```

### 5b — Add `top_supported_extraction_failures` to summary and report.json

Add a helper function:

```python
def _top_supported_extraction_failures(review_items: List[Dict[str, Any]], n: int = 15) -> List[Dict[str, Any]]:
    """Return top N extraction failure reasons from review items that matched a supported KPI."""
    supported_fails = [
        item for item in review_items
        if item.get("case_type", "UNKNOWN") != "UNKNOWN"
    ]
    counter: Counter = Counter()
    for item in supported_fails:
        reason = str(item.get("reason", ""))
        counter[reason] += 1
    return [{"reason": r, "count": c} for r, c in counter.most_common(n)]
```

In `load_backlog()`, add to the `summary` dict:

```python
"top_supported_extraction_failures": _top_supported_extraction_failures(review_items),
```

Add to `_report_json_payload()`:

```python
"top_supported_extraction_failures": summary.get("top_supported_extraction_failures", []),
```

### 5c — Add `Top Unknown Subject Patterns` section to `report.md`

In `_build_markdown_report`, add a new section after the "Top Review Reasons" section:

```python
    if summary.get("top_unknown_subject_patterns"):
        lines += [
            "",
            "## Top Unknown Subject Patterns",
            "",
            "| Count | Subject Prefix |",
            "|------:|----------------|",
        ]
        for entry in summary["top_unknown_subject_patterns"][:15]:
            lines.append(f"| {entry['count']} | `{entry['subject_prefix']}` |")
```

---

## Change 6 — Add tests in `tests/test_backlog_loader.py`

Add a new test class `TestReduceReview` after the existing `TestTriageBuckets` class. All tests use the existing `BacklogLoaderTestCase` base class and `_make_kpi_email` helper.

```python
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
```

---

## Verification steps (run in this order)

```bash
# 1. Compile check
.venv/bin/python -m compileall src

# 2. Full test suite — must still pass all existing + new tests
.venv/bin/python -m unittest discover -v

# 3. Dry-run on the real backlog
.venv/bin/python src/agent.py load-backlog --source json --path data/my_backlog.json --dry-run
```

Target after all changes:
- `recognized_unsupported_kpi` should increase significantly (open_callback ~9,603, plain callback ~3,059, possible shutdown ~1,633, etc.)
- `safe_rejected_non_kpi` should increase (evolve account ~1,104, new contact ~205)
- `accepted_kpi` should increase (data absence ~3,847, govt directive ~3,141)
- `review_required` should drop substantially (target: under 5,000)
- `ai_calls` must remain 0

---

## Report format

```
Change N: [DONE | SKIPPED | FAILED]
File: <filename>
Summary: <1-2 sentences>

Tests: [PASS | FAIL]
Ran: N tests, N failures

FINAL: [PASS | FAIL]
```
