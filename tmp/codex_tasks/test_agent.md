# Codex Task: Final Test Agent — Backlog Loading Mode Validation

You are the final validation agent for Backlog Loading Mode.

**Do NOT modify code** unless a test command requires a tiny import/path fix, and report before making any fix.

Your working directory is the project root (`/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent`).

---

## Absolute constraints

- Do NOT run AI-enabled tests
- Do NOT call Claude
- Do NOT use --enable-ai, --live-ai, --require-ai
- Do NOT send email
- Do NOT poll IMAP
- Do NOT modify core logic files (src/backlog_loader.py, src/agent.py, src/database.py, etc.)

---

## Commands to run in order

Use `.venv/bin/python` if `.venv/bin/python` exists. Otherwise use `python3`.

First check:
```bash
ls .venv/bin/python 2>/dev/null && echo "venv available" || echo "use python3"
```

### Step 1: Compile check
```bash
.venv/bin/python -m compileall src
```
Expected: No syntax errors.

### Step 2: Existing demo still works
```bash
.venv/bin/python src/agent.py demo
```
Expected: Completes successfully, shows case results table.

### Step 3: Existing offline harness still works
```bash
.venv/bin/python src/agent.py test-demo-scale --offline --emails 25 --seed 42
```
Expected: All checks pass (PASS result).

### Step 4: Backlog dry-run
```bash
.venv/bin/python src/agent.py load-backlog --source json --path data/backlog_sample.json --dry-run
```
Expected:
- Prints summary with "DRY RUN"
- Reports ai_calls=0, outbound=0, followups=0
- Creates report files under data/backlog_runs/<timestamp>/
- Exits with code 0

### Step 5: Backlog commit
```bash
.venv/bin/python src/agent.py load-backlog --source json --path data/backlog_sample.json --commit
```
Expected:
- Prints summary with "COMMIT"
- Reports ai_calls=0, outbound=0, followups=0
- Creates report files under data/backlog_runs/<timestamp>/
- Exits with code 0

### Step 6: Verify no AI flag
After Step 5, check the report.json that was created. Confirm:
- `ai_calls == 0`
- `outbound_emails == 0`
- `followups_scheduled == 0`

### Step 7: Verify no outbound in DB
```bash
.venv/bin/python -c "import sys; sys.path.insert(0,'src'); import database as db; db.init_schema(); conn = db.get_connection(); rows = conn.execute('SELECT COUNT(*) as n FROM outbound_messages').fetchone(); print('outbound_messages count:', rows['n'])"
```
Expected: 0

### Step 8: Verify no followups in DB
```bash
.venv/bin/python -c "import sys; sys.path.insert(0,'src'); import database as db; db.init_schema(); conn = db.get_connection(); rows = conn.execute('SELECT COUNT(*) as n FROM followups').fetchone(); print('followups count:', rows['n'])"
```
Expected: 0

### Step 9: Unit tests
```bash
.venv/bin/python -m unittest discover -v
```
Expected: All tests pass (OK).

### Step 10: Error handling test — missing --dry-run/--commit
```bash
.venv/bin/python src/agent.py load-backlog --source json --path data/backlog_sample.json 2>&1 || true
```
Expected: Prints a clear error message (not a traceback) and exits non-zero.

---

## Report format

Report each step as:
```
Step N: [PASS | FAIL | WARN]
Command: <command>
Output: <relevant excerpt>
Notes: <anything notable>
```

At the end:
```
FINAL RESULT: [PASS | FAIL]
Commands run: N
Passed: N
Failed: N
Warnings: N

AI-enabled commands run: 0 (confirmed)
Real SMTP/IMAP used: No (confirmed)

Database paths used: <list>
Report paths created: <list>

Remaining concerns: <any notes>
```
