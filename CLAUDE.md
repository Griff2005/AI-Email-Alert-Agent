# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the project root. `src/` is added to `sys.path` at startup, so modules import each other without package prefixes. Use `.venv/bin/python` (the project venv) rather than the system `python3`.

```bash
# Setup
python3 -m pip install -r requirements.txt
cp .env.example .env        # placeholder values are safe for demo mode

# Run the demo (offline, no IMAP/SMTP/AI required)
.venv/bin/python src/agent.py demo

# Start the web UI at http://localhost:5000
.venv/bin/python src/agent.py run

# Offline safety harness (isolated DB, no AI, no network)
.venv/bin/python src/agent.py test-demo-scale --offline --emails 25 --seed 42

# Full test suite
.venv/bin/python -m unittest discover -s tests -p "test_*.py"

# Single test module
.venv/bin/python -m unittest tests.test_connection_discovery -v

# Single test case
.venv/bin/python -m unittest tests.test_backlog_loader.TestCommitMode.test_commit_creates_cases -v

# Backlog import (dry-run first, then commit)
.venv/bin/python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run
.venv/bin/python src/agent.py load-backlog --source json --path data/backlog_sample.json --commit
# Optional flags: --resume (skip already-imported), --report-detail full|summary

# Connection discovery (AI required; use --dry-run to preview)
.venv/bin/python src/agent.py discover-connections --max-ai-calls 5 --dry-run
.venv/bin/python src/agent.py merge-connection-hypotheses --dry-run

# Building groups
.venv/bin/python src/agent.py show-building-groups
.venv/bin/python src/agent.py rebuild-building-groups
.venv/bin/python src/agent.py generate-building-draft --group-id <id>

# Demo / operations tooling (DEMO_MODE=true required for build/reset)
.venv/bin/python src/agent.py build-demo-scenario
.venv/bin/python src/agent.py reset-demo-db --yes --database PATH
.venv/bin/python src/agent.py replay --path data/demo_replay.json
.venv/bin/python src/agent.py safety-check

# Memory and observability
.venv/bin/python src/agent.py memory-rebuild
.venv/bin/python src/agent.py memory-report
.venv/bin/python src/agent.py patterns
.venv/bin/python src/agent.py observability-report --output data/observability/latest.json
```

## Architecture

### The deterministic-first rule

The pipeline never calls AI unless explicitly opted in. AI is disabled by default in every `RuntimeOptions` instance and every `AiUsageConfig`. Product modules must **never** import `claude_client` directly — all model access goes through `ai_gateway.get_ai_gateway().call_json(...)` or `.call_text(...)`.

### Module roles

**Core pipeline:**
- **`agent.py`** — CLI only. Each `cmd_*` function owns one subcommand. It configures `RuntimeOptions`, the AI gateway, and `db.init_schema()`, then delegates to domain modules. `_configure_runtime_from_args()` is the shared setup helper.
- **`case_manager.py`** — Main pipeline orchestration. `process_email()` and `process_reply()` are the primary entry points; safety decisions belong here.
- **`classifier.py` / `extractor.py`** — Deterministic KPI classification and field extraction. No AI. `content_safety.py` runs `detect_injection()` before any AI path.
- **`backlog_loader.py`** — Standalone historical import. Zero AI, zero outbound, zero follow-ups. Subject must pass the six-type allowlist before body classification. `_process_record()` is the per-email processing core.

**Infrastructure:**
- **`config.py`** — Single `Config` singleton. All paths and credentials come from here. `.is_imap_configured()` / `.is_smtp_configured()` detect placeholder values and gate live network calls.
- **`runtime_options.py`** — Thread-local `RuntimeOptions` singleton. Controls AI calls, outbound drafts, and follow-ups for the current run.
- **`ai_gateway.py`** — `AiGateway` singleton (`get_ai_gateway()`). Enforces `AiUsageConfig` budgets with an O(1) running counter, maintains a JSON cache, and records per-call telemetry by purpose. Valid purposes are in `AI_PURPOSES`; use `"connection_discovery"` for discovery calls. In tests, install a mock via `gateway.set_test_transports(json_transport=fn, transport_mode="allowed")`.
- **`database.py`** — All SQL lives here. `init_schema()` creates tables idempotently. Uses thread-local connections protected by `_write_lock`. `update_case()` accepts only the fields in `_ALLOWED_CASE_UPDATE_FIELDS`. `get_recent_events()` is the helper for event queries.
- **`time_utils.py`** — Shared UTC helpers (`utc_now_iso()`, `utc_now_naive()`). All modules use this directly; do not add per-module wrappers.

**Building groups (Phase 2):**
- **`building_groups.py`** — Groups cases by building+contractor. `get_or_create_group()` is the upsert entry point. Uses `db._write_lock` internally for atomicity — callers must not hold other locks when calling it.
- **`group_email_builder.py`** — Builds consolidated outbound drafts for a group. `create_group_email_draft()` → `validate_draft_quality()` → `approve_group_email_draft()` is the approval flow. No AI.
- **`communication_planner.py`** — Plans outbound sequences across building groups; coordinates timing and case deduplication.

