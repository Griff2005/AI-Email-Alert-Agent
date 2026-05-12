# Backlog Loading Mode Design Specification

## 1. Purpose

Backlog Loading Mode is a standalone import workflow that lets the Email Alert Triage Agent load a controlled batch of historical or staged KPI emails into the same SQLite database used by the demo, without becoming tightly coupled to the live/demo processing path.

The immediate goal is demo support, not production migration.

This mode should help show that the agent can start with a small backlog of prior KPI alerts, build useful case history, and surface memory/pattern signals before new emails arrive. It should support the same original six demo case types already used by the current agent.

Backlog Loading Mode should be implemented as its own isolated module or small package. It may reuse stable shared utilities such as database helpers, classifier constants, extraction helpers, and memory functions where safe, but the import workflow itself should be separate from the normal live/demo email pipeline.

The mode should add data to the same database so the existing Flask UI can display imported emails, cases, events, memory observations, and pattern signals.

---

## 2. Scope

### In Scope

Backlog Loading Mode should support the current six demo KPI case types:

- `CAT1_COMPLIANCE`
- `CAT5_COMPLIANCE`
- `DATA_ABSENCE`
- `MAINTENANCE_HOURS_SHORTFALL`
- `MAJOR_WORK_OVERDUE`
- `GOVERNMENT_DIRECTIVE`

It should support:

- Loading a small or medium batch of staged backlog emails
- Filtering out clearly non-KPI emails
- Deterministic classification for the six supported KPI families
- Deterministic extraction for the six supported KPI templates
- Creating or updating cases using the existing grouping logic
- Writing inbound email records to the same SQLite database
- Writing extracted fields, case events, memory observations, and pattern flags
- Preserving original sender and recipient fields where available
- Producing a dry-run report before commit
- Producing a commit report after import
- Adding enough data for the web UI to show a backlog/demo history

### Out of Scope For First Version

Do not support all KPI families yet.

Do not build:

- PST import
- CSV import
- IMAP folder import
- broad unsupported KPI classification
- full historical mailbox migration
- production-grade import recovery
- production contact/routing intelligence
- AI-heavy fallback processing
- outbound email creation from backlog records
- follow-up scheduling from backlog records
- escalation from backlog records

If the backlog file contains non-supported KPI emails, they should be rejected or placed in a review/rejected section of the report. They should not be forced into the wrong case type.

---

## 3. Design Principle

Backlog Loading Mode is separate from Live Mode.

| Mode | Purpose | Sends Emails | Schedules Follow-Ups | Uses AI By Default | Code Path |
|---|---|---:|---:|---:|---|
| Live Mode | Manage new KPI emails from the agent inbox | Demo-safe drafts or sends | Yes, when enabled | No | Normal agent pipeline |
| Demo / Sample Mode | Show the core workflow with sample emails | Demo-safe drafts or dry-run | Optional | No | Normal demo pipeline |
| Backlog Loading Mode | Import staged historical KPI emails into database and memory | No | No | No | Standalone importer |

The core rule:

> Backlog Loading Mode should build context, not create live operational pressure.

The second core rule:

> Backlog Loading Mode should be isolated enough that it can be replaced, removed, or expanded later without destabilizing the main demo agent.

---

## 4. Recommended File Structure

Create a standalone backlog importer module.

Recommended structure:

```text
src/
  backlog_loader.py        # CLI-facing backlog import workflow
  backlog_sources.py       # JSON source loader and email normalization, optional if useful
  backlog_report.py        # Dry-run/commit report writer, optional if useful
```

If that feels too many files for the current MVP, use one file:

```text
src/backlog_loader.py
```

The importer should be self-contained enough that a developer can understand it without reading the live IMAP loop.

### Acceptable shared dependencies

The backlog importer may call:

- `database.py` for inserts and read helpers
- `classifier.py` for supported case type constants and deterministic matching, if clean
- `extractor.py` for deterministic extraction and grouping key generation
- `memory.py` for observations and pattern detection
- `claude_client.sanitize_email_content()` only for normalization/sanitization if already used consistently
- `runtime_options.py` only if needed to explicitly disable outbound, follow-ups, and AI

