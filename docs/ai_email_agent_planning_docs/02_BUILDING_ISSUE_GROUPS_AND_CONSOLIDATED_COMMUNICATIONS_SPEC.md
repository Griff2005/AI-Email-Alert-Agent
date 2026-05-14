# Building Issue Groups and Consolidated Communications Spec

## 1. Purpose

This spec defines the building/contractor grouping layer and consolidated communication workflow. This is the most important next product change.

## 2. Problem

The current case pipeline can process alerts and create/update individual cases. However, one-alert-one-email is not operationally useful. If several KPI issues exist for the same building and contractor, the agent should create one consolidated building-level email.

## 3. Target Model

```text
Alert → Individual Case → Building Issue Group → Consolidated Draft → Reply/Follow-up
```

## 4. Building Issue Group Definition

A Building Issue Group represents all active supported cases sharing:

```text
same normalized building + same normalized contractor
```

Suggested grouping key:

```text
building_group::{normalized_building}::{normalized_contractor}
```

## 5. Individual Case vs Group

Individual cases should continue to track specific issues:

- Data Absence
- Maintenance Hours Shortfall
- Major Work Overdue
- CAT1 Compliance
- CAT5 Compliance
- Government Directive

Building groups should track communication and coordination:

- all open child cases,
- new issues since last email,
- last consolidated draft/email,
- latest replies,
- manual review blockers,
- group status,
- group health.

## 6. Group Statuses

| Status | Meaning |
|---|---|
| `open` | Group has active child cases |
| `pending_communication` | One or more cases need to be communicated |
| `draft_generated` | Consolidated draft exists |
| `awaiting_review` | Human review is needed before send |
| `email_sent` | Consolidated email has been sent |
| `awaiting_response` | Waiting for contractor/client reply |
| `updated_since_last_email` | New cases arrived after last email |
| `ready_for_followup` | Follow-up window reached |
| `closed` | All child cases are closed/inactive |

## 7. Communication Flow

```text
1. New KPI email arrives.
2. Individual case is created or updated.
3. Case is attached to Building Issue Group.
4. Case is marked pending communication.
5. No email is sent immediately.
6. User or policy generates group-level consolidated draft.
7. Draft is reviewed.
8. Draft may be sent or held.
9. Replies attach to the group and can be mapped back to child cases.
```

## 8. Consolidated Email Format

### Subject

```text
Action Required: Open KPI Items for {building}
```

Follow-up:

```text
Follow-up: Open KPI Items for {building}
```

### Body Structure

```text
Hello,

The following KPI items are currently open for {building}.

Please review each item below and provide the requested information.

1. {Case Title}
- Case type: {case_type}
- Device: {device_or_na}
- Period/Due date: {period_or_due_date}
- Current status: {status}
- Summary: {summary}

Required response:
- {case_type_specific_instruction_1}
- {case_type_specific_instruction_2}

Please reply with updates for each item. Where applicable, include current status, expected completion date, supporting documentation, and any access, approval, scheduling, or system blockers.

Thank you,
Solucore
```

## 9. Required Response Instructions by Case Type

### DATA_ABSENCE
Ask for:

- confirmation whether maintenance data has been uploaded,
- latest maintenance activity date,
- reason data was missing or delayed,
- any system/access issue preventing upload,
- expected date of correction if not yet resolved.

### MAINTENANCE_HOURS_SHORTFALL
Ask for:

- maintenance hours completed for the period,
- missing time records,
- reason for the shortfall,
- corrective plan,
- expected completion date.

### MAJOR_WORK_OVERDUE
Ask for:

- current work status,
- reason work is overdue,
- revised expected completion date,
- access, parts, approval, or scheduling blockers,
- supporting documentation if available.

### CAT1_COMPLIANCE / CAT5_COMPLIANCE
Ask for:

- test status,
- scheduled or completed date,
- contractor confirmation,
- supporting documentation if completed,
- reason the test cannot be completed by due date if applicable.

### GOVERNMENT_DIRECTIVE
Ask for:

- current compliance status,
- action taken or planned,
- expected completion date,
- supporting evidence/documentation,
- any extension, blocker, or authority communication.

## 10. New Since Last Email

When a new issue arrives after a consolidated email was sent:

- attach it to the same group,
- set `new_since_last_email = true`,
- do not send a new email immediately,
- include it in the next follow-up or group update.

## 11. Suppression Rules

Do not generate or send a group email if:

- group has no open cases,
- building or contractor is missing,
- unresolved blocking manual review exists,
- cooldown is active,
- a draft already exists and awaits review,
- recipient confidence is too low,
- demo recipient override is invalid.

## 12. Proposed Tables

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

## 13. Backend Modules

### `building_groups.py`

Responsible for grouping cases by building/contractor.

Functions:

- `normalize_group_value(value)`
- `build_grouping_key(building, contractor)`
- `get_or_create_group(building, contractor)`
- `attach_case_to_group(case_id)`
- `rebuild_all_groups()`
- `list_building_groups(filters)`
- `get_group_summary(group_id)`

### `response_requirements.py`

Responsible for case-type-specific response requirements.

Functions:

- `get_required_response_items(case_type)`
- `build_case_requirements(case_id)`
- `calculate_case_completeness(case_id)`

### `communication_planner.py`

Responsible for deciding whether a group should communicate.

Functions:

- `evaluate_group_communication_status(group_id)`
- `list_groups_ready_for_draft()`
- `suppress_group_communication(group_id, reason)`

### `group_email_builder.py`

Responsible for creating consolidated drafts.

Functions:

- `build_consolidated_email(group_id)`
- `build_followup_email(group_id)`
- `create_group_email_draft(group_id)`
- `validate_draft_quality(draft)`

## 14. UI Requirements

### `/building-groups`

Show building, contractor, open issue count, new since last email count, review count, last email, status, and actions.

### `/building-groups/<group_id>`

Show group summary, child cases, new issues, received emails, sent/draft emails, replies, reviews, pattern flags, AI hypotheses, and draft generation actions.

## 15. CLI Commands

```powershell
python srcgent.py rebuild-building-groups
python srcgent.py show-building-groups
python srcgent.py generate-building-draft --group-id GROUP_ID
python srcgent.py generate-building-draft --all-ready
```

## 16. Acceptance Criteria

1. Eligible cases link to building groups.
2. Missing building/contractor cases are flagged for review.
3. Group page lists child cases.
4. Consolidated draft includes all open child cases.
5. Draft includes response instructions by case type.
6. New cases after last email are flagged as new.
7. Follow-up drafts include new and unresolved items.
8. No group email sends automatically in MVP.
9. Existing individual case behavior still works.
10. Backlog import can populate groups.
