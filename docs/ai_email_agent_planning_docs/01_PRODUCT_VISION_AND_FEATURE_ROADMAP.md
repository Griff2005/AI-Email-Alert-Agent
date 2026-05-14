# Product Vision and Feature Roadmap

## 1. Vision

The AI Email Alert Agent should become a building-level KPI coordination assistant. Its job is not only to parse alerts, but to maintain a coherent operational picture of what is happening at each building, who is responsible, what information is missing, what follow-up has happened, and what patterns may be emerging.

## 2. Principles

### Deterministic First
Use rules and structured data for classification, extraction, grouping, communication suppression, case linking, and pattern flags wherever possible.

### AI as a Hypothesis Generator
AI may suggest relationships or summarize structured evidence, but should not create confirmed facts, close cases, escalate, score contractors/mechanics, or send emails on its own.

### Cases Remain the Source of Truth
Individual cases represent actual KPI issues. Building groups coordinate cases, but should not replace the underlying case records.

### Communication Is a Group-Level Workflow
The system should move from alert-level email generation to building-level consolidated drafts.

### Review Must Be Context-Rich
Reviewers should see source emails, fields, missing data, related groups, related cases, replies, patterns, and AI hypotheses in one place.

### External Emails Must Be Action-Oriented
Every outbound email should explicitly tell recipients what information or documentation they must provide.

## 3. Complete Feature Inventory

