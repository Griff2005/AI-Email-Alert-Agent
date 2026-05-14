# Codex Task: Backlog Triage Buckets

Improve deterministic triage in `src/backlog_loader.py` and `src/extractor.py`.
The goal is to eliminate the massive "review" bucket by properly classifying
recognized emails. Do not enable AI, outbound, follow-ups, or new case types.

**Working directory:** `/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent`
**Python:** `.venv/bin/python`

---

## Constraints

- Zero AI calls
- No outbound messages
- No follow-ups
- No new case types beyond the existing six
- No schema changes
- All 60 existing tests must still pass
- Run with `.venv/bin/python` only

---

## Change 1: New triage actions in `src/backlog_loader.py`

The current `action` values are: `accepted`, `review`, `rejected`, `duplicate`.

Add a fifth: `recognized_unsupported_kpi`.

Used when: the subject matches a known-but-unsupported KPI family.
No case is created. No outbound. No manual_review DB row. Written to
`data/backlog_runs/<timestamp>/unsupported_kpis.json` only.

---

## Change 2: Unsupported KPI detection in `src/backlog_loader.py`

Add this constant near `_BACKLOG_SUBJECT_PATTERNS`:

```python
_UNSUPPORTED_KPI_PATTERNS: tuple[tuple[str, str], ...] = (
    ("callback alert", "CALLBACK_ALERT"),
    ("back in service", "BACK_IN_SERVICE"),
    ("service alert", "SERVICE_ALERT"),
    ("callback status", "CALLBACK_STATUS"),
    ("consultant report", "CONSULTANT_REPORT"),
    ("expiring licen", "EXPIRING_LICENSE"),   # matches licence and license
    ("expiring permit", "EXPIRING_PERMIT"),
    ("uptime lower than expectation", "UPTIME_LOW"),
    ("mtbc too low", "MTBC_TOO_LOW"),
    ("callback ratio too high", "CALLBACK_RATIO_HIGH"),
    ("callbacks exceed expectation", "CALLBACKS_EXCEED"),
    ("all callbacks exceed", "CALLBACKS_EXCEED"),
)
```

Add this function:

```python
def _match_subject_to_unsupported_kpi(subject: str) -> Optional[str]:
    """Return recognized-but-unsupported KPI family name, or None."""
    lowered = subject.lower()
    for pattern, family in _UNSUPPORTED_KPI_PATTERNS:
        if pattern in lowered:
            return family
    return None
```

Update `_classify_for_backlog` so that when `subject_case_type is None`,
before returning UNKNOWN it first checks `_match_subject_to_unsupported_kpi`.
If a match is found, return:

```python
{
    "source": "backlog_unsupported_kpi",
    "case_type": "UNKNOWN",
    "unsupported_family": family,
    "reason": f"Recognized unsupported KPI family: {family}.",
}
```

If no match at all, return the existing UNKNOWN result.

Update `_process_record`: after `classification = _classify_for_backlog(normalized)`,
if `classification.get("unsupported_family")`, return:

```python
_decorate_for_report(
    normalized,
    {
        "action": "recognized_unsupported_kpi",
        "unsupported_family": classification["unsupported_family"],
        "reason": classification["reason"],
        "classification": classification,
    },
)
```

---

## Change 3: Wire recognized_unsupported_kpi into load_backlog counters

In `load_backlog`, add:

```python
unsupported_kpi_items: List[Dict[str, Any]] = []
```

In the result dispatch loop, add:

```python
elif result["action"] == "recognized_unsupported_kpi":
    unsupported_kpi_items.append(result)
```

Add to summary dict:
```python
"recognized_unsupported_kpi": len(unsupported_kpi_items),
"unsupported_kpi_items": unsupported_kpi_items,
"unsupported_kpi_counts_by_family": _count_by_family(unsupported_kpi_items),
```

Add helper:
```python
def _count_by_family(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        family = item.get("unsupported_family", "UNKNOWN")
        counter[family] += 1
    return dict(counter.most_common())
```

---

## Change 4: Write `unsupported_kpis.json` report

In `_report_paths`, add:
```python
"unsupported_kpis_json": str(report_dir / "unsupported_kpis.json"),
```

In `_write_reports`, add:
```python
Path(report_paths["unsupported_kpis_json"]).write_text(
    json.dumps(_unsupported_kpi_report_items(results["unsupported_kpi_items"]), indent=2, sort_keys=True),
    encoding="utf-8",
)
```

