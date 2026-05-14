# Codex Fix Task: Fix Round 01 — Backlog Loading Mode

You are a careful Python developer fixing specific issues in `src/backlog_loader.py` and adjacent files.

**Working directory:** `/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent`

---

## Absolute constraints

- Do NOT call AI, enable AI, or import ai_gateway unless necessary for an existing required interface
- Do NOT add outbound messages, follow-ups, or auto-closure
- Do NOT run `--enable-ai`, `--live-ai`, `--require-ai`
- Do NOT send email or poll IMAP
- Preserve all 50 existing tests (they must still pass after your changes)
- Run tests with `.venv/bin/python -m unittest discover -v` before declaring done

---

## Use `.venv/bin/python`

```bash
ls .venv/bin/python && echo "ok"
```

---

## Fix 1: Hardcode `_SUPPORTED_CASE_TYPES` in `src/backlog_loader.py`

**Problem:** Line 27 derives `_SUPPORTED_CASE_TYPES` from `classifier.CASE_TYPES`:
```python
_SUPPORTED_CASE_TYPES = tuple(case_type for case_type in CASE_TYPES if case_type != "UNKNOWN")
```
This means any future classifier expansion silently broadens backlog scope.

**Fix:** Replace with a hardcoded frozenset. Also remove `CASE_TYPES` from the classifier import (keep `_NOISE_PATTERNS` and `_deterministic_classification`).

Replace the line at the top of the file with:
```python
_SUPPORTED_CASE_TYPES: frozenset[str] = frozenset({
    "CAT1_COMPLIANCE",
    "CAT5_COMPLIANCE",
    "DATA_ABSENCE",
    "MAINTENANCE_HOURS_SHORTFALL",
    "MAJOR_WORK_OVERDUE",
    "GOVERNMENT_DIRECTIVE",
})
```

Change the import from:
```python
from classifier import CASE_TYPES, _NOISE_PATTERNS, _deterministic_classification
```
to:
```python
from classifier import _NOISE_PATTERNS, _deterministic_classification
```

---

## Fix 2: Add backlog-specific subject gate in `src/backlog_loader.py`

**Problem:** `_classify_for_backlog` calls `_deterministic_classification()` which matches on body text as well as subject. A generic email whose body happens to contain a supported KPI phrase can slip through the supported-subject gate and become a case.

**Fix:** Add a subject-pattern table and a subject-gate check BEFORE calling `_deterministic_classification`. The subject must match a known supported pattern; if not, route to review.

Add this constant near `_SUPPORTED_CASE_TYPES` at the top of the file:
```python
_BACKLOG_SUBJECT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("cat1", "CAT1_COMPLIANCE"),
    ("cat5", "CAT5_COMPLIANCE"),
    ("data absence", "DATA_ABSENCE"),
    ("maintenance data is not up to date", "DATA_ABSENCE"),
    ("maintenance data has never been submitted", "DATA_ABSENCE"),
    ("maintenance hours less than required", "MAINTENANCE_HOURS_SHORTFALL"),
    ("maintenance hours shortfall", "MAINTENANCE_HOURS_SHORTFALL"),
    ("major scheduled work is overdue", "MAJOR_WORK_OVERDUE"),
    ("scheduled work is overdue", "MAJOR_WORK_OVERDUE"),
    ("outstanding government directive", "GOVERNMENT_DIRECTIVE"),
    ("government directive", "GOVERNMENT_DIRECTIVE"),
)
```

Add this helper function:
```python
def _match_subject_to_case_type(subject: str) -> Optional[str]:
    """Return supported case type if subject matches a known pattern, else None."""
    lowered = subject.lower()
    for pattern, case_type in _BACKLOG_SUBJECT_PATTERNS:
        if pattern in lowered:
            return case_type
    return None
```

Then rewrite `_classify_for_backlog` to gate on subject first:
```python
def _classify_for_backlog(record: Dict[str, Any]) -> Dict[str, Any]:
    subject_case_type = _match_subject_to_case_type(record["subject"])
    if subject_case_type is None:
        # Subject does not match any supported backlog KPI pattern.
        return {
            "source": "backlog_subject_gate",
            "case_type": "UNKNOWN",
            "reason": "Subject did not match any supported backlog KPI pattern.",
        }
    result = _deterministic_classification(record["subject"], record["body"])
    if result["source"] == "deterministic" and result["case_type"] in _SUPPORTED_CASE_TYPES:
        return result
    if result["source"] == "noise":
        return result
    # Subject matched but classifier is uncertain — trust the subject match.
    return {
        **result,
        "case_type": subject_case_type,
        "source": "backlog_subject_override",
        "reason": f"Subject matched {subject_case_type}; body classification was ambiguous.",
    }
```

