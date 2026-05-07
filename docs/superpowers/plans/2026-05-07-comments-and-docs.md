# Comments, Pydocs & Documentation Webpage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Google-style docstrings and targeted inline comments to all 11 Python source files, then build a standalone dark-theme developer reference site at `docs/index.html`.

**Architecture:** Part 1 edits each Python file in-place, adding/replacing docstrings and inserting inline comments at 8 specific locations. Part 2 writes a single self-contained HTML file with inline CSS/JS — no build step required.

**Tech Stack:** Python 3, Google-style docstrings, vanilla HTML/CSS/JS, JetBrains Mono + Inter via Google Fonts CDN.

**Note:** This project has no git repository. Replace all `git commit` steps with the syntax-check verification command shown in each task.

---

## File Map

**Modified:**
- `src/config.py`
- `src/database.py`
- `src/claude_client.py`
- `src/classifier.py`
- `src/extractor.py`
- `src/case_manager.py`
- `src/email_reader.py`
- `src/email_sender.py`
- `src/followup.py`
- `src/web/app.py`
- `src/agent.py`

**Created:**
- `docs/index.html`

---

## Task 1: Pydocs — `config.py` and `claude_client.py`

**Files:** Modify `src/config.py`, `src/claude_client.py`

- [ ] **Step 1: Update `src/config.py`**

Replace the `Config` class docstring and all method docstrings with the following:

```python
class Config:
    """Central configuration object loaded from environment variables.

    All values are sourced exclusively from environment variables loaded
    from the project-root ``.env`` file via python-dotenv. Instantiated once
    at module level as the ``config`` singleton; all other modules import
    that instance directly.

    Attributes:
        AGENT_EMAIL: Inbox address the agent monitors via IMAP.
        AGENT_EMAIL_PASSWORD: App password for IMAP and SMTP authentication.
        IMAP_HOST: IMAP server hostname (e.g. ``imap.gmail.com``).
        IMAP_PORT: IMAP SSL port (default 993).
        SMTP_HOST: SMTP server hostname (e.g. ``smtp.gmail.com``).
        SMTP_PORT: SMTP STARTTLS port (default 587).
        DEMO_RECIPIENT_EMAIL: All outbound mail is redirected here in DEMO_MODE.
        DEMO_MODE: When True, enforces recipient redirect and demo disclaimers.
        CLAUDE_MODEL: Claude model identifier passed to the CLI (default Haiku).
        FLASK_HOST: Interface Flask binds to (default ``0.0.0.0``).
        FLASK_PORT: Port Flask listens on (default 5000).
        FLASK_DEBUG: Enable Flask debug mode (default False).
        DATABASE_PATH: Absolute path to the SQLite database file.
        CLAUDE_CACHE_ENABLED: Toggle the on-disk prompt/response cache.
        CLAUDE_CACHE_PATH: Absolute path to the JSON cache file.
        FOLLOWUP_CHECK_INTERVAL: Seconds between follow-up deadline checks.
    """
```

```python
    @classmethod
    def is_imap_configured(cls) -> bool:
        """Return True only if IMAP credentials are real, not placeholder values.

        Checks all three required values: host, email address, and password.
        Used by ``email_reader.poll_inbox`` and the IMAP loop in ``agent.py``
        to decide whether to attempt a real inbox connection.

        Returns:
            True if IMAP_HOST, AGENT_EMAIL, and AGENT_EMAIL_PASSWORD are all
            non-placeholder; False otherwise.
        """
```

```python
    @classmethod
    def is_smtp_configured(cls) -> bool:
        """Return True only if SMTP credentials are real, not placeholder values.

        Used by ``email_sender.send_draft`` to choose between a live send and
        a dry-run log. Only checks host and password — the sender address
        is the same credential as IMAP.

        Returns:
            True if SMTP_HOST and AGENT_EMAIL_PASSWORD are non-placeholder.
        """
```

```python
    @classmethod
    def validate(cls) -> None:
        """Log configuration status at agent startup.

        Prints warnings when credentials are placeholder values and confirms
        the demo recipient address when DEMO_MODE is active. Does not raise —
        placeholder credentials are intentional for demo mode and must not
        prevent the agent from starting.
        """
```

- [ ] **Step 2: Update `src/claude_client.py`**

Replace all function docstrings with the following. Also add the two inline injection-layer comments shown.

```python
def detect_injection(text: str) -> bool:
    """Return True if any known prompt-injection pattern is found in text.

    Scans for eight regex patterns covering common override phrasings such as
    "ignore previous instructions", "you are now", and "forget everything".
    Called on both inbound email content (layer 1) and Claude's own output
    (layer 2) to detect if injected content leaked into the response.

    Args:
        text: Raw string to scan. Not modified.

    Returns:
        True if at least one injection pattern matches; False otherwise.
    """
```

```python
def sanitize_email_content(raw: str) -> str:
    """Strip HTML, normalise whitespace, and wrap content in safety delimiters.

    Prepares untrusted email body text for safe embedding in a Claude prompt.
    Three-step process:
    1. Strip HTML tags via BeautifulSoup (falls back to regex on parse error).
    2. Collapse whitespace runs and remove blank lines.
    3. Wrap in ``--- EMAIL CONTENT START ---`` / ``--- EMAIL CONTENT END ---``
       delimiters to make the data boundary explicit in the prompt.

    Args:
        raw: Raw email body string, which may contain HTML.

    Returns:
        Sanitised plain-text string wrapped in safety delimiters, ready to
        embed directly in a Claude prompt after an instruction block.
    """
```

```python
def call_claude(prompt: str, use_cache: bool = True) -> str:
    """Call the Claude CLI with ``--print`` and return its stdout.

    Invokes ``claude --print --model <CLAUDE_MODEL>`` as a subprocess, passing
    ``prompt`` via stdin. Checks an on-disk SHA-256-keyed JSON cache before
    spawning a subprocess; stores the response on a cache miss.

    Args:
        prompt: Full prompt string to pass via stdin to the Claude CLI.
        use_cache: If True and ``CLAUDE_CACHE_ENABLED`` is set, check the
            on-disk cache before invoking the CLI and store on a miss.

    Returns:
        Claude's response as a stripped string.

    Raises:
        FileNotFoundError: If the ``claude`` binary is not found on PATH.
        RuntimeError: If the Claude CLI exits with a non-zero return code.
    """
```

Add these two inline comments at the locations described:

```python
    # Layer 1: scan inbound email content for injection attempts before sending to Claude
    injection_in_body = detect_injection(body)   # (in classify_email, classifier.py)
```

```python
    # Layer 2: scan Claude's own output — injected email content may have leaked into the response
    if detect_injection(response):               # (in call_claude, claude_client.py)
```

```python
def call_claude_json(prompt: str, use_cache: bool = True) -> dict:
    """Call Claude and parse the response as JSON.

    Wraps ``call_claude`` and strips markdown code fences (`` ```json ... ``` ``)
    before parsing so callers always receive a clean dict regardless of whether
    Claude wraps its output.

    Args:
        prompt: Full prompt string. Should instruct Claude to respond with
            valid JSON only.
        use_cache: Passed through to ``call_claude``.

    Returns:
        Parsed dict from Claude's JSON response.

    Raises:
        ValueError: If the response cannot be parsed as JSON after stripping
            code fences. Raw response is included in the error message.
        FileNotFoundError: Propagated from ``call_claude`` if CLI not found.
        RuntimeError: Propagated from ``call_claude`` on non-zero exit.
    """
```

- [ ] **Step 3: Verify syntax**

```bash
cd "/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent"
python3 -m py_compile src/config.py && echo "config.py OK"
python3 -m py_compile src/claude_client.py && echo "claude_client.py OK"
```

Expected: both print `OK` with no errors.

---

## Task 2: Pydocs — `classifier.py` and `extractor.py`

**Files:** Modify `src/classifier.py`, `src/extractor.py`

- [ ] **Step 1: Update `src/classifier.py`**

```python
def quick_filter(subject: str) -> bool:
    """Return True if the subject contains any known KPI alert trigger keyword.

    A fast pre-check performed before any Claude call. Filters out obvious
    non-KPI emails (out-of-office replies, spam, unrelated system messages)
    without spending AI tokens on them. Case-insensitive substring match
    against ``_TRIGGER_KEYWORDS``.

    Args:
        subject: Raw email subject line.

    Returns:
        True if at least one trigger keyword is found; False to skip the email.
    """
```

Add this inline comment directly above the `return` in `quick_filter`:

```python
    # Pre-filter prevents unnecessary Claude calls for non-KPI emails (replies, spam, out-of-office)
    return any(kw in subject_lower for kw in _TRIGGER_KEYWORDS)
```

```python
def classify_email(subject: str, body: str) -> Dict[str, Any]:
    """Classify an email into one of the six KPI alert case types using Claude.

    Sanitises the body before embedding it in the prompt. Detects injection in
    both subject and body before the Claude call — injection is flagged in the
    return value but does not prevent classification.

    Validates Claude's response: coerces unrecognised ``case_type`` values to
    ``'UNKNOWN'`` and clamps ``confidence`` to ``[0.0, 1.0]``.

    Args:
        subject: Email subject line.
        body: Raw email body (HTML stripped by ``sanitize_email_content``).

    Returns:
        Dict with keys:

        - ``case_type`` (str): One of the seven values in ``CASE_TYPES``.
        - ``confidence`` (float): Clamped to ``[0.0, 1.0]``.
        - ``reasoning`` (str): One-sentence explanation from Claude.
        - ``injection_detected`` (bool): True if injection patterns were found.
    """
```

- [ ] **Step 2: Update `src/extractor.py`**

```python
def extract_fields(subject: str, body: str, case_type: str) -> Dict[str, Any]:
    """Extract structured compliance fields from a KPI alert email using Claude.

    Provides the pre-classified ``case_type`` to Claude so the model focuses on
    the most relevant fields for that alert type. All 12 possible fields are
    requested; Claude returns ``null`` for those not present in the email.

    Post-processing converts ``"null"``, ``"none"``, and empty strings to
    Python ``None`` so callers can reliably test ``if field_value:``.

    Args:
        subject: Email subject line.
        body: Raw email body (HTML stripped internally).
        case_type: Pre-classified case type (e.g. ``'CAT1_COMPLIANCE'``).

    Returns:
        Dict with keys: ``building``, ``device``, ``contractor``, ``due_date``,
        ``scheduled_date``, ``period``, ``hours_required``, ``hours_actual``,
        ``description``, ``last_activity_date``, ``elapsed_days``,
        ``directive_tasks``. Each value is a non-empty string or ``None``.
    """
```

