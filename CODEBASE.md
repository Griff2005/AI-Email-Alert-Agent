# Codebase Reference

This repository is a deterministic-first demo agent for elevator KPI alert triage. The code should stay simple: core product behavior in focused modules, demo safety as a first-class concern, and AI behind one explicit gateway.

## Project Structure

```text
src/
  agent.py              CLI entry point and command wiring
  config.py             .env loading and static configuration
  runtime_options.py    run-scoped AI/outbound/follow-up switches
  database.py           SQLite schema and query helpers
  classifier.py         supported KPI family classification
  extractor.py          field extraction, grouping keys, outbound templates
  case_manager.py       email and reply orchestration
  reply_analyzer.py     deterministic reply interpretation
  memory.py             entity memory, observations, links, pattern flags
  email_reader.py       optional IMAP polling
  email_sender.py       outbound draft/send path with demo guardrails
  followup.py           overdue follow-up scheduler logic
  demo_fixtures.py      small synthetic offline dataset
  demo_scale_harness.py offline safety/demo validator
  web/
    app.py              Flask routes
    templates/          case list, details, reviews, events, patterns
data/
  sample_emails.json    committed sample emails
tests/
  test_demo_scale.py
  test_ai_usage.py
  test_memory.py
```

Generated files such as SQLite databases, harness reports, AI usage reports, caches, and `__pycache__` are ignored.

## Main Data Flow

1. `agent.py` loads a sample or inbound email and stores it in `emails`.
2. `case_manager.process_email()` rejects obvious noise with `classifier.quick_filter()`.
3. `classifier.classify_email()` classifies the six supported KPI families deterministically first.
4. `extractor.extract_fields_with_meta()` extracts required fields from known templates.
5. `extractor.generate_grouping_key()` creates the deduplication key.
6. `database.get_case_by_grouping_key()` decides whether to create or update a case.
7. `case_manager` stores extracted fields, case events, follow-up deadline, and memory observations.
8. `memory.detect_patterns_for_case()` updates deterministic pattern flags and related-case links.
9. `extractor.generate_email_body()` produces a deterministic outbound template.
10. `email_sender.create_and_send()` creates a draft and, when configured, sends only through demo-safe routing.

Reply handling uses `case_manager.process_reply()` and `reply_analyzer.analyze_reply()`. It records reply events and manual reviews but never closes a case automatically.

## Core Modules

- `classifier.py`: supported case type matching plus prompt-injection detection. Unsupported alert families should resolve to `UNKNOWN` unless intentionally added to the MVP.
- `extractor.py`: deterministic field parsing, date normalization, grouping key generation, and outbound templates.
- `case_manager.py`: the main orchestration point. Keep safety decisions visible here.
- `database.py`: owns schema creation and all SQL helpers. Prefer additive schema changes and avoid table/column renames without a migration plan.
- `memory.py`: stores entities, observations, related cases, and deterministic pattern flags. Pattern flags should be explainable from stored evidence.
- `ai_gateway.py`: the only approved model access path. It enforces enablement, budgets, cache accounting, and reports.
- `email_sender.py`: preserves `intended_to` vs `actual_to` and demo recipient override.
- `followup.py`: idempotent follow-up generation and escalation review creation.
- `web/app.py`: Flask case list, case detail, review queue, event feed, and pattern views.

## Safety-Critical Behavior

Preserve these rules:

- AI is disabled by default.
- Live AI requires explicit `--enable-ai` and a call budget.
- Product modules do not call `claude_client` directly except through `ai_gateway.py`.
- `DEMO_MODE=true` redirects outbound mail to `DEMO_RECIPIENT_EMAIL`.
- `outbound_messages` keeps `intended_to` and `actual_to`.
- Placeholder SMTP credentials produce dry-run sends.
- Placeholder IMAP credentials disable polling.
- The offline harness blocks SMTP and IMAP.
- Cases are never auto-closed from replies or follow-ups.
- Prompt-injection content creates manual-review pressure, not automatic action.

## SQLite Tables

Operational and audit tables:

- `emails`
- `cases`
- `case_events`
- `extracted_fields`
- `outbound_messages`
- `followups`
- `followup_actions`
- `manual_reviews`

Memory tables:

- `entities`
- `entity_aliases`
- `observations`
- `case_links`
- `pattern_flags`

`cases.grouping_key` is unique and is the deduplication gate.

## Offline Harness

Run:

```bash
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
```

The harness is intentionally offline-only. It:

- creates `data/test_runs/<timestamp>/test_agent.db`
- blocks `smtplib.SMTP`, `smtplib.SMTP_SSL`, and `imaplib.IMAP4_SSL`
- configures placeholder SMTP/IMAP credentials
- disables AI and writes an AI usage report with zero live calls
- processes synthetic supported KPI alerts
- validates case creation, duplicate grouping, replies, prompt-injection handling, outbound recipient override, memory pattern creation, and Flask smoke when Flask is installed
- writes concise `report.json`, `report.md`, and `harness.log`

Optional follow-up simulation:

```bash
python src/agent.py test-demo-scale --offline --emails 50 --seed 42 --enable-followups
```

The harness is not a production audit. Keep it focused on whether the demo path is safe and working.

## Known Limitations

- The parser supports the current six demo KPI families only.
- Memory patterns are deterministic heuristics and should be presented as signals, not proof.
- The Flask UI is a demo interface, not an authenticated production app.
- The database layer is intentionally simple SQLite and should not be treated as a multi-tenant production store.
- AI-assisted ambiguous handling exists, but normal validation should remain offline and deterministic.
