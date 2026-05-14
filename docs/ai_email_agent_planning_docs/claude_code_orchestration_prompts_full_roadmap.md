# Claude Code Orchestration Prompts: Full Roadmap Implementation

## Purpose

Use this document to have Claude Code orchestrate the implementation of the next-stage AI Email Alert Agent roadmap while minimizing Claude Code usage.

Claude Code should **not** do the bulk coding itself. Claude should act as the coordinator/orchestrator and delegate planning, implementation, review, and bug fixing to **Codex CLI agents**.

The planning docs are expected to be available at:

```text
./docs/ai_email_alert_planning_docs/
```

Expected planning docs:

```text
00_INDEX.md
01_PRODUCT_VISION_AND_FEATURE_ROADMAP.md
02_BUILDING_ISSUE_GROUPS_AND_CONSOLIDATED_COMMUNICATIONS_SPEC.md
03_REVIEW_REPLY_AND_CASE_WORKFLOW_SPEC.md
04_INTELLIGENCE_MEMORY_AND_CONNECTION_DISCOVERY_SPEC.md
05_UI_UX_DASHBOARDS_AND_OPERATIONS_SPEC.md
06_DATA_MODEL_BACKEND_AND_API_SPEC.md
07_IMPLEMENTATION_ROADMAP_TESTING_AND_ACCEPTANCE_PLAN.md
```

This prompt pack should be placed at:

```text
./docs/ai_email_alert_planning_docs/claude_code_orchestration_prompts_full_roadmap.md
```

---

# 0. How to Use This Prompt Pack

## Recommended workflow

Run these prompts in order.

1. **Master Orchestrator Prompt**
2. **Architecture Agent Dispatch**
3. **Design Agent Dispatch**
4. **Phase 1 Coding Dispatch: Building Issue Groups**
5. **Phase 2 Coding Dispatch: Consolidated Communications**
6. **Phase 3 Coding Dispatch: Manual Review, Replies, and Draft Workflow**
7. **Phase 4 Coding Dispatch: Scalable Connection Discovery**
8. **Phase 5 Coding Dispatch: UI, Dashboards, and Operations**
9. **Phase 6 Coding Dispatch: Import/Demo Tooling and Safety Utilities**
10. **Review Agent Dispatch**
11. **Bug Fix Agent Dispatch**
12. **Final Validation and Handoff Prompt**

Each phase should be treated as a separate implementation unit. Do not attempt to implement the whole roadmap in one giant pass.

## Usage optimization rule

Claude Code should:

- read high-level outputs,
- dispatch Codex agents,
- inspect summaries,
- run/interpret tests,
- decide next actions,
- avoid manually writing large code blocks unless absolutely necessary.

Codex CLI agents should:

- inspect relevant docs/code,
- make code changes,
- add tests,
- run validation,
- report exactly what changed.

## Safety rule

The system must remain safe:

- AI disabled by default.
- AI usage requires explicit budget.
- Backlog mode sends nothing.
- Connection discovery excludes unsupported KPI emails.
- AI hypotheses never mutate cases.
- Cases never auto-close.
- Demo recipient override remains intact.
- Normal outbound remains draft-first unless explicit send path is used.
- No production infrastructure unless specifically requested.
- No Prometheus/Grafana/OpenTelemetry.
- No auth/RBAC yet.
- No Postgres/Alembic migration yet.
- No all-KPI expansion yet.

---

# 0A. Global Git and Orchestration Rules

Claude Code is the orchestrator. Codex CLI agents perform architecture review, design, coding, review, and bug fixing.

Claude Code must use git branches and atomic commits throughout the implementation.

Before implementation begins:

- Inspect `git status --short`
- Confirm the current branch with `git branch --show-current`
- Stop if there are uncommitted user changes
- Create a top-level feature branch unless already on an appropriate branch

Recommended branch:

```bash
git checkout -b feature/building-level-agent-roadmap
```

For large phases, Claude Code may create phase branches and merge them back after validation.

Recommended phase branches:

```bash
git checkout -b feature/phase-1-building-groups
git checkout -b feature/phase-2-consolidated-communications
git checkout -b feature/phase-3-review-reply-workflow
git checkout -b feature/phase-4-scalable-discovery
git checkout -b feature/phase-5-ui-operations
git checkout -b feature/phase-6-demo-import-tooling
```

Every commit must be atomic and represent one coherent change. Do not create one giant roadmap commit.

Good atomic commits include:

- schema additions for building groups
- backend helpers for building groups
- tests for building group behavior
- consolidated building draft generation
- response requirement templates
- manual review source email context
- packetized pattern discovery
- observability UI page
- safety check command

Before each commit, run:

```bash
git status --short
git diff --stat
```

Run relevant validation before committing.

Do not commit:

- `.env`
- local databases
- logs
- cache files
- `.DS_Store`
- `__pycache__`
- generated runtime reports
- private credentials
- local virtual environments
- temporary agent scratch files outside `.agent_runs`

Each Codex agent run must be saved under:

```text
.agent_runs/<timestamp>_<agent_name>/
```