Add:
```python
def _unsupported_kpi_report_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "subject": item.get("subject"),
            "from_addr": item.get("from_addr"),
            "received_at": item.get("received_at"),
            "unsupported_family": item.get("unsupported_family"),
            "reason": item.get("reason"),
            "body_preview": item.get("body_preview", ""),
        }
        for item in items
    ]
```

---

## Change 5: Update report.json payload

In `_report_json_payload`, add these fields alongside the existing ones:

```python
"recognized_unsupported_kpi": summary["recognized_unsupported_kpi"],
"unsupported_kpi_counts_by_family": summary["unsupported_kpi_counts_by_family"],
"top_review_reasons": _top_review_reasons(summary.get("review_items", [])),
```

Add helper:
```python
def _top_review_reasons(items: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    counter: Counter[str] = Counter()
    for item in items:
        reason = item.get("reason", "unknown")
        counter[reason] += 1
    return [{"reason": r, "count": c} for r, c in counter.most_common(limit)]
```

---

## Change 6: Update report.md

In `_build_markdown_report`, update the Counts table to include the new bucket:

```
| Emails scanned             | N |
| Accepted (supported KPI)   | N |
| Recognized unsupported KPI | N |
| Safe rejected (non-KPI)    | N |
| Review required            | N |
| Duplicate inputs           | N |
```

Add a section after Counts:

```markdown
## Unsupported KPI Families

| Family | Count |
| --- | ---: |
| CALLBACK_ALERT | N |
...
```

Add a section:

```markdown
## Top Review Reasons

- `<reason>` — N
```

Add `unsupported_kpis.json` to the Files section.

---

## Change 7: Update stdout print

In `_print_summary`, update the printed lines to include the new bucket.
Replace the current 3-line count block with:

```
[BACKLOG]   Emails scanned:             N
[BACKLOG]   Accepted (supported KPI):   N
[BACKLOG]   Recognized unsupported KPI: N
[BACKLOG]   Safe rejected (non-KPI):    N
[BACKLOG]   Review required:            N
[BACKLOG]   Duplicate inputs:           N
```

---

## Change 8: Expand safe non-KPI rejection in `_NON_KPI_PATTERNS`

Add these patterns to `_NON_KPI_PATTERNS`:

```python
"undeliverable",
"delivery status notification",
"delivery failure",
"mail delivery",
"postmaster",
"auto-reply",
"auto reply",
"out of office",
"automatic reply",
"ndr:",
"system notification",
"password reset",
"login notification",
"account notification",
"subscription",
"unsubscribe",
```

---

## Change 9: Add DATA_ABSENCE subject pattern in `_BACKLOG_SUBJECT_PATTERNS`

Add at the top of the tuple (more specific patterns first):

```python
("data absence:", "DATA_ABSENCE"),
```

This matches subjects like:
"Data Absence: Maintenance Data is not up to date - 55 Bloor Street, Toronto"

---

## Change 10: Fix DATA_ABSENCE body signature validation

In `_validate_body_signature` for DATA_ABSENCE, expand to:

```python
if case_type == "DATA_ABSENCE":
    lowered_stripped = re.sub(r"&\w+;", " ", lowered)  # strip HTML entities
    return any(
        phrase in lowered_stripped
        for phrase in (
            "missing", "not submitted", "absence", "elapsed",
            "not up to date", "never been submitted", "data has never",
            "last activity", "maintenance data",
        )
    )
```

---

## Change 11: Fix DATA_ABSENCE field extraction in `src/extractor.py`

In `_extract_case_specific_fields` for `case_type == "DATA_ABSENCE"`:

1. **Strip HTML entities from the body before label matching.**
   Add a helper at module level:
   ```python
   def _strip_html_entities(text: str) -> str:
       """Replace common HTML entities and &nbsp; with plain text equivalents."""
       text = re.sub(r"&nbsp;", " ", text)
       text = re.sub(r"&amp;", "&", text)
       text = re.sub(r"&lt;", "<", text)
       text = re.sub(r"&gt;", ">", text)
       text = re.sub(r"&[a-zA-Z]+;", " ", text)
       return text
   ```