| # | Feature | Category | Priority | Description |
|---:|---|---|---|---|
| 1 | Building Issue Groups | Communication model | Critical | Group open cases by normalized building + contractor as a parent coordination record above individual cases. |
| 2 | Consolidated Building Emails | Communication model | Critical | Generate one building-wide email covering all open cases for a building/contractor instead of one email per alert. |
| 3 | Required Response Templates | Communication quality | Critical | Define the exact data recipients must provide for each case type and inject it into drafts/follow-ups. |
| 4 | Communication Queue | Communication model | High | Separate case detection from email generation so alerts can be batched, suppressed, or reviewed before communication. |
| 5 | New Since Last Email Tracking | Communication model | High | Mark cases/alerts that arrived after the last group email and include them in the next update/follow-up. |
| 6 | Communication Cooldown | Safety/noise control | Medium | Prevent repeated emails to the same building/contractor within a configured window. |
| 7 | Email Batching Policies | Communication model | Medium | Support manual-only, time-window, scheduled digest, threshold, and severity-based communication modes. |
| 8 | Internal vs External Notes | Safety | High | Keep AI hypotheses and internal pattern notes separate from external email content. |
| 9 | Client vs Contractor Email Versions | Communication model | Medium | Generate action-oriented contractor emails and awareness-focused client summaries. |
| 10 | Draft Approval Workflow | Safety | High | Move generated emails through draft, review, approved, sent, rejected, and revised states. |
| 11 | Manual Review Source Email Visibility | Review workflow | Critical | Show the received email body and metadata when a case is up for manual review. |
| 12 | Manual Review Context Upgrade | Review workflow | Critical | Show source email, case, building group, related cases, previous emails, replies, patterns, and hypotheses together. |
| 13 | Review Categories | Review workflow | Medium | Classify manual reviews by missing field, ambiguous building, ambiguous contractor, prompt injection, duplicate uncertainty, reply completion claim, etc. |
| 14 | Reply-to-Case Mapping | Reply workflow | High | Map replies to consolidated building emails back to the child cases they address. |
| 15 | Reply Completeness Assistant | Reply workflow | High | Compare replies against required response checklists and identify missing data. |
| 16 | Case Closure Assistant | Case lifecycle | Medium | Recommend possible closure candidates without closing automatically. |
| 17 | Ask for Missing Data Button | Communication quality | Medium | Generate a clarification draft asking only for missing fields/evidence. |
| 18 | Review Assignment | Operations | Later | Assign review items to roles or users. |
| 19 | Pattern-Based Connection Discovery | Intelligence | Critical | Use deterministic pattern flags as high-signal seeds for AI hypothesis generation. |
| 20 | Building-Group Connection Discovery | Intelligence | High | Run AI discovery on building/contractor groups so intelligence aligns with consolidated communications. |
| 21 | Packetized Full Supported-Data Discovery | Intelligence | High | Run discovery over all supported data using small evidence packets instead of one giant prompt. |
| 22 | Hypothesis Merge and Deduplication | Intelligence | Medium | Merge overlapping AI hypotheses and strengthen repeated evidence. |
| 23 | Hypothesis Review Workflow | Intelligence | High | Review, accept, reject, annotate, or convert AI hypotheses into rule candidates. |
| 24 | Rule Candidate Generation | Intelligence | Medium | Turn recurring accepted hypotheses into proposed deterministic rules. |
| 25 | Promote to Deterministic Rule | Intelligence | Future | After business/developer approval, convert rule candidates into deterministic pattern detection. |
| 26 | Entity Alias Management | Data quality | High | Detect and review building, contractor, client, and device aliases. |
| 27 | Device Normalization | Data quality | Medium | Normalize device identifiers such as car 1, elevator 1, 1 #12345, etc. |
| 28 | Recipient Intelligence | Contacts/routing | Medium | Suggest likely recipients from historical distribution and response patterns, review-only at first. |
| 29 | Who Usually Responds View | Contacts/routing | Medium | Show which contacts actually reply by building/contractor. |
| 30 | Response Quality Tracking | Reply workflow | High | Classify replies as complete, vague, promised action, completion claim without evidence, unrelated, etc. |
| 31 | Building Groups Page | UI | Critical | List building/contractor groups with issue counts, status, last email, and new issues. |
| 32 | Building Group Detail Page | UI | Critical | Show group summary, child cases, source emails, drafts, replies, reviews, patterns, and hypotheses. |
| 33 | Contractor Dashboard | UI | Medium | Show open buildings, cases, reviews, response history, and pattern signals by contractor. |
| 34 | Building Dashboard | UI | Medium | Rank buildings by open issue load, new issues, reviews, patterns, and last contact. |
| 35 | Needs Attention Queue | UI | High | Unified queue for manual reviews, draft approvals, reply mapping, follow-ups, hypotheses, and blocked communications. |
| 36 | Case Timeline View | UI | Medium | Show email received, case created/updated, fields extracted, group linked, draft generated, reply received, etc. |
| 37 | Building Timeline View | UI | High | Show chronological history at building/contractor group level. |
| 38 | Observability UI Page | Operations | Medium | Turn observability JSON into a human-readable dashboard. |
| 39 | Job Status Page | Operations | Medium | Show backlog import, discovery, memory rebuild, and follow-up job progress/status. |
| 40 | Demo Mode Banner | Safety/UI | High | Show a prominent banner with demo mode and actual outbound recipient override. |
| 41 | Settings Page | Operations | Medium | Show safe read-only config: demo mode, AI budget, SMTP/IMAP state, DB path, etc. |
| 42 | Natural Language Search | UI/search | Future | Allow plain-language search for operational views. |
| 43 | Saved Views | UI | Medium | Provide filters like Ready for Email, Awaiting Response, Needs Review, New Since Last Email, Active Patterns. |
| 44 | Daily Internal Summary | Reporting | Medium | Generate daily internal report of new cases, reviews, follow-ups, patterns, and AI hypotheses. |
| 45 | Contractor Weekly Digest | Reporting | Future | Weekly open issue digest per contractor/building. |
| 46 | Client Awareness Digest | Reporting | Future | Client-facing portfolio summary, separate from contractor action requests. |
| 47 | Top Problem Buildings Report | Reporting | Medium | Rank buildings by open cases, overdue count, review count, and repeated patterns. |
| 48 | Top Recurring Contractors Report | Reporting | Medium | Show recurring issue volume by contractor using neutral language. |
| 49 | Data Quality Dashboard | Operations | High | Track missing fields, unknown classification rate, review rate, parser failures, and extraction trends. |
| 50 | Parser Improvement Queue | Operations | Medium | Surface most common extraction failures as developer work items. |
| 51 | Unsupported KPI Roadmap Report | Planning | Medium | Use unsupported family counts to decide which KPI families to support next. |
| 52 | AI Cost Dashboard | Operations | High | Show AI calls, blocked calls, cache hits, usage by purpose, and estimated cost. |
| 53 | AI Cost Estimator | Operations | High | Estimate packet count, prompt size, and call budget before AI jobs run. |
| 54 | Import Progress Bar | Import | High | Show backlog import progress, counts, elapsed time, and ETA. |
| 55 | Import Resume Support | Import | Medium | Resume interrupted imports using message IDs/idempotency. |
| 56 | Backlog Performance Mode | Import | High | Use chunked/batched transactions for large imports. |
| 57 | Report Detail Modes | Import | Medium | Support summary vs full report output for large backlog runs. |
| 58 | Replay Mode | Demo/testing | Medium | Replay historical/demo email sequences into the live pipeline. |
| 59 | Demo Scenario Builder | Demo/testing | High | Build deterministic demo data with known cases, groups, patterns, replies, and hypotheses. |
| 60 | Reset Demo DB Command | Demo/testing | Medium | Safely reset only a configured demo DB with confirmation. |
| 61 | Safety Check Command | Safety | High | Validate AI budget, demo recipient override, no auto-close, no backlog outbound, unsupported exclusion, etc. |
| 62 | Draft Quality Check | Safety | High | Validate drafts before approval/send: recipients, required instructions, no internal notes, no speculation. |
| 63 | Recipient Instruction Enforcement | Safety | High | Block approval/send if required response instructions are missing. |
| 64 | Why Email Was Not Sent Log | Safety/observability | Medium | Record suppression reasons such as cooldown, missing contractor, open review, or no new issues. |
| 65 | AI Prompt Audit Page | AI governance | Medium | Show purpose, scope, included case IDs, size, validation result, and rejected outputs. |
| 66 | Prompt Size Guardrails | AI governance | High | Split or block oversized AI prompts before timeout. |
| 67 | Sandbox Mode | Testing | Medium | Process live-like inputs into an isolated DB with no outbound drafts. |
| 68 | Role-Based Views | Production | Future | Separate reviewer, manager, developer/admin experiences. |
| 69 | Configurable Taxonomy | Production | Future | Move stable case policy into config/YAML when rules mature. |
| 70 | Audit Export | Reporting | Medium | Export case/group history to CSV/PDF for review or reporting. |

