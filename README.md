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
| `IMAP_HOST` / `AGENT_EMAIL` / `AGENT_EMAIL_PASSWORD` | Optional inbox polling. Placeholder values disable IMAP. |
| `SMTP_HOST` / `SMTP_PORT` | Optional SMTP. Placeholder values produce dry-run sends. |
| `CLAUDE_MODEL` | Model used only when AI is explicitly enabled. |

If your shell does not have `python`, use `python3` in the commands below.

## Run The Demo

Process the sample KPI emails:

```bash
python src/agent.py demo
```

This initializes SQLite, loads `data/sample_emails.json`, classifies and extracts deterministic fields, creates or updates cases, records memory observations, and creates safe outbound draft/dry-run messages.

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
- Placeholder SMTP credentials produce dry-run sends.
- Placeholder IMAP credentials disable inbox polling.
- Prompt-injection patterns are detected in inbound emails and replies.
- Cases are never closed automatically.
- The offline harness never touches `data/agent.db`.

## Project Layout

```text
src/
  agent.py              CLI entry point
  config.py             environment-backed settings
  runtime_options.py    per-run AI/outbound/follow-up switches
  database.py           SQLite schema and query helpers
  classifier.py         deterministic-first KPI classification
  extractor.py          deterministic extraction and outbound templates
  case_manager.py       main case pipeline and reply handling
  memory.py             entities, observations, links, pattern flags
  reply_analyzer.py     deterministic-first reply interpretation
  email_reader.py       optional IMAP polling
  email_sender.py       SMTP/dry-run outbound with demo guardrails
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