---

## Fix 3: Fix `_synthetic_message_id` for stability and collision resistance in `src/backlog_loader.py`

**Problem 1:** The hash only covers `body[:200]`, so long emails with identical first 200 chars can collide.
**Problem 2:** When `received_at` is missing, `_generated_at_timestamp()` is used as a substitute, making the ID non-deterministic across runs.

**Fix in `_normalize_record`:** Separate the "raw received_at used for the synthetic ID" from "received_at used for storage". Store raw value for ID, use a fallback timestamp only for DB storage.

Change the relevant lines in `_normalize_record` from:
```python
received_at = str(raw.get("received_at", "")).strip() or _generated_at_timestamp()
normalized = {
    "message_id": str(raw.get("message_id", "")).strip() or _synthetic_message_id(subject, received_at, body),
    ...
    "received_at": received_at,
}
```
to:
```python
received_at_raw = str(raw.get("received_at", "")).strip()
normalized = {
    "message_id": str(raw.get("message_id", "")).strip() or _synthetic_message_id(subject, received_at_raw, body),
    ...
    "received_at": received_at_raw or _generated_at_timestamp(),
}
```

**Fix `_synthetic_message_id`** to hash the full body:
```python
def _synthetic_message_id(subject: str, received_at: str, body: str) -> str:
    """Stable synthetic ID — hashes full body; uses empty string if received_at absent."""
    digest = hashlib.sha256(f"{subject}|{received_at}|{body}".encode("utf-8")).hexdigest()
    return f"backlog:{digest}"
```

---

## Fix 4: Be honest about `manual_reviews_created` in `src/backlog_loader.py`

**Problem:** The `manual_reviews` table has `case_id TEXT NOT NULL` with a foreign key constraint. Backlog review items don't have cases created for them, so they CANNOT be inserted into `manual_reviews` without schema changes. The current code tracks `manual_reviews_created` but hardcodes it to 0, which is both accurate and misleading.

**Fix:** Remove the misleading `manual_reviews_created` tracking from the summary and report. Instead, the review items already appear in `review_candidates.json`. Make this honest:

1. Remove `manual_reviews_created` from the `summary` dict in `load_backlog()`.
2. Remove `manual_reviews_created` from `_report_json_payload()`.
3. Remove `manual_reviews_created` from the `report.md` table.
4. Remove `manual_reviews_created` from the stdout `[BACKLOG]` output.
5. Remove `manual_reviews_created: 0` from the commit return dict in `_process_record()` (line ~553).
6. Add a note in the report.md summary that review candidates are listed in `review_candidates.json` and are NOT in the live review queue.

This is the correct MVP behavior: review items are surfaced in the file report, not the live review queue (which requires a case first).

---

## Fix 5: Mark backlog-imported emails as processed in `src/backlog_loader.py`

**Problem:** `db.insert_email()` sets `processed = 0`. Backlog imports should be marked processed immediately since they are historical records, not active pipeline work. Unprocessed backlog emails make historical data look like pending operations.

**Fix:** After calling `db.insert_email(...)` in the commit path inside `_process_record`, call:
```python
db.mark_email_processed(email_id)
```

This uses the existing `mark_email_processed` helper already in `database.py`.

Add this call RIGHT AFTER the `db.insert_email(...)` block, before creating the case.

---

## Fix 6: Add `expected_memory_observations` to `report.json` payload

**Problem:** `_report_json_payload()` does not include `expected_memory_observations` in the payload dict, even though the summary dict has it. This field is missing from dry-run reports.

**Fix:** In `_report_json_payload()`, add the field to the payload:
```python
"expected_memory_observations": summary.get("expected_memory_observations", 0),
```

Add it next to `memory_observations_created`.

---

## Fix 7: Remove unnecessary AI gateway coupling from `src/backlog_loader.py`

**Problem:** `load_backlog()` imports and calls `get_ai_gateway()` just to reset it and call `build_report()`. This creates an unnecessary dependency on the AI subsystem for a zero-AI module.