### Avoid coupling to

Avoid calling the full `case_manager.process_email()` directly if it causes outbound drafts, follow-ups, or live-case side effects.

If `case_manager` has reusable lower-level helpers, they can be used. But the importer should have its own clear workflow so backlog-specific safety is obvious.

---

## 5. Backlog Source Format

The first version should support JSON only.

Example command:

```bash
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run
```

Expected input shape:

```json
[
  {
    "message_id": "backlog-001@example.test",
    "subject": "[BACKLOG DEMO] Maintenance Hours Less Than Required",
    "from_addr": "kpi-alerts@example.test",
    "to_addrs": ["client.contact@example.test", "contractor.dispatch@example.test"],
    "cc_addrs": ["consultant@example.test"],
    "bcc_addrs": [],
    "reply_to": "kpi-alerts@example.test",
    "received_at": "2026-04-15T09:00:00",
    "body": "Client: Example Client 001\nBuilding: 123 Example Road, Example City\n..."
  }
]
```

### Required fields

Minimum required fields:

- `subject`
- `body`

Recommended fields:

- `message_id`
- `from_addr`
- `to_addrs`
- `cc_addrs`
- `bcc_addrs`
- `reply_to`
- `received_at`

If `message_id` is missing, generate a stable synthetic ID from:

```text
backlog:<subject hash>:<received_at hash>:<body hash>
```

If recipients are missing, keep them empty and record that recipient data was unavailable.

---

## 6. Command Interface

Add a CLI command:

```bash
python src/agent.py load-backlog
```

### Recommended arguments

```bash
python src/agent.py load-backlog \
  --source json \
  --path data/backlog_sample.json \
  --dry-run
```

| Argument | Required | Description |
|---|---:|---|
| `--source` | Yes | First version should only support `json` |
| `--path` | Yes | Path to backlog JSON file |
| `--dry-run` | No | Parse, filter, classify, and estimate import without writing database changes |
| `--commit` | No | Actually import accepted emails into the database |
| `--limit` | No | Max number of records to process |
| `--report-dir` | No | Defaults to `data/backlog_runs/<timestamp>/` |
| `--strict` | No | Default true. Only import the six supported KPI case types |

### Required mode selection

The command should require either `--dry-run` or `--commit`.

Valid:

```bash
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run
python src/agent.py load-backlog --source json --path data/backlog_sample.json --commit
```

Invalid:

```bash
python src/agent.py load-backlog --source json --path data/backlog_sample.json
```

If neither flag is provided, print a clear error.

---

## 7. Processing Pipeline

Backlog Loading Mode should have a simple, explicit workflow.

```text
Load JSON records
  -> normalize email records
  -> capture sender and recipients
  -> reject obvious non-KPI emails
  -> classify against six supported KPI families
  -> validate body signature for matched case type
  -> extract fields deterministically
  -> generate grouping key
  -> dry-run: count expected creates/updates/rejections
  -> commit: insert email record
  -> commit: create or update case
  -> commit: store extracted fields
  -> commit: write backlog-specific case events
  -> commit: write memory observations
  -> commit: detect memory patterns
  -> never create outbound drafts
  -> never schedule follow-ups
  -> write report
```

### Important

This pipeline should not call any code path that generates outbound messages or schedules follow-ups unless that behavior is explicitly disabled and verified.

---

## 8. KPI Filtering

The importer should only import the six supported demo case types.

### Supported subject patterns

| Case Type | Accepted Subject Patterns |
|---|---|
| `CAT1_COMPLIANCE` | `CAT1`, `CAT1 Reminder`, `CAT1 Tests Reminder` |
| `CAT5_COMPLIANCE` | `CAT5`, `CAT5 Reminder`, `CAT5 Tests Reminder` |
| `DATA_ABSENCE` | `Data Absence`, `Maintenance Data is not up to date`, `Maintenance data has never been submitted` |
| `MAINTENANCE_HOURS_SHORTFALL` | `Maintenance Hours Less Than Required`, `Maintenance Hours Shortfall` |
| `MAJOR_WORK_OVERDUE` | `Major Scheduled Work is Overdue`, `Scheduled Work is Overdue` |
| `GOVERNMENT_DIRECTIVE` | `Outstanding Government Directive`, `Government Directive` |