Each folder must contain:

```text
prompt.md
output.md
commands.txt
validation.txt, when applicable
```

Claude Code must record commit hashes for completed phases.

Codex agents should make code changes and tests, but Claude Code should manage phase gates, review loops, validation, branching, and commits.

Do not force-push or rewrite history unless explicitly instructed.

---

# 1. Master Orchestrator Prompt for Claude Code

Use this first.

```text
You are working in the existing Email Alert Triage Agent repository.

Your role is to act as an orchestration agent, not the main coding agent.

You must use Codex CLI agents for the actual architecture review, design planning, coding, review, and bug fixing work. Claude Code should coordinate, inspect results, run validation, manage git hygiene, and make decisions about what to dispatch next.

The planning docs are located at:

./docs/ai_email_alert_planning_docs/

This full prompt pack is located at:

./docs/ai_email_alert_planning_docs/claude_code_orchestration_prompts_full_roadmap.md

Read these docs first at a high level:

- ./docs/ai_email_alert_planning_docs/00_INDEX.md
- ./docs/ai_email_alert_planning_docs/01_PRODUCT_VISION_AND_FEATURE_ROADMAP.md
- ./docs/ai_email_alert_planning_docs/07_IMPLEMENTATION_ROADMAP_TESTING_AND_ACCEPTANCE_PLAN.md
- ./docs/ai_email_alert_planning_docs/claude_code_orchestration_prompts_full_roadmap.md

Then inspect the repository structure:

git status --short
git branch --show-current
find src -maxdepth 3 -type f | sort
find tests -maxdepth 3 -type f | sort
find docs -maxdepth 3 -type f | sort

Confirm that Codex CLI is available:

codex --version

If the Codex CLI syntax differs from previous usage, run:

codex --help

Use the installed Codex CLI in non-interactive mode wherever possible. If the exact command syntax differs, use the equivalent installed Codex CLI command.

Create a working folder for agent outputs:

.agent_runs/

For each dispatched Codex agent, save its prompt and output under:

.agent_runs/<timestamp>_<agent_name>/

Do not start by coding.

First produce a concise orchestration plan with:

1. Current repository state.
2. Current branch and whether a feature branch is needed.
3. Planning docs found or missing.
4. Codex CLI availability.
5. Proposed implementation phases.
6. Safety constraints that must not be broken.
7. Validation commands that will be run after each phase.
8. Branching and atomic commit strategy.
9. Rollback strategy.

Implementation phases should be:

Phase 1: Building Issue Groups foundation.
Phase 2: Consolidated building communications.
Phase 3: Manual review, replies, missing data, and draft workflow.
Phase 4: Scalable connection discovery.
Phase 5: UI, dashboards, and operations.
Phase 6: Import/demo tooling and safety utilities.
Phase 7: final review, bug fixing, validation, docs sync.

For every phase:

- Dispatch a Codex coding agent.
- Have the coding agent add/update tests.
- Run validation.
- Dispatch a Codex review agent after meaningful milestones.
- Dispatch bug fix agents as needed.
- Commit atomically after coherent validated changes.
- Do not let Claude Code implement large code changes directly.

Global code quality requirements:

- Clean, simplified, maintainable Python.
- Human-readable.
- Industry-standard.
- Consistent with existing project style.
- Well-commented where helpful.
- PyDoc/docstrings for public functions/classes and non-obvious helpers.
- No spaghetti code.
- No broad rewrites.
- No production infrastructure.

Global safety requirements:

- AI disabled by default.
- All model calls through ai_gateway.py.
- Backlog mode remains no-AI, no-outbound, no-followups, no-escalation.
- Connection discovery must never include unsupported KPI emails.
- AI hypotheses are proposed only and never mutate cases.
- Cases are never auto-closed.
- Outbound email remains draft-first and demo-recipient-safe.
- Existing demo, backlog, observability, and test commands must continue passing.

Validation commands to run regularly:

python -m compileall src
python -m unittest discover
python src/agent.py demo
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
python src/agent.py observability-report
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

On macOS, prefer ./.venv/bin/python when available.

Do not proceed to implementation until the orchestration plan is written.
```

---

# 2. Architecture Agent Dispatch Prompt

Claude should dispatch this to a Codex CLI architecture agent.

