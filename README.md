# Email Alert Triage Agent

A deterministic-first Python demo agent for triaging elevator KPI alert emails. It classifies known alert families, extracts key fields, creates or updates SQLite-backed cases, drafts safe outbound follow-up messages, tracks replies without auto-closing cases, and exposes a small Flask UI.

AI is optional, budgeted, centralized through the AI gateway, and disabled by default.

## Supported Demo Case Types

- `CAT1_COMPLIANCE`
- `CAT5_COMPLIANCE`
- `DATA_ABSENCE`
- `MAINTENANCE_HOURS_SHORTFALL`
- `MAJOR_WORK_OVERDUE`
- `GOVERNMENT_DIRECTIVE`

## Setup

Use Python 3.9+.

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` for local demo settings. Placeholder IMAP/SMTP values are safe: inbox polling and live sending are disabled when credentials are placeholders.

Important settings:

| Variable | Purpose |
| --- | --- |
| `DEMO_MODE` | Keep `true` for demo safety. |
| `DEMO_RECIPIENT_EMAIL` | Actual recipient for all demo outbound mail. |
| `DATABASE_PATH` | SQLite database path, default `data/agent.db`. |
| `OBSERVABILITY_LOG_PATH` | Local JSONL operational event log, default `data/observability/events.jsonl`. |
| `IMAP_HOST` / `AGENT_EMAIL` / `AGENT_EMAIL_PASSWORD` | Optional inbox polling. Placeholder values disable IMAP. |
| `SMTP_HOST` / `SMTP_PORT` | Optional SMTP for explicit send helpers. Placeholder values prevent live SMTP and produce dry-run status only if sending is explicitly invoked. |
| `CLAUDE_MODEL` | Model used only when AI is explicitly enabled. |

If your shell does not have `python`, use `python3` in the commands below.

## Run The Demo

Process the sample KPI emails:

```bash
python src/agent.py demo
```

This initializes SQLite, loads `data/sample_emails.json`, classifies and extracts deterministic fields, creates or updates cases, and records memory observations. The demo command disables outbound generation; normal processing creates outbound drafts only when outbound generation is enabled.

## Run The Web UI

```bash
python src/agent.py run
```

Then open:

```text
http://localhost:5000
```

The `run` command starts Flask and the follow-up scheduler. IMAP polling starts only when non-placeholder IMAP credentials are configured.

The web UI includes case list, case detail, reviews, events, and Memory / Intelligence views. Case detail pages show deterministic pattern signals, related cases, entity connections, and recent observations. The Patterns page shows active pattern signals and supporting evidence from `pattern_flags.evidence_json`.

The web app also exposes a read-only local observability snapshot at:

```text
http://localhost:5000/observability.json
```

This endpoint reports counts and safety checks from the existing SQLite audit tables. It does not poll IMAP, send SMTP, enable AI, or change case state.

## Local Observability

Generate a JSON metrics and safety snapshot from the current SQLite database:

```bash
python src/agent.py observability-report
```

Optionally write the snapshot to a file:

```bash
python src/agent.py observability-report --output data/observability/latest.json
```

The snapshot includes email pipeline counts, case counts, open manual review reasons, event types, outbound and follow-up status counts, basic email-to-case age/latency from available audit timestamps, compact current-process AI usage, and demo safety checks such as outbound recipient override violations.

Structured operational breadcrumbs are written as command-level JSONL events for selected CLI boundaries such as ingest, demo, backlog completion, and observability report writes. The log path is `OBSERVABILITY_LOG_PATH`, defaulting to `data/observability/events.jsonl`. This is an in-repo observability foundation, not a production Prometheus/Grafana/OpenTelemetry deployment.

## Safe Offline Harness

Run the concise offline demo validator:

```bash
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
```

The harness:

- uses an isolated database under `data/test_runs/<timestamp>/test_agent.db`
- disables AI
- blocks IMAP and SMTP
- processes synthetic supported KPI alerts
- validates duplicate grouping, recipient override, prompt-injection handling, replies, memory pattern creation, and UI smoke when Flask is installed
- writes concise `report.json` and `report.md`

Optional:

```bash
python src/agent.py test-demo-scale --offline --emails 50 --seed 42 --enable-followups
```

## Backlog Loading Mode

Backlog Loading Mode imports staged historical KPI emails from JSON into the same SQLite case history used by the demo so the UI can show prior alerts, related events, and memory signals before new inbox traffic arrives. It is a standalone import path: No AI. No outbound mail. No follow-ups. No escalations.

```bash
# Dry-run (preview only, no database changes):
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