```python
def generate_grouping_key(
    case_type: str,
    building: Optional[str],
    device: Optional[str],
    period: Optional[str],
) -> str:
    """Generate a normalised deterministic deduplication key for a compliance scenario.

    Two KPI alert emails for the same building, device, and period produce the
    same key despite minor formatting differences. This key is the UNIQUE
    constraint in the ``cases`` table that prevents duplicate case creation.

    Normalisation applied to each component: lowercase, strip whitespace,
    collapse internal spaces, ``None`` → empty string.

    Key format: ``{case_type}|{building}|{device}|{period}``

    Args:
        case_type: Classified case type string.
        building: Building name/address, or None.
        device: Device identifier, or None.
        period: Reporting period string, or None.

    Returns:
        Pipe-delimited normalised string.
        Example: ``'cat1_compliance|123 example road|b-4 #731842|'``
    """
```

Add this inline comment above the `normalize` inner function:

```python
    # Normalise to handle minor formatting differences between alert emails for the same building
    def normalize(value: Optional[str]) -> str:
```

```python
def generate_email_body(case_type: str, fields: Dict[str, Any], case_id: str) -> str:
    """Generate a professional outbound follow-up email body using Claude.

    Always called with ``use_cache=False`` so each case gets a freshly written
    email. Does not include salutation or subject line — the sender module
    adds those. Instructs Claude to keep the body under 200 words and include
    a 5 business day response deadline.

    Args:
        case_type: Case type string used to frame the compliance issue.
        fields: Extracted fields dict. Only non-None values are sent to Claude.
        case_id: Case UUID included in the body for traceability.

    Returns:
        Plain-text email body string (no HTML, no markdown).
    """
```

- [ ] **Step 3: Verify syntax**

```bash
cd "/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent"
python3 -m py_compile src/classifier.py && echo "classifier.py OK"
python3 -m py_compile src/extractor.py && echo "extractor.py OK"
```

---

## Task 3: Pydocs — `email_reader.py`, `email_sender.py`, `followup.py`

**Files:** Modify `src/email_reader.py`, `src/email_sender.py`, `src/followup.py`

- [ ] **Step 1: Update `src/email_reader.py`**

```python
def _decode_header_value(raw: Optional[Any]) -> str:
    """Decode an RFC 2047-encoded email header value to a plain Python string.

    Handles encoded-word sequences (e.g. ``=?UTF-8?B?...?=``) from non-ASCII
    subject lines. Falls back to UTF-8 with ``errors='replace'`` if the
    declared charset is unrecognised.

    Args:
        raw: Raw header value — str, bytes, or encoded-header object.
            Returns empty string for None.

    Returns:
        Decoded plain-text string.
    """
```

```python
def _extract_body(msg: email.message.Message) -> str:
    """Extract the plain-text body from a parsed email message.

    Walks multipart messages collecting ``text/plain`` parts and ignoring
    attachments. Falls back to ``text/html`` if no plain-text part is found.
    All decoding uses ``errors='replace'`` to survive malformed charsets in
    third-party alert systems.

    Args:
        msg: Parsed email message from ``email.message_from_bytes``.

    Returns:
        Concatenated body text. Empty string if no suitable part is found.
    """
```

```python
def poll_inbox(mark_seen: bool = True) -> List[Dict[str, str]]:
    """Connect to the IMAP inbox and fetch all unseen messages.

    Returns an empty list when IMAP credentials are placeholder values so the
    demo runs without a real inbox. All error paths (connection failure, IMAP
    error, per-message parse error) are caught and logged — the polling loop
    in ``agent.py`` continues rather than crashing.

    Args:
        mark_seen: If True, sets the ``\\Seen`` IMAP flag on each fetched
            message so it is not returned on the next poll.

    Returns:
        List of dicts with keys: ``email_id``, ``message_id``, ``subject``,
        ``from_addr``, ``to_addr``, ``received_at``, ``raw_body``.
        Empty list on any failure.
    """
```

- [ ] **Step 2: Update `src/email_sender.py`**

```python
def create_draft(
    case_id: str,
    subject: str,
    body: str,
    intended_to: str,
    intended_cc: str = "",
) -> str:
    """Save an outbound message to the database as a draft without sending.

    Applies DEMO_MODE guardrails unconditionally when ``config.DEMO_MODE``
    is True: overrides recipient to ``DEMO_RECIPIENT_EMAIL``, prepends
    ``[DEMO]`` to the subject, and appends a disclaimer footer to the body.
    The ``intended_to`` address is stored for audit only and never used for
    actual delivery.

    Args:
        case_id: UUID of the case this message relates to.
        subject: Email subject line.
        body: Email body text.
        intended_to: Production recipient address — stored for audit, not sent to.
        intended_cc: Production CC addresses — stored for audit, not sent to.

    Returns:
        The ``msg_id`` UUID of the created draft record.
    """
```

```python
def send_draft(msg_id: str, confirm: bool = False) -> bool:
    """Send a saved draft via SMTP.

    Falls back to a dry-run log when SMTP is not configured — marks the record
    ``sent_dry_run`` and logs the subject and recipient rather than failing.

    Args:
        msg_id: UUID of the draft in ``outbound_messages``.
        confirm: Must be True to proceed. Guards against accidental sends.

    Returns:
        True if sent or logged as dry-run; False if skipped (already sent).
    """
```

```python
def create_and_send(
    case_id: str,
    subject: str,
    body: str,
    intended_to: str,
    intended_cc: str = "",
    auto_send: bool = False,
) -> str:
    """Create a draft and optionally send it immediately.

    Used by ``case_manager._create_new_case`` after generating an email body.

    Args:
        case_id: UUID of the associated case.
        subject: Email subject line.
        body: Email body text.
        intended_to: Production recipient (audit only in DEMO_MODE).
        intended_cc: Production CC addresses (audit only in DEMO_MODE).
        auto_send: If True, call ``send_draft`` immediately after creating.

    Returns:
        The ``msg_id`` UUID of the created message record.
    """
```

Add this inline comment in `create_and_send` above the `send_draft` call:

```python
        # confirm=True is correct: the demo recipient redirect in create_draft() is the
        # safety guardrail — withholding the send is unnecessary once the redirect is applied.
        send_draft(msg_id, confirm=True)
```

- [ ] **Step 3: Update `src/followup.py`**

Add this inline comment above `_ESCALATION_THRESHOLD`:

```python
# After this many unanswered follow-ups, the case is flagged for senior manual review.
_ESCALATION_THRESHOLD = 3
```

```python
def _build_followup_subject(case: dict, follow_count: int) -> str:
    """Build a numbered follow-up subject line for a case.

    Args:
        case: Case dict with at least ``building`` and ``case_type`` keys.
        follow_count: Current follow-up number (1-based).

    Returns:
        String in format: ``'Follow-Up #N: Case Type Title — Building Name'``
    """
```

```python
def check_and_process_followups() -> None:
    """Check for overdue follow-up deadlines and generate reminder emails.

    Called by APScheduler on every ``FOLLOWUP_CHECK_INTERVAL`` tick.
    For each open case whose deadline has passed:
    1. Increment ``follow_count`` in the ``followups`` table.
    2. Log a ``followup_triggered`` case event.
    3. Generate a follow-up email body via Claude (plain-text fallback on error).
    4. Create an email draft via ``email_sender.create_draft``.
    5. If ``follow_count >= _ESCALATION_THRESHOLD``, log ``escalated`` event
       and insert a ``manual_reviews`` record.
    """
```

```python
def start_scheduler() -> BackgroundScheduler:
    """Start and return the APScheduler background follow-up checker.

    Creates a daemon ``BackgroundScheduler`` (exits when the main process stops)
    and registers ``check_and_process_followups`` on an interval of
    ``config.FOLLOWUP_CHECK_INTERVAL`` seconds.

    Returns:
        The running ``BackgroundScheduler`` instance. The caller should hold
        a reference to prevent premature garbage collection.
    """
```

- [ ] **Step 4: Verify syntax**

```bash
cd "/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent"
python3 -m py_compile src/email_reader.py && echo "email_reader.py OK"
python3 -m py_compile src/email_sender.py && echo "email_sender.py OK"
python3 -m py_compile src/followup.py && echo "followup.py OK"
```

---

## Task 4: Pydocs — `database.py`

**Files:** Modify `src/database.py`

- [ ] **Step 1: Update module-level helpers**

Add this inline comment in `get_connection` above the WAL pragma:

```python
        # WAL mode lets readers proceed concurrently with the single writer —
        # critical when Flask, APScheduler, and the IMAP thread run simultaneously.
        _local.conn.execute("PRAGMA journal_mode=WAL")
```

Add this inline comment in `_execute_write` above the `with` block:

```python
    # Single write lock serialises all INSERT/UPDATE/DELETE across threads.
    # SQLite supports only one writer at a time; this prevents IntegrityErrors
    # under concurrent access from Flask routes and the APScheduler job.
    with _write_lock:
```

Replace `get_connection` docstring:

```python
def get_connection() -> sqlite3.Connection:
    """Return a per-thread SQLite connection, creating it on first call.

    Uses ``threading.local`` so each thread (Flask request handler, APScheduler
    job, IMAP polling loop) gets its own isolated connection, satisfying
    SQLite's single-thread requirement without disabling the check entirely.

    Configures each new connection with WAL journal mode for concurrent
    read access and ``PRAGMA foreign_keys=ON`` for referential integrity.

    Returns:
        An open ``sqlite3.Connection`` with ``row_factory = sqlite3.Row``.
    """
```

Replace `_execute_write` docstring:

```python
def _execute_write(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    """Execute a write statement under the module-level write lock.

    Serialises all INSERT/UPDATE/DELETE operations across threads. Read
    operations bypass this function and run directly on their thread-local
    connection, benefiting from WAL concurrent read support.

    Args:
        sql: SQL statement to execute (INSERT, UPDATE, or DELETE).
        params: Positional bind parameters for the statement.

    Returns:
        The ``sqlite3.Cursor`` from the executed statement.
    """
```

Replace `init_schema` docstring:

```python
def init_schema() -> None:
    """Create all tables and indexes if they do not yet exist.

    Safe to call on every startup — all statements use ``IF NOT EXISTS``.
    Creates seven tables and five performance indexes in a single
    ``executescript`` call under the write lock.
    """
```

- [ ] **Step 2: Update `emails` table functions**

```python
def insert_email(...) -> None:
    """Insert an inbound KPI alert email record.

    Uses ``INSERT OR IGNORE`` on the unique ``message_id`` column — calling
    this twice with the same email is safe; the second call is silently
    ignored rather than raising ``IntegrityError``.

    Args:
        email_id: Application UUID for internal references.
        message_id: RFC 2822 Message-ID header value (unique per email server).
        thread_id: IMAP thread ID, or None.
        subject: Decoded subject line.
        from_addr: Decoded sender address.
        to_addr: Decoded recipient address.
        received_at: ISO 8601 receipt timestamp.
        raw_body: Original body (may contain HTML).
        normalized_text: HTML-stripped, whitespace-normalised body.
    """
```

```python
def mark_email_processed(email_id: str) -> None:
    """Set the ``processed`` flag to 1 for the given email.

    Called at the end of ``case_manager.process_email`` once the email has
    been fully classified and its case created or updated.

    Args:
        email_id: UUID of the email to mark as processed.
    """
```