2. **Extract building from subject** when not found in body.
   The e-volve subject format is:
   `"Data Absence: Maintenance Data is not up to date - 55 Bloor Street, Toronto"`
   Building is the text after the final ` - ` in the subject.

   In `_extract_case_specific_fields` for DATA_ABSENCE:
   ```python
   if case_type == "DATA_ABSENCE":
       clean_body = _strip_html_entities(body)
       # Building from subject if not in body
       if not fields.get("building") and " - " in subject:
           fields["building"] = subject.rsplit(" - ", 1)[-1].strip()
       # Re-run common field extraction on HTML-stripped body
       for field_name, labels in _LABELS.items():
           if fields.get(field_name):
               continue
           for label in labels:
               value = _capture_line(clean_body, label)
               if value:
                   fields[field_name] = _strip_html_entities(value).strip()
                   break
       if not fields.get("description"):
           extracted["description"] = "Maintenance data has never been submitted"
       return extracted
   ```

   Note: the function must return `extracted`, not `fields` — only write
   newly extracted values into `extracted` and return that. Use `fields` only
   for read-only lookups.

3. **Support contractor from "Details:" label.**
   Add `"Details"` to the contractor labels in `_LABELS`:
   ```python
   "contractor": ("Contractor", "Details"),
   ```

---

## Change 12: Fix MAJOR_WORK_OVERDUE field extraction in `src/extractor.py`

The real e-volve MAJOR_WORK_OVERDUE body format uses a vertical table:

```
scheduled major maintenance at the above noted site appear to be overdue
Device
ScheduledDate
Description
1 #36578
Scheduled Maintenance
04-Aug-2019
<description text>
```

In `_extract_case_specific_fields` for `case_type == "MAJOR_WORK_OVERDUE"`:

1. **Extract building from subject** if not found in body (same pattern as DATA_ABSENCE):
   After ` - ` in the subject.

2. **Parse the vertical table format** for device, scheduled_date, description:

   ```python
   def _parse_major_work_vertical(body: str) -> Dict[str, Optional[str]]:
       """Parse e-volve vertical-layout MAJOR_WORK_OVERDUE body."""
       result: Dict[str, Optional[str]] = {
           "device": None, "scheduled_date": None, "description": None
       }
       lines = [l.strip() for l in body.splitlines() if l.strip()]
       # Find the header sentinel
       try:
           header_idx = next(
               i for i, l in enumerate(lines)
               if l.lower() in ("scheduleddate", "scheduled date", "scheduled_date")
           )
       except StopIteration:
           return result
       # Lines just before the header contain device info
       if header_idx >= 2:
           candidate = lines[header_idx - 2]
           device_match = re.search(r"\d+\s*#\d+", candidate)
           if device_match:
               result["device"] = device_match.group(0).strip()
       # Lines just after the header: Description header, then date, then description text
       after = lines[header_idx + 1:]  # skip "Description" label if present
       if after and after[0].lower() == "description":
           after = after[1:]
       if len(after) >= 1:
           result["scheduled_date"] = _normalize_date(after[0])
       if len(after) >= 2:
           result["description"] = after[1]
       return result
   ```

   Call this in `_extract_case_specific_fields`:
   ```python
   if case_type == "MAJOR_WORK_OVERDUE":
       # Building from subject if not in body
       if not fields.get("building") and " - " in subject:
           extracted["building"] = subject.rsplit(" - ", 1)[-1].strip()
       # Standard label extraction
       if not fields.get("description"):
           extracted["description"] = _capture_line(body, "Description") or _capture_line(body, "Work Description")
       # Vertical table extraction (e-volve format)
       if not fields.get("device") or not fields.get("scheduled_date"):
           vertical = _parse_major_work_vertical(body)
           for k, v in vertical.items():
               if v and not fields.get(k) and not extracted.get(k):
                   extracted[k] = v
       if extracted.get("description"):
           extracted["work_item"] = extracted["description"]
       return extracted
   ```

3. **Relax the body signature** for MAJOR_WORK_OVERDUE to also match the e-volve phrase:
   In `_validate_body_signature`:
   ```python
   if case_type == "MAJOR_WORK_OVERDUE":
       return any(phrase in lowered for phrase in (
           "scheduled", "overdue", "scheduleddate",
           "major maintenance", "appear to be overdue",
       ))
   ```

