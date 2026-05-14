# Codex Task: Hyper-Critical Code Review — Backlog Loading Mode

You are a hyper-critical code reviewer for the Email Alert Triage Agent repository.

**FIRST: Read `Backlog Loading Mode Design Spec.md`** — it is the source of truth.

**Do NOT edit files. Review only.**

Your working directory is the project root.

---

## Files to inspect

Read and review ALL of:
- `src/backlog_loader.py`
- `src/agent.py` (focus on the `load-backlog` command additions and `cmd_load_backlog`)
- `src/database.py` (check if backlog mode calls any disallowed helpers)
- `src/classifier.py` (verify backlog mode does not call `classify_email()` which could trigger AI)
- `src/extractor.py` (verify backlog mode does not trigger AI extraction path)
- `src/memory.py` (verify memory integration is safe)
- `tests/test_backlog_loader.py`
- `README.md` (focus on new Backlog Loading Mode section)
- `CODEBASE.md` (focus on new backlog_loader.py entry)
- `data/backlog_sample.json` (if present)

---

## Required evaluation

Evaluate against ALL of these requirements from the design spec:

### Scope
- [ ] Standalone importer (backlog_loader.py is separate from case_manager, email_sender, etc.)
- [ ] JSON source only (no EML, CSV, PST, IMAP folder import code)
- [ ] Six supported KPI case types only (no all-KPI expansion)
- [ ] `--dry-run` / `--commit` required (error if neither provided)

### Filtering
- [ ] Non-KPI emails rejected (noise filter active)
- [ ] Unsupported KPI-like emails do NOT become cases (review bucket only)
- [ ] Body signature validation implemented
- [ ] Duplicate input detection (same message_id)
- [ ] Duplicate case grouping/update works (grouping_key logic)

### Commit behavior
- [ ] Accepted KPI emails inserted into `emails` table
- [ ] Cases created or updated (not duplicated)
- [ ] Extracted fields stored
- [ ] Case events written (`backlog_case_created`, `backlog_case_updated`, `backlog_email_imported`)
- [ ] Memory observations recorded
- [ ] Pattern detection called

### Safety (CRITICAL — verify in code, not just docs)
- [ ] **ZERO AI calls** — `classify_email()` is NOT called; `get_ai_gateway().call_json()` or `.call_text()` are NOT called anywhere in backlog_loader.py
- [ ] **No outbound messages** — `outbound_messages` table is NOT written to; `email_sender` is NOT imported
- [ ] **No follow-ups** — `followups` table is NOT written to; `followup.py` is NOT imported
- [ ] **No auto-closure** — case status is never set to "closed" by backlog import
- [ ] **No escalation**
- [ ] **Dry-run does not modify DB** — confirm with test AND code inspection
- [ ] **No schema changes** — no ALTER TABLE, no new CREATE TABLE in backlog_loader.py

### Recipients
- [ ] Recipient fields captured from input records
- [ ] `recipient_summary.json` written

### Reports
- [ ] `report.json` written with required fields (including `ai_calls: 0`, `outbound_emails: 0`, `followups_scheduled: 0`)
- [ ] `report.md` written
- [ ] `rejected.json` written
- [ ] `review_candidates.json` written
- [ ] `recipient_summary.json` written
- [ ] Report dir under `data/backlog_runs/<timestamp>/`

### Tests
- [ ] `tests/test_backlog_loader.py` exists
- [ ] Dry-run does not modify DB (test exists)
- [ ] Commit imports KPI emails (test exists)
- [ ] Non-KPI emails rejected (test exists)
- [ ] Unsupported KPI not forced into case (test exists)
- [ ] Duplicate input detected (test exists)
- [ ] No outbound messages (test exists)
- [ ] No follow-ups (test exists)
- [ ] Reports written (test exists)
- [ ] AI calls = 0 asserted (test exists)
- [ ] Uses temp DB, not data/agent.db

### Code quality
- [ ] Code is not bloated (no giant harness reintroduced)
- [ ] No unnecessary coupling to case_manager, email_sender, email_reader, followup
- [ ] CLI UX is clear (error messages helpful)
- [ ] Existing demo commands not modified or broken

### Documentation
- [ ] README.md has backlog mode section with correct commands
- [ ] CODEBASE.md has backlog_loader.py entry
- [ ] Docs do not mention AI, Codex, ChatGPT, or internal tooling
- [ ] Docs do not claim unsupported features exist

---

## Return format

Return your review in this exact structure:

```
VERDICT: [APPROVED | APPROVED WITH MINOR ISSUES | NEEDS FIXES]

BLOCKING ISSUES:
1. [Issue description — file:line if possible]
...
(empty if none)

NON-BLOCKING ISSUES:
1. [Issue description]
...
(empty if none)

SUGGESTED FIXES:
1. [What to fix and where]
...
(empty if none)

FILES WITH ISSUES:
- [filename]: [brief description of issues]

TEST GAPS:
1. [Missing test or weak test]
...
(empty if none)

SAFETY CONCERNS:
1. [Specific safety issue]
...
(empty if none)
```

Be extremely critical. Do not be polite. Be useful.

A verdict of APPROVED means you are confident:
- Safety rules are enforced in code (not just docs)
- All required behavior is implemented
- Tests would catch regressions
- The existing demo pipeline is not broken