```text
You are the Architecture Agent for the Email Alert Triage Agent.

Your task is to read the roadmap docs and current repository, then produce an implementation architecture plan. Do not code unless explicitly asked in a later prompt.

Planning docs path:

./docs/ai_email_alert_planning_docs/

Read:

- 00_INDEX.md
- 01_PRODUCT_VISION_AND_FEATURE_ROADMAP.md
- 02_BUILDING_ISSUE_GROUPS_AND_CONSOLIDATED_COMMUNICATIONS_SPEC.md
- 03_REVIEW_REPLY_AND_CASE_WORKFLOW_SPEC.md
- 04_INTELLIGENCE_MEMORY_AND_CONNECTION_DISCOVERY_SPEC.md
- 05_UI_UX_DASHBOARDS_AND_OPERATIONS_SPEC.md
- 06_DATA_MODEL_BACKEND_AND_API_SPEC.md
- 07_IMPLEMENTATION_ROADMAP_TESTING_AND_ACCEPTANCE_PLAN.md

Inspect current source and tests.

Produce an architecture plan covering:

1. Current system architecture summary.
2. New modules needed.
3. Existing modules that should be extended.
4. Tables to add.
5. Functions/services to add.
6. UI routes to add.
7. CLI commands to add.
8. Data flow changes.
9. Safety invariants.
10. Testing strategy.
11. Phase-by-phase implementation plan.
12. Risks and mitigations.
13. Suggested atomic commit boundaries.

Do not propose broad rewrites.

Do not add production infrastructure.

Keep individual cases as the source of truth.

Add Building Issue Groups as a coordination layer above cases.

Connection discovery must exclude unsupported KPI emails.

AI hypotheses must remain proposed and non-mutating.

Backlog mode must remain no-AI/no-outbound/no-followups/no-escalation.

Output a detailed architecture report only. Do not modify files.
```

---

# 3. Design Agent Dispatch Prompt

Claude should dispatch this to a Codex CLI design agent after the architecture report is available.

```text
You are the Design Agent for the Email Alert Triage Agent.

Your task is to convert the planning docs and architecture report into detailed implementation designs. Do not code yet.

Read:

- ./docs/ai_email_alert_planning_docs/
- latest architecture agent output in .agent_runs/

Inspect current source code and tests.

Produce a detailed design document for implementation with:

1. Exact database schema additions.
2. Backward-compatible schema initialization approach.
3. New backend modules and public functions.
4. Existing modules to touch and why.
5. CLI parser additions and command behavior.
6. Flask routes and template requirements.
7. Test files to add/update.
8. Safety checks to preserve.
9. Step-by-step implementation order.
10. Atomic commit plan.
11. Acceptance criteria per phase.

Required design areas:

- Building Issue Groups.
- Building group case linking.
- Consolidated building emails.
- Required response templates by case type.
- New-since-last-email tracking.
- Manual review source email visibility.
- Missing data checklists.
- Reply-to-case/group mapping.
- Draft approval and draft quality checks.
- Pattern-based connection discovery.
- Building-group connection discovery.
- Packetized full supported-data discovery.
- Hypothesis merge/dedupe.
- Needs Attention queue.
- Observability UI page.
- Job status/import progress.
- Safety check command.

Do not implement all-KPI support.

Do not analyze unsupported KPI emails in connection discovery.

Do not introduce production auth, Postgres, Alembic, queues, Prometheus, Grafana, or OpenTelemetry.

Output a detailed design plan only. Do not modify files.
```

---

# 4. Phase 1 Coding Dispatch: Building Issue Groups Foundation

```text
You are a Codex Coding Agent implementing Phase 1 of the Email Alert Triage Agent roadmap.

Read:

- ./docs/ai_email_alert_planning_docs/02_BUILDING_ISSUE_GROUPS_AND_CONSOLIDATED_COMMUNICATIONS_SPEC.md
- ./docs/ai_email_alert_planning_docs/06_DATA_MODEL_BACKEND_AND_API_SPEC.md
- ./docs/ai_email_alert_planning_docs/07_IMPLEMENTATION_ROADMAP_TESTING_AND_ACCEPTANCE_PLAN.md
- architecture/design outputs in .agent_runs/

Goal:

Implement Building Issue Groups as a parent layer above individual cases.

Scope:

1. Add database tables:
   - building_issue_groups
   - building_issue_group_cases

2. Add backend module:
   - src/building_groups.py

3. Implement:
   - normalize_group_value(value)
   - build_grouping_key(building, contractor)
   - get_or_create_group(building, contractor)
   - attach_case_to_group(case_id)
   - rebuild_all_groups()
   - get_group_summary(group_id)
   - list_building_groups(filters=None)

4. Integrate with case creation/update:
   - when a case has building and contractor, attach it to the correct group.
   - do not break existing individual case grouping.
   - if building or contractor is missing, do not group automatically.

5. Add CLI commands:
   - python src/agent.py rebuild-building-groups
   - python src/agent.py show-building-groups

6. Add minimal read-only UI:
   - GET /building-groups
   - GET /building-groups/<group_id>

7. Add tests.

Safety constraints:

- Do not send emails.
- Do not generate drafts yet.
- Do not change case grouping semantics.
- Do not close cases.
- Do not enable AI.
- Do not touch backlog safety behavior.
- Do not add production infrastructure.

Code quality:

- Clean, maintainable Python.
- Type hints for public helpers.
- PyDoc/docstrings for public functions/classes and non-obvious helpers.
- Keep modules focused.
- Follow existing project style.

Tests to add/update:

- group created for case with building and contractor.
- same building/contractor reuses group.
- missing building or contractor does not auto-group.
- rebuild_all_groups is idempotent.
- backlog-imported cases can be grouped by rebuild command.
- UI routes load.
- existing tests still pass.

Validation commands:

python -m compileall src
python -m unittest discover
python src/agent.py demo
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
python src/agent.py observability-report
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

Commit guidance:

- Commit schema separately if large enough.
- Commit backend helpers separately if large enough.
- Commit UI/tests separately if large enough.
- Keep commits atomic and validated.

Final response format:

### Summary
### Files changed
### Behavior implemented
### Tests added
### Validation results
### Suggested commits or commit hash
### Deferred items
```