### Rejected records

The importer should reject:

- Non-KPI emails
- Unsupported KPI families
- Emails with no supported KPI trigger
- Emails with supported subject but missing required body structure
- Emails with prompt-injection patterns, unless the design chooses to store them only as manual review records

Rejected records should be written to the report, not silently ignored.

---

## 9. Non-KPI Filtering

The backlog file may contain emails that are not KPI alerts.

The importer should use simple deterministic rejection rules for obvious non-KPI records.

Examples:

- `out of office`
- `automatic reply`
- `undeliverable`
- `delivery status notification`
- `read receipt`
- `meeting accepted`
- `meeting declined`
- `invoice`
- `statement`
- `newsletter`
- empty subject/body

The first version should log common rejected subjects and rejection reasons in the report.

It does not need persistent learned filtering yet.

### Report output

Write:

```text
data/backlog_runs/<timestamp>/rejected.json
```

Each rejected item should include:

- subject
- sender
- received_at
- rejection reason
- body preview

---

## 10. Body Signature Validation

Subject matching is not enough. The body should be checked for expected KPI structure.

| Case Type | Body Signature Examples |
|---|---|
| CAT1 / CAT5 | device format such as `B-1 #700001`, test/reminder language, building context |
| Data Absence | data missing/stale wording, building, contractor, last activity or elapsed days where available |
| Maintenance Hours | `Contract Hours`, `Actual Hours`, reporting period, building, contractor |
| Major Work Overdue | `ScheduledDate` or scheduled date text, device/work item, contractor |
| Government Directive | `DueDate`, `Device/Report Date`, directive/corrective action description |

If the subject matches but the body signature fails, the email should be placed in the review bucket instead of imported as a normal case.

---

## 11. Recipient Capture

Backlog mode should preserve original recipient data when available.

### Fields to capture or report

- `from_addr`
- `to_addrs`
- `cc_addrs`
- `bcc_addrs`
- `reply_to`
- display names, if parsing them is easy
- email domains

### First version behavior

If the existing database already has fields for recipients, store them.

If it does not, do not force a schema change unless needed. Instead:

- include recipient data in backlog report
- include recipient data in case event summaries when useful
- preserve it in imported email body/metadata if practical

Recipient history should not automatically determine follow-up routing in the first version. It is context for later.

### Report output

Write:

```text
data/backlog_runs/<timestamp>/recipient_summary.json
```

Include:

- unique recipients
- recipients by domain
- recipients by supported KPI family
- top To recipients
- top Cc recipients
- unknown/missing recipient count

---

## 12. AI Usage Policy

Backlog Loading Mode should be zero-AI for the first version.

Do not use AI fallback in the first build.

Reason:

- the demo is nearly done
- the codebase was just cleaned up
- the importer should remain standalone and predictable
- unknown or weak records can be set aside for review

Future versions may add AI fallback through the existing AI gateway, but not in this first build.

### First version rule

| Situation | Action |
|---|---|
| Deterministic supported KPI match | Import if body signature and required fields are valid |
| Supported subject but weak body | Review bucket |
| Unsupported KPI subject | Rejected or review bucket, depending on clarity |
| Obvious non-KPI | Rejected |
| Unknown | Review bucket |
| Prompt injection | Review bucket |

AI calls should remain 0.

---

## 13. Database Behavior

### Existing tables to reuse

Backlog mode should use the same database so the existing UI can show imported data.

Reuse:

- `emails`
- `cases`
- `case_events`
- `extracted_fields`
- `entities`
- `observations`
- `case_links`
- `pattern_flags`
- `manual_reviews`, for import review items

### Avoid schema changes

The first version should avoid schema changes.

If import metadata must be stored, prefer case events:

```text
Event type: backlog_imported
Summary: Historical backlog email imported into this case.
```

If an additive schema change is truly needed, keep it small and explain why before implementing it.

