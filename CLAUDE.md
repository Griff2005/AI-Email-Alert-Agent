# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the project root. `src/` is added to `sys.path` at startup by `agent.py`, so modules import each other without package prefixes.

```bash
# Setup
python3 -m pip install -r requirements.txt
cp .env.example .env        # placeholder values are safe for demo mode

# Run the demo (offline, no IMAP/SMTP required)
python src/agent.py demo

# Start the web UI at http://localhost:5000
python src/agent.py run

# Offline safety harness (isolated DB, no AI, no network)
python src/agent.py test-demo-scale --offline --emails 25 --seed 42

# Full test suite
python3 -m unittest discover -s tests -p "test_*.py"

# Single test module
python3 -m unittest tests.test_connection_discovery -v

# Single test case
python3 -m unittest tests.test_backlog_loader.TestCommitMode.test_commit_creates_cases -v

# Observability snapshot
python src/agent.py observability-report --output data/observability/latest.json

# Backlog import (dry-run first, then commit)
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run
python src/agent.py load-backlog --source json --path data/backlog_sample.json --commit

# Connection discovery (AI required; --max-ai-calls must be > 0)
python src/agent.py discover-connections --max-ai-calls 5 --dry-run
```

## Architecture

### The deterministic-first rule

The normal pipeline never calls AI unless explicitly opted in with `--enable-ai --max-ai-calls N`. AI is disabled by default in every `RuntimeOptions` instance and in every `AiUsageConfig`. Product modules must **never** import `claude_client` directly — all model access goes through `ai_gateway.get_ai_gateway().call_json(...)` or `.call_text(...)`.

### Module roles

- **`agent.py`** — CLI only. Each `cmd_*` function owns one subcommand. It configures `RuntimeOptions`, the AI gateway, and `db.init_schema()`, then delegates to domain modules.
- **`config.py`** — Single `Config` class, instantiated once as `config`. All paths and credentials come from here. Placeholder credential detection (`.is_imap_configured()`, `.is_smtp_configured()`) gates live network calls.
- **`runtime_options.py`** — Thread-local `RuntimeOptions` singleton (`runtime_options`). Controls whether AI calls, outbound drafts, and follow-ups are active for the current run.
- **`ai_gateway.py`** — `AiGateway` singleton (`get_ai_gateway()`). Enforces `AiUsageConfig` budgets, maintains a JSON cache, and records per-call telemetry. In tests, install a mock via `gateway.set_test_transports(json_transport=fn, transport_mode="allowed")` before calling any AI path.
- **`database.py`** — All SQL lives here. `init_schema()` creates tables idempotently. Uses thread-local connections protected by a write lock. `update_case()` accepts only the fields in `_ALLOWED_CASE_UPDATE_FIELDS` — do not bypass this guard.
- **`case_manager.py`** — Main pipeline orchestration. `process_email()` and `process_reply()` are the primary entry points; safety decisions should stay visible here.
- **`classifier.py` / `extractor.py`** — Deterministic KPI classification and field extraction. No AI.
- **`backlog_loader.py`** — Standalone historical import. Zero AI, zero outbound, zero follow-ups. Subject must match the hardcoded six-type allowlist before body classification.
- **`connection_discovery.py`** — AI-assisted hypothesis discovery. Read-only against cases; never mutates, sends, schedules, or escalates. The caller (CLI or test) must configure and pre-arm the gateway before calling `run_discovery()`.
- **`memory.py`** — Entities, observations, case links, deterministic pattern flags. Patterns are heuristics, not proof.
- **`observability.py`** — Read-only metrics snapshots + JSONL event log. Never enables AI, sends email, or mutates cases.
- **`web/app.py`** — Flask UI only. Routes are read-only except for schema initialization. `/connection-hypotheses.json` and `/observability.json` never trigger AI or mutate state.

### Six supported case types (hardcoded throughout)

`CAT1_COMPLIANCE`, `CAT5_COMPLIANCE`, `DATA_ABSENCE`, `MAINTENANCE_HOURS_SHORTFALL`, `MAJOR_WORK_OVERDUE`, `GOVERNMENT_DIRECTIVE` — defined in `constants.py` as `SUPPORTED_CASE_TYPES`. Unsupported or UNKNOWN types must never appear in AI prompts, hypotheses, or backlog imports.

### Demo safety invariants — never break these

- `DEMO_MODE=true` redirects all outbound to `DEMO_RECIPIENT_EMAIL`. `outbound_messages` stores both `intended_to` and `actual_to`.
- Normal case processing creates **drafts only**. `send_draft()` / `create_and_send()` are explicit opt-in paths.
- Cases are **never auto-closed** from replies, follow-ups, or AI output.
- Prompt-injection detections create manual-review pressure, not automatic action.
- The offline harness (`test-demo-scale --offline`) never touches `data/agent.db`; it uses an isolated temp DB under `data/test_runs/`.

## Test Patterns

Tests use `unittest.TestCase`. The standard setUp/tearDown pattern for any test that touches the database or AI gateway:

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

To mock AI in a test without blocking it:

```python
gateway = get_ai_gateway()
gateway.reset()
gateway.configure(AiUsageConfig(enabled=True, max_calls=5, budget_mode="fail", config_version="test"))
gateway.set_test_transports(json_transport=lambda prompt, model: {"hypotheses": [...]}, transport_mode="allowed")
```

`transport_mode="allowed"` means calls are counted as live (`live_call=True`) and appear in `build_report()["total_ai_calls"]`. Use `transport_mode="mocked"` to mark them as synthetic.