---

# 5. Phase 2 Coding Dispatch: Consolidated Building Communications

```text
You are a Codex Coding Agent implementing Phase 2 of the Email Alert Triage Agent roadmap.

Read:

- ./docs/ai_email_alert_planning_docs/02_BUILDING_ISSUE_GROUPS_AND_CONSOLIDATED_COMMUNICATIONS_SPEC.md
- ./docs/ai_email_alert_planning_docs/06_DATA_MODEL_BACKEND_AND_API_SPEC.md
- Phase 1 implementation.
- architecture/design outputs in .agent_runs/

Goal:

Implement group-level consolidated draft generation, required response instructions, and new-since-last-email tracking.

Scope:

1. Add database tables if not already present:
   - building_group_emails
   - communication_queue, only if needed for clean design

2. Add modules:
   - src/response_requirements.py
   - src/communication_planner.py
   - src/group_email_builder.py

3. Implement response requirements by case type:
   - DATA_ABSENCE
   - MAINTENANCE_HOURS_SHORTFALL
   - MAJOR_WORK_OVERDUE
   - CAT1_COMPLIANCE
   - CAT5_COMPLIANCE
   - GOVERNMENT_DIRECTIVE

4. Implement consolidated draft generation:
   - one draft per Building Issue Group.
   - includes all open child cases.
   - includes required response instructions.
   - clearly asks recipient what data/status/evidence to provide.
   - external email does not include internal AI hypotheses.
   - creates draft only, never sends.

5. Implement new-since-last-email tracking:
   - cases added after last group email are marked new_since_last_email.
   - after draft/sent status, mark included cases appropriately.

6. Add CLI command:
   - python src/agent.py generate-building-draft --group-id GROUP_ID

7. Add UI action:
   - POST /building-groups/<group_id>/generate-draft

8. Add UI section on group detail:
   - open child cases
   - new since last email
   - latest draft/sent emails
   - required response summary

9. Add draft quality check:
   - building present
   - contractor present
   - issues listed
   - response instructions included
   - no internal notes
   - demo recipient safety maintained

Safety constraints:

- Draft only.
- Do not auto-send.
- Preserve intended_to / actual_to behavior.
- Preserve DEMO_MODE recipient override.
- Do not include AI hypotheses externally.
- Do not enable AI.
- Do not schedule follow-ups yet.
- Do not close cases.

Code quality:

- Clean, maintainable Python.
- Type hints and docstrings.
- No giant functions.
- Keep email body generation readable and testable.

Tests:

- consolidated draft includes all open child cases.
- draft includes required response instructions by case type.
- draft excludes internal AI hypotheses.
- draft preserves intended/actual recipients.
- new_since_last_email is tracked.
- draft quality check blocks missing required instructions.
- no send occurs automatically.
- existing tests still pass.

Validation commands:

python -m compileall src
python -m unittest discover
python src/agent.py demo
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
python src/agent.py observability-report
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

Commit guidance:

- Commit response requirement templates separately if appropriate.
- Commit draft generation separately.
- Commit UI/tests separately.
- Keep commits atomic and validated.

Final response format:

### Summary
### Files changed
### Behavior implemented
### Tests added
### Validation results
### Safety preserved
### Suggested commits or commit hash
### Deferred items
```

---

# 6. Phase 3 Coding Dispatch: Manual Review, Replies, Missing Data, and Draft Workflow