Do not rename or drop existing tables.

### Email deduplication

Backlog mode should avoid importing the same email repeatedly.

Use one or more of:

- original message ID
- generated stable message ID from subject + sender + received date + body hash
- normalized body hash for reporting duplicates within the source file

If an email already exists, skip it and count it as duplicate input.

### Case deduplication

Case deduplication should reuse the existing grouping key logic.

Repeated historical KPI alerts should update the same case where appropriate.

---

## 14. Case Creation and Updates

For supported KPI emails that pass validation:

### New case

Create a case if no existing grouping key is found.

Add events such as:

- `backlog_case_created`
- `backlog_email_imported`
- `backlog_memory_updated`

### Existing case

Update the existing case if grouping key already exists.

Add events such as:

- `backlog_case_updated`
- `backlog_email_imported`
- `backlog_memory_updated`

### Do not

Do not create outbound messages.

Do not schedule follow-ups.

Do not mark cases closed.

Do not escalate.

---

## 15. Memory Behavior

Backlog mode should feed the existing memory layer.

For each accepted supported KPI email, record:

- building entity
- device entity, if present
- contractor entity, if present
- issue type observation
- case-specific observation
- related case links
- pattern flags, if thresholds are met

Existing memory functions should be reused where practical.

Mechanic names should not be inferred.

If no mechanic data appears, report:

```text
Mechanic data: not available from imported KPI emails
```

---

## 16. Outbound and Follow-Up Safety

Backlog mode must not create live pressure.

### Outbound rules

Default and first version:

- Do not generate outbound messages
- Do not create drafts
- Do not send emails
- Do not call SMTP

### Follow-up rules

Default and first version:

- Do not create follow-up deadlines
- Do not increment follow-up counts
- Do not trigger escalations
- Do not create overdue follow-up events

This should be enforced in code, not just documented.

---

## 17. Manual Review Behavior

Backlog mode should create manual review records only for import-quality or safety issues.

Examples:

- Supported subject but invalid body signature
- Missing required fields after deterministic extraction
- Possible prompt injection
- Ambiguous grouping key
- Potential duplicate input that cannot be safely resolved
- Unsupported KPI family that may be worth future support
- Unknown classification

Manual review reason format:

```text
Backlog import review: subject matched a supported KPI, but required body fields were missing.
```

---

## 18. Dry Run Report

Dry run should parse and classify without changing the database.

### Dry-run metrics

| Metric | Description |
|---|---|
| Emails scanned | Total records loaded from source |
| Accepted supported KPI emails | Records that can create/update implemented cases |
| Rejected emails | Non-KPI or unsupported records |
| Review candidates | Records needing manual review |
| Duplicate input emails | Duplicate records within source or already in database |
| Expected new cases | Estimated cases to create |
| Expected case updates | Estimated case updates |
| Expected memory observations | Estimated memory facts to add |
| Unique recipients found | Count of unique recipient addresses |
| Common rejected subjects | Top rejected subject signatures |
| AI calls | Must be 0 |
| Outbound emails | Must be 0 |
| Follow-ups scheduled | Must be 0 |

### Report files

Write reports to:

```text
data/backlog_runs/<timestamp>/report.json
data/backlog_runs/<timestamp>/report.md
data/backlog_runs/<timestamp>/rejected.json
data/backlog_runs/<timestamp>/review_candidates.json
data/backlog_runs/<timestamp>/recipient_summary.json
```

### Console example

```text
Backlog Load Dry Run
--------------------
Source: data/backlog_sample.json
Emails scanned: 75
Accepted supported KPI emails: 48
Rejected emails: 17
Review candidates: 10
Duplicate input emails: 0
Expected new cases: 22
Expected case updates: 26
Unique recipients found: 14
AI calls: 0
Outbound emails: 0
Follow-ups scheduled: 0

No database changes were committed.
Report: data/backlog_runs/20260508T140000Z/report.md
```

---

## 19. Commit Report

Commit mode should write actual import results.

Example:

