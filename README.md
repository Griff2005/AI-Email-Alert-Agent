# Solucore Email Alert Triage Agent

A Python demo agent that automatically triages KPI alert emails for elevator compliance management. Uses the Claude AI CLI for intelligent classification and field extraction, SQLite for persistence, and Flask for a case management web UI.

---

## Features

- Classifies 6 types of KPI alert emails using Claude AI
- Extracts structured fields (building, device, contractor, due dates, etc.)
- Deduplicates cases using normalized grouping keys
- Adds Advanced Memory v1 with persistent entity memory, observations, case links, and deterministic pattern flags
- Background scheduler checks follow-up deadlines every 5 minutes
- Flask web UI for case management, event timeline, review queue, and memory/pattern visibility
- Uses deterministic memory context to add neutral recurrence notes to outbound emails when appropriate
- Demo safety guardrails: all outbound email is redirected to a single review address
- Prompt injection detection with automatic flagging

---

## Prerequisites

- Python 3.9+
- `claude` CLI installed and authenticated (`claude --version` should work)
- pip

---

## Setup

### 1. Install dependencies

From the project root directory:

```bash
python3 -m pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your values. For demo purposes, the placeholder values work fine — IMAP/SMTP will be disabled and all output is logged locally.

Key settings:

| Variable | Description |
|----------|-------------|
| `AGENT_EMAIL` | The agent's dedicated mailbox (placeholder OK for demo) |
| `AGENT_EMAIL_PASSWORD` | Mailbox password (placeholder OK for demo) |
| `IMAP_HOST` | IMAP server hostname |
| `SMTP_HOST` | SMTP server hostname |
| `DEMO_RECIPIENT_EMAIL` | Your email — all outbound goes HERE only in demo mode |
| `DEMO_MODE` | `true` keeps all emails as drafts; `false` to send |
| `CLAUDE_MODEL` | AI model to use (default: `claude-haiku-4-5-20251001`) |

**Note:** When `IMAP_HOST` or credentials are placeholder values, inbox polling is automatically disabled. Flask and the scheduler still start normally.

---

## Running the Demo

### Quick demo (recommended first run)

Processes all 7 sample emails through the full AI pipeline and displays results:

```bash
python src/agent.py demo
```

This will:
1. Initialize the SQLite database
2. Load 7 sample KPI alert emails
3. Classify each email using Claude AI
4. Extract fields (building, device, contractor, dates, etc.)
5. Create cases with grouping keys for deduplication
6. Print a formatted results table

### Ingest only

```bash
python src/agent.py ingest
```

### Safe large-scale demo harness

Stress-test the current demo pipeline with synthetic KPI alerts, simulated replies, simulated follow-ups, a temporary SQLite database, and hard network blocks for IMAP/SMTP:

```bash
python src/agent.py test-demo-scale --offline --emails 250
```

Add deterministic memory connection validation:

```bash
python src/agent.py test-demo-scale --offline --emails 150 --seed 42 --validate-memory-connections
```

Optional manual AI-enabled run for later:

```bash
python src/agent.py test-demo-scale --emails 25 --require-ai --validate-memory-connections
```

If your shell only exposes `python3`, substitute `python3` in the commands above.

Offline validation note:
- The code validation for this repository update was run offline only.
- AI-enabled harness runs remain available for manual testing, but they were not executed as part of this change.

What this validates:
- New case creation through the real `case_manager.process_email()` pipeline
- Duplicate grouping and distinct-case separation using the current grouping architecture
- Draft / fake-send creation with the existing outbound sender path
- Contractor and client reply handling through `process_reply()`
- Overdue follow-up generation through `followup.check_and_process_followups()`
- Prompt-injection detection and manual-review creation
- Deterministic memory connection auditing across entities, observations, case links, pattern flags, and `evidence_json`
- Flask UI smoke checks for `/cases`, `/events`, `/reviews`, and one case detail page

Safety guarantees in the harness:
- Uses `data/test_runs/<timestamp>/test_agent.db`, never `data/agent.db`
- Forces `DEMO_MODE=true` and `DEMO_RECIPIENT_EMAIL=demo-recipient@example.test`
- Hard-blocks `smtplib.SMTP`, `smtplib.SMTP_SSL`, and `imaplib.IMAP4_SSL`
- Rewrites any non-test intended recipient values to safe placeholder domains inside the harness
- Never polls a real inbox and never sends a real email, even if `.env` contains live credentials

Synthetic data coverage:
- CAT1 compliance
- CAT5 compliance
- Data absence
- Maintenance hours shortfall
- Major work overdue
- Government directive
- Duplicate alerts
- Repeated building/device/contractor patterns
- Contractor replies, client replies, vague replies, completed replies, revised dates, and prompt-injection attempts
- Repeated missed follow-ups for escalation behavior

Offline vs AI-enabled mode:
- `--offline` replaces Claude calls with a deterministic local shim and is the recommended mode for 100-250 email structural tests
- Default mode uses the real Claude CLI when available
- If Claude is unavailable and `--require-ai` is not set, the harness falls back to the offline shim and marks AI-dependent checks as skipped in the report
- Use `--require-ai` when you want the command to fail instead of falling back
- The harness never uses AI as the evaluator. Pass/fail checks are deterministic and code-based even when the product path itself uses Claude.

Extraction validation behavior:
- Structured fields stay strict: `case_type`, `building`, `device`, `contractor`, `due_date`, `scheduled_date`, `period`, `hours_required`, `hours_actual`, `last_activity_date`, `elapsed_days`, and structured directive fields
- Free-text descriptions are validated semantically with deterministic normalization and keyword rules
- Description wording differences are reported as warnings, not hard failures, when the structured extraction is correct
- Missing optional descriptions for CAT reminders are accepted

Memory validation behavior:
- `--validate-memory-connections` audits the full chain from synthetic fixture metadata to stored cases, entities, observations, case links, pattern flags, and `evidence_json`
- The audit checks expected vs actual flags, missing flags, unexpected flags, false-positive links, duplicate flags, and mechanic-only behavior
- Mechanic recurrence tests stay disabled unless `--include-mechanics` is set

Reports:
- JSON: `data/test_runs/<timestamp>/report.json`
- Markdown: `data/test_runs/<timestamp>/report.md`
- Captured harness log: `data/test_runs/<timestamp>/harness.log`
- Retained test DB: `data/test_runs/<timestamp>/test_agent.db`
- The run directory is kept by default, including `test_agent.db`, `report.json`, `report.md`, and `harness.log`
- The console summary prints the retained run directory, database path, and report path at the end of every run

### Memory rebuild and reporting

Backfill deterministic memory from existing cases and recalculate active pattern flags:

```bash
python src/agent.py memory-rebuild
```

List active pattern flags:

```bash
python src/agent.py patterns
```

Inspect the stored memory context for a single case:

```bash
python src/agent.py memory-report --case-id <CASE_ID>
```

---

## Starting the Full Agent

```bash
python src/agent.py run
```

This starts:
- **IMAP polling** (every 60 seconds, if credentials are configured)
- **Follow-up scheduler** (every 5 minutes)
- **Flask web server** on `http://localhost:5000`

