# Intelligence, Memory, and Connection Discovery Spec

## 1. Purpose

This spec defines how the intelligence layer should scale beyond small AI discovery runs.

The goal is to run connection discovery across all supported data and deterministic patterns without timeouts, excessive usage, or unsupported-email analysis.

## 2. Core Rule

Do not run AI over all raw emails.

Run AI over structured, supported, evidence-backed packets.

```text
Supported case data + memory + patterns
    ↓
Evidence packets
    ↓
AI hypothesis generation
    ↓
Validation
    ↓
Merge/dedupe
    ↓
Human review
```

## 3. Allowed Data

AI discovery may use:

- supported cases,
- extracted fields linked to supported cases,
- observations linked to supported cases,
- case links between supported cases,
- deterministic pattern flags,
- building issue groups,
- manual review categories tied to supported cases,
- communication metadata as context only.

AI discovery must not use:

- unsupported KPI email content,
- unsupported KPI report files,
- unsupported KPI family names as evidence,
- rejected non-KPI records,
- raw unsupported backlog bodies,
- unsupported email text.

## 4. Discovery Modes

### Small Case Mode

```powershell
python srcgent.py discover-connections --limit 50 --max-ai-calls 5
```

Good for demos and small runs.

### Pattern Mode

```powershell
python srcgent.py discover-connections --scope patterns --max-ai-calls 20
```

Uses deterministic pattern flags as AI seeds.

### Building Group Mode

```powershell
python srcgent.py discover-connections --scope building-groups --max-ai-calls 25
```

Analyzes building/contractor groups.

### Entity Mode

```powershell
python srcgent.py discover-connections --scope entities --entity-type building --max-ai-calls 50
```

Analyzes buildings, contractors, devices, or clients.

### Full Supported Mode

```powershell
python srcgent.py discover-connections --scope all-supported --packet-by entity --max-ai-calls 100
```

Runs across all supported data using packets.

### Merge Mode

```powershell
python srcgent.py merge-connection-hypotheses --max-ai-calls 10
```

Merges similar hypotheses.

## 5. Packet Types

| Packet Type | Purpose |
|---|---|
| `case_chunk` | Small batch of supported cases |
| `pattern_flag` | One deterministic pattern and evidence |
| `building_group` | One building/contractor group |
| `building_entity` | Supported cases for a building |
| `contractor_entity` | Supported cases for a contractor |
| `device_entity` | Supported cases for a device |
| `case_type_window` | Case type within a time window |
| `manual_review_cluster` | Related review issues |

## 6. Packet Limits

Recommended defaults:

```text
max_cases_per_packet = 25
max_patterns_per_packet = 5
max_events_per_case = 5
max_fields_per_case = 20
max_observations_per_packet = 50
max_prompt_chars = 40000
```

If a prompt exceeds the limit, split the packet or skip with a recorded reason.

## 7. Packet Schema

```json
{
  "packet_id": "packet-123",
  "packet_type": "building_group",
  "scope": {
    "supported_case_types_only": true,
    "unsupported_emails_excluded": true
  },
  "entity": {
    "entity_type": "building_contractor_group",
    "building": "123 Example Road",
    "contractor": "Example Elevator Co."
  },
  "cases": [],
  "pattern_flags": [],
  "observations": [],
  "known_links": [],
  "manual_review_summary": {},
  "communication_summary": {}
}
```

## 8. Pattern-Based Discovery

Pattern flags are ideal AI seeds because they are already deterministic and evidence-backed.

Prompt goal:

```text
Given this deterministic pattern and its supporting cases, identify whether there is a broader reviewable connection across case types, contractors, devices, buildings, or timelines. Do not repeat the obvious pattern unless there is additional insight.
```

Example:

- Deterministic pattern: repeated data absence at Building A.
- AI hypothesis: repeated data absence overlaps with maintenance-hour shortfall under the same contractor, suggesting a possible reporting process issue.

## 9. Building-Group Discovery

Run on building groups after they exist.

Input:

- building,
- contractor,
- open child cases,
- recent closed cases,
- pattern flags,
- manual review categories,
- previous communications,
- missing data checklist.

Output:

- group-level hypotheses,
- possible cross-case relationships,
- internal review notes.

Do not include these hypotheses in external emails by default.

## 10. Discovery Run Tracking

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

## 11. Hypothesis Types

- `building_contractor_pattern`
- `cross_case_type_relationship`
- `device_recurrence`
- `contractor_reporting_pattern`
- `timeline_sequence`
- `manual_review_cluster`
- `compliance_dependency`
- `data_quality_dependency`
- `communication_response_pattern`
- `evidence_gap_pattern`

## 12. Validation Rules

A hypothesis is valid only if:

1. all case IDs exist,
2. all case IDs are supported case types,
3. referenced pattern IDs exist,
4. no unsupported KPI evidence is referenced,
5. summary avoids blame/conclusion language,
6. recommendation does not tell system to send/escalate/close,
7. confidence/risk values are valid,
8. reasoning is present,
9. recommended human review is present.

## 13. Merge and Dedupe

Deterministic duplicate key:

```text
hypothesis_type + building + contractor + device + case_types + overlapping_case_ids
```

Merge when:

- same entity,
- same hypothesis type,
- overlapping cases,
- similar summary.

Confidence can increase when multiple packets produce similar hypotheses.

## 14. Rule Candidate Workflow

```text
AI hypothesis
    ↓
Human accepts
    ↓
Rule candidate created
    ↓
Developer/business review
    ↓
Deterministic rule implemented
```

## 15. Acceptance Criteria

1. Pattern-scope discovery exists.
2. Building-group discovery exists after group implementation.
3. Full supported discovery is packetized.
4. No unsupported KPI data is included.
5. Prompt size guard prevents timeouts.
6. Runs and packets are tracked.
7. Hypotheses are validated.
8. Duplicates are merged/grouped.
9. Humans can accept/reject hypotheses.
10. Accepted hypotheses can become rule candidates.