```text
You are a Codex Coding Agent implementing Phase 3 of the Email Alert Triage Agent roadmap.

Read:

- ./docs/ai_email_alert_planning_docs/03_REVIEW_REPLY_AND_CASE_WORKFLOW_SPEC.md
- ./docs/ai_email_alert_planning_docs/06_DATA_MODEL_BACKEND_AND_API_SPEC.md
- current Phase 1 and Phase 2 code.
- architecture/design outputs in .agent_runs/

Goal:

Improve manual review, reply handling, missing data tracking, and draft approval workflow.

Scope:

1. Manual review context:
   - review detail page shows source email subject/body.
   - shows linked case.
   - shows related Building Issue Group.
   - shows extracted fields.
   - shows missing fields.
   - shows related child cases and prior communications if available.

2. Missing data checklist:
   - add src/case_requirements.py or extend response_requirements.py.
   - define required data by case type.
   - generate checklist for a case.
   - compute case completeness score.

3. Reply-to-group/case mapping:
   - add reply_case_mappings table if needed.
   - add src/reply_mapping.py.
   - attach replies to Building Issue Group when replying to group email.
   - allow manual mapping to child cases.
   - do not auto-close cases.

4. Reply completeness assistant:
   - deterministic first.
   - compare reply text to missing data checklist.
   - mark response complete/partial/vague/completion_claimed_no_evidence.
   - create manual review for ambiguous completion claims.

5. Clarification drafts:
   - generate draft asking only for missing data.
   - draft only, no send.
   - preserve demo recipient override.

6. Draft approval workflow:
   - add status fields/helpers where appropriate.
   - draft states: draft_generated, needs_review, approved, sent, rejected, revised.
   - implement simple approve/reject UI actions if low-risk.
   - avoid broad UI rebuild.

Safety constraints:

- No auto-close.
- No auto-send.
- No AI required.
- If AI is used later, it must go through ai_gateway and be budgeted, but do not implement AI reply mapping now unless already designed as optional.
- No production routing.
- No unsupported KPI analysis.
- Preserve demo safety.

Tests:

- manual review detail includes source email.
- missing data checklist generated by case type.
- completeness score works.
- reply attaches to group.
- reply can map to child cases.
- reply completeness detects missing required data.
- clarification draft asks only for missing data.
- completion claims create review, not closure.
- draft approval states behave.
- existing tests pass.

Validation commands:

python -m compileall src
python -m unittest discover
python src/agent.py demo
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
python src/agent.py observability-report
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

Commit guidance:

- Commit missing data/checklist foundation separately.
- Commit manual review UI context separately.
- Commit reply mapping separately.
- Commit draft workflow separately if large enough.
- Keep commits atomic and validated.

Final response format:

### Summary
### Files changed
### Behavior implemented
### Tests added
### Validation results
### Safety preserved
### Suggested commits or commit hash
### Deferred items
```

---

# 7. Phase 4 Coding Dispatch: Scalable Connection Discovery

```text
You are a Codex Coding Agent implementing Phase 4 of the Email Alert Triage Agent roadmap.

Read:

- ./docs/ai_email_alert_planning_docs/04_INTELLIGENCE_MEMORY_AND_CONNECTION_DISCOVERY_SPEC.md
- ./docs/ai_email_alert_planning_docs/06_DATA_MODEL_BACKEND_AND_API_SPEC.md
- current connection_discovery.py
- current database.py
- current ai_gateway.py
- architecture/design outputs in .agent_runs/

Goal:

Scale connection discovery so it can run over all supported data through packets, pattern flags, and building groups without giant prompts or timeouts.

Scope:

1. Add discovery run tracking:
   - connection_discovery_runs
   - connection_discovery_packets

2. Add or extend modules:
   - src/discovery_packets.py
   - src/connection_discovery.py

3. Add discovery scopes:
   - existing small case mode
   - patterns
   - building-groups
   - all-supported

4. Pattern-based discovery:
   - build packets from deterministic pattern_flags and supporting supported cases.
   - AI should identify broader relationships, not simply repeat the pattern.

5. Building-group discovery:
   - build packets from Building Issue Groups and child supported cases.
   - include missing data summary, group communication state, and pattern flags.
   - exclude unsupported KPI data.

6. Full supported discovery:
   - packetize by building, contractor, device, case type, or building group.
   - never send one giant prompt.
   - use prompt size guard.

7. Prompt size guard:
   - estimate prompt chars.
   - split oversized packets.
   - skip/log packets that cannot be safely split.

8. Merge/dedupe:
   - deterministic duplicate grouping by type/entity/case overlap.
   - optional AI merge command only if simple and budgeted.
   - add command: python src/agent.py merge-connection-hypotheses --max-ai-calls N if practical.

9. Add CLI options:
   - --scope patterns
   - --scope building-groups
   - --scope all-supported
   - --packet-by building|contractor|device|case-type|entity
   - --batch-size N
   - --max-prompt-chars N
   - --dry-run
   - --max-ai-calls N

10. Observability:
   - log run ID, packets created/analyzed, AI calls, hypotheses created/rejected, unsupported_records_included.
   - unsupported_records_included must always be 0.

Safety constraints:

- Unsupported KPI emails must never be included.
- Rejected non-KPI records must never be included.
- Raw unsupported backlog content must never be included.
- AI hypotheses remain proposed.
- AI does not mutate cases/groups.
- AI does not send, escalate, schedule, or close.
- AI calls through ai_gateway only.
- Explicit max AI calls required.
- No live AI in tests.

Tests:

- pattern packets exclude unsupported data.
- building group packets include supported child cases only.
- all-supported discovery creates multiple packets.
- oversized prompt is split or skipped.
- discovery run tracking records counts.
- invalid hypotheses rejected.
- duplicate hypotheses grouped/merged.
- unsupported_records_included remains 0.
- AI budget enforced.
- existing connection discovery behavior still works.
- existing demo/backlog commands pass.

Validation commands:

python -m compileall src
python -m unittest discover
python src/agent.py demo
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
python src/agent.py observability-report
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

Commit guidance:

- Commit discovery run schema separately if large enough.
- Commit packet builders separately.
- Commit CLI scope expansion separately.
- Commit merge/dedupe separately.
- Keep commits atomic and validated.

Final response format:

### Summary
### Files changed
### Discovery scopes implemented
### Unsupported KPI exclusion proof
### Tests added
### Validation results
### Safety preserved
### Suggested commits or commit hash
### Deferred items
```