**Reply workflow (Phase 3):**
- **`reply_mapping.py`** — Maps incoming replies to cases via `attach_reply_to_group()`, `propose_reply_case_mappings()`, and `analyze_reply_completeness()`.
- **`response_requirements.py`** — Per-case-type data requirement definitions. `get_required_response_items()` returns the requirement schema; `build_case_requirements()` upserts rows in `case_data_requirements`. Returns DB rows with column `requirement_key` (not `key`).
- **`reply_analyzer.py`** — Parses reply content to extract completeness signals.

**AI discovery (Phase 4):**
- **`connection_discovery.py`** — AI-assisted hypothesis discovery. Read-only against cases; never mutates, sends, or escalates. Routes to `_run_small_case_discovery` (< threshold) or `_run_packetized_discovery` (large sets). The caller must configure and pre-arm the gateway before calling `run_discovery()`. Use bare `raise` (not `raise exc`) to preserve tracebacks.
- **`discovery_packets.py`** — Packet builders for large discovery runs. `build_all_supported_packets()` is the main entry point. Every packet asserts `unsupported_records_included == 0`. Handles context-window splitting via `_split_chunk_to_prompt_size()`. Cached AI responses do not count against `max_ai_calls`; only `"allowed"` and `"mocked"` statuses do.

**Memory and observability:**
- **`memory.py`** — Entities, observations, case links, and deterministic pattern flags. `detect_patterns_for_case()` runs 9 pattern types. Patterns are heuristics, not proof.
- **`observability.py`** — Read-only metrics snapshots + JSONL event log. Never enables AI, sends email, or mutates cases.

**Web UI (Phase 5):**
- **`web/app.py`** — Flask routes. Read-only except for schema initialization. AI is off by default (no explicit `RuntimeOptions` setup needed in routes). Routes: `/`, `/needs-attention`, `/replies`, `/connection-hypotheses`, `/observability`, `/settings`, `/jobs`, and building-group detail routes.

**Demo tooling:**
- **`demo_fixtures.py`** — Deterministic seed data used by `build-demo-scenario`. Fixed grouping keys derived from building+contractor name hashes ensure idempotent re-runs.
- **`demo_scale_harness.py`** — Offline large-scale test harness. Always uses an isolated temp DB under `data/test_runs/`; never touches `data/agent.db`.

### Six supported case types (hardcoded throughout)

`CAT1_COMPLIANCE`, `CAT5_COMPLIANCE`, `DATA_ABSENCE`, `MAINTENANCE_HOURS_SHORTFALL`, `MAJOR_WORK_OVERDUE`, `GOVERNMENT_DIRECTIVE` — defined in `constants.py` as both `SUPPORTED_CASE_TYPES` (list) and `SUPPORTED_CASE_TYPES_SET` (frozenset). Import `SUPPORTED_CASE_TYPES_SET` for membership checks; do not define local frozenset copies. Unsupported or UNKNOWN types must never appear in AI prompts, hypotheses, or backlog imports.

### Demo safety invariants — never break these

- `DEMO_MODE=true` redirects all outbound to `DEMO_RECIPIENT_EMAIL`. `outbound_messages` stores both `intended_to` and `actual_to`.
- Normal case processing creates **drafts only**. `send_draft()` / `create_and_send()` are explicit opt-in paths.
- Cases are **never auto-closed** from replies, follow-ups, or AI output.
- `reset-demo-db` requires `--yes` and a path whose filename contains `demo`, `test`, or `tmp` — the safety check inspects `path.name`, not the full path string.
- `build-demo-scenario` exits with code 1 if `DEMO_MODE=false`.
- The offline harness (`test-demo-scale --offline`) never touches `data/agent.db`.

## Test Patterns

Tests use `unittest.TestCase`. Some tests are skipped with `@unittest.skip("env-blocked: Flask test client + SQLite WAL deadlocks in Python 3.14 + macOS")` — this is a known environment constraint; do not remove the skips.

Standard setUp/tearDown for any test that touches the database or AI gateway:

```python
def setUp(self):
    self.temp_dir = tempfile.TemporaryDirectory()
    self._orig_db_path = config.DATABASE_PATH
    config.DATABASE_PATH = Path(self.temp_dir.name) / "test_agent.db"
    config.CLAUDE_CACHE_PATH = Path(self.temp_dir.name) / "claude_cache.json"
    config.AI_REPORT_PATH = Path(self.temp_dir.name) / "ai_usage.json"
    config.OBSERVABILITY_LOG_PATH = Path(self.temp_dir.name) / "events.jsonl"
    db.close_connection()
    db.init_schema()
    ai_gateway.reset_gateway()

def tearDown(self):
    ai_gateway.reset_gateway()
    db.close_connection()
    config.DATABASE_PATH = self._orig_db_path
    # restore other config overrides...
    self.temp_dir.cleanup()
```

`ai_gateway.reset_gateway()` is a module-level function — do not call it as `get_ai_gateway().reset_gateway()` (that method does not exist on the instance).

To mock AI in a test:

```python
gateway = get_ai_gateway()
gateway.reset()
gateway.configure(AiUsageConfig(enabled=True, max_calls=5, budget_mode="fail", config_version="test"))
gateway.set_test_transports(json_transport=lambda prompt, model: {"hypotheses": [...]}, transport_mode="allowed")
```

`transport_mode="allowed"` counts calls as live and they appear in `build_report()["total_ai_calls"]`. Use `transport_mode="mocked"` to mark them synthetic.
