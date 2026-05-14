# Codex Task: Phase 2 — Reporting and Sample Data

You are implementing Phase 2 of Backlog Loading Mode for the Email Alert Triage Agent.

**FIRST: Read `Backlog Loading Mode Design Spec.md`** — it is the source of truth.

Phase 1 has already implemented `src/backlog_loader.py` with the core import pipeline.

Your working directory is the project root.

---

## What you must build

### Report writing under `data/backlog_runs/<timestamp>/`

Add a `_write_reports(results, report_dir, dry_run) -> None` function in `src/backlog_loader.py` (or a helper in the same file) that writes these files:

#### `report.json`
A machine-readable summary. Include at minimum:
```json
{
  "mode": "dry_run" or "commit",
  "generated_at": "<ISO timestamp>",
  "source_path": "<path>",
  "emails_scanned": N,
  "accepted_kpi": N,
  "rejected": N,
  "review_candidates": N,
  "duplicate_inputs": N,
  "new_cases_expected_or_created": N,
  "case_updates_expected_or_done": N,
  "memory_observations_created": N,
  "pattern_flags_created": N,
  "manual_reviews_created": N,
  "ai_calls": 0,
  "outbound_emails": 0,
  "followups_scheduled": 0,
  "common_rejected_subjects": [{"subject": "...", "count": N, "reason": "..."}],
  "unique_recipients": N
}
```

If dry_run, add a prominent field:
```json
"dry_run_note": "No database changes were committed. This is a preview only."
```

#### `report.md`
A human-readable markdown summary. Include:
- Header with mode (DRY RUN or COMMIT), timestamp
- Counts table (scanned, accepted, rejected, review, duplicates)
- Cases table (new, updated)
- Safety confirmation: "AI calls: 0 | Outbound: 0 | Follow-ups: 0"
- Top rejected subjects
- Link to rejected.json, review_candidates.json, recipient_summary.json
- If dry_run, a bold note: "**DRY RUN — No database changes committed.**"

#### `rejected.json`
Array of rejected records:
```json
[
  {
    "subject": "...",
    "from_addr": "...",
    "received_at": "...",
    "rejection_reason": "...",
    "body_preview": "<first 200 chars of body>"
  }
]
```

#### `review_candidates.json`
Array of records placed in review bucket (weak body, unsupported KPI-like, ambiguous):
```json
[
  {
    "subject": "...",
    "from_addr": "...",
    "received_at": "...",
    "review_reason": "...",
    "classified_as": "UNKNOWN" or "<partial case_type>",
    "body_preview": "<first 200 chars>"
  }
]
```

#### `recipient_summary.json`
```json
{
  "unique_recipients": N,
  "missing_recipient_count": N,
  "by_domain": {"example.test": N, ...},
  "top_to_recipients": [{"address": "...", "count": N}, ...],
  "top_cc_recipients": [{"address": "...", "count": N}, ...],
  "by_kpi_family": {"CAT1_COMPLIANCE": N, ...}
}
```

### Update `load_backlog()` to call `_write_reports()`

After processing all records, call `_write_reports(results, report_dir, dry_run)`.
Print the report directory path to stdout.

Ensure the report dir is created if it does not exist:
```python
from datetime import datetime
from pathlib import Path
from config import PROJECT_ROOT

def _default_report_dir() -> Path:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    return PROJECT_ROOT / "data" / "backlog_runs" / ts
```

### Print a clean summary to stdout

After writing reports:
```
[BACKLOG] ============================================================
[BACKLOG] Backlog Loading Mode — DRY RUN (or COMMIT)
[BACKLOG] ============================================================
[BACKLOG]   Emails scanned:        N
[BACKLOG]   Accepted (KPI):        N
[BACKLOG]   Rejected:              N
[BACKLOG]   Review candidates:     N
[BACKLOG]   Duplicate inputs:      N
[BACKLOG]   New cases:             N
[BACKLOG]   Case updates:          N
[BACKLOG]   AI calls:              0
[BACKLOG]   Outbound emails:       0
[BACKLOG]   Follow-ups scheduled:  0
[BACKLOG] ============================================================
[BACKLOG]   Reports written to: data/backlog_runs/<timestamp>/
[BACKLOG] ============================================================
```

---

### Sample data: `data/backlog_sample.json`

Create a JSON file with 10-15 generic placeholder records covering:

1. All six supported case types (at least one each):
   - CAT1_COMPLIANCE
   - CAT5_COMPLIANCE
   - DATA_ABSENCE
   - MAINTENANCE_HOURS_SHORTFALL
   - MAJOR_WORK_OVERDUE
   - GOVERNMENT_DIRECTIVE

2. At least 2-3 rejected non-KPI emails:
   - An out-of-office auto-reply
   - An invoice/statement email
   - A newsletter

3. At least 1 unsupported KPI-like email (has "compliance" in subject but no matching pattern — should go to review)

4. At least 1 duplicate of an earlier record (same message_id or same subject/body)

Use ONLY generic placeholder content:
- Client names: "Example Client 001", "Example Client 002"
- Buildings: "123 Example Road, Example City", "456 Demo Street, Sample Town"
- Devices: "B-1 #700001", "B-2 #700042"
- Contractors: "Example Maintenance Co", "Demo Lift Services Ltd"
- Domains: `example.test`, `demo.test`
- Dates: use fixed dates like "2026-01-15T09:00:00", "2026-02-01T10:30:00"

Example body for MAINTENANCE_HOURS_SHORTFALL:
```
Client: Example Client 001
Building: 123 Example Road, Example City
Contractor: Example Maintenance Co
Reporting Period: January 2026
Contract Hours: 40
Actual Hours: 22
Status: Maintenance hours are below the required threshold for this period.
```

Example body for CAT1_COMPLIANCE:
```
Client: Example Client 001
Building: 123 Example Road, Example City
Device: B-1 #700001
Contractor: Example Maintenance Co
CAT1 Tests Reminder: CAT1 compliance tests are due for this device.
```

---

## Hard constraints

- DO NOT generate outbound messages in reports.
- DO NOT trigger AI in the report writer.
- DO NOT use real client names, real buildings, real devices, real emails.
- The report writer must not modify the database.
- Keep code small — reporting should be straightforward file writes.

---

## At the end, report

- Files modified (list them)
- Report fields added
- Sample data records added (count by case type)
- Assumptions
