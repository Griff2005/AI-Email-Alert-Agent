# Data Model, Backend, and API Spec

## 1. Purpose

This spec defines backend modules, tables, routes, CLI commands, invariants, and migration rules for the next product phase.

## 2. New Tables Summary

Required new tables:

- `building_issue_groups`
- `building_issue_group_cases`
- `building_group_emails`
- `communication_queue`
- `case_data_requirements`
- `reply_case_mappings`
- `connection_discovery_runs`
- `connection_discovery_packets`
- `rule_candidates`
- `job_runs`

## 3. Table Definitions

### `building_issue_groups`

```sql
CREATE TABLE IF NOT EXISTS building_issue_groups (
    group_id TEXT PRIMARY KEY,
    grouping_key TEXT UNIQUE NOT NULL,
    building TEXT NOT NULL,
    normalized_building TEXT NOT NULL,
    contractor TEXT NOT NULL,
    normalized_contractor TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    health_status TEXT,
    last_email_sent_at TEXT,
    next_email_allowed_at TEXT,
    last_response_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### `building_issue_group_cases`

```sql
CREATE TABLE IF NOT EXISTS building_issue_group_cases (
    group_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    included_in_email_at TEXT,
    new_since_last_email INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (group_id, case_id)
);
```

### `building_group_emails`

```sql
CREATE TABLE IF NOT EXISTS building_group_emails (
    group_email_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    outbound_msg_id TEXT,
    email_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft_generated',
    summary_json TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
```

### `communication_queue`

```sql
CREATE TABLE IF NOT EXISTS communication_queue (
    queue_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    case_id TEXT,
    queue_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### `case_data_requirements`

```sql
CREATE TABLE IF NOT EXISTS case_data_requirements (
    requirement_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    requirement_key TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'missing',
    source TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(case_id, requirement_key)
);
```

### `reply_case_mappings`

```sql
CREATE TABLE IF NOT EXISTS reply_case_mappings (
    mapping_id TEXT PRIMARY KEY,
    reply_email_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    group_id TEXT,
    mapping_source TEXT NOT NULL,
    confidence TEXT,
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at TEXT NOT NULL
);
```

### `connection_discovery_runs`

```sql
CREATE TABLE IF NOT EXISTS connection_discovery_runs (
    run_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    max_ai_calls INTEGER NOT NULL,
    ai_calls_used INTEGER DEFAULT 0,
    packets_created INTEGER DEFAULT 0,
    packets_analyzed INTEGER DEFAULT 0,
    hypotheses_created INTEGER DEFAULT 0,
    hypotheses_rejected INTEGER DEFAULT 0,
    unsupported_records_included INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    config_json TEXT
);
```

### `connection_discovery_packets`

```sql
CREATE TABLE IF NOT EXISTS connection_discovery_packets (
    packet_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    packet_type TEXT NOT NULL,
    entity_type TEXT,
    entity_value TEXT,
    case_count INTEGER DEFAULT 0,
    pattern_count INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    ai_call_used INTEGER DEFAULT 0,
    hypotheses_created INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT
);
```

### `rule_candidates`

```sql
CREATE TABLE IF NOT EXISTS rule_candidates (
    rule_candidate_id TEXT PRIMARY KEY,
    source_hypothesis_id TEXT,
    rule_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    proposed_condition_json TEXT NOT NULL,
    proposed_action_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    review_notes TEXT
);
```

### `job_runs`

```sql
CREATE TABLE IF NOT EXISTS job_runs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    progress_current INTEGER DEFAULT 0,
    progress_total INTEGER DEFAULT 0,
    summary_json TEXT,
    error TEXT
);
```

## 4. Backend Modules

### `building_groups.py`

Core functions:

```python
def normalize_group_value(value: str) -> str: ...
def build_grouping_key(building: str, contractor: str) -> str: ...
def get_or_create_group(building: str, contractor: str) -> str: ...
def attach_case_to_group(case_id: str) -> str | None: ...
def rebuild_all_groups() -> dict: ...
def get_group_summary(group_id: str) -> dict: ...
def list_building_groups(filters: dict | None = None) -> list[dict]: ...
```

### `response_requirements.py`

```python
def get_required_response_items(case_type: str) -> list[str]: ...
def build_case_requirements(case_id: str) -> list[dict]: ...
def calculate_case_completeness(case_id: str) -> dict: ...
def validate_required_response_in_email(case_ids: list[str], body: str) -> list[str]: ...
```

### `communication_planner.py`

```python
def evaluate_group_communication_status(group_id: str) -> dict: ...
def list_groups_ready_for_draft() -> list[dict]: ...
def suppress_group_communication(group_id: str, reason: str) -> None: ...
```

### `group_email_builder.py`

```python
def build_consolidated_email(group_id: str) -> dict: ...
def build_followup_email(group_id: str) -> dict: ...
def build_clarification_email(case_ids: list[str]) -> dict: ...
def create_group_email_draft(group_id: str) -> str: ...
def validate_draft_quality(draft: dict) -> dict: ...
```

### `reply_mapping.py`

```python
def attach_reply_to_group(reply_email_id: str, group_id: str) -> None: ...
def propose_reply_case_mappings(reply_email_id: str) -> list[dict]: ...
def save_reply_case_mapping(reply_email_id: str, case_id: str, source: str) -> None: ...
def analyze_reply_completeness(reply_email_id: str, case_id: str) -> dict: ...
```

### `discovery_packets.py`

```python
def build_pattern_packets(limit: int | None = None) -> list[dict]: ...
def build_building_group_packets(limit: int | None = None) -> list[dict]: ...
def build_entity_packets(entity_type: str, limit: int | None = None) -> list[dict]: ...
def split_oversized_packet(packet: dict, max_chars: int) -> list[dict]: ...
```

### `jobs.py`

```python
def start_job(job_type: str, total: int | None = None) -> str: ...
def update_job(job_id: str, current: int, summary: dict | None = None) -> None: ...
def complete_job(job_id: str, summary: dict | None = None) -> None: ...
def fail_job(job_id: str, error: str) -> None: ...
```

## 5. Web Routes

```text
GET  /building-groups
GET  /building-groups/<group_id>
POST /building-groups/<group_id>/generate-draft
POST /building-groups/<group_id>/mark-reviewed

GET  /drafts
GET  /drafts/<draft_id>
POST /drafts/<draft_id>/approve
POST /drafts/<draft_id>/reject
POST /drafts/<draft_id>/send

GET  /replies
GET  /replies/<email_id>
POST /replies/<email_id>/map-case

GET  /connection-hypotheses
GET  /connection-hypotheses/<hypothesis_id>
POST /connection-hypotheses/<hypothesis_id>/accept
POST /connection-hypotheses/<hypothesis_id>/reject
POST /connection-hypotheses/<hypothesis_id>/convert-to-rule-candidate
GET  /connection-discovery-runs

GET /observability
GET /jobs
GET /settings
```

## 6. CLI Commands

```powershell
python srcgent.py rebuild-building-groups
python srcgent.py show-building-groups
python srcgent.py generate-building-draft --group-id GROUP_ID
python srcgent.py discover-connections --scope patterns --max-ai-calls 20
python srcgent.py discover-connections --scope building-groups --max-ai-calls 25
python srcgent.py discover-connections --scope all-supported --packet-by entity --max-ai-calls 100
python srcgent.py merge-connection-hypotheses --max-ai-calls 10
python srcgent.py safety-check
python srcgent.py reset-demo-db
python srcgent.py build-demo-scenario
python srcgent.py replay --path data\demo_replay.json
```

## 7. Invariants

1. Every building group has building and contractor.
2. Every group-child link points to an existing case.
3. AI hypotheses never mutate cases.
4. Group emails preserve intended and actual recipients.
5. Backlog mode never sends email.
6. Connection discovery never includes unsupported KPI evidence.
7. Case closure requires human action.
8. Drafts pass quality checks before approval/send.
9. Demo actual recipient equals demo recipient.
10. Prompt size guard blocks oversized AI calls.

## 8. Migration Approach

For MVP:

- use `CREATE TABLE IF NOT EXISTS`,
- avoid destructive schema changes,
- avoid renaming existing columns,
- add compatibility checks before `ALTER TABLE`,
- defer formal migrations until production planning.