---

# 8. Phase 5 Coding Dispatch: UI, Dashboards, and Operations

```text
You are a Codex Coding Agent implementing Phase 5 of the Email Alert Triage Agent roadmap.

Read:

- ./docs/ai_email_alert_planning_docs/05_UI_UX_DASHBOARDS_AND_OPERATIONS_SPEC.md
- ./docs/ai_email_alert_planning_docs/06_DATA_MODEL_BACKEND_AND_API_SPEC.md
- current Flask app and templates.
- architecture/design outputs in .agent_runs/

Goal:

Add the UI and operations surfaces needed to use the new workflow.

Scope:

1. Building Groups UI:
   - /building-groups
   - /building-groups/<group_id>
   - actions to generate draft if Phase 2 exists.

2. Needs Attention queue:
   - route /needs-attention
   - show manual reviews, drafts needing approval, groups ready for draft, replies needing mapping, hypotheses awaiting review.
   - read-only first if simpler.

3. Drafts UI:
   - /drafts
   - /drafts/<draft_id>
   - show intended_to and actual_to.
   - show quality check result.
   - demo safety banner.

4. Replies UI:
   - /replies
   - /replies/<email_id>
   - show mapping status if Phase 3 exists.

5. Connection Hypotheses UI:
   - /connection-hypotheses
   - /connection-hypotheses/<hypothesis_id>
   - accept/reject actions if low-risk.
   - clearly label hypotheses as proposed.

6. Observability UI:
   - /observability
   - human-readable page for existing observability snapshot.

7. Job status UI:
   - /jobs if job_runs exists.
   - show imports/discovery/memory jobs.

8. Settings UI:
   - /settings
   - read-only config view.
   - do not expose secrets.

9. Demo mode banner:
   - visible on main pages when DEMO_MODE=true.
   - show actual demo recipient.

Safety constraints:

- UI must not expose secrets.
- UI must not send emails accidentally.
- Any send/approve action must require explicit POST and preserve demo safety.
- AI hypotheses must not be shown as confirmed facts.
- No unsupported KPI data in connection discovery UI evidence.
- No broad redesign.

Tests:

- all new routes load.
- demo banner appears in demo mode.
- settings page hides secrets.
- hypotheses page labels proposed status.
- drafts page shows intended/actual recipients.
- needs attention queue returns expected item types.
- observability page loads and does not mutate state.
- existing UI tests pass.

Validation commands:

python -m compileall src
python -m unittest discover
python src/agent.py demo
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
python src/agent.py observability-report
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

Commit guidance:

- Commit route/backend UI support separately from templates if useful.
- Commit safety banner separately if useful.
- Commit tests with related UI changes.
- Keep commits atomic and validated.

Final response format:

### Summary
### Files changed
### UI routes added
### Safety notes
### Tests added
### Validation results
### Suggested commits or commit hash
### Deferred items
```

---

# 9. Phase 6 Coding Dispatch: Import, Demo Tooling, and Safety Utilities

```text
You are a Codex Coding Agent implementing Phase 6 of the Email Alert Triage Agent roadmap.

Read:

- ./docs/ai_email_alert_planning_docs/01_PRODUCT_VISION_AND_FEATURE_ROADMAP.md
- ./docs/ai_email_alert_planning_docs/07_IMPLEMENTATION_ROADMAP_TESTING_AND_ACCEPTANCE_PLAN.md
- current backlog_loader.py
- current observability.py
- current agent.py
- current demo_scale_harness.py
- architecture/design outputs in .agent_runs/

Goal:

Improve operational utility, large import usability, demo repeatability, and safety validation.

Scope:

1. Backlog import progress:
   - print progress every N records.
   - show processed/accepted/rejected/review/unsupported counts.
   - show elapsed time and rough ETA if practical.

2. Import resume support:
   - make resume behavior explicit.
   - skip already imported message IDs.
   - add --resume flag if useful.
   - do not duplicate emails/cases.

3. Backlog performance mode:
   - batch/chunk commit where safe.
   - do not rewrite database module broadly.
   - preserve existing semantics.

4. Report detail modes:
   - --report-detail summary
   - --report-detail full
   - summary mode avoids huge per-item files.

5. Demo scenario builder:
   - python src/agent.py build-demo-scenario
   - create deterministic curated data for demos.
   - no real recipients.
   - no AI required.
   - no SMTP/IMAP.

6. Reset demo DB command:
   - python src/agent.py reset-demo-db
   - require confirmation or --yes.
   - only allow if database path clearly points to demo/test DB, or require explicit --database path.
   - do not accidentally delete production-looking DB.

7. Replay mode:
   - python src/agent.py replay --path data/demo_replay.json
   - process staged emails through normal pipeline with safe options.
   - no live IMAP.

8. Safety check command:
   - python src/agent.py safety-check
   - validate demo recipient override.
   - validate AI budget behavior.
   - validate backlog no-outbound/no-AI.
   - validate unsupported KPI exclusion from discovery.
   - validate no auto-closure.

9. Observability:
   - include last import job/progress if job_runs exists.
   - include safety-check summary if useful.

Safety constraints:

- Do not send emails.
- Do not poll IMAP.
- Do not enable AI.
- Do not break backlog import.
- Do not corrupt existing DB.
- Do not delete DB unless explicit safe command with confirmation.

Tests:

- progress output can be exercised with small sample.
- resume skips duplicates.
- report-detail summary produces smaller report.
- reset-demo-db refuses unsafe path or missing confirmation.
- build-demo-scenario is deterministic.
- replay uses safe normal pipeline.
- safety-check passes in safe environment.
- existing tests pass.

Validation commands:

python -m compileall src
python -m unittest discover
python src/agent.py demo
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
python src/agent.py observability-report
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run
python src/agent.py safety-check

Commit guidance:

- Commit import progress/resume separately from demo tooling.
- Commit safety-check separately if large enough.
- Keep commits atomic and validated.

Final response format:

### Summary
### Files changed
### Commands added
### Tests added
### Validation results
### Safety preserved
### Suggested commits or commit hash
### Deferred items
```