```python
def get_unprocessed_emails() -> List[sqlite3.Row]:
    """Return all emails not yet processed by the case pipeline.

    Ordered by ``received_at`` ascending (oldest first — FIFO processing).

    Returns:
        List of ``sqlite3.Row`` objects. Empty list if all emails are processed.
    """
```

```python
def get_email_by_id(email_id: str) -> Optional[sqlite3.Row]:
    """Return a single email row by its application UUID.

    Args:
        email_id: UUID assigned when the email was inserted.

    Returns:
        Matching ``sqlite3.Row``, or None if not found.
    """
```

- [ ] **Step 3: Update `cases` table functions**

```python
def get_case_by_grouping_key(grouping_key: str) -> Optional[sqlite3.Row]:
    """Return an existing case with the given grouping key, or None.

    The grouping key is the deduplication gate: if a case exists for this
    building/device/period combination, the new email updates it rather than
    creating a duplicate.

    Args:
        grouping_key: Normalised key from ``extractor.generate_grouping_key``.

    Returns:
        Matching ``sqlite3.Row``, or None if no case exists yet.
    """
```

```python
def get_case_by_id(case_id: str) -> Optional[sqlite3.Row]:
    """Return a case by its UUID.

    Args:
        case_id: UUID of the case.

    Returns:
        Matching ``sqlite3.Row``, or None if not found.
    """
```

```python
def insert_case(...) -> None:
    """Insert a new compliance case record.

    Sets ``status`` to ``'open'`` and both timestamps to the current UTC time.
    The ``grouping_key`` column has a UNIQUE constraint — a duplicate key
    raises ``sqlite3.IntegrityError``.

    Args:
        case_id: Application UUID.
        case_type: One of the six KPI case type constants.
        grouping_key: Normalised deduplication key.
        building: Building address/name, or None.
        device: Device identifier, or None.
        contractor: Contractor name, or None.
        due_date: Compliance deadline string, or None.
        period: Reporting period string, or None.
        priority: One of ``'low'``, ``'medium'``, ``'high'``, ``'critical'``.
    """
```

```python
def update_case(case_id: str, updates: Dict[str, Any]) -> None:
    """Update arbitrary fields on an existing case.

    Always appends ``updated_at = <now>`` to ``updates`` regardless of what
    other fields are included. Builds the SET clause dynamically from the
    dict keys.

    Args:
        case_id: UUID of the case to update.
        updates: Dict mapping column names to new values.
    """
```

```python
def get_all_cases(status_filter: Optional[str] = None) -> List[sqlite3.Row]:
    """Return all cases, optionally filtered by status.

    Ordered by ``created_at`` descending (newest first).

    Args:
        status_filter: ``'open'`` or ``'closed'`` to filter, or None for all.

    Returns:
        List of ``sqlite3.Row`` objects. Empty list if no cases match.
    """
```

- [ ] **Step 4: Update remaining table functions**

```python
def insert_case_event(...) -> None:
    """Append an immutable event to a case's audit trail.

    Events are never updated or deleted. Every state change is recorded here:
    creation, new email, reply, follow-up, escalation, closure.

    Args:
        event_id: Application UUID.
        case_id: UUID of the owning case.
        event_type: Short machine-readable label (e.g. ``'case_created'``,
            ``'reply_received'``, ``'email_sent'``, ``'escalated'``).
        description: Human-readable description.
        source_email_id: UUID of the triggering email, or None for system events.
    """
```

```python
def get_events_for_case(case_id: str) -> List[sqlite3.Row]:
    """Return all events for a case ordered chronologically (oldest first).

    Args:
        case_id: UUID of the case.

    Returns:
        List of ``sqlite3.Row`` objects. Empty list if no events exist.
    """
```

```python
def insert_extracted_field(...) -> None:
    """Store a single field extracted from a KPI alert email.

    Keeps raw extraction data separate from the case row so the source of
    each field value can be audited independently.

    Args:
        field_id: Application UUID.
        case_id: UUID of the case the field was extracted for.
        email_id: UUID of the email the field was extracted from.
        field_name: Field key (e.g. ``'building'``, ``'due_date'``).
        field_value: Extracted string, or None.
        confidence_score: Float 0.0–1.0.
    """
```

```python
def get_fields_for_case(case_id: str) -> List[sqlite3.Row]:
    """Return all extracted fields for a case, ordered alphabetically by name.

    Args:
        case_id: UUID of the case.

    Returns:
        List of ``sqlite3.Row`` objects.
    """
```

```python
def insert_outbound_message(...) -> None:
    """Insert a draft or sent outbound email record.

    Stores both the intended production recipient (``intended_to``) and the
    actual demo recipient (``actual_to``) separately. In DEMO_MODE these
    will differ — ``actual_to`` is always the demo address.

    Args:
        msg_id: Application UUID.
        case_id: UUID of the associated case.
        intended_to: Production recipient (audit only in DEMO_MODE).
        intended_cc: Production CC addresses (audit only in DEMO_MODE).
        actual_to: Address the email was actually sent to.
        subject: Final subject (includes ``[DEMO]`` prefix in DEMO_MODE).
        body: Final body (includes disclaimer footer in DEMO_MODE).
        status: ``'draft'`` or ``'sent'``.
    """
```

```python
def update_outbound_message_status(msg_id: str, status: str, sent_at: Optional[str] = None) -> None:
    """Update the delivery status of an outbound message.

    Args:
        msg_id: UUID of the message.
        status: New value — typically ``'sent'``, ``'sent_dry_run'``, or ``'failed'``.
        sent_at: ISO 8601 delivery timestamp. If None, ``sent_at`` column unchanged.
    """
```

```python
def get_messages_for_case(case_id: str) -> List[sqlite3.Row]:
    """Return all outbound messages for a case in insertion order.

    Args:
        case_id: UUID of the case.

    Returns:
        List of ``sqlite3.Row`` objects ordered by ``rowid`` ascending.
    """
```

```python
def upsert_followup(followup_id: str, case_id: str, deadline: str) -> None:
    """Insert a follow-up deadline for a case, ignoring duplicates.

    Uses ``INSERT OR IGNORE`` — if a record already exists for this ``case_id``
    (UNIQUE constraint), the call is a no-op. This ensures re-processing an
    email for an existing case never resets the follow-up counter.

    Args:
        followup_id: Application UUID.
        case_id: UUID of the case.
        deadline: ISO 8601 timestamp after which the follow-up should fire.
    """
```

```python
def get_overdue_followups() -> List[sqlite3.Row]:
    """Return all follow-up records whose deadline has passed and are not closed.

    Joins with ``cases`` to exclude follow-ups for already-closed cases.
    Ordered by deadline ascending (most overdue first).

    Returns:
        Rows with all ``followups`` columns plus ``case_status`` from the join.
    """
```

```python
def increment_followup_count(case_id: str) -> int:
    """Increment the follow-up counter and record the current check time.

    Thread-safe: acquires the write lock for the read-modify-write sequence.

    Args:
        case_id: UUID of the case.

    Returns:
        New ``follow_count`` value after incrementing, or 0 if not found.
    """
```

```python
def close_followup(case_id: str) -> None:
    """Mark the follow-up record for a case as closed.

    Called when a case is closed so the scheduler stops generating reminders.

    Args:
        case_id: UUID of the case.
    """
```

```python
def get_followup_for_case(case_id: str) -> Optional[sqlite3.Row]:
    """Return the follow-up record for a case.

    Args:
        case_id: UUID of the case.

    Returns:
        Matching ``sqlite3.Row``, or None if no follow-up has been scheduled.
    """
```

```python
def insert_manual_review(...) -> None:
    """Flag a case for manual human review.

    Used in four situations: low classification confidence, injection detected,
    reply suggests possible resolution, escalation threshold reached.

    Args:
        review_id: Application UUID.
        case_id: UUID of the case to flag.
        email_id: UUID of the triggering email, or None for system-triggered reviews.
        reason: Human-readable explanation of why review is required.
    """
```

```python
def get_open_manual_reviews() -> List[sqlite3.Row]:
    """Return all unresolved manual review records with case context.

    Joins with ``cases`` for ``case_type``, ``building``, and ``status``.
    Ordered by ``flagged_at`` descending (most recently flagged first).

    Returns:
        List of ``sqlite3.Row`` objects. Empty if all reviews are resolved.
    """
```

```python
def resolve_manual_review(review_id: str) -> None:
    """Mark a manual review item as resolved.

    Args:
        review_id: UUID of the review record.
    """
```

- [ ] **Step 5: Verify syntax**

```bash
cd "/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent"
python3 -m py_compile src/database.py && echo "database.py OK"
```

---

## Task 5: Pydocs — `case_manager.py`, `agent.py`, `web/app.py`

**Files:** Modify `src/case_manager.py`, `src/agent.py`, `src/web/app.py`

- [ ] **Step 1: Update `src/case_manager.py`**

```python
def process_email(
    email_id: str,
    subject: str,
    body: str,
    from_addr: str = "",
    received_at: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Run the full case pipeline for a single inbound KPI alert email.

    Seven-step pipeline:
    1. ``quick_filter`` — skip non-KPI emails without a Claude call.
    2. ``classify_email`` — identify case type and confidence score.
    3. Route low-confidence / UNKNOWN emails straight to manual review.
    4. ``extract_fields`` — pull building, device, contractor, dates, hours.
    5. ``generate_grouping_key`` — deterministic deduplication key.
    6. Create new case or update the existing one with the same key.
    7. Generate and send outbound follow-up email for new cases only.

    Args:
        email_id: UUID of the email already stored in ``emails`` table.
        subject: Email subject line.
        body: Raw email body text.
        from_addr: Sender address (informational, not used for routing).
        received_at: ISO 8601 receipt timestamp. Defaults to current UTC.
        verbose: If True, print progress messages to stdout.

    Returns:
        Dict with keys:

        - ``action`` (str): ``'created'``, ``'updated'``, ``'skipped'``, ``'review_flagged'``.
        - ``case_id`` (str | None): UUID of the affected case.
        - ``case_type`` (str): Classified case type.
        - ``grouping_key`` (str | None): Generated key.
        - ``injection_detected`` (bool): True if injection patterns were found.
    """
```

```python
def _create_new_case(
    case_id: str,
    case_type: str,
    grouping_key: str,
    email_id: str,
    fields: Dict[str, Any],
    received_at: str,
) -> None:
    """Create a new case record and trigger the initial outbound email.

    Four actions in sequence:
    1. Insert the case row with priority from ``_CASE_TYPE_PRIORITY``.
    2. Store each extracted field as a separate ``extracted_fields`` row.
    3. Schedule a follow-up deadline 7 days out.
    4. Generate email body via Claude and send to demo recipient.

    Args:
        case_id: Pre-generated UUID.
        case_type: Classified case type.
        grouping_key: Normalised deduplication key.
        email_id: UUID of the triggering email.
        fields: Extracted fields dict from ``extract_fields``.
        received_at: ISO 8601 timestamp for audit events.
    """
```

