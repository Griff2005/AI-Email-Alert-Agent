# Implementation Roadmap, Testing, and Acceptance Plan

## 1. Implementation Philosophy

Build this roadmap in small safe slices. Preserve existing demo behavior. Avoid broad rewrites. Keep AI bounded. Keep outbound safe.

## 2. Phase Plan

| Phase | Theme | Main Outcome |
|---|---|---|
| 1 | Building grouping foundation | Cases grouped by building/contractor |
| 2 | Consolidated communication | Group-level drafts with required response instructions |
| 3 | Review and reply workflow | Source emails visible, replies mapped, missing data tracked |
| 4 | Intelligence scaling | Pattern and group-based AI discovery with packetization |
| 5 | UI/operations | Dashboards, queues, observability, settings, job status |
| 6 | Import/demo tooling | Progress, resume, scenario builder, reset demo DB |
| 7 | Production planning | Routing, auth, migrations, deployment, all-KPI support |

## 3. Phase 1: Building Grouping Foundation

### Build

- Add tables for building groups and group-case links.
- Add `building_groups.py`.
- Link cases to groups during case creation/update.
- Add rebuild command.
- Add basic building groups UI.

### Tests

- Create group for case with building/contractor.
- Reuse group for same building/contractor.
- Missing building/contractor does not silently group.
- Rebuild is idempotent.
- Backlog import can populate groups.
- Existing case grouping still works.

### Acceptance

- Eligible open cases are linked to groups.
- Group page shows grouped cases.
- Existing regression tests pass.

## 4. Phase 2: Consolidated Communication

### Build

- Add response requirements map.
- Add communication planner.
- Add group email builder.
- Add manual group draft generation.
- Add draft quality check.
- Track new since last email.

### Tests

- Draft includes all open child cases.
- Draft includes required instructions.
- Draft excludes internal AI hypotheses.
- Draft preserves intended/actual recipients.
- Draft quality check blocks missing instructions.
- No automatic send.

### Acceptance

- One consolidated group draft can replace multiple alert-level emails.
- Draft is review-only by default.
- Demo override remains intact.

## 5. Phase 3: Review and Reply Workflow

### Build

- Manual review source email display.
- Missing data checklist.
- Reply-to-group attachment.
- Manual reply-case mapping.
- Reply completeness assistant.
- Clarification draft generation.
- Closure assistant.

### Tests

- Review detail shows source email.
- Missing data checklist generated.
- Reply attaches to group.
- Reply maps to cases.
- Reply completeness detects missing data.
- Closure assistant never closes automatically.

### Acceptance

- Reviewer can understand and act from one page.
- Replies are connected to group/cases.
- Missing data drives clarification drafts.

## 6. Phase 4: Intelligence Scaling

### Build

- Pattern discovery scope.
- Building group discovery scope.
- Packetized all-supported discovery.
- Discovery run/packet tracking.
- Prompt size guard.
- Hypothesis merge/dedupe.
- Rule candidate workflow.

### Tests

- Pattern packets exclude unsupported data.
- Building group packets include supported child cases only.
- Prompt guard splits oversized packets.
- Discovery run records counts.
- Invalid hypotheses rejected.
- Duplicate hypotheses grouped.
- AI budget enforced.

### Acceptance

- Discovery runs over large data without one giant prompt.
- Unsupported KPI data excluded.
- Hypotheses remain proposed.
- Humans can accept/reject.

## 7. Phase 5: UI and Operations

### Build

- Needs Attention queue.
- Building groups/detail pages.
- Drafts/replies pages.
- Hypotheses page.
- Observability page.
- Settings page.
- Job status page.
- Demo safety banner.

### Tests

- Pages load.
- No secrets exposed.
- Demo banner correct.
- Draft page shows intended/actual recipients.
- Hypotheses labeled proposed.

### Acceptance

- User can operate core workflow from UI.
- Facts, drafts, reviews, and hypotheses are clearly separated.

## 8. Phase 6: Import and Demo Tooling