# Commit (import into database):
python src/agent.py load-backlog --source json --path data/backlog_sample.json --commit
```

Input format (top-level array of records):

```json
[
  {
    "message_id": "backlog-001@example.test",
    "thread_id": null,
    "subject": "CAT1 Tests Reminder",
    "from_addr": "kpi-alerts@example.test",
    "to_addrs": ["ops@example.test"],
    "cc_addrs": ["manager@example.test"],
    "bcc_addrs": [],
    "reply_to": "kpi-alerts@example.test",
    "received_at": "2026-01-15T09:00:00",
    "body": "Client: Example Client 001\nBuilding: 123 Example Road, Example City\n..."
  }
]
```

Supported case types: `CAT1_COMPLIANCE`, `CAT5_COMPLIANCE`, `DATA_ABSENCE`, `MAINTENANCE_HOURS_SHORTFALL`, `MAJOR_WORK_OVERDUE`, `GOVERNMENT_DIRECTIVE`.
Filtering behavior: supported KPI records are imported, obvious non-KPI messages are rejected, and supported subjects with weak bodies or missing required fields go to review.
Safety: No AI. No outbound emails. No follow-ups. No escalations.
Reports are written to `data/backlog_runs/<timestamp>/` after each run.
Optional arguments: `--limit <N>` limits records processed. `--report-dir <PATH>` overrides the default report output directory.

The backlog loader itself accepts JSON only. The optional one-off `src/pst_to_backlog_json.py` helper can convert PST data to that JSON shape when `libpff-python` is installed, but PST import is not part of the runtime backlog loader.

## Reply Handling

Process a pasted reply for a case:

```bash
python src/agent.py reply --case-id <CASE_ID>
```

Replies are analyzed deterministically first. Completion claims, prompt-injection attempts, access blockers, and ambiguous replies are flagged for manual review as needed. Cases are never auto-closed.

## Memory Commands

```bash
python src/agent.py memory-rebuild
python src/agent.py patterns
python src/agent.py memory-report --case-id <CASE_ID>
```

The memory layer stores entities, observations, related-case links, and deterministic pattern flags. It does not use AI to decide whether a pattern exists.
Pattern signals are review-oriented indicators based on stored data, not proof of root cause. Mechanic/technician intelligence appears only when explicit mechanic or technician data exists in KPI emails or replies.

## AI Usage Rules

Normal commands run with AI disabled. To allow AI for ambiguous cases, opt in explicitly with a budget:

```bash
python src/agent.py demo --enable-ai --max-ai-calls 10
```

All model access goes through `src/ai_gateway.py`. Do not call the Claude client directly from product modules.

## Safety Model

- `DEMO_MODE=true` redirects all outbound mail to `DEMO_RECIPIENT_EMAIL`.
- Outbound records store both `intended_to` and `actual_to`.
- Normal case processing creates outbound drafts only; sending requires an explicit send helper/path.
- Placeholder SMTP credentials produce dry-run sends only if an explicit send path is invoked.
- Placeholder IMAP credentials disable inbox polling.
- Prompt-injection patterns are detected in inbound emails and replies.
- Cases are never closed automatically.
- The offline harness never touches `data/agent.db`.

## Project Layout

```text
src/
  agent.py              CLI entry point
  config.py             environment-backed settings
  constants.py          shared case/status/event/review string constants
  time_utils.py         UTC timestamp helpers preserving existing storage format
  observability.py      local JSON metrics snapshots and structured event logs
  runtime_options.py    per-run AI/outbound/follow-up switches
  content_safety.py     transport-agnostic sanitization and injection helpers
  database.py           SQLite schema and query helpers
  classifier.py         deterministic-first KPI classification
  extractor.py          deterministic extraction and outbound templates
  case_manager.py       main case pipeline and reply handling
  memory.py             entities, observations, links, pattern flags
  reply_analyzer.py     deterministic-first reply interpretation
  email_reader.py       optional IMAP polling
  email_sender.py       outbound drafts and explicit send helpers with demo guardrails
  followup.py           background follow-up processing
  demo_fixtures.py      synthetic offline fixture data
  demo_scale_harness.py safe offline demo validator
  web/                  Flask UI
data/
  sample_emails.json    committed sample demo alerts
tests/
  test_*.py             unit coverage for safety and core behavior
```

## Not Production Ready

This is an MVP/demo codebase. It is structured to show the workflow safely and repeatably, not to run unattended production operations.
