# Solucore Email Alert Triage Agent

A Python demo agent that automatically triages KPI alert emails for elevator compliance management. Uses the Claude AI CLI for intelligent classification and field extraction, SQLite for persistence, and Flask for a case management web UI.

---

## Features

- Classifies 6 types of KPI alert emails using Claude AI
- Extracts structured fields (building, device, contractor, due dates, etc.)
- Deduplicates cases using normalized grouping keys
- Background scheduler checks follow-up deadlines every 5 minutes
- Flask web UI for case management, event timeline, and review queue
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
| `/cases/<id>` | Case detail: extracted fields, event timeline, outbound messages |
| `/reviews` | Manual review queue (injection flags, low confidence, escalations) |
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
  agent.py              CLI entry point (ingest / demo / run / reply)
  config.py             Env var loading via python-dotenv
  database.py           SQLite schema + all query helpers (thread-safe)
  claude_client.py      claude CLI subprocess wrapper + cache + injection detection
  classifier.py         Email classification via Claude (6 case types)
  extractor.py          Field extraction + grouping key generation + email body gen
  case_manager.py       Case pipeline orchestration + reply analysis
  email_reader.py       IMAP inbox polling (graceful degradation on placeholder creds)
  email_sender.py       SMTP outbound with demo guardrails
  followup.py           APScheduler background follow-up checker
  web/
    app.py              Flask application + routes
    templates/
      base.html         Navigation + shared styles
      cases.html        Case table with stats and filters
      case_detail.html  Case detail, timeline, messages, fields
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
3. **Email body generation** — generate follow-up email text (on demand)

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

7 SQLite tables:
- `emails` — inbound email records
- `cases` — case master records with status, priority, grouping key
- `case_events` — chronological event log per case
- `extracted_fields` — structured fields extracted by AI
- `outbound_messages` — draft and sent follow-up emails
- `followups` — follow-up deadlines and escalation tracking
- `manual_reviews` — items flagged for human review

---

## Notes

- The `claude` CLI must be installed and authenticated before running
- All Python stdlib modules are used for email (imaplib, smtplib) — no extra email library needed
- The Claude response cache (`data/claude_cache.json`) prevents re-spending tokens on demo reruns
- Python 3.9+ is required; tested on Python 3.9.25