```python
def _update_existing_case(
    case_id: str,
    email_id: str,
    fields: Dict[str, Any],
    subject: str,
) -> None:
    """Update an existing case with data from a subsequent alert email.

    Appends an ``email_received`` event and refreshes case fields where the
    new email provides non-null values. Does not send a new outbound email —
    the case is already in progress.

    Args:
        case_id: UUID of the existing case.
        email_id: UUID of the new alert email.
        fields: Extracted fields from the new email.
        subject: Subject line used in the event description.
    """
```

Add this inline comment in `process_reply` above the return statement that never closes the case:

```python
    # Cases must never be closed automatically — only explicit human confirmation
    # (CLI prompt or web UI button) can set status to 'closed'.
```

```python
def process_reply(
    case_id: str,
    reply_text: str,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Analyse a manually submitted reply and update the case event log.

    Sanitises the reply and prompts Claude to assess whether it indicates the
    compliance issue has been resolved. Appends a ``reply_received`` event.
    If the reply suggests resolution, also inserts a ``manual_reviews`` record.

    Cases are NEVER auto-closed by this function. Only explicit human action
    (CLI confirmation or web UI) can change ``status`` to ``'closed'``.

    Args:
        case_id: UUID of the case the reply relates to.
        reply_text: Raw pasted reply content.
        verbose: If True, print analysis results to stdout.

    Returns:
        Dict with keys:

        - ``analysis`` (dict): Full parsed JSON from Claude's assessment.
        - ``satisfies_action`` (bool): Whether reply indicates resolution.
        - ``flagged_for_review`` (bool): Whether a manual review was created.
        - ``event_id`` (str): UUID of the ``reply_received`` event.

    Raises:
        ValueError: If ``case_id`` does not exist in the database.
    """
```

```python
def get_case_summary(case_id: str) -> Dict[str, Any]:
    """Return a combined summary dict for a case including all related records.

    Assembles case row, event log, outbound messages, extracted fields, and
    follow-up status from four queries. Used by the web UI case detail route.

    Args:
        case_id: UUID of the case.

    Returns:
        Dict with keys ``'case'``, ``'events'``, ``'messages'``, ``'fields'``,
        ``'followup'``. Returns empty dict if the case is not found.
    """
```

- [ ] **Step 2: Update `src/agent.py`**

```python
def _load_sample_emails():
    """Load and return the sample email list from ``data/sample_emails.json``.

    Returns:
        List of email dicts with keys: ``id``, ``subject``, ``from``,
        ``to``, ``date``, ``body``.

    Raises:
        SystemExit: If the file is not found at the expected path.
    """
```

```python
def _store_email(em: dict) -> str:
    """Insert a sample email dict into the database and return its email_id.

    Args:
        em: Email dict with at least ``subject``, ``from``, ``to``,
            ``date``, and ``body`` keys.

    Returns:
        The ``email_id`` string used for the inserted record.
    """
```

```python
def cmd_ingest(args):
    """Process all sample emails from ``data/sample_emails.json``.

    Safe to run multiple times — duplicate emails are ignored and existing
    cases are updated rather than duplicated.

    Args:
        args: Parsed argparse namespace (no additional attributes used).
    """
```

```python
def cmd_demo(args):
    """Run the demo: ingest all sample emails and print a formatted results table.

    Same pipeline as ``cmd_ingest`` but with formatted terminal output to
    showcase the system. Lists all case UUIDs at the end for use with
    the ``reply`` command.

    Args:
        args: Parsed argparse namespace (no additional attributes used).
    """
```

```python
def cmd_run(args):
    """Start the full agent: IMAP polling, follow-up scheduler, and Flask.

    Startup sequence:
    1. Initialise database schema.
    2. Start APScheduler background follow-up checker.
    3. If IMAP is configured: start daemon polling thread (60s interval).
    4. Start Flask web server (blocking).

    Args:
        args: Parsed argparse namespace (no additional attributes used).
    """
```

Add this inline comment in `cmd_run` above the `use_reloader=False` line:

```python
        # use_reloader=False prevents Werkzeug's reloader from forking the process,
        # which would start a second APScheduler instance and double all follow-up jobs.
        use_reloader=False,
```

```python
def cmd_reply(args):
    """Interactive CLI handler for processing a reply to a specific case.

    Prompts for reply content terminated by ``---END---``, calls
    ``case_manager.process_reply``, and if resolution is indicated, asks
    the user to confirm before closing. Cases are never closed without
    explicit human confirmation.

    Args:
        args: Parsed argparse namespace. Must include ``case_id``.
    """
```

- [ ] **Step 3: Update `src/web/app.py`**

```python
@app.route("/")
def index():
    """Redirect the root URL to the cases list."""
```

```python
@app.route("/cases")
def cases():
    """Render the case list table.

    Accepts ``?status=open`` or ``?status=closed`` query parameter for filtering.
    """
```

```python
@app.route("/cases/<case_id>")
def case_detail(case_id):
    """Render the detail page for a single case.

    Loads case row, event timeline, outbound messages, extracted fields, and
    follow-up status. Redirects with a flash error if the case is not found.
    """
```

```python
@app.route("/cases/<case_id>/close", methods=["POST"])
def close_case(case_id):
    """Manually close a case.

    Updates ``status`` to ``'closed'``, closes the follow-up record, and logs
    a ``case_closed`` event. Only reachable via explicit human form submission.
    """
```

```python
@app.route("/cases/<case_id>/resolve-review", methods=["POST"])
def resolve_review_for_case(case_id):
    """Mark a specific manual review item as resolved.

    Expects ``review_id`` in the POST form body.
    """
```

```python
@app.route("/reviews")
def reviews():
    """Render the manual review queue with case context."""
```

```python
@app.route("/events")
def events():
    """Render a global feed of the 100 most recent case events."""
```

- [ ] **Step 4: Verify syntax**

```bash
cd "/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent"
python3 -m py_compile src/case_manager.py && echo "case_manager.py OK"
python3 -m py_compile src/agent.py && echo "agent.py OK"
python3 -m py_compile src/web/app.py && echo "web/app.py OK"
```

- [ ] **Step 5: Full import check**

```bash
cd "/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent"
python3 -c "
import sys; sys.path.insert(0, 'src')
import config, database, claude_client, classifier, extractor
import case_manager, email_reader, email_sender, followup
print('All modules import cleanly')
"
```

Expected: `All modules import cleanly`

---

## Task 6: Build `docs/index.html`

**Files:** Create `docs/index.html`

- [ ] **Step 1: Create `docs/` directory**

```bash
mkdir -p "/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/docs"
```

- [ ] **Step 2: Write `docs/index.html`**

