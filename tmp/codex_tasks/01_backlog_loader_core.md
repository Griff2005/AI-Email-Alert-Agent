# Codex Task: Phase 1 — Core Backlog Loader

You are implementing Phase 1 of Backlog Loading Mode for the Email Alert Triage Agent.

**FIRST: Read `Backlog Loading Mode Design Spec.md`** — it is the source of truth.

Your working directory is the project root (`/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent`).

---

## Context you must know before writing code

### Existing infrastructure (DO NOT duplicate, DO reuse)

**classifier.py**
- `CASE_TYPES` list: `["CAT1_COMPLIANCE", "CAT5_COMPLIANCE", "DATA_ABSENCE", "MAINTENANCE_HOURS_SHORTFALL", "MAJOR_WORK_OVERDUE", "GOVERNMENT_DIRECTIVE", "UNKNOWN"]`
- `_NOISE_PATTERNS`: tuple of obvious non-KPI patterns (out of office, auto reply, undeliverable, etc.)
- `_RULES`: tuple of `_Rule(case_type, confidence, [patterns])` — the deterministic matching rules
- `_deterministic_classification(subject, body) -> dict` — returns dict with `case_type`, `confidence`, `source` ("deterministic"/"noise"/"ambiguous"), `reason`, `matched_rules`
- `quick_filter(subject) -> bool` — returns False for obvious noise
- **WARNING**: `classify_email()` calls AI for ambiguous cases. Do NOT call `classify_email()`. Call `_deterministic_classification()` directly (prefix underscore is fine, it's a module-level function, just not exported from `__init__`). If result is "ambiguous", treat as unsupported/review.

**extractor.py**
- `extract_fields(subject, body, case_type) -> dict` — deterministic wrapper
- `extract_fields_with_meta(subject, body, case_type, email_id, case_id) -> (fields, meta)` — deterministic extraction, may call AI if required fields are missing
- `generate_grouping_key(case_type, building, device, period) -> str` — deterministic, use this directly
- **WARNING**: `extract_fields_with_meta` may call AI when required fields are missing. For backlog mode, call `extract_fields()` (which calls `_extract_common_fields` and `_extract_case_specific_fields` deterministically) and do NOT call AI.
- Safe approach: import and call `extractor.extract_fields(subject, body, case_type)` — this calls `extract_fields_with_meta` which falls through to `gateway.call_json` only if required fields are missing. Since the AI gateway has `enabled=False` by default, the `outcome.payload` will be None and it will just return what was found plus a manual_review meta. This is SAFE — no actual AI call is made.

**database.py**
- `init_schema()` — safe to call
- `insert_email(email_id, message_id, thread_id, subject, from_addr, to_addr, received_at, raw_body, normalized_text)` — `to_addr` is TEXT, store as semicolon-joined or JSON for multiple recipients
- `get_email_by_message_id(message_id) -> Optional[Row]` — use for deduplication check
- `get_case_by_grouping_key(grouping_key) -> Optional[Row]` — use for case dedup
- `insert_case(case_id, case_type, grouping_key, building, device, contractor, due_date, period, priority, status, created_at, updated_at)` — check actual signature
- `update_case(case_id, updates: dict)` — updates is a dict of column->value
- `insert_case_event(event_id, case_id, event_type, description, source_email_id=None, created_at=None)`
- `insert_extracted_field(field_id, case_id, email_id, field_name, field_value, confidence_score=1.0)`
- `get_all_cases() -> list`
- NO schema changes allowed

**memory.py** — use for observations and pattern detection
- `record_issue_observation(case_id, case_type, building, device, contractor, email_id, observed_at)` — records an observation
- `run_pattern_detection(case_id)` — detects patterns for this case
- `upsert_entity(entity_type, canonical_name, first_seen, last_seen)` — adds/updates an entity
- `get_memory_context_for_case(case_id)` — not needed in backlog loader

**config.py**
- `from config import config, PROJECT_ROOT`
- `config.DATABASE_PATH` — path to SQLite db
- `PROJECT_ROOT` — Path to project root

### emails table schema
```
email_id TEXT PRIMARY KEY
message_id TEXT UNIQUE
thread_id TEXT
subject TEXT NOT NULL
from_addr TEXT
to_addr TEXT          -- store as semicolon-joined string for multiple recipients
received_at TEXT NOT NULL
raw_body TEXT
normalized_text TEXT
processed INTEGER DEFAULT 0
```

### cases table schema
```
case_id TEXT PRIMARY KEY
case_type TEXT NOT NULL
status TEXT NOT NULL DEFAULT 'open'
owner TEXT
priority TEXT NOT NULL DEFAULT 'medium'
grouping_key TEXT UNIQUE
building TEXT
device TEXT
contractor TEXT
due_date TEXT
period TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

---

## What you must build

### File: `src/backlog_loader.py`

A standalone backlog import workflow with this structure:

```python
"""
backlog_loader.py — Standalone importer for staged historical KPI emails.

Supports JSON source only. Zero-AI. No outbound. No follow-ups.
"""
```

#### Functions to implement

**`load_backlog(source, path, dry_run, limit=None, report_dir=None) -> dict`**
- Main entry point called from agent.py
- Returns a summary/results dict
- Loads JSON file at `path`
- Runs the full pipeline
- Returns report data

**`_load_json_source(path) -> list[dict]`**
- Opens and parses JSON file
- Returns list of raw record dicts
- Raises clear error on failure

**`_normalize_record(raw) -> dict`**
- Normalizes a raw JSON record into a consistent internal dict
- Required fields: `subject` and `body`
- Generates stable `message_id` if missing: `backlog:<sha256(subject+received_at+body[:200])>`
- Captures: `message_id`, `subject`, `body`, `from_addr`, `to_addrs` (list), `cc_addrs` (list), `bcc_addrs` (list), `reply_to`, `received_at`
- Returns None or raises ValueError if subject or body are missing/empty

**`_is_obvious_non_kpi(record) -> tuple[bool, str]`**
- Checks subject against noise patterns (out of office, auto reply, undeliverable, delivery status notification, read receipt, meeting accepted, meeting declined, invoice, statement, newsletter, empty subject/body)
- Returns (True, reason_string) if non-KPI, (False, "") if potentially KPI

**`_classify_for_backlog(record) -> dict`**
- Calls `classifier._deterministic_classification(subject, body)`
- Returns result dict
- If source == "deterministic": supported KPI
- If source == "noise": reject
- If source == "ambiguous": put in review bucket (UNKNOWN)
- Never calls AI

**`_validate_body_signature(case_type, body) -> bool`**
- Checks body for expected KPI structure per case type:
  - CAT1_COMPLIANCE / CAT5_COMPLIANCE: look for device format or test/reminder keywords
  - DATA_ABSENCE: look for "data" + ("missing" or "not submitted" or "absence" or "elapsed")
  - MAINTENANCE_HOURS_SHORTFALL: look for "hours" + ("required" or "contract" or "actual")
  - MAJOR_WORK_OVERDUE: look for "scheduled" or "overdue" or "scheduleddate"
  - GOVERNMENT_DIRECTIVE: look for "directive" or "duedate" or "due date"
- Returns True if signature found, False otherwise

**`_process_record(record, dry_run) -> dict`**
- Full per-record pipeline:
  1. Normalize record
  2. Check obvious non-KPI -> reject
  3. Deterministic classify
  4. If UNKNOWN/ambiguous -> review bucket
  5. Validate body signature -> if fails, review bucket
  6. Extract fields with `extractor.extract_fields(subject, body, case_type)`
  7. Generate grouping key with `extractor.generate_grouping_key(case_type, building, device, period)`
  8. If dry_run: return expected action only (no DB writes)
  9. If commit: check for duplicate email (by message_id), skip if exists
  10. If commit: insert email record
  11. If commit: check for existing case by grouping key
  12. If commit: create or update case
  13. If commit: insert extracted fields
  14. If commit: insert case events (backlog_case_created or backlog_case_updated, backlog_email_imported)
  15. If commit: call memory.upsert_entity for building/device/contractor
  16. If commit: call memory.record_issue_observation if the function exists
  17. If commit: call memory.run_pattern_detection(case_id)
- Returns result dict: action, case_type, case_id, grouping_key, message_id, reason, etc.

**`_collect_recipient_summary(records) -> dict`**
- From normalized records, collect:
  - unique recipients (all addresses)
  - domain breakdown
  - top To recipients (most frequent)
  - top Cc recipients
  - count of records with missing recipient data
- Returns dict

### File: `src/agent.py` changes

Add `load-backlog` subparser and command in the existing `main()` function.

Add after the `test-demo-scale` parser block:

```python
backlog_parser = subparsers.add_parser("load-backlog", help="Import staged backlog KPI emails from JSON")
backlog_parser.add_argument("--source", required=True, choices=["json"], help="Source format (json only)")
backlog_parser.add_argument("--path", required=True, type=Path, help="Path to backlog JSON file")
backlog_parser.add_argument("--dry-run", action="store_true", default=False, help="Parse and classify without writing to database")
backlog_parser.add_argument("--commit", action="store_true", default=False, help="Import accepted emails into database")
backlog_parser.add_argument("--limit", type=int, default=None, help="Maximum number of records to process")
backlog_parser.add_argument("--report-dir", type=Path, default=None, help="Directory for report output (default: data/backlog_runs/<timestamp>/)")
```

Add a `cmd_load_backlog(args)` function that:
1. Validates exactly one of --dry-run or --commit is set, prints error and exits if neither/both
2. Calls `db.init_schema()`
3. Imports and calls `backlog_loader.load_backlog(...)`
4. Prints summary to stdout
5. Does NOT call `_configure_runtime_from_args` (backlog mode has no AI budget)

Add dispatch in the `if args.command == ...` chain:
```python
elif args.command == "load-backlog":
    cmd_load_backlog(args)
```

Also update the `epilog` string to mention `load-backlog`.

---

## Hard constraints

- ZERO AI calls. Do not call `classify_email()`. Do not call `extract_fields_with_meta()` with AI enabled. Do not call `get_ai_gateway().call_json()` or `.call_text()`.
- NO outbound messages. No `outbound_messages` table inserts.
- NO follow-up records. No `followups` or `followup_actions` table inserts.
- NO auto-closure. Do not set case status to "closed".
- NO escalation.
- NO schema changes (no ALTER TABLE, no new CREATE TABLE).
- NO imports from `case_manager.py` — it triggers outbound/followup side effects.
- NO imports from `email_sender.py` or `email_reader.py`.
- NO `followup.py` imports.
- Dry-run must not touch the database at all.

---

## Safety pattern for AI gateway

Since the AI gateway is disabled by default, calling `extract_fields()` is safe — it will fall through gracefully. But to be explicit and safe, do NOT call the AI gateway at all. Add this guard at the start of `load_backlog()`:

```python
from ai_gateway import get_ai_gateway
gateway = get_ai_gateway()
gateway.reset()
# Explicitly ensure AI is off for backlog mode
```

And use `_deterministic_classification` (the private function from classifier) directly.

---

## Code style

- Keep the module readable and focused.
- No huge docstrings.
- Inline comments only where safety is critical (e.g., "# Never creates outbound messages").
- Functions should be short and do one thing.
- Use `uuid.uuid4()` for IDs.
- Use `datetime.utcnow().isoformat()` for timestamps.

---

## At the end, report

- Files modified (list them)
- Key functions added (list them)
- Any assumptions made
- Any places that may need follow-up in Phase 2 (reporting/samples)
