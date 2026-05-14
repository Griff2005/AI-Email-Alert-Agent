# Codex Task: Phase 4 — Documentation Updates

You are implementing Phase 4 of Backlog Loading Mode for the Email Alert Triage Agent.

**FIRST: Read `Backlog Loading Mode Design Spec.md`** — it is the source of truth.

Phases 1-3 have implemented `src/backlog_loader.py`, reporting, sample data, and tests.

Your working directory is the project root.

---

## What you must update

### `README.md`

Add a new section "Backlog Loading Mode" after the existing demo/sample sections. Include:

1. **Overview**: One paragraph — what backlog mode does (import staged historical KPI emails, build case history, no outbound, no AI).

2. **Command**:
```bash
# Dry-run (preview only, no database changes):
python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

# Commit (import into database):
python src/agent.py load-backlog --source json --path data/backlog_sample.json --commit
```

3. **Input format** — show the JSON record shape with all fields.

4. **Supported case types** — list the six types.

5. **Filtering behavior** — brief description: supported KPI only, non-KPI rejected, weak-body goes to review.

6. **Safety** — "Zero AI calls. No outbound emails. No follow-ups. No escalations."

7. **Reports** — "Reports are written to `data/backlog_runs/<timestamp>/` after each run."

8. **Optional arguments** — brief table or list of `--limit`, `--report-dir`.

Keep the section concise (under ~40 lines).

---

### `CODEBASE.md`

Add an entry for `src/backlog_loader.py` under the source files section. Include:

1. **Purpose**: Standalone backlog import workflow for staged historical KPI emails.
2. **Main entry point**: `load_backlog(source, path, dry_run, limit, report_dir)`
3. **Key functions**: brief list (normalize, classify, validate, extract, group, write reports)
4. **Safety**: zero-AI, no outbound, no follow-up, dry-run does not write DB
5. **Report output**: `data/backlog_runs/<timestamp>/`
6. **Dependencies**: database.py, classifier.py (deterministic path only), extractor.py (deterministic path only), memory.py

Keep it concise (under ~20 lines for this entry).

---

## Hard constraints

- Do NOT mention Claude, Codex, ChatGPT, or any AI assistant in documentation.
- Do NOT overstate production readiness.
- Do NOT document EML, CSV, PST, IMAP folder import, or all-KPI support as implemented — they are not.
- Do NOT add any features that don't exist yet.
- Keep documentation concise.

---

## At the end, report

- Files modified (list them)
- Documentation sections updated (brief description of each)