Write the complete file below to `docs/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Email Alert Triage Agent — Developer Reference</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0d1117;
    --sidebar-bg: #161b22;
    --border: #30363d;
    --text: #f0f6fc;
    --text-muted: #8b949e;
    --text-dim: #6e7681;
    --accent: #58a6ff;
    --accent-bg: #1f2d4a;
    --green: #56d364;
    --green-bg: #1a2f1e;
    --purple: #a78bfa;
    --purple-bg: #2d1b69;
    --red: #ff7b72;
    --orange: #ffa657;
    --blue-str: #a5d6ff;
    --card-bg: #161b22;
    --card-header: #1c2128;
    --sidebar-width: 240px;
    --font-sans: 'Inter', system-ui, sans-serif;
    --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
  }

  html { scroll-behavior: smooth; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-sans);
    font-size: 14px;
    line-height: 1.6;
    display: flex;
    min-height: 100vh;
  }

  /* ── Sidebar ── */
  #sidebar {
    width: var(--sidebar-width);
    background: var(--sidebar-bg);
    border-right: 1px solid var(--border);
    position: fixed;
    top: 0; left: 0; bottom: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    z-index: 10;
  }

  .sidebar-brand {
    padding: 20px 16px 16px;
    border-bottom: 1px solid var(--border);
  }
  .sidebar-brand h1 { font-size: 13px; font-weight: 700; color: var(--text); letter-spacing: 0.2px; }
  .sidebar-brand p { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

  .sidebar-nav { padding: 12px 8px; flex: 1; }

  .nav-section-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    color: var(--text-dim);
    padding: 8px 8px 4px;
    text-transform: uppercase;
  }

  .nav-link {
    display: block;
    padding: 5px 8px;
    border-radius: 4px;
    color: var(--text-muted);
    text-decoration: none;
    font-size: 13px;
    transition: color 0.15s, background 0.15s;
    cursor: pointer;
  }
  .nav-link:hover { color: var(--text); background: rgba(255,255,255,0.05); }
  .nav-link.active { color: var(--accent); background: var(--accent-bg); }

  .nav-link.sub { padding-left: 18px; font-size: 12px; }

  /* ── Main content ── */
  #content {
    margin-left: var(--sidebar-width);
    flex: 1;
    padding: 48px 56px 80px;
    max-width: 900px;
  }

  /* ── Section headings ── */
  .section { margin-bottom: 56px; }

  .section-badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: var(--accent);
    background: var(--accent-bg);
    border: 1px solid var(--accent);
    border-radius: 4px;
    padding: 2px 8px;
    margin-bottom: 10px;
  }

  h2.section-title {
    font-size: 26px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 8px;
  }

  h3.module-title {
    font-size: 20px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 6px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  h3.module-title::after { content: '▾'; font-size: 14px; color: var(--text-muted); }
  h3.module-title.collapsed::after { content: '▸'; }

  .section-desc {
    color: var(--text-muted);
    margin-bottom: 20px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 16px;
  }

  /* ── Stats row ── */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin: 24px 0;
  }
  .stat-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    text-align: center;
  }
  .stat-card .num { font-size: 28px; font-weight: 700; color: var(--text); }
  .stat-card .label { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

  /* ── Function cards ── */
  .fn-card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: 12px;
  }
  .fn-card-header {
    background: var(--card-header);
    border-bottom: 1px solid var(--border);
    padding: 10px 14px;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .fn-kind {
    font-size: 10px;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 3px;
    font-family: var(--font-mono);
  }
  .fn-kind.def  { background: var(--accent-bg); color: var(--accent); }
  .fn-kind.cls  { background: var(--purple-bg); color: var(--purple); }
  .fn-kind.meth { background: var(--green-bg);  color: var(--green); }

  .fn-name { font-family: var(--font-mono); font-size: 13px; color: #d2a8ff; font-weight: 500; }
  .fn-sig  { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); }

  .fn-card-body { padding: 12px 14px; }
  .fn-desc { color: var(--text-muted); font-size: 13px; margin-bottom: 12px; }
  .fn-desc code { background: var(--card-header); padding: 1px 5px; border-radius: 3px; font-family: var(--font-mono); font-size: 12px; color: var(--accent); }

  .args-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .args-table th { text-align: left; font-size: 10px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: var(--text-dim); padding: 4px 8px; border-bottom: 1px solid var(--border); }
  .args-table td { padding: 5px 8px; border-bottom: 1px solid rgba(48,54,61,0.5); vertical-align: top; }
  .args-table td:first-child { font-family: var(--font-mono); color: var(--orange); font-size: 11px; white-space: nowrap; }
  .args-table td:nth-child(2) { font-family: var(--font-mono); color: #79c0ff; font-size: 11px; white-space: nowrap; }
  .args-table td:last-child { color: var(--text-muted); }

  .fn-returns { margin-top: 10px; font-size: 12px; color: var(--text-muted); }
  .fn-returns .ret-label { color: var(--green); font-weight: 600; }
  .fn-raises { margin-top: 6px; font-size: 12px; }
  .fn-raises .raise-label { color: var(--red); font-weight: 600; }
  .fn-raises li { color: var(--text-muted); margin-left: 16px; }
  .fn-raises code { color: var(--red); font-family: var(--font-mono); font-size: 11px; }

  /* ── Code blocks ── */
  .code-block-wrap { position: relative; margin: 16px 0; }
  pre {
    background: var(--card-header);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px 16px;
    overflow-x: auto;
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.7;
    color: var(--text);
  }
  .copy-btn {
    position: absolute;
    top: 8px; right: 8px;
    background: var(--border);
    border: none;
    border-radius: 4px;
    color: var(--text-muted);
    font-size: 11px;
    padding: 3px 8px;
    cursor: pointer;
    font-family: var(--font-sans);
    transition: background 0.15s, color 0.15s;
  }
  .copy-btn:hover { background: var(--accent-bg); color: var(--accent); }
  .copy-btn.copied { color: var(--green); }

  /* ── Data flow strip ── */
  .flow-strip {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin: 20px 0;
  }
  .flow-node {
    padding: 5px 12px;
    border-radius: 5px;
    font-size: 12px;
    font-weight: 500;
    font-family: var(--font-mono);
    border: 1px solid;
  }
  .flow-node.blue   { background: var(--accent-bg);  border-color: var(--accent);  color: var(--accent); }
  .flow-node.green  { background: var(--green-bg);   border-color: var(--green);   color: var(--green); }
  .flow-node.purple { background: var(--purple-bg);  border-color: var(--purple);  color: var(--purple); }
  .flow-node.red    { background: #2d1717;            border-color: var(--red);     color: var(--red); }
  .flow-arrow { color: var(--text-dim); font-size: 16px; }

  /* ── Tables ── */
  table.ref-table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 16px 0; }
  table.ref-table th { text-align: left; padding: 8px 12px; background: var(--card-header); border: 1px solid var(--border); color: var(--text-muted); font-weight: 600; font-size: 12px; }
  table.ref-table td { padding: 8px 12px; border: 1px solid var(--border); color: var(--text-muted); vertical-align: top; }
  table.ref-table td:first-child { font-family: var(--font-mono); color: var(--accent); font-size: 12px; }

  /* ── Badges ── */
  .badge { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 10px; margin-right: 4px; }
  .badge.blue   { background: var(--accent-bg); color: var(--accent); }
  .badge.green  { background: var(--green-bg);  color: var(--green); }
  .badge.purple { background: var(--purple-bg); color: var(--purple); }
  .badge.red    { background: #2d1717;           color: var(--red); }
  .badge.orange { background: #2d1f0a;           color: var(--orange); }

  /* ── Collapsible module body ── */
  .module-body { overflow: hidden; transition: max-height 0.25s ease; }
  .module-body.collapsed { max-height: 0 !important; }

  /* ── Inline code ── */
  code { background: var(--card-header); padding: 1px 5px; border-radius: 3px; font-family: var(--font-mono); font-size: 12px; color: var(--accent); }

  p { color: var(--text-muted); margin-bottom: 12px; }
  ul { color: var(--text-muted); padding-left: 20px; margin-bottom: 12px; }
  li { margin-bottom: 4px; }

  .divider { border: none; border-top: 1px solid var(--border); margin: 40px 0; }
</style>
</head>
<body>

<!-- ═══════════════════════════════ SIDEBAR ═══════════════════════════════ -->
<nav id="sidebar">
  <div class="sidebar-brand">
    <h1>Email Triage Agent</h1>
    <p>Developer Reference</p>
  </div>
  <div class="sidebar-nav">
    <div class="nav-section-label">Getting Started</div>
    <a class="nav-link" href="#overview">Overview</a>
    <a class="nav-link" href="#architecture">Architecture</a>
    <a class="nav-link" href="#dataflow">Data Flow</a>
    <a class="nav-link" href="#quickstart">Quick Start</a>

    <div class="nav-section-label" style="margin-top:12px">Modules</div>
    <a class="nav-link sub" href="#mod-agent">agent.py</a>
    <a class="nav-link sub" href="#mod-config">config.py</a>
    <a class="nav-link sub" href="#mod-database">database.py</a>
    <a class="nav-link sub" href="#mod-claude">claude_client.py</a>
    <a class="nav-link sub" href="#mod-classifier">classifier.py</a>
    <a class="nav-link sub" href="#mod-extractor">extractor.py</a>
    <a class="nav-link sub" href="#mod-casemanager">case_manager.py</a>
    <a class="nav-link sub" href="#mod-emailreader">email_reader.py</a>
    <a class="nav-link sub" href="#mod-emailsender">email_sender.py</a>
    <a class="nav-link sub" href="#mod-followup">followup.py</a>
    <a class="nav-link sub" href="#mod-webapp">web/app.py</a>

    <div class="nav-section-label" style="margin-top:12px">Reference</div>
    <a class="nav-link" href="#schema">Database Schema</a>
    <a class="nav-link" href="#security">Security Model</a>
    <a class="nav-link" href="#demomode">Demo vs Production</a>
  </div>
</nav>

<!-- ═══════════════════════════════ CONTENT ═══════════════════════════════ -->
<main id="content">

  <!-- OVERVIEW -->
  <section id="overview" class="section">
    <div class="section-badge">Overview</div>
    <h2 class="section-title">Email Alert Triage Agent</h2>
    <p>An AI-powered compliance case management system for elevator KPI alerts. Classifies inbound alert emails, extracts structured fields, creates or updates compliance cases, generates professional outbound follow-up emails, and tracks deadlines with automatic escalation.</p>
    <p>The AI brain is <strong>Claude Haiku</strong>, invoked via the <code>claude --print</code> CLI in headless mode — no direct SDK dependency required.</p>
    <div class="stats-grid">
      <div class="stat-card"><div class="num">11</div><div class="label">Python modules</div></div>
      <div class="stat-card"><div class="num">6</div><div class="label">KPI case types</div></div>
      <div class="stat-card"><div class="num">7</div><div class="label">Database tables</div></div>
      <div class="stat-card"><div class="num">CLI</div><div class="label">Claude Haiku AI</div></div>
    </div>
  </section>

  <hr class="divider">

  <!-- ARCHITECTURE -->
  <section id="architecture" class="section">
    <div class="section-badge">Architecture</div>
    <h2 class="section-title">Architecture</h2>
    <p>The agent is structured as a pipeline of focused modules. Each module has one clear responsibility and communicates through well-defined function interfaces.</p>
    <div class="code-block-wrap">
      <button class="copy-btn">Copy</button>
      <pre>
  ┌─────────────────────────────────────────────────────────────┐
  │                      agent.py (CLI)                         │
  │  ingest | demo | run | reply                                │
  └──────────────┬──────────────────────────────────────────────┘
                 │
        ┌────────▼────────┐      ┌─────────────────┐
        │  email_reader   │      │    followup.py   │
        │  (IMAP polling) │      │  (APScheduler)   │
        └────────┬────────┘      └────────┬─────────┘
                 │                        │
        ┌────────▼────────────────────────▼─────────┐
        │              case_manager.py               │
        │  classify → extract → group → act          │
        └──┬──────────┬────────────────┬─────────────┘
           │          │                │
   ┌───────▼──┐ ┌─────▼─────┐ ┌──────▼──────────┐
   │classifier│ │ extractor │ │  email_sender   │
   │ (Claude) │ │ (Claude)  │ │ (SMTP + guards) │
   └──────────┘ └─────┬─────┘ └─────────────────┘
                      │
              ┌───────▼──────┐      ┌─────────┐
              │  database.py │      │web/app  │
              │  (SQLite)    │◄─────│(Flask)  │
              └──────────────┘      └─────────┘
      </pre>
    </div>
  </section>

  <hr class="divider">

  <!-- DATA FLOW -->
  <section id="dataflow" class="section">
    <div class="section-badge">Data Flow</div>
    <h2 class="section-title">Data Flow</h2>
    <p>The path of a single inbound KPI alert email through the full system:</p>
    <div class="flow-strip">
      <div class="flow-node blue">IMAP Inbox</div>
      <span class="flow-arrow">→</span>
      <div class="flow-node green">quick_filter</div>
      <span class="flow-arrow">→</span>
      <div class="flow-node green">classify_email</div>
      <span class="flow-arrow">→</span>
      <div class="flow-node green">extract_fields</div>
      <span class="flow-arrow">→</span>
      <div class="flow-node purple">case_manager</div>
      <span class="flow-arrow">→</span>
      <div class="flow-node purple">SQLite</div>
      <span class="flow-arrow">→</span>
      <div class="flow-node green">generate_email_body</div>
      <span class="flow-arrow">→</span>
      <div class="flow-node red">email_sender</div>
    </div>
    <table class="ref-table" style="margin-top:20px">
      <thead><tr><th>#</th><th>Step</th><th>Module</th><th>Description</th></tr></thead>
      <tbody>
        <tr><td>1</td><td>Receive</td><td>email_reader.py</td><td>Poll IMAP inbox for UNSEEN messages; decode headers and body</td></tr>
        <tr><td>2</td><td>Quick filter</td><td>classifier.py</td><td>Subject-line keyword check — skip non-KPI emails without a Claude call</td></tr>
        <tr><td>3</td><td>Classify</td><td>classifier.py</td><td>Claude identifies case type and confidence score</td></tr>
        <tr><td>4</td><td>Extract</td><td>extractor.py</td><td>Claude pulls building, device, contractor, dates, hours from the email</td></tr>
        <tr><td>5</td><td>Deduplicate</td><td>case_manager.py</td><td>Generate grouping key; find existing case or create new one</td></tr>
        <tr><td>6</td><td>Store</td><td>database.py</td><td>Persist case, extracted fields, follow-up deadline, and audit events</td></tr>
        <tr><td>7</td><td>Notify</td><td>email_sender.py</td><td>Generate email body via Claude; send to demo recipient via SMTP</td></tr>
      </tbody>
    </table>
  </section>

  <hr class="divider">

  <!-- QUICK START -->
  <section id="quickstart" class="section">
    <div class="section-badge">Quick Start</div>
    <h2 class="section-title">Quick Start</h2>

    <p><strong>1. Install dependencies</strong></p>
    <div class="code-block-wrap">
      <button class="copy-btn">Copy</button>
      <pre>cd "AI Email Alert Agent"
pip install -r requirements.txt</pre>
    </div>

    <p><strong>2. Configure credentials</strong></p>
    <div class="code-block-wrap">
      <button class="copy-btn">Copy</button>
      <pre>cp .env.example .env
# Edit .env — fill in AGENT_EMAIL, AGENT_EMAIL_PASSWORD,
# IMAP_HOST, SMTP_HOST, and DEMO_RECIPIENT_EMAIL</pre>
    </div>

    <p><strong>3. Run the demo (no real inbox required)</strong></p>
    <div class="code-block-wrap">
      <button class="copy-btn">Copy</button>
      <pre>python3 src/agent.py demo</pre>
    </div>

    <p><strong>4. Start the full agent + web UI</strong></p>
    <div class="code-block-wrap">
      <button class="copy-btn">Copy</button>
      <pre>python3 src/agent.py run
# Open http://localhost:5000</pre>
    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: agent.py ══════════════════════ -->
  <section id="mod-agent" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">agent.py</h3>
    <p class="section-desc">CLI entry point. Parses arguments and dispatches to one of four commands: <code>ingest</code>, <code>demo</code>, <code>run</code>, <code>reply</code>.</p>
    <div class="module-body">

      <div class="fn-card">
        <div class="fn-card-header">
          <span class="fn-kind def">def</span>
          <span class="fn-name">_load_sample_emails</span>
          <span class="fn-sig">() → list</span>
        </div>
        <div class="fn-card-body">
          <p class="fn-desc">Load and return the sample email list from <code>data/sample_emails.json</code>.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>List of email dicts with keys: <code>id</code>, <code>subject</code>, <code>from</code>, <code>to</code>, <code>date</code>, <code>body</code>.</div>
          <div class="fn-raises"><span class="raise-label">Raises: </span><ul><li><code>SystemExit</code> — If the file is not found.</li></ul></div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header">
          <span class="fn-kind def">def</span>
          <span class="fn-name">_store_email</span>
          <span class="fn-sig">(em: dict) → str</span>
        </div>
        <div class="fn-card-body">
          <p class="fn-desc">Insert a sample email dict into the database and return its <code>email_id</code>. Sanitises the body via <code>sanitize_email_content</code> before storing.</p>
          <table class="args-table"><thead><tr><th>Arg</th><th>Type</th><th>Description</th></tr></thead><tbody>
            <tr><td>em</td><td>dict</td><td>Email dict with at least <code>subject</code>, <code>from</code>, <code>to</code>, <code>date</code>, <code>body</code>.</td></tr>
          </tbody></table>
          <div class="fn-returns"><span class="ret-label">Returns: </span>The <code>email_id</code> string used for the inserted record.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header">
          <span class="fn-kind def">def</span>
          <span class="fn-name">cmd_ingest</span>
          <span class="fn-sig">(args) → None</span>
        </div>
        <div class="fn-card-body">
          <p class="fn-desc">Process all sample emails from <code>data/sample_emails.json</code>. Safe to run multiple times — duplicates are ignored.</p>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header">
          <span class="fn-kind def">def</span>
          <span class="fn-name">cmd_demo</span>
          <span class="fn-sig">(args) → None</span>
        </div>
        <div class="fn-card-body">
          <p class="fn-desc">Run the demo: ingest all sample emails and print a formatted results table with case UUIDs for use with the <code>reply</code> command.</p>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header">
          <span class="fn-kind def">def</span>
          <span class="fn-name">cmd_run</span>
          <span class="fn-sig">(args) → None</span>
        </div>
        <div class="fn-card-body">
          <p class="fn-desc">Start the full agent: initialise schema → start APScheduler → start IMAP polling thread (if configured) → start Flask (blocking). Passes <code>use_reloader=False</code> to prevent Werkzeug from forking the process and doubling the scheduler.</p>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header">
          <span class="fn-kind def">def</span>
          <span class="fn-name">cmd_reply</span>
          <span class="fn-sig">(args) → None</span>
        </div>
        <div class="fn-card-body">
          <p class="fn-desc">Interactive CLI reply handler. Prompts for reply content terminated by <code>---END---</code>, calls <code>case_manager.process_reply</code>, and if resolution is indicated, asks the user to confirm before closing. Cases are never closed without explicit confirmation.</p>
          <table class="args-table"><thead><tr><th>Arg</th><th>Type</th><th>Description</th></tr></thead><tbody>
            <tr><td>args</td><td>Namespace</td><td>Must include <code>case_id</code> attribute.</td></tr>
          </tbody></table>
        </div>
      </div>

    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: config.py ══════════════════════ -->
  <section id="mod-config" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">config.py</h3>
    <p class="section-desc">Environment configuration loader. All credentials read from <code>.env</code> via python-dotenv. Exposes a single <code>config</code> singleton imported by all other modules.</p>
    <div class="module-body">

      <div class="fn-card">
        <div class="fn-card-header">
          <span class="fn-kind cls">class</span>
          <span class="fn-name">Config</span>
        </div>
        <div class="fn-card-body">
          <p class="fn-desc">Central configuration object. All values sourced from environment variables. Instantiated once as the module-level <code>config</code> singleton.</p>
          <table class="args-table"><thead><tr><th>Attribute</th><th>Type</th><th>Description</th></tr></thead><tbody>
            <tr><td>AGENT_EMAIL</td><td>str</td><td>Inbox address the agent monitors via IMAP.</td></tr>
            <tr><td>AGENT_EMAIL_PASSWORD</td><td>str</td><td>App password for IMAP and SMTP authentication.</td></tr>
            <tr><td>IMAP_HOST</td><td>str</td><td>IMAP server hostname.</td></tr>
            <tr><td>IMAP_PORT</td><td>int</td><td>IMAP SSL port (default 993).</td></tr>
            <tr><td>SMTP_HOST</td><td>str</td><td>SMTP server hostname.</td></tr>
            <tr><td>SMTP_PORT</td><td>int</td><td>SMTP STARTTLS port (default 587).</td></tr>
            <tr><td>DEMO_RECIPIENT_EMAIL</td><td>str</td><td>All outbound mail redirected here in DEMO_MODE.</td></tr>
            <tr><td>DEMO_MODE</td><td>bool</td><td>Enforces recipient redirect and demo disclaimers.</td></tr>
            <tr><td>CLAUDE_MODEL</td><td>str</td><td>Claude model identifier passed to the CLI.</td></tr>
            <tr><td>DATABASE_PATH</td><td>Path</td><td>Absolute path to the SQLite database file.</td></tr>
            <tr><td>CLAUDE_CACHE_ENABLED</td><td>bool</td><td>Toggle the on-disk prompt/response cache.</td></tr>
            <tr><td>FOLLOWUP_CHECK_INTERVAL</td><td>int</td><td>Seconds between follow-up deadline checks.</td></tr>
          </tbody></table>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header">
          <span class="fn-kind meth">classmethod</span>
          <span class="fn-name">is_imap_configured</span>
          <span class="fn-sig">() → bool</span>
        </div>
        <div class="fn-card-body">
          <p class="fn-desc">Return True only if IMAP host, email, and password are all non-placeholder values.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>True if real IMAP credentials are present; False otherwise.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header">
          <span class="fn-kind meth">classmethod</span>
          <span class="fn-name">is_smtp_configured</span>
          <span class="fn-sig">() → bool</span>
        </div>
        <div class="fn-card-body">
          <p class="fn-desc">Return True only if SMTP host and password are non-placeholder values.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>True if real SMTP credentials are present; False otherwise.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header">
          <span class="fn-kind meth">classmethod</span>
          <span class="fn-name">validate</span>
          <span class="fn-sig">() → None</span>
        </div>
        <div class="fn-card-body">
          <p class="fn-desc">Log configuration status at startup. Prints warnings for placeholder credentials. Does not raise.</p>
        </div>
      </div>

    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: database.py ══════════════════════ -->
  <section id="mod-database" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">database.py</h3>
    <p class="section-desc">SQLite schema creation and all query helpers. Thread-safe via thread-local connections and a module-level write lock.</p>
    <div class="module-body">

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">get_connection</span><span class="fn-sig">() → sqlite3.Connection</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Return a per-thread SQLite connection, creating it on first call. Uses <code>threading.local</code> so Flask, APScheduler, and the IMAP thread each get their own connection. Configures WAL mode for concurrent reads and <code>PRAGMA foreign_keys=ON</code>.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>Open connection with <code>row_factory = sqlite3.Row</code>.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">_execute_write</span><span class="fn-sig">(sql: str, params: tuple) → Cursor</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Execute a write statement under the module-level write lock. Serialises all INSERT/UPDATE/DELETE across threads — SQLite supports only one concurrent writer.</p>
          <table class="args-table"><thead><tr><th>Arg</th><th>Type</th><th>Description</th></tr></thead><tbody>
            <tr><td>sql</td><td>str</td><td>INSERT, UPDATE, or DELETE statement.</td></tr>
            <tr><td>params</td><td>tuple</td><td>Bind parameters.</td></tr>
          </tbody></table>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">init_schema</span><span class="fn-sig">() → None</span></div>
        <div class="fn-card-body"><p class="fn-desc">Create all 7 tables and 5 indexes if they do not exist. Safe to call on every startup.</p></div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">insert_email</span><span class="fn-sig">(...) → None</span></div>
        <div class="fn-card-body"><p class="fn-desc">Insert an inbound KPI alert email. Uses <code>INSERT OR IGNORE</code> on <code>message_id</code> — safe to call twice with the same email.</p></div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">get_case_by_grouping_key</span><span class="fn-sig">(grouping_key: str) → Row | None</span></div>
        <div class="fn-card-body"><p class="fn-desc">The deduplication gate. Returns an existing case if one exists for this building/device/period combination, or None to create a new case.</p></div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">insert_case</span><span class="fn-sig">(...) → None</span></div>
        <div class="fn-card-body"><p class="fn-desc">Insert a new compliance case. Sets status to <code>'open'</code> and both timestamps to current UTC. <code>grouping_key</code> has a UNIQUE constraint.</p></div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">insert_case_event</span><span class="fn-sig">(...) → None</span></div>
        <div class="fn-card-body"><p class="fn-desc">Append an immutable event to a case's audit trail. Events are never updated or deleted. Every state change is recorded here.</p></div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">upsert_followup</span><span class="fn-sig">(followup_id, case_id, deadline) → None</span></div>
        <div class="fn-card-body"><p class="fn-desc">Insert a follow-up deadline record. <code>INSERT OR IGNORE</code> on <code>case_id</code> (UNIQUE) — re-processing an email never resets the follow-up counter.</p></div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">get_overdue_followups</span><span class="fn-sig">() → List[Row]</span></div>
        <div class="fn-card-body"><p class="fn-desc">Return all follow-up records whose deadline has passed and whose case is still open. Ordered by deadline ascending (most overdue first).</p></div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">increment_followup_count</span><span class="fn-sig">(case_id: str) → int</span></div>
        <div class="fn-card-body"><p class="fn-desc">Thread-safe read-modify-write. Increments <code>follow_count</code> and records <code>last_check</code>.</p>
        <div class="fn-returns"><span class="ret-label">Returns: </span>New <code>follow_count</code> value.</div>
        </div>
      </div>

    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: claude_client.py ══════════════════════ -->
  <section id="mod-claude" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">claude_client.py</h3>
    <p class="section-desc">Claude CLI subprocess wrapper. All AI calls in the project go through this module. Includes SHA-256 response cache and two-layer prompt injection defence.</p>
    <div class="module-body">

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">detect_injection</span><span class="fn-sig">(text: str) → bool</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Scan text for 8 prompt-injection regex patterns. Called at layer 1 (inbound email content) and layer 2 (Claude's own response) as a two-layer defence.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>True if any pattern matches.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">sanitize_email_content</span><span class="fn-sig">(raw: str) → str</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Strip HTML → collapse whitespace → wrap in <code>--- EMAIL CONTENT START/END ---</code> delimiters. Makes the data boundary explicit in every Claude prompt.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>Sanitised plain-text string ready to embed in a prompt.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">call_claude</span><span class="fn-sig">(prompt: str, use_cache: bool = True) → str</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Invoke <code>claude --print --model &lt;CLAUDE_MODEL&gt;</code> via subprocess with <code>prompt</code> on stdin. Checks the SHA-256-keyed JSON cache first; stores the response on a miss. Also scans Claude's output for injection-like language (layer 2).</p>
          <table class="args-table"><thead><tr><th>Arg</th><th>Type</th><th>Description</th></tr></thead><tbody>
            <tr><td>prompt</td><td>str</td><td>Full prompt string passed via stdin.</td></tr>
            <tr><td>use_cache</td><td>bool</td><td>Check/store in on-disk cache (default True).</td></tr>
          </tbody></table>
          <div class="fn-returns"><span class="ret-label">Returns: </span>Claude's response as a stripped string.</div>
          <div class="fn-raises"><span class="raise-label">Raises: </span><ul>
            <li><code>FileNotFoundError</code> — <code>claude</code> not found on PATH.</li>
            <li><code>RuntimeError</code> — Non-zero CLI exit code.</li>
          </ul></div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">call_claude_json</span><span class="fn-sig">(prompt: str, use_cache: bool = True) → dict</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Wraps <code>call_claude</code>. Strips markdown code fences before parsing so callers always receive a clean dict.</p>
          <div class="fn-raises"><span class="raise-label">Raises: </span><ul><li><code>ValueError</code> — Response is not valid JSON after stripping fences.</li></ul></div>
        </div>
      </div>

    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: classifier.py ══════════════════════ -->
  <section id="mod-classifier" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">classifier.py</h3>
    <p class="section-desc">Email classification via Claude CLI. Two-stage: fast keyword pre-filter, then Claude for precise classification with confidence scoring.</p>
    <div class="module-body">

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">quick_filter</span><span class="fn-sig">(subject: str) → bool</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Case-insensitive keyword check against the subject line only. Prevents unnecessary Claude calls for non-KPI emails (out-of-office replies, spam).</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>True if a KPI trigger keyword is found; False to skip.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">classify_email</span><span class="fn-sig">(subject: str, body: str) → dict</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Classify an email into one of 6 KPI case types using Claude. Runs injection detection on subject and body before the Claude call. Coerces unrecognised types to <code>'UNKNOWN'</code>; clamps confidence to <code>[0.0, 1.0]</code>.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>Dict with <code>case_type</code>, <code>confidence</code>, <code>reasoning</code>, <code>injection_detected</code>.</div>
        </div>
      </div>

    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: extractor.py ══════════════════════ -->
  <section id="mod-extractor" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">extractor.py</h3>
    <p class="section-desc">Field extraction, grouping key generation, and outbound email body generation — all via Claude CLI.</p>
    <div class="module-body">

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">extract_fields</span><span class="fn-sig">(subject, body, case_type) → dict</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Prompt Claude to extract up to 12 structured fields from a KPI alert email. All values are either a non-empty string or Python <code>None</code>.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>Dict with keys: <code>building</code>, <code>device</code>, <code>contractor</code>, <code>due_date</code>, <code>scheduled_date</code>, <code>period</code>, <code>hours_required</code>, <code>hours_actual</code>, <code>description</code>, <code>last_activity_date</code>, <code>elapsed_days</code>, <code>directive_tasks</code>.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">generate_grouping_key</span><span class="fn-sig">(case_type, building, device, period) → str</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Deterministic dedup key. Two emails for the same scenario produce the same key despite minor formatting differences. Format: <code>{case_type}|{building}|{device}|{period}</code> (all components normalised to lowercase, collapsed whitespace, empty string for None).</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>Pipe-delimited normalised string. Example: <code>cat1_compliance|123 example road|b-4 #731842|</code></div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">generate_email_body</span><span class="fn-sig">(case_type, fields, case_id) → str</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Claude writes a professional plain-text outbound email body (max 200 words, 5-day deadline). Always called with <code>use_cache=False</code> so each case gets a fresh email.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>Plain-text email body string.</div>
        </div>
      </div>

    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: case_manager.py ══════════════════════ -->
  <section id="mod-casemanager" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">case_manager.py</h3>
    <p class="section-desc">Central pipeline orchestrator. Coordinates classify → extract → deduplicate → store → notify for every inbound email.</p>
    <div class="module-body">

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">process_email</span><span class="fn-sig">(email_id, subject, body, ...) → dict</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Seven-step pipeline: quick_filter → classify → route low-confidence → extract_fields → grouping_key → create/update case → send outbound email.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>Dict with <code>action</code> (<code>'created'</code>/<code>'updated'</code>/<code>'skipped'</code>/<code>'review_flagged'</code>), <code>case_id</code>, <code>case_type</code>, <code>grouping_key</code>, <code>injection_detected</code>.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">_create_new_case</span><span class="fn-sig">(...) → None</span></div>
        <div class="fn-card-body"><p class="fn-desc">Insert case row → store extracted fields → schedule follow-up deadline → generate and send outbound email.</p></div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">_update_existing_case</span><span class="fn-sig">(...) → None</span></div>
        <div class="fn-card-body"><p class="fn-desc">Append <code>email_received</code> event and refresh any non-null fields from the new email. No new outbound email — the case is already in progress.</p></div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">process_reply</span><span class="fn-sig">(case_id, reply_text, verbose) → dict</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Analyse a pasted reply with Claude. Appends a <code>reply_received</code> event. Cases are <strong>never auto-closed</strong> — only explicit human action can set <code>status='closed'</code>.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>Dict with <code>analysis</code>, <code>satisfies_action</code>, <code>flagged_for_review</code>, <code>event_id</code>.</div>
          <div class="fn-raises"><span class="raise-label">Raises: </span><ul><li><code>ValueError</code> — If <code>case_id</code> not found.</li></ul></div>
        </div>
      </div>

    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: email_reader.py ══════════════════════ -->
  <section id="mod-emailreader" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">email_reader.py</h3>
    <p class="section-desc">IMAP inbox polling. Gracefully degrades to empty list when credentials are placeholder values.</p>
    <div class="module-body">

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">poll_inbox</span><span class="fn-sig">(mark_seen: bool = True) → list</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Connect via IMAP SSL and fetch all UNSEEN messages. All error paths return an empty list rather than raising — the polling loop continues on the next cycle.</p>
          <table class="args-table"><thead><tr><th>Arg</th><th>Type</th><th>Description</th></tr></thead><tbody>
            <tr><td>mark_seen</td><td>bool</td><td>Set the <code>\Seen</code> flag on fetched messages.</td></tr>
          </tbody></table>
          <div class="fn-returns"><span class="ret-label">Returns: </span>List of dicts with: <code>email_id</code>, <code>message_id</code>, <code>subject</code>, <code>from_addr</code>, <code>to_addr</code>, <code>received_at</code>, <code>raw_body</code>.</div>
        </div>
      </div>

    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: email_sender.py ══════════════════════ -->
  <section id="mod-emailsender" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">email_sender.py</h3>
    <p class="section-desc">SMTP outbound email with enforced demo safety guardrails. In DEMO_MODE, all mail is unconditionally redirected to <code>DEMO_RECIPIENT_EMAIL</code>.</p>
    <div class="module-body">

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">create_draft</span><span class="fn-sig">(case_id, subject, body, intended_to, ...) → str</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Save message to <code>outbound_messages</code> as <code>status='draft'</code> without sending. Applies DEMO_MODE guardrails unconditionally: recipient override, <code>[DEMO]</code> subject prefix, disclaimer footer.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span><code>msg_id</code> UUID of the created draft.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">send_draft</span><span class="fn-sig">(msg_id: str, confirm: bool = False) → bool</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Send a saved draft via SMTP. If SMTP is not configured, logs the email and marks it <code>sent_dry_run</code> instead of failing.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>True if sent or dry-run logged; False if skipped.</div>
        </div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">create_and_send</span><span class="fn-sig">(..., auto_send: bool = False) → str</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Create a draft and optionally send immediately. Passes <code>confirm=True</code> because the DEMO_MODE redirect in <code>create_draft()</code> is the safety guardrail — withholding the send is unnecessary once the redirect is applied.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span><code>msg_id</code> UUID.</div>
        </div>
      </div>

    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: followup.py ══════════════════════ -->
  <section id="mod-followup" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">followup.py</h3>
    <p class="section-desc">Background APScheduler job. Checks follow-up deadlines every 5 minutes and escalates cases after 3 unanswered follow-ups.</p>
    <div class="module-body">

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">check_and_process_followups</span><span class="fn-sig">() → None</span></div>
        <div class="fn-card-body"><p class="fn-desc">For each overdue open case: increment counter → log event → generate email draft via Claude → escalate and flag for manual review if <code>follow_count ≥ 3</code>.</p></div>
      </div>

      <div class="fn-card">
        <div class="fn-card-header"><span class="fn-kind def">def</span><span class="fn-name">start_scheduler</span><span class="fn-sig">() → BackgroundScheduler</span></div>
        <div class="fn-card-body">
          <p class="fn-desc">Create a daemon <code>BackgroundScheduler</code> and register <code>check_and_process_followups</code> on the configured interval.</p>
          <div class="fn-returns"><span class="ret-label">Returns: </span>Running <code>BackgroundScheduler</code> instance.</div>
        </div>
      </div>

    </div>
  </section>

  <hr class="divider">

  <!-- ══════════════════════ MODULE: web/app.py ══════════════════════ -->
  <section id="mod-webapp" class="section">
    <div class="section-badge">Module</div>
    <h3 class="module-title" onclick="toggleModule(this)">web/app.py</h3>
    <p class="section-desc">Flask case management UI. Serves at <code>http://localhost:5000</code> via <code>python3 src/agent.py run</code>.</p>
    <div class="module-body">

      <table class="ref-table">
        <thead><tr><th>Route</th><th>Method</th><th>Description</th></tr></thead>
        <tbody>
          <tr><td>/</td><td>GET</td><td>Redirect to <code>/cases</code>.</td></tr>
          <tr><td>/cases</td><td>GET</td><td>Case list table. Accepts <code>?status=open|closed</code> filter.</td></tr>
          <tr><td>/cases/&lt;id&gt;</td><td>GET</td><td>Case detail: events timeline, outbound messages, extracted fields, follow-up status.</td></tr>
          <tr><td>/cases/&lt;id&gt;/close</td><td>POST</td><td>Manually close a case. Logs <code>case_closed</code> event.</td></tr>
          <tr><td>/cases/&lt;id&gt;/resolve-review</td><td>POST</td><td>Resolve a manual review item by <code>review_id</code>.</td></tr>
          <tr><td>/reviews</td><td>GET</td><td>Manual review queue with case context.</td></tr>
          <tr><td>/events</td><td>GET</td><td>Global feed of 100 most recent case events.</td></tr>
        </tbody>
      </table>

    </div>
  </section>

  <hr class="divider">

  <!-- DATABASE SCHEMA -->
  <section id="schema" class="section">
    <div class="section-badge">Reference</div>
    <h2 class="section-title">Database Schema</h2>
    <p>Seven SQLite tables with five performance indexes.</p>

    <h3 style="color:var(--text);font-size:16px;margin:24px 0 8px">emails</h3>
    <p style="margin-bottom:8px">Every inbound KPI alert email received. <code>INSERT OR IGNORE</code> on <code>message_id</code> prevents duplicates.</p>
    <table class="ref-table"><thead><tr><th>Column</th><th>Type</th><th>Purpose</th></tr></thead><tbody>
      <tr><td>email_id</td><td>TEXT PK</td><td>Application UUID.</td></tr>
      <tr><td>message_id</td><td>TEXT UNIQUE</td><td>RFC 2822 Message-ID header. Dedup key.</td></tr>
      <tr><td>subject</td><td>TEXT</td><td>Decoded subject line.</td></tr>
      <tr><td>raw_body</td><td>TEXT</td><td>Original body (may contain HTML).</td></tr>
      <tr><td>normalized_text</td><td>TEXT</td><td>HTML-stripped, whitespace-normalised body.</td></tr>
      <tr><td>processed</td><td>INTEGER</td><td>0 until case pipeline completes; then 1.</td></tr>
    </tbody></table>

    <h3 style="color:var(--text);font-size:16px;margin:24px 0 8px">cases</h3>
    <p style="margin-bottom:8px">One row per active compliance case. <code>grouping_key</code> is UNIQUE — the deduplication gate.</p>
    <table class="ref-table"><thead><tr><th>Column</th><th>Type</th><th>Purpose</th></tr></thead><tbody>
      <tr><td>case_id</td><td>TEXT PK</td><td>Application UUID.</td></tr>
      <tr><td>case_type</td><td>TEXT</td><td>One of 6 KPI case type constants.</td></tr>
      <tr><td>status</td><td>TEXT</td><td><code>open</code> or <code>closed</code>.</td></tr>
      <tr><td>priority</td><td>TEXT</td><td><code>low</code> / <code>medium</code> / <code>high</code> / <code>critical</code>.</td></tr>
      <tr><td>grouping_key</td><td>TEXT UNIQUE</td><td>Normalised dedup key.</td></tr>
      <tr><td>building / device / contractor</td><td>TEXT</td><td>Extracted compliance fields.</td></tr>
      <tr><td>due_date / period</td><td>TEXT</td><td>Extracted dates.</td></tr>
    </tbody></table>

    <h3 style="color:var(--text);font-size:16px;margin:24px 0 8px">case_events</h3>
    <p>Immutable audit log. Every state change appends a row — nothing is ever deleted.</p>

    <h3 style="color:var(--text);font-size:16px;margin:24px 0 8px">extracted_fields</h3>
    <p>Raw extraction data separate from the case row — each field with its source email and confidence score.</p>

    <h3 style="color:var(--text);font-size:16px;margin:24px 0 8px">outbound_messages</h3>
    <p>Every drafted/sent email. Stores both <code>intended_to</code> (production audit) and <code>actual_to</code> (always demo address in DEMO_MODE).</p>

    <h3 style="color:var(--text);font-size:16px;margin:24px 0 8px">followups</h3>
    <p>One record per open case. Tracks deadline, last check, and follow count.</p>

    <h3 style="color:var(--text);font-size:16px;margin:24px 0 8px">manual_reviews</h3>
    <p>Cases requiring human attention: low confidence, injection detected, reply suggests resolution, escalation threshold reached.</p>

    <h3 style="color:var(--text);font-size:16px;margin:24px 0 8px">Indexes</h3>
    <table class="ref-table"><thead><tr><th>Index</th><th>Purpose</th></tr></thead><tbody>
      <tr><td>idx_cases_grouping_key</td><td>Dedup lookup on every email processed.</td></tr>
      <tr><td>idx_cases_status</td><td>Case list filter by open/closed.</td></tr>
      <tr><td>idx_case_events_case_id</td><td>Event timeline on case detail page.</td></tr>
      <tr><td>idx_followups_status</td><td>Scheduler's overdue follow-up query.</td></tr>
      <tr><td>idx_manual_reviews_resolved</td><td>Review queue page.</td></tr>
    </tbody></table>
  </section>

  <hr class="divider">

  <!-- SECURITY MODEL -->
  <section id="security" class="section">
    <div class="section-badge">Reference</div>
    <h2 class="section-title">Security Model</h2>

    <h3 style="color:var(--text);font-size:16px;margin:20px 0 8px">Prompt Injection Prevention</h3>
    <p>Every piece of email content passes through <code>sanitize_email_content()</code> before being embedded in a Claude prompt:</p>
    <ol style="color:var(--text-muted);padding-left:20px;line-height:2">
      <li><strong>HTML stripping</strong> — BeautifulSoup removes all tags.</li>
      <li><strong>Whitespace normalisation</strong> — collapses spaces/tabs, removes blank lines.</li>
      <li><strong>Delimiter wrapping</strong> — content enclosed in <code>--- EMAIL CONTENT START ---</code> / <code>--- EMAIL CONTENT END ---</code>.</li>
      <li><strong>Inbound scan</strong> (layer 1) — <code>detect_injection()</code> checks body and subject before the Claude call.</li>
      <li><strong>Output scan</strong> (layer 2) — Claude's response is also scanned for override language.</li>
    </ol>

    <h3 style="color:var(--text);font-size:16px;margin:20px 0 8px">Demo Recipient Enforcement</h3>
    <p>In <code>DEMO_MODE=true</code>, the <code>actual_to</code> override in <code>create_draft()</code> is <strong>unconditional</strong>. No calling code can bypass it. The intended production address is stored in <code>intended_to</code> for audit only.</p>

    <h3 style="color:var(--text);font-size:16px;margin:20px 0 8px">No Auto-Closure</h3>
    <p>Cases can only be closed by explicit human action: the <code>reply</code> CLI prompt or the web UI "Close Case" button. No automated process — including email content, reply analysis, or follow-up generation — can close a case.</p>
  </section>

  <hr class="divider">

  <!-- DEMO VS PRODUCTION -->
  <section id="demomode" class="section">
    <div class="section-badge">Reference</div>
    <h2 class="section-title">Demo vs Production Mode</h2>
    <p>Controlled by <code>DEMO_MODE</code> in <code>.env</code>. Classification, extraction, and case management are identical in both modes.</p>
    <table class="ref-table">
      <thead><tr><th>Behaviour</th><th>DEMO_MODE=true</th><th>DEMO_MODE=false</th></tr></thead>
      <tbody>
        <tr><td>Outbound recipient</td><td>Always <code>DEMO_RECIPIENT_EMAIL</code></td><td><code>intended_to</code> from routing rules</td></tr>
        <tr><td>Subject prefix</td><td><code>[DEMO]</code> prepended</td><td>No prefix</td></tr>
        <tr><td>Body footer</td><td>Demo disclaimer appended</td><td>No disclaimer</td></tr>
        <tr><td>SMTP not configured</td><td>Dry-run logged, marked <code>sent_dry_run</code></td><td>Same</td></tr>
        <tr><td>Intended recipient</td><td>Stored in <code>outbound_messages.intended_to</code></td><td>Same</td></tr>
      </tbody>
    </table>
  </section>

</main>

<script>
  // ── Copy buttons ──
  document.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const pre = btn.closest('.code-block-wrap').querySelector('pre');
      navigator.clipboard.writeText(pre.textContent.trim()).then(() => {
        btn.textContent = 'Copied!';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1500);
      });
    });
  });

  // ── Collapsible module sections ──
  function toggleModule(heading) {
    heading.classList.toggle('collapsed');
    const body = heading.nextElementSibling.nextElementSibling; // skip .section-desc
    if (!body || !body.classList.contains('module-body')) return;
    if (body.classList.contains('collapsed')) {
      body.style.maxHeight = body.scrollHeight + 'px';
      body.classList.remove('collapsed');
    } else {
      body.style.maxHeight = body.scrollHeight + 'px';
      requestAnimationFrame(() => body.classList.add('collapsed'));
    }
  }

  // Set initial max-height for all module bodies so collapse transition works
  document.querySelectorAll('.module-body').forEach(b => {
    b.style.maxHeight = b.scrollHeight + 'px';
  });

  // ── Active section highlight in sidebar ──
  const sections = document.querySelectorAll('section[id]');
  const navLinks = document.querySelectorAll('.nav-link');

  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        navLinks.forEach(l => l.classList.remove('active'));
        const active = document.querySelector('.nav-link[href="#' + entry.target.id + '"]');
        if (active) active.classList.add('active');
      }
    });
  }, { rootMargin: '-20% 0px -70% 0px', threshold: 0 });

  sections.forEach(s => observer.observe(s));

  // ── Smooth scroll for sidebar links ──
  navLinks.forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const target = document.querySelector(link.getAttribute('href'));
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
</script>
</body>
</html>
```

- [ ] **Step 3: Verify in browser**

Open `docs/index.html` directly in your browser:

```bash
open "/Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/docs/index.html"
```

Verify:
- Page renders with dark theme (no white flash)
- Sidebar is fixed while content scrolls
- All sidebar links scroll to the correct section
- Active link highlights as you scroll through sections
- Copy buttons on code blocks work (click one, paste somewhere to confirm)
- Clicking a module heading collapses and expands its function cards

---

## Self-Review Checklist

- [ ] All 11 files updated: config, claude_client, classifier, extractor, email_reader, email_sender, followup, database, case_manager, agent, web/app
- [ ] 8 inline comment locations covered: WAL pragma, write lock, `confirm=True`, no-auto-close, quick_filter pre-filter, grouping key normalisation, injection layer 1 + layer 2, `use_reloader=False`
- [ ] All docstrings are Google-style with Args/Returns/Raises where applicable
- [ ] `docs/index.html` opens at `file://` with no server required
- [ ] Sidebar active highlighting works while scrolling
- [ ] Copy buttons functional
- [ ] Module collapse/expand functional