## 4. Top Priority Recommendations

1. Building Issue Groups
2. Consolidated Building Emails
3. Required Response Templates by Case Type
4. Manual Review Source Email Visibility
5. New Since Last Email Tracking
6. Pattern-Based Connection Discovery
7. Reply Completeness Assistant
8. Building Group Dashboard
9. Draft Quality Check
10. AI Discovery Batching and Prompt Size Guardrails

## 5. Recommended Product Phases

### Phase 1: Communication Model
Build Building Issue Groups, consolidated drafts, required response instructions, and new-since-last-email tracking.

### Phase 2: Review Workflow
Upgrade manual review context, source email visibility, reply mapping, missing data checklists, and draft approval.

### Phase 3: Intelligence Scaling
Add pattern-based discovery, building-group discovery, packetized all-supported discovery, and hypothesis merge/dedupe.

### Phase 4: Operations and UI
Add needs-attention queue, observability page, job status, settings, demo banner, safety check, and import progress.

### Phase 5: Production Planning
Plan contact routing, auth, deployment, migration strategy, all-KPI support, and production monitoring.

## 6. Definition of Product Success

The next version is successful when a user can open the app and immediately answer:

- Which buildings need attention?
- Which contractor is responsible?
- What issues are open at that building?
- What information are we waiting for?
- What has already been communicated?
- What new issues arrived since the last email?
- What source emails support the cases?
- What patterns or AI hypotheses are worth reviewing?
- What should I do next?