---

# 10. Review Agent Dispatch Prompt

Run this after each major phase or after every 2 phases.

```text
You are the Review Agent for the Email Alert Triage Agent.

Your task is to perform a hyper-critical review of the latest implementation changes.

Read:

- planning docs in ./docs/ai_email_alert_planning_docs/
- architecture/design outputs in .agent_runs/
- latest changed files
- test files

Do not implement fixes unless explicitly asked. Produce a review report.

Review categories:

1. Correctness
2. Safety
3. Maintainability
4. Test coverage
5. Data model consistency
6. UI behavior
7. CLI behavior
8. AI boundary
9. Backlog safety
10. Unsupported KPI exclusion
11. Outbound/demo recipient safety
12. Case mutation/auto-close safety
13. Performance/scaling risks
14. Documentation accuracy
15. Git hygiene and atomicity

Safety invariants to verify:

- AI disabled by default.
- AI calls only through ai_gateway.py.
- Unsupported KPI emails excluded from connection discovery.
- Backlog mode sends nothing, schedules nothing, uses no AI.
- Cases never auto-close.
- AI hypotheses never mutate cases.
- Normal outbound is draft-first and demo-recipient-safe.
- Consolidated emails do not include internal AI hypotheses.
- Drafts include required response instructions.
- UI does not expose secrets.

Git hygiene to verify:

- Work is on a feature branch.
- Commits are atomic where commits exist.
- No runtime artifacts are staged.
- No .env or secrets are staged.
- Commit messages are clear.

Run or request validation:

python -m compileall src
python -m unittest discover
python src/agent.py demo
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
python src/agent.py observability-report
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

Output:

### Review Verdict
Use one:
- APPROVED
- APPROVED WITH MINOR FIXES
- NEEDS FIXES
- BLOCKED

### Blocking Issues
### Non-Blocking Issues
### Missing Tests
### Safety Concerns
### Maintainability Concerns
### Git/Commit Concerns
### Suggested Bug Fix Agent Prompts
### Validation Results
```

---

# 11. Bug Fix Agent Dispatch Prompt Template

Use this when the Review Agent finds issues.

```text
You are a Codex Bug Fix Agent for the Email Alert Triage Agent.

Your task is to fix only the issues listed below. Do not expand scope.

Issues to fix:

<PASTE REVIEW AGENT ISSUES HERE>

Relevant docs:

./docs/ai_email_alert_planning_docs/

Relevant changed files:

<PASTE FILE LIST HERE>

Rules:

- Make the smallest safe fix.
- Add or update tests for every behavior fix.
- Do not rewrite modules.
- Do not add features.
- Preserve safety invariants.
- Preserve existing public behavior unless the issue explicitly requires change.

Safety invariants:

- AI disabled by default.
- AI through ai_gateway.py only.
- Backlog no-AI/no-outbound/no-followups/no-escalation.
- Unsupported KPI emails excluded from connection discovery.
- Cases never auto-close.
- AI hypotheses never mutate cases.
- Outbound draft/demo recipient safety preserved.
- No production infrastructure.

Run validation:

python -m compileall src
python -m unittest discover

If the fix touches demo/backlog/UI, also run:

python src/agent.py demo
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
python src/agent.py observability-report
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

Final response:

### Summary
### Files changed
### Issues fixed
### Tests added/updated
### Validation results
### Remaining concerns
### Suggested commit message
```

---

# 12. Final Validation and Handoff Prompt for Claude Code

Use after all phases and bug fixes.

