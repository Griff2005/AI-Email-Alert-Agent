# Review, Reply, and Case Workflow Spec

## 1. Purpose

This spec defines manual review, reply handling, missing data tracking, draft approval, and case lifecycle improvements.

## 2. Manual Review Requirements

Manual review must show enough context for a user to make a decision without hunting through the database.

Each review item should show:

- review reason,
- review category,
- source email subject,
- sender,
- received date,
- email body,
- linked case,
- building group,
- extracted fields,
- missing fields,
- related child cases,
- previous outbound emails,
- replies,
- patterns,
- AI hypotheses if relevant.

## 3. Review Categories

Recommended categories:

| Category | Meaning |
|---|---|
| `missing_required_field` | Required extraction field missing |
| `ambiguous_building` | Building cannot be determined safely |
| `ambiguous_contractor` | Contractor cannot be determined safely |
| `duplicate_uncertainty` | Grouping/deduplication uncertain |
| `prompt_injection` | Unsafe instruction-like content detected |
| `reply_claims_completion` | Reply may indicate resolution |
| `evidence_missing` | Completion claimed without proof |
| `unsupported_format` | Known unsupported/unknown input |
| `ai_hypothesis_review` | AI hypothesis needs human review |
| `communication_blocked` | Draft cannot be safely generated/sent |

## 4. Missing Data Checklist

Each case should expose what is known and what is missing.

### Common Fields

- building
- contractor
- device
- due date or period
- source email
- latest response
- evidence received
- expected completion date

### Case-Type Requirements

#### DATA_ABSENCE

- building
- contractor
- last activity date or no-submission indicator
- elapsed days
- contractor response
- expected correction date
- upload confirmation

#### MAINTENANCE_HOURS_SHORTFALL

- building
- contractor
- reporting period
- required hours
- actual hours
- shortfall amount
- reason for shortfall
- corrective plan
- expected completion date

#### MAJOR_WORK_OVERDUE

- building
- contractor
- device
- scheduled date
- work description
- current status
- revised completion date
- blockers

#### CAT1 / CAT5

- building
- contractor
- device
- due date
- scheduled/completed date
- supporting documentation
- blocker/extension if overdue

#### GOVERNMENT_DIRECTIVE

- building
- contractor/responsible party
- directive identifier if available
- device if applicable
- due date
- compliance status
- supporting documentation
- extension/blocker notes

## 5. Completeness Score

Calculate:

```text
completed_required_items / total_required_items
```

Show:

```text
Case completeness: 72%
Missing: contractor response, expected completion date, evidence
```

## 6. Reply Handling

### Reply Attachment

When a reply is received:

1. If it is a reply to a consolidated email, attach it to the Building Issue Group.
2. If it references a specific case, attach it to that case.
3. If ambiguous, attach to group and create review.

### Reply Mapping

Initial version: human maps reply to child cases.

Later version: AI proposes mappings, human confirms.

### Reply Categories

- `complete_response`
- `partial_response`
- `vague_response`
- `completion_claimed`
- `completion_claimed_no_evidence`
- `future_action_promised`
- `needs_clarification`
- `unrelated_reply`

## 7. Reply Completeness Assistant

For a mapped reply, compare reply content against required data checklist.

Output example:

```text
Reply addresses:
- current status

Still missing:
- expected completion date
- supporting documentation
```

## 8. Clarification Drafts

Generate clarification drafts asking only for missing information.

Rules:

- include missing data list,
- avoid blame,
- avoid internal AI hypotheses,
- preserve demo recipient override,
- do not send automatically.

## 9. Closure Assistant

The agent should never auto-close cases.

It may recommend closure candidates with evidence:

```text
Possible closure candidate:
Contractor replied that maintenance data was uploaded.
Evidence present: upload date.
Evidence missing: none.
Recommended action: human may close after review.
```

Block closure recommendation if:

- evidence missing,
- completion claim vague,
- manual review unresolved,
- compliance acceptance pending,
- building group still awaiting response.

## 10. Draft Approval

Draft states:

| Status | Meaning |
|---|---|
| `draft_generated` | System generated draft |
| `needs_review` | Human review required |
| `approved` | Ready to send |
| `sent` | Sent |
| `rejected` | Rejected |
| `revised` | Human edited |

Quality checks:

- subject present,
- building present,
- issues listed,
- required response instructions present,
- no internal notes,
- no unreviewed AI hypothesis,
- demo recipient correct,
- no blame language.

## 11. Tables

```sql
ALTER TABLE manual_reviews ADD COLUMN review_category TEXT;
ALTER TABLE manual_reviews ADD COLUMN blocking INTEGER DEFAULT 1;
ALTER TABLE manual_reviews ADD COLUMN context_json TEXT;
```

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

## 12. Acceptance Criteria

1. Manual review detail shows source email.
2. Review detail links to case and building group.
3. Missing data checklist is visible.
4. Replies attach to groups.
5. Replies can be mapped to child cases.
6. Reply completeness detects missing fields.
7. Clarification drafts ask only for missing data.
8. Closure is never automatic.
9. Draft approval states are tracked.
10. Draft quality checks run before approval/send.