```text
Backlog Load Commit
-------------------
Emails scanned: 75
Imported supported KPI emails: 48
Rejected emails: 17
Review candidates: 10
Cases created: 22
Cases updated: 26
Memory observations created: 140
Pattern flags created: 8
Manual reviews created: 10
Unique recipients captured: 14
AI calls: 0
Outbound emails: 0
Follow-ups scheduled: 0

Database updated successfully.
Report: data/backlog_runs/20260508T141500Z/report.md
```

---

## 20. UI Behavior

The web UI should be able to show imported backlog records through existing pages.

### Dashboard / emails page

If the current UI has dashboard or emails/backlog views, imported backlog records should appear naturally because they are stored in the same database.

Useful display labels:

- backlog imported
- backlog case created
- backlog case updated
- backlog review needed

### Events

Backlog imports should create clear events:

- `backlog_email_imported`
- `backlog_case_created`
- `backlog_case_updated`
- `backlog_memory_updated`

Use existing event naming conventions if cleaner names already exist.

---

## 21. Error Handling

Backlog mode should fail safely.

### Source errors

Examples:

- file not found
- unsupported source type
- invalid JSON
- missing required source fields

These should stop the import before commit.

### Record errors

Examples:

- missing subject
- missing body
- invalid date
- parse failure
- unsupported KPI trigger
- recipient parsing failure

These should be counted as rejected or review candidate records.

### Commit safety

For first version:

- Prefer a transaction if feasible
- If transaction wrapping is too invasive, commit record-by-record but report partial completion clearly on error
- Never send emails during commit
- Never schedule follow-ups during commit

---

## 22. Acceptance Criteria

Backlog Loading Mode is complete when:

1. `python src/agent.py load-backlog --source json --path <file> --dry-run` works.
2. `python src/agent.py load-backlog --source json --path <file> --commit` works.
3. Dry run writes a clear report without changing the database.
4. Commit mode imports accepted supported KPI emails into the same database.
5. Only the six original supported KPI case types are imported as cases.
6. Non-KPI emails are rejected and summarized.
7. Unsupported KPI emails are rejected or sent to review, not forced into a case.
8. Original recipients are captured or summarized.
9. AI calls remain 0.
10. Outbound emails remain 0.
11. Follow-ups scheduled remain 0.
12. Cases are created or updated using existing grouping logic.
13. Memory observations and pattern flags are created from accepted backlog emails.
14. Duplicate input emails are detected and skipped.
15. The existing demo command still works.
16. The existing offline harness still works.
17. No live IMAP or SMTP behavior is changed.
18. Backlog importer code is standalone and does not bloat the main case pipeline.

---

## 23. Suggested Implementation Plan

### Phase 1: Standalone JSON Backlog Loader

- Add `src/backlog_loader.py`
- Add `load-backlog` CLI command in `agent.py`
- Support `--source json`
- Normalize records
- Preserve sender and recipients
- Add supported KPI filtering
- Add non-KPI rejection
- Add dry-run report
- Add commit mode

### Phase 2: Database Import

- Insert accepted email records
- Create/update cases using grouping keys
- Store extracted fields
- Write backlog-specific events
- Avoid outbound and followups

### Phase 3: Memory Integration

- Record observations
- Detect pattern flags
- Add memory update events

### Phase 4: Reporting

- `report.md`
- `report.json`
- `rejected.json`
- `review_candidates.json`
- `recipient_summary.json`

### Phase 5: Optional Later Expansion

Only after demo is shown:

- `.eml` folder support
- broader KPI recognition
- unsupported KPI review category
- optional AI fallback with strict budget
- backlog source metadata columns if needed

---

## 24. Recommended First Build Scope

Build only this first:

- standalone `backlog_loader.py`
- `load-backlog` CLI command
- JSON source support
- dry-run mode
- commit mode
- six supported KPI case types only
- deterministic classification and extraction
- non-KPI rejection
- recipient capture in report
- no AI
- no outbound
- no followups
- memory observation and pattern update
- report files

Do not build PST, CSV, IMAP folder import, all-KPI support, or AI fallback in the first version.

The first version should be simple, demo-safe, and isolated from the main workflow.