```text
You are performing the final validation and handoff for the Email Alert Triage Agent roadmap implementation.

Do not add new features.

Your tasks:

1. Inspect git status.
2. Review all changed files.
3. Confirm docs have been updated.
4. Run full validation.
5. Dispatch a final Codex Review Agent if not already done.
6. Dispatch bug fix agents only for blocking issues.
7. Produce final implementation summary.

Run:

git status --short
git branch --show-current
git log --oneline -10
python -m compileall src
python -m unittest discover
python src/agent.py demo
python src/agent.py test-demo-scale --offline --emails 25 --seed 42
python src/agent.py observability-report
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

If new commands exist, also run safe/dry-run versions:

python src/agent.py rebuild-building-groups
python src/agent.py show-building-groups
python src/agent.py safety-check

If discovery scopes exist, run mocked/offline tests only. Do not run live AI unless explicitly requested.

Confirm:

- Building Issue Groups implemented.
- Consolidated drafts implemented.
- Required response instructions implemented.
- New since last email tracked.
- Manual review source email context visible.
- Missing data checklist implemented.
- Reply mapping implemented if in scope.
- Scalable discovery scopes implemented if in scope.
- UI routes load.
- Safety invariants preserved.
- Docs updated.
- Branches and commits are clean and atomic.
- No runtime artifacts or secrets are staged.

Final response format:

### Final Verdict
Use:
- READY
- READY WITH MINOR FOLLOW-UPS
- NOT READY

### Summary
### Features implemented
### Files changed
### Commands added
### UI routes added
### Database changes
### Safety validation
### Test validation
### Git branches and commits
### Known limitations
### Recommended next steps
### Suggested commit message
```

---

# 13. Optional Claude Usage Optimization Instructions

Use this in any Claude Code orchestration prompt if usage starts getting high.

```text
Usage optimization mode:

- Do not read entire large files unless needed.
- Use grep/search first.
- Ask Codex agents to inspect and report details.
- Only inspect changed sections or summaries where possible.
- Prefer dispatching Codex review/fix agents over manually reading every file.
- Keep Claude responses concise.
- Keep final summaries structured.
- Do not generate full code in Claude unless a tiny patch is faster than dispatching another agent.
```

---

# 14. Phase Gate Policy

Do not move to the next phase unless:

1. Phase implementation completed.
2. Phase tests added.
3. Compile passes.
4. Unit tests pass.
5. Demo command passes.
6. Backlog dry-run passes.
7. Review agent approves or only minor follow-ups remain.
8. Safety invariants are confirmed.
9. Git status is reviewed.
10. Relevant changes are committed atomically.
11. Commit hash is recorded.

This matters because the feature set is large. The safest way to implement everything is by gates, not by one giant coding sprint.

---

# 15. Suggested Branching Strategy

```bash
git checkout -b feature/building-level-agent-roadmap
```

Suggested phase branches:

```text
feature/phase-1-building-groups
feature/phase-2-consolidated-communications
feature/phase-3-review-reply-workflow
feature/phase-4-scalable-discovery
feature/phase-5-ui-operations
feature/phase-6-demo-import-tooling
```

Suggested commits:

```text
Add building issue group schema
Implement building group case linking
Add building group UI routes
Add consolidated building response requirements
Implement building group draft generation
Add manual review source email context
Add missing data checklist support
Add reply mapping workflow
Add packetized pattern discovery
Add building group connection discovery
Add hypothesis merge support
Add needs attention queue
Add observability UI page
Add backlog import progress
Add safety check command
Finalize roadmap validation
```

For this run, Claude Code should create atomic commits after each coherent validated work unit. Do not commit secrets, runtime artifacts, local databases, logs, cache files, `.env`, or unrelated files.

---

# 16. Comprehensive Implementation Checklist

The implementation should satisfy the checklist embedded in the Mac master handoff prompt. At minimum, the final state must include:

## Orchestration and Git

- planning docs verified
- Codex prompt pack verified
- Codex CLI verified
- `.agent_runs/` created
- feature branch used
- phase branches used where helpful
- atomic commits created
- commit hashes recorded
- no runtime artifacts/secrets committed

## Phase 1

- building groups schema
- building group linking
- rebuild/show commands
- building group routes
- grouping tests

## Phase 2

- consolidated group drafts
- response requirement templates
- new-since-last-email tracking
- draft quality checks
- no auto-send

## Phase 3

- manual review source email context
- missing data checklist
- reply mapping
- reply completeness
- clarification drafts
- closure assistant as recommendation only

## Phase 4

- discovery runs and packets
- pattern-based discovery
- building-group discovery
- all-supported packetized discovery
- prompt size guard
- merge/dedupe
- rule candidates

## Phase 5

- building group UI
- needs attention queue
- draft/reply/hypothesis pages
- observability UI
- settings page
- demo banner

## Phase 6

- import progress
- import resume
- report detail modes
- demo scenario builder
- reset demo DB
- replay mode
- safety check command

## Cross-cutting

- AI safety preserved
- unsupported KPI exclusion preserved
- backlog safety preserved
- email safety preserved
- case safety preserved
- tests and docs updated

---

# 17. Main Outcome

The goal is to turn the agent from an alert-by-alert email triage demo into a building-level KPI coordination assistant that groups related cases, sends consolidated action-oriented communications, improves review and reply handling, scales intelligence through pattern-based AI discovery, and keeps risky behavior behind explicit safety controls.