4. **Relax REQUIRED_FIELDS for MAJOR_WORK_OVERDUE** — remove `contractor` from required
   since e-volve bodies often don't include it:
   ```python
   "MAJOR_WORK_OVERDUE": ("building", "device", "scheduled_date"),
   ```

---

## Change 13: Update tests in `tests/test_backlog_loader.py`

Add a new test class `TestTriageBuckets` with at least these tests:

```python
class TestTriageBuckets(BacklogLoaderTestCase):

    def test_callback_alert_is_recognized_unsupported_kpi(self):
        record = {
            "message_id": "cb-001@test.example",
            "subject": "Callback Alert - 123 Test Street, Toronto",
            "from_addr": "alerts@test.example",
            "to_addrs": ["ops@test.example"],
            "cc_addrs": [], "bcc_addrs": [], "reply_to": "",
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
            "cc_addrs": [], "bcc_addrs": [], "reply_to": "",
            "received_at": "2026-01-11T09:00:00",
            "body": "Client: Test Client\nBuilding: 456 Demo Avenue\nDevice is back in service.",
        }
        result = self._run_loader([record], dry_run=True)
        self.assertEqual(1, result["recognized_unsupported_kpi"])
        self.assertEqual(0, result["review_candidates"])

    def test_data_absence_evolve_format_accepted(self):
        """Subject contains building after ' - '; body uses HTML entities."""
        record = {
            "message_id": "da-evolve-001@test.example",
            "subject": "Data Absence: Maintenance Data is not up to date - 55 Bloor Street, Toronto",
            "from_addr": "kpi-alerts@test.example",
            "to_addrs": ["ops@test.example"],
            "cc_addrs": [], "bcc_addrs": [], "reply_to": "",
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
        """Vertical-layout MAJOR_WORK_OVERDUE body with building from subject."""
        record = {
            "message_id": "mwo-evolve-001@test.example",
            "subject": "Major Scheduled Work is Overdue - 99 Demo Road, Toronto",
            "from_addr": "projects@test.example",
            "to_addrs": ["facilities@test.example"],
            "cc_addrs": [], "bcc_addrs": [], "reply_to": "",
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
            "cc_addrs": [], "bcc_addrs": [], "reply_to": "",
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
                "from_addr": "a@test.example", "to_addrs": ["b@test.example"],
                "cc_addrs": [], "bcc_addrs": [], "reply_to": "",
                "received_at": "2026-01-17T09:00:00",
                "body": "callback alert details here",
            },
            {
                "message_id": "bis-002@test.example",
                "subject": "Back in Service Notification",
                "from_addr": "a@test.example", "to_addrs": ["b@test.example"],
                "cc_addrs": [], "bcc_addrs": [], "reply_to": "",
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
            "from_addr": "a@test.example", "to_addrs": ["b@test.example"],
            "cc_addrs": [], "bcc_addrs": [], "reply_to": "",
            "received_at": "2026-01-19T09:00:00",
            "body": "Callback alert details.",
        }
        self._run_loader([record], dry_run=True)
        self.assertTrue(self._report_path("unsupported_kpis.json").exists())

    def test_report_json_has_recognized_unsupported_kpi_count(self):
        record = {
            "message_id": "cb-004@test.example",
            "subject": "Callback Alert",
            "from_addr": "a@test.example", "to_addrs": ["b@test.example"],
            "cc_addrs": [], "bcc_addrs": [], "reply_to": "",
            "received_at": "2026-01-20T09:00:00",
            "body": "Callback alert details.",
        }
        self._run_loader([record], dry_run=True)
        report = self._read_json(self._report_path("report.json"))
        self.assertIn("recognized_unsupported_kpi", report)
        self.assertEqual(1, report["recognized_unsupported_kpi"])
```

---

## Validation

Run in this order:

```bash
# 1. Compile check
.venv/bin/python -m compileall src

# 2. Full test suite — all tests must pass
.venv/bin/python -m unittest discover -v

# 3. Dry-run on the real backlog (if available)
.venv/bin/python src/agent.py load-backlog --source json --path data/my_backlog.json --dry-run
```

Target from the 50,585-email dry run:
- `recognized_unsupported_kpi` should capture a large portion of the 44,996 that were going to review
- `review_required` should be much smaller (genuinely ambiguous records only)
- `safe_rejected_non_kpi` may grow slightly from expanded noise patterns
- `accepted_kpi` (5,562) should stay roughly the same or improve slightly
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
