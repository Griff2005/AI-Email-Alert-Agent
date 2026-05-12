# Codebase Reference

This repository is a deterministic-first demo agent for elevator KPI alert triage. The code should stay simple: core product behavior in focused modules, demo safety as a first-class concern, and AI behind one explicit gateway.

## Project Structure

```text
src/
  agent.py              CLI entry point and command wiring
  config.py             .env loading and static configuration
  constants.py          small shared case/status/event/review string constants
  time_utils.py         UTC timestamp helpers for existing SQLite text fields
  observability.py      local metrics snapshots and JSONL operational events
  runtime_options.py    run-scoped AI/outbound/follow-up switches
  content_safety.py     sanitization and prompt-injection helpers
  database.py           SQLite schema and query helpers
  backlog_loader.py     standalone backlog import workflow
  connection_discovery.py  AI-assisted connection hypothesis discovery (read-only; never mutates cases)
  pst_to_backlog_json.py one-off PST-to-JSON helper for staged backlog files
  classifier.py         supported KPI family classification
  extractor.py          field extraction, grouping keys, outbound templates
  case_manager.py       email and reply orchestration
  reply_analyzer.py     deterministic reply interpretation
  memory.py             entity memory, observations, links, pattern flags
  ai_gateway.py         model access gateway with budget enforcement
  claude_client.py      low-level Claude transport, re-exporting legacy safety helpers
  email_reader.py       optional IMAP polling
  email_sender.py       outbound drafts and explicit send helpers with demo guardrails
  followup.py           overdue follow-up scheduler logic
  demo_fixtures.py      small synthetic offline dataset
  demo_scale_harness.py offline safety/demo validator
  web/
    app.py              Flask routes
    templates/          case list, details, reviews, events, patterns
data/
  sample_emails.json    committed sample demo emails
  backlog_sample.json   committed sample backlog import records
docs/
  *.docx                project discovery and design documents
tests/
  test_ai_usage.py
  test_backlog_loader.py
  test_case_manager_cleanup.py
  test_classifier_extractor_cleanup.py
  test_connection_discovery.py
  test_content_safety.py
  test_database_reporting.py
  test_demo_scale.py
  test_memory.py
  test_observability.py
  test_pst_converter.py
  test_web_memory_ui.py
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
10. `email_sender.create_draft()` creates an outbound draft only. Sending is not part of the normal new-case path.

Reply handling uses `case_manager.process_reply()` and `reply_analyzer.analyze_reply()`. It records reply events and manual reviews but never closes a case automatically.

Observability is read-only against the pipeline state after ensuring the SQLite schema exists:

1. `observability.build_metrics_snapshot()` reads existing SQLite audit tables and current `ai_gateway` counters.
2. `agent.py observability-report` prints the snapshot and can write it to JSON with `--output`.
3. `web/app.py` exposes the same snapshot at `/observability.json`.
4. `observability.append_structured_event()` writes local JSONL breadcrumbs to `OBSERVABILITY_LOG_PATH` for command-level operational events.
5. Snapshot AI usage is compact by default and omits full per-call records.
6. No observability path enables AI, sends email, polls IMAP, schedules follow-ups, or mutates cases.

The backlog import flow is separate from the main pipeline:

1. `agent.py load-backlog` calls `backlog_loader.load_backlog()`.
2. Records are normalized and filtered through a subject gate (must match a known KPI pattern).
3. Body signatures are validated per case type.
4. Accepted records are inserted into `emails` (marked processed immediately), `cases`, `extracted_fields`, `case_events`, and `memory` tables.
5. Reports are written to `data/backlog_runs/<timestamp>/`.
6. No outbound messages, follow-ups, or AI calls are made.

## Core Modules

- `classifier.py`: supported case type matching plus prompt-injection detection. Unsupported alert families should resolve to `UNKNOWN` unless intentionally added to the MVP.
- `constants.py`: shared strings for the six demo case types, common statuses, event labels, and safety-critical review reasons.
- `time_utils.py`: centralized UTC timestamp helpers. Database-facing helpers preserve the existing naive ISO text format.
- `observability.py`: local in-repo observability foundation. Builds JSON metrics snapshots for ingest volume, case/review/event counts, outbound/follow-up status, latency, current-process AI usage, and demo safety checks. Writes structured JSONL operational events without external telemetry dependencies.
- `connection_discovery.py`: optional AI-assisted connection hypothesis discovery. Main entry point: `run_discovery(max_ai_calls, limit, building, case_type_filter, dry_run)`. Reads supported cases only — never includes UNKNOWN or unsupported types. Validates each AI-produced hypothesis before storage (confidence enum, risk_level enum, non-empty case IDs in the supported set, no prohibited action language). Stores accepted hypotheses as `proposed` items only. Never modifies cases, sends emails, schedules follow-ups, escalates, or closes cases. The caller must pre-configure the AI gateway before calling `run_discovery`. Dry-run mode prints hypotheses without writing to the database. Writes a JSONL observability event with `unsupported_kpi_included=0`.
- `backlog_loader.py`: standalone backlog import workflow for staged historical KPI emails. Main entry point: `load_backlog(source, path, dry_run, limit, report_dir)`. Validates records through a subject-pattern gate (hardcoded six-type allowlist) before body classification, so classifier expansion cannot silently widen import scope. Dry-run mode previews without touching the database. Imported emails are immediately marked processed. Report output: `data/backlog_runs/<timestamp>/`. Dependencies: `database.py`, deterministic paths in `classifier.py` and `extractor.py`, and `memory.py`.
- `pst_to_backlog_json.py`: optional one-off PST-to-JSON utility. It imports `libpff-python` lazily so tests and core runtime imports do not fail when PST tooling is absent.
- `extractor.py`: deterministic field parsing, date normalization, grouping key generation, and outbound templates.
- `case_manager.py`: the main orchestration point. Keep safety decisions visible here.
- `database.py`: owns schema creation and all SQL helpers. `update_case()` accepts only known mutable columns. Prefer additive schema changes and avoid table/column renames without a migration plan.
- `memory.py`: stores entities, observations, related cases, and deterministic pattern flags. Pattern flags should be explainable from stored evidence.
- `ai_gateway.py`: the only approved model access path. It enforces enablement, budgets, cache accounting, and reports. Product modules must not call `claude_client` directly.
- `content_safety.py`: transport-agnostic prompt-injection detection and email sanitization. `claude_client.py` re-exports these helpers for compatibility, but product modules should import from `content_safety.py`.
- `claude_client.py`: low-level Claude CLI transport used by `ai_gateway.py`.
- `email_sender.py`: preserves `intended_to` vs `actual_to` and demo recipient override. Normal new-case processing creates drafts only; `send_draft()` and `create_and_send()` are explicit send helpers.
- `followup.py`: idempotent follow-up generation and escalation review creation.
- `web/app.py`: Flask case list, case detail, review queue, event feed, and Memory / Intelligence views. The UI renders deterministic pattern signals, related cases, entity connections, observations, and evidence from stored memory records. Exposes a read-only `/connection-hypotheses.json` endpoint that returns proposed connection hypotheses without triggering AI, mutating cases, or sending email.

## Safety-Critical Behavior

Preserve these rules:

- AI is disabled by default.
- Live AI requires explicit `--enable-ai` and a call budget.
- Product modules do not call or import `claude_client` directly except through `ai_gateway.py`.
- `DEMO_MODE=true` redirects outbound mail to `DEMO_RECIPIENT_EMAIL`.
- `outbound_messages` keeps `intended_to` and `actual_to`.
- Normal case processing creates outbound drafts only when outbound generation is enabled.
- Placeholder SMTP credentials produce dry-run sends only if an explicit send path is invoked.
- Placeholder IMAP credentials disable polling.
- The offline harness blocks SMTP and IMAP.
- Cases are never auto-closed from replies or follow-ups.
- Prompt-injection content creates manual-review pressure, not automatic action.
- Observability commands and routes are read-only against case state except for schema initialization and optional local JSON/JSONL report files.

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

Connection discovery tables:

- `connection_hypotheses`
- `connection_hypothesis_cases`

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

## Local Observability

Run:

```bash
python src/agent.py observability-report
python src/agent.py observability-report --output data/observability/latest.json
```

The Flask app exposes the same data at:

```text
/observability.json
```

The snapshot is built from existing SQLite tables and current-process AI gateway counters. It reports dashboard totals, email pipeline counts, case counts by status/type, open manual review reasons, case event counts, outbound/follow-up status counts, email-to-case-created age/latency from available audit timestamps, compact AI usage, and demo safety checks.

Structured local events are JSONL records written to `OBSERVABILITY_LOG_PATH` (`data/observability/events.jsonl` by default). Current command-level events cover selected CLI boundaries such as ingest, demo, backlog completion, and observability report writes. This is intentionally not a production monitoring stack: no external collector, no Prometheus/Grafana deployment, no alert manager, and no background worker were added.

## Backlog Loading Mode

Run a dry-run preview (no database changes):

```bash
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run
```

Commit the import:

```bash
python src/agent.py load-backlog --source json --path data/backlog_sample.json --commit
```

The backlog importer is intentionally restricted:

- Accepts JSON source only.
- Imports only the six supported demo KPI case types (hardcoded, not derived from the classifier).
- Subject must match a known KPI pattern; body-only matches are routed to review.
- Makes zero model calls, creates no outbound messages, and schedules no follow-ups.
- Dry-run writes no database rows.
- Imported emails are marked processed immediately so they do not appear as pipeline work.
- Review candidates appear in `review_candidates.json` only — they are not written to the live review queue.
- Reports written to `data/backlog_runs/<timestamp>/`: `report.json`, `report.md`, `rejected.json`, `review_candidates.json`, `recipient_summary.json`.
- PST conversion, when needed, is handled by the optional `src/pst_to_backlog_json.py` utility before invoking backlog mode. The backlog loader remains JSON-only.

## Connection Discovery

Run a dry-run preview (no database writes):

```bash
python src/agent.py discover-connections --max-ai-calls 5 --dry-run
```

Store proposed hypotheses:

```bash
python src/agent.py discover-connections --max-ai-calls 5
```

The discovery command is intentionally restricted:

- Requires an explicit `--max-ai-calls N` budget (fails loudly if missing or zero).
- Analyzes only the six supported KPI case types — UNKNOWN and unsupported types are excluded at every layer.
- Never modifies cases, sends emails, schedules follow-ups, escalates, or closes cases.
- Validates all AI-produced hypotheses before storage (confidence enum, risk_level enum, non-empty supported case IDs, no prohibited action language).
- Stores accepted hypotheses as `status='proposed'` in `connection_hypotheses` + `connection_hypothesis_cases`.
- Dry-run prints hypotheses without touching the database.
- Writes a JSONL observability event with `unsupported_kpi_included=0`.
- Proposed hypotheses are readable at `/connection-hypotheses.json` (read-only Flask endpoint).

## Known Limitations

- The parser supports the current six demo KPI families only.
- Memory patterns are deterministic heuristics and should be presented as signals, not proof.
- Mechanic intelligence is shown only when explicit mechanic or technician observations exist.
- The Flask UI is a demo interface, not an authenticated production app.
- The database layer is intentionally simple SQLite and should not be treated as a multi-tenant production store.
- AI-assisted ambiguous handling exists, but normal validation should remain offline and deterministic.
- Observability is local and read-only. Production monitoring, alerting, retention, and incident response still need a separate design before live use.