### Build

- Backlog import progress.
- Import resume.
- Batch/performance mode.
- Report detail modes.
- Replay mode.
- Demo scenario builder.
- Reset demo DB.

### Tests

- Progress updates.
- Resume avoids duplicates.
- Summary report is smaller.
- Demo scenario deterministic.
- Reset requires confirmation.

### Acceptance

- Large imports are observable and resumable.
- Demos can be rebuilt reliably.

## 9. Phase 7: Production Planning

Plan but do not rush:

- contact/routing registry,
- approval workflow by role,
- auth/RBAC,
- database migration framework,
- deployment plan,
- all-KPI support,
- monitoring/alerting,
- retention policy.

## 10. Regression Commands

Run after every phase:

```powershell
python -m compileall src
python -m unittest discover
python srcgent.py demo
python srcgent.py test-demo-scale --offline --emails 25 --seed 42
python srcgent.py observability-report
python srcgent.py load-backlog --source json --path dataacklog_sample.json --dry-run
```

## 11. Safety Acceptance Criteria

Before a feature is complete:

1. AI disabled by default.
2. AI usage requires explicit budget.
3. AI cannot mutate cases.
4. AI cannot send email.
5. Backlog mode sends nothing.
6. Cases do not auto-close.
7. Demo recipient override preserved.
8. Drafts show intended and actual recipients.
9. Unsupported KPI data excluded from connection discovery.
10. External emails do not include internal AI hypotheses.

## 12. Performance Criteria

### Backlog Import

- progress visible,
- resume supported,
- batch transactions available,
- summary report mode available.

### AI Discovery

- no giant prompt,
- prompt guard enforced,
- packetized scopes supported,
- usage budget enforced,
- timeouts logged and recoverable.

### UI

- pagination for large tables,
- no full email bodies in list views,
- fast loading group/case pages.

## 13. Risk Register

| Risk | Mitigation |
|---|---|
| Email spam | Communication queue, cooldown, manual approval |
| AI hallucination | evidence validation, proposed status, human review |
| Unsafe recipients | demo override, draft quality checks |
| Prompt timeouts | packetization and prompt-size guards |
| Review overload | categories and Needs Attention queue |
| Bad grouping | alias review and manual correction |
| Import slowness | progress, resume, batch mode |
| Scope creep | phase gates and acceptance criteria |

## 14. Suggested Development Prompts

### Prompt 1: Building Groups Foundation

```text
Implement Building Issue Groups as a parent layer above individual cases. Group open cases by normalized building + contractor. Add tables, backend helpers, rebuild command, and basic UI list/detail. Do not change existing case grouping. Do not send emails.
```

### Prompt 2: Consolidated Drafts

```text
Implement group-level consolidated draft generation. Include all open child cases and required response instructions by case type. Drafts only. No automatic send. Preserve demo recipient override.
```

### Prompt 3: Manual Review Context

```text
Upgrade manual review pages to show source email, linked case, building group, extracted fields, missing fields, and related cases. Add missing data checklist.
```

### Prompt 4: Pattern-Based Discovery

```text
Add connection discovery scope=patterns. Build evidence packets from deterministic pattern flags and supporting supported cases only. Exclude unsupported KPI data. Use AI gateway with budget and prompt size guard.
```

### Prompt 5: UI Operations Layer

```text
Add Needs Attention queue, Observability page, Job Status page, Drafts page, and Demo Mode banner. Keep UI simple and read-focused.
```

## 15. Definition of Done for Next Major Version

The next major version is done when:

1. Cases are grouped by building/contractor.
2. Building group pages show all child cases and context.
3. Consolidated drafts can be generated manually.
4. Drafts include required response instructions.
5. New issues since last email are tracked.
6. Manual reviews show source emails.
7. Replies can attach to groups.
8. Pattern-based connection discovery exists.
9. Unsupported KPI emails remain excluded from AI discovery.
10. UI has a clear Needs Attention queue.
11. Safety checks pass.
12. Regression tests pass.