**To demo the follow-up scheduler immediately** (without waiting 7 days for a deadline to pass), backdating a case deadline will trigger it on the next 5-minute tick:

```bash
sqlite3 data/agent.db "UPDATE followups SET deadline = '2026-01-01T00:00:00' WHERE rowid = 1"
```

Then wait up to 5 minutes and refresh the Events page to see the follow-up event and draft email.

---

## Viewing the Web UI

After running `demo` or `ingest`, start Flask separately if needed:

```bash
cd src && python -c "from web.app import app; app.run(port=5000)"
```

Or use `python src/agent.py run` which starts Flask as part of the full agent.

Open `http://localhost:5000` in your browser.

### Web UI pages

| URL | Description |
|-----|-------------|
| `/cases` | Case management table with status/priority filters |
| `/cases/<id>` | Case detail: extracted fields, Memory / Intelligence, event timeline, outbound messages |
| `/reviews` | Manual review queue (injection flags, low confidence, escalations, high-severity pattern reviews) |
| `/patterns` | Active deterministic pattern flags across all cases |
| `/events` | Recent events feed across all cases |

---

## Interactive Reply Handler

Process a reply email for an existing case:

```bash
python src/agent.py reply --case-id <CASE_ID>
```

You'll be prompted to paste reply content (end with `---END---` on its own line). Claude analyzes the reply, updates the case event log, and prompts you to confirm any state changes. Cases are **never auto-closed** — only manual confirmation closes a case.

---

## Architecture Overview

```
data/
  sample_emails.json    7 demo KPI alert emails
  agent.db              SQLite database (auto-created)
  claude_cache.json     Claude response cache (SHA256-keyed)

src/
  agent.py              CLI entry point (ingest / demo / run / reply / memory-rebuild / patterns / memory-report / test-demo-scale)
  config.py             Env var loading via python-dotenv
  database.py           SQLite schema + all query helpers (thread-safe)
  claude_client.py      claude CLI subprocess wrapper + cache + injection detection
  classifier.py         Email classification via Claude (6 case types)
  extractor.py          Field extraction + grouping key generation + email body gen
  demo_fixtures.py      Synthetic KPI email, reply, and follow-up test data generation
  demo_scale_harness.py Safe large-scale harness with offline Claude shim, network blocking, and reporting
  memory.py             Deterministic memory, observations, links, and pattern rules
  case_manager.py       Case pipeline orchestration + reply analysis
  email_reader.py       IMAP inbox polling (graceful degradation on placeholder creds)
  email_sender.py       SMTP outbound with demo guardrails
  followup.py           APScheduler background follow-up checker
  web/
    app.py              Flask application + routes
    templates/
      base.html         Navigation + shared styles
      cases.html        Case table with stats and filters
      case_detail.html  Case detail, memory, timeline, messages, fields
      patterns.html     Active memory/pattern overview
      reviews.html      Manual review queue
      events.html       Recent events feed
```

