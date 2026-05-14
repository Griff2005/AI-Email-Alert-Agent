# AI Email Alert Agent Planning Docs

## Purpose

This document pack captures the full next-stage roadmap for the AI Email Alert Agent. It includes the feature ideas discussed so far, the building/contractor grouping direction, consolidated communications, manual review improvements, scalable AI connection discovery, UI/operations improvements, backend design, data model changes, testing strategy, and implementation phases.

The docs are written as implementation handoff material. A development agent should be able to use them as the source of truth for building the next major version.

## Product Direction

The product should evolve from an alert triage demo into a **building-level KPI coordination assistant**.

The system should:

- keep individual KPI cases as the source of truth,
- group open cases by building and contractor,
- send consolidated building-level emails instead of one email per alert,
- track new issues until the next building-level communication,
- show received source emails during manual review,
- tell recipients exactly what data to provide,
- use deterministic memory for known patterns,
- use AI only for bounded, reviewable hypotheses,
- keep speculative intelligence separate from confirmed facts,
- preserve safety around outbound email, AI usage, and case closure.

## Document Set

1. `01_PRODUCT_VISION_AND_FEATURE_ROADMAP.md`  
   Full feature inventory, product principles, and recommended priorities.

2. `02_BUILDING_ISSUE_GROUPS_AND_CONSOLIDATED_COMMUNICATIONS_SPEC.md`  
   Building/contractor case grouping, consolidated emails, batching, response instructions, and communication rules.

3. `03_REVIEW_REPLY_AND_CASE_WORKFLOW_SPEC.md`  
   Manual review context, source email visibility, reply mapping, missing data checklists, closure recommendations, and draft approval.

4. `04_INTELLIGENCE_MEMORY_AND_CONNECTION_DISCOVERY_SPEC.md`  
   Pattern-based discovery, building-group discovery, all-supported packetized discovery, merge/dedupe, and hypothesis review.

5. `05_UI_DASHBOARDS_AND_OPERATIONS_SPEC.md`  
   UI pages, dashboards, needs-attention queue, observability UI, settings, demo safety, and operational workflows.

6. `06_DATA_MODEL_BACKEND_AND_API_SPEC.md`  
   Database tables, backend modules, APIs/routes, CLI commands, data invariants, and migration approach.

7. `07_IMPLEMENTATION_ROADMAP_TESTING_AND_ACCEPTANCE_PLAN.md`  
   Phased implementation plan, testing strategy, acceptance criteria, safety gates, and risk register.

## Core Architecture Concept

```text
KPI Email Alert
    ↓
Individual Case
    ↓
Building Issue Group
    ↓
Consolidated Communication
    ↓
Reply / Follow-up / Review
    ↓
Memory / Patterns / AI Hypotheses
```

Individual cases are detailed issue records. Building Issue Groups coordinate communication and grouping. AI hypotheses remain reviewable suggestions, not confirmed facts.
