# UI, Dashboards, and Operations Spec

## 1. Purpose

The UI should make the agent feel like an operational assistant, not a database viewer.

Users should be able to answer:

- What needs attention?
- Which buildings have active issues?
- Which contractors are involved?
- What emails have been sent or received?
- What data is missing?
- What patterns are emerging?
- What AI hypotheses need review?
- Is the system safe and healthy?

## 2. Navigation

Recommended navigation:

```text
Dashboard
Building Groups
Cases
Needs Attention
Manual Reviews
Drafts
Replies
Patterns
Connection Hypotheses
Contractors
Buildings
Observability
Settings
```

## 3. Dashboard

Cards:

- Open building groups
- Open cases
- New issues since last email
- Manual reviews
- Drafts needing approval
- Replies needing mapping
- Active pattern flags
- Proposed AI hypotheses
- Follow-ups due
- AI calls used
- Demo recipient violations

Recent activity should show key events across cases, groups, drafts, replies, reviews, patterns, and hypotheses.

## 4. Building Groups Page

Route:

```text
/building-groups
```

Columns:

- Building
- Contractor
- Open Issues
- New Since Last Email
- Manual Reviews
- Last Email
- Status
- Health
- Actions

Filters:

- Status
- Contractor
- Building
- Has new issues
- Has manual reviews
- Ready for draft
- Awaiting response
- Has patterns
- Has AI hypotheses

## 5. Building Group Detail Page

Route:

```text
/building-groups/<group_id>
```

Sections:

1. Group summary
2. Health/status panel
3. Open child cases
4. New since last email
5. Required response summary
6. Draft/sent emails
7. Replies
8. Manual reviews
9. Source received emails
10. Pattern flags
11. AI hypotheses
12. Timeline
13. Actions

Actions:

- Generate consolidated draft
- View latest draft
- Mark group reviewed
- Run discovery for group
- Export group summary

## 6. Needs Attention Queue

Unified task list for humans.

Item types:

- building group ready for draft,
- draft needs approval,
- manual review open,
- reply needs mapping,
- follow-up due,
- AI hypothesis awaiting review,
- missing data blocking communication,
- stale group awaiting response,
- parser failure trend.

Columns:

- Type
- Priority
- Building
- Contractor
- Summary
- Age
- Action

## 7. Manual Reviews UI

List columns:

- Review category
- Case type
- Building
- Contractor
- Reason
- Blocking?
- Age
- Action

Detail must include source email body, extracted fields, missing fields, related cases, building group, previous communications, patterns, and actions.

## 8. Drafts UI

Draft list:

- Subject
- Group/Case
- Intended To
- Actual To
- Status
- Created
- Quality Check Result
- Actions

Draft detail:

- subject,
- intended recipients,
- actual recipients,
- body,
- internal notes,
- required response checklist,
- quality check results,
- approve/reject/send actions.

## 9. Replies UI

Replies list:

- From
- Subject
- Received
- Linked Group
- Mapped Cases
- Completeness
- Needs Review

Reply detail:

- reply body,
- linked group,
- suggested mappings,
- required data checklist,
- missing data,
- actions.

## 10. Patterns UI

Show pattern flags with type, entity, severity, evidence count, status, linked cases, and related hypotheses.

Pattern detail should allow running connection discovery for that pattern.

## 11. Connection Hypotheses UI

List:

- Summary
- Type
- Scope
- Confidence
- Risk
- Evidence Count
- Status
- Created

Detail:

- reasoning,
- recommended review,
- evidence cases/patterns/observations,
- source discovery run,
- validation status.

Actions:

- Accept
- Reject
- Mark useful
- Link to group
- Convert to rule candidate

## 12. Contractor Dashboard

Show contractor-level issue load:

- Contractor
- Open Buildings
- Open Cases
- Manual Reviews
- Drafts Pending
- Last Response
- Pattern Signals

Use neutral language. Do not frame as blame.

## 13. Building Dashboard

Rank buildings by:

- open cases,
- open groups,
- new since email,
- reviews,
- patterns,
- last communication,
- group health.

## 14. Observability Page

Route:

```text
/observability
```

Sections:

- Pipeline counts
- AI usage
- Outbound status
- Follow-up status
- Safety checks
- Recent command events
- Manual review load
- Backlog/import status
- Connection discovery runs

## 15. Job Status Page

Show:

- backlog import,
- connection discovery,
- memory rebuild,
- pattern detection,
- follow-up run,
- replay run.

Fields:

- job ID,
- type,
- status,
- started,
- elapsed,
- progress,
- records processed,
- errors,
- result link.

## 16. Settings Page

Show read-only config:

- demo mode,
- demo recipient,
- AI enabled,
- AI model,
- AI budget,
- SMTP configured,
- IMAP configured,
- follow-ups enabled,
- database path,
- observability log path.

Never expose secrets.

## 17. Demo Safety Banner

Show globally when demo mode is active:

```text
DEMO MODE ENABLED
All outbound email redirected to {DEMO_RECIPIENT_EMAIL}
```

## 18. Acceptance Criteria

1. Building groups are visible.
2. Group detail shows child cases and source emails.
3. Reviews include source email context.
4. Drafts show intended and actual recipients.
5. Demo banner is visible.
6. Patterns and hypotheses are clearly distinguished.
7. Needs Attention queue shows actionable work.
8. Observability is human-readable.
9. Settings page exposes no secrets.
10. AI hypotheses are never labeled as confirmed facts.