### AI Integration

All AI calls use the `claude` CLI via subprocess:

```python
subprocess.run(
    ["claude", "--print", "--model", "claude-haiku-4-5-20251001"],
    input=prompt,
    capture_output=True,
    text=True,
    timeout=90,
)
```

Three AI tasks per email:
1. **Classification** — identify case type and confidence
2. **Field extraction** — extract building, device, contractor, dates, hours, etc.
3. **Email body generation** — generate follow-up email text with optional deterministic recurrence context

### Deduplication

Each case gets a normalized grouping key:
```
{case_type}|{building_normalized}|{device_normalized}|{period_normalized}
```

Re-ingesting the same email updates the existing case rather than creating a duplicate.

### Demo Safety Guardrails

When `DEMO_MODE=true` (default):
- All outbound emails are redirected to `DEMO_RECIPIENT_EMAIL`
- Subject prefix `[DEMO]` is added automatically
- A disclaimer footer is appended to every email body
- Emails default to `draft` status and are never sent automatically
- Intended production recipients are stored in the database for audit purposes only
- Inbound email and reply content cannot change recipients, configuration, memory thresholds, schema, or case closure behavior
- Replies can add observations, but they cannot auto-close a case or resolve a pattern flag

The `test-demo-scale` harness adds stronger isolation on top of demo mode:
- Overrides the database path to a per-run temporary file under `data/test_runs/`
- Replaces non-test intended recipient values with `example.com` placeholders
- Monkeypatches SMTP and IMAP classes so any live network attempt fails immediately
- Keeps its own report and Claude cache files under the same run directory
- Retains the SQLite test database by default for post-run inspection

### Advanced Memory v1

Advanced Memory v1 is SQLite-only and deterministic. It stores:
- Canonical entities for buildings, devices, contractors, issue types, and mechanics/technicians when available
- Structured observations from KPI emails, reply handling, and follow-up events
- Case links for related building/device/contractor/work-item records
- Pattern flags generated by database-backed rules, not by Claude

Supported pattern flags:
- `repeated_building_issue`
- `repeated_device_issue`
- `repeated_contractor_issue`
- `repeated_no_response`
- `repeated_data_absence`
- `repeated_major_work_overdue`
- `repeated_maintenance_shortfall`
- `mechanic_recurrence`
- `mechanic_rotation`

Mechanic intelligence is only available when a mechanic or technician name appears explicitly in an inbound email, reply, or extracted field. The agent does not infer mechanic identities.

---

## Supported Case Types

| Case Type | Trigger Keywords |
|-----------|-----------------|
| CAT1_COMPLIANCE | "CAT1 Reminder", "CAT1 Tests" |
| CAT5_COMPLIANCE | "CAT5 Reminder", "CAT5 Tests" |
| DATA_ABSENCE | "Data Absence", "Maintenance Data is not up to date" |
| MAINTENANCE_HOURS_SHORTFALL | "Maintenance Hours Less Than Required" |
| MAJOR_WORK_OVERDUE | "Major Scheduled Work is Overdue", "Scheduled Work is Overdue" |
| GOVERNMENT_DIRECTIVE | "Outstanding Government Directive" |

---

## Database Schema

12 SQLite tables:
- `emails` — inbound email records
- `cases` — case master records with status, priority, grouping key
- `case_events` — chronological event log per case
- `extracted_fields` — structured fields extracted by AI
- `outbound_messages` — draft and sent follow-up emails
- `followups` — follow-up deadlines and escalation tracking
- `manual_reviews` — items flagged for human review
- `entities` — canonical normalized entities used by the memory layer
- `entity_aliases` — alternate names for canonical entities
- `observations` — structured facts learned from emails, replies, and follow-ups
- `case_links` — deterministic relationships between related cases
- `pattern_flags` — active or historical memory / intelligence findings

---

## Notes

- The `claude` CLI must be installed and authenticated before running
- All Python stdlib modules are used for email (imaplib, smtplib) — no extra email library needed
- The Claude response cache (`data/claude_cache.json`) prevents re-spending tokens on demo reruns
- Python 3.9+ is required; tested on Python 3.9.25