**Fix:**
1. Remove `from ai_gateway import get_ai_gateway` from the imports.
2. Remove `gateway = get_ai_gateway()` and `gateway.reset()` from `load_backlog()`.
3. Replace `ai_report = gateway.build_report()` with `ai_report = {"total_ai_calls": 0}`.
4. Verify the summary still gets `"ai_calls": 0`.

---

## Fix 8: Fix documentation issues

### README.md — clarify JSON example format

The current README shows a single JSON object as the "Input format" example, but the loader requires a top-level array. The example is misleading. Fix it:

Change the section that shows the single `{...}` object to make clear it shows ONE record WITHIN a top-level array:

```markdown
Input format (top-level array of records):

```json
[
  {
    "message_id": "backlog-001@example.test",
    ...
  }
]
```
```

### README.md and CODEBASE.md — remove AI wording from backlog docs

The word "AI" appears in the backlog section of README.md and CODEBASE.md (e.g., "Safety: Zero AI calls."). The doc constraint says backlog docs should not mention AI. Replace "Zero AI calls" / "No AI processing" with "No AI. No outbound emails. No follow-ups. No escalations." in any backlog-specific doc section.

In CODEBASE.md line ~55, remove the word "AI" from the backlog_loader.py entry. Replace phrases like "zero-AI" with just "deterministic only" or "no model calls".

---

## Fix 9: Reset `data/agent.db` for clean testing

The file `data/agent.db` has stale data from a prior demo run on 2026-05-08 (4 outbound records, 4 followups, duplicate grouping keys). This causes:
- `python src/agent.py demo` to fail with `UNIQUE constraint failed: cases.grouping_key`
- DB checks for outbound_messages and followups to show non-zero counts

**Fix:** Delete `data/agent.db` to reset to a clean state. The demo will re-create it from scratch.

```bash
rm -f data/agent.db
```

Run this step FIRST before running any test commands.

---

## Verification steps (run in this order after all code fixes)

```bash
# 1. Verify db reset
rm -f data/agent.db

# 2. Compile check
.venv/bin/python -m compileall src

# 3. Full test suite — must still pass all 50 tests
.venv/bin/python -m unittest discover -v

# 4. Demo (should work now with clean DB)
.venv/bin/python src/agent.py demo

# 5. Backlog dry-run
.venv/bin/python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run

# 6. Backlog commit
.venv/bin/python src/agent.py load-backlog --source json --path data/backlog_sample.json --commit

# 7. Verify no outbound in DB (should be 0 now with clean DB)
.venv/bin/python -c "import sys; sys.path.insert(0,'src'); import database as db; db.init_schema(); conn = db.get_connection(); rows = conn.execute('SELECT COUNT(*) as n FROM outbound_messages').fetchone(); print('outbound_messages count:', rows['n'])"

# 8. Verify no followups in DB
.venv/bin/python -c "import sys; sys.path.insert(0,'src'); import database as db; db.init_schema(); conn = db.get_connection(); rows = conn.execute('SELECT COUNT(*) as n FROM followups').fetchone(); print('followups count:', rows['n'])"

# 9. Verify backlog report.json has expected_memory_observations in dry_run mode
.venv/bin/python -c "
import json, subprocess, pathlib, glob
r = subprocess.run(['.venv/bin/python', 'src/agent.py', 'load-backlog', '--source', 'json', '--path', 'data/backlog_sample.json', '--dry-run'], capture_output=True)
runs = sorted(glob.glob('data/backlog_runs/*/report.json'))
if runs:
    data = json.loads(pathlib.Path(runs[-1]).read_text())
    print('mode:', data.get('mode'))
    print('expected_memory_observations:', data.get('expected_memory_observations'))
    print('ai_calls:', data.get('ai_calls'))
"

# 10. Error handling test
.venv/bin/python src/agent.py load-backlog --source json --path data/backlog_sample.json 2>&1 | head -5 || true
```

All tests must pass. The demo must succeed. Backlog dry-run and commit must succeed. Verify:
- `expected_memory_observations` appears in dry-run report.json
- `manual_reviews_created` does NOT appear in report.json
- `ai_calls = 0` in all backlog reports
- `outbound_messages count: 0` after clean DB
- `followups count: 0` after clean DB

---

## Report format

```
Fix N: [DONE | SKIPPED | FAILED]
File: <filename>
Changes: <1-2 sentence summary>

Tests: [PASS | FAIL]
Ran: N tests, N failures

FINAL: [PASS | FAIL]
```
