"""
database.py — SQLite schema creation and all database query helpers.
Uses a module-level write lock for thread safety with Flask + APScheduler.
"""

import sqlite3
import threading
from typing import Any, Dict, List, Optional

from config import config
from constants import (
    EVENT_BACKLOG_CASE_CREATED,
    EVENT_BACKLOG_CASE_UPDATED,
    EVENT_BACKLOG_EMAIL_IMPORTED,
    EVENT_CASE_CREATED,
    EVENT_EMAIL_RECEIVED,
    SUPPORTED_CASE_TYPES,
)
from time_utils import utc_now_iso

# Thread-local storage for connections — each thread gets its own connection
_local = threading.local()
_write_lock = threading.Lock()

_ALLOWED_CASE_UPDATE_FIELDS = frozenset(
    {
        "status",
        "owner",
        "priority",
        "building",
        "device",
        "contractor",
        "due_date",
        "period",
    }
)
_CREATED_EMAIL_EVENT_TYPES = (EVENT_CASE_CREATED, EVENT_BACKLOG_CASE_CREATED)
_UPDATED_EMAIL_EVENT_TYPES = (
    EVENT_EMAIL_RECEIVED,
    EVENT_BACKLOG_CASE_UPDATED,
    EVENT_BACKLOG_EMAIL_IMPORTED,
)


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
    if not hasattr(_local, "conn") or _local.conn is None:
        config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(
            str(config.DATABASE_PATH),
            check_same_thread=False,
            timeout=30,
        )
        _local.conn.row_factory = sqlite3.Row
        # WAL mode lets readers proceed concurrently with the single writer —
        # critical when Flask, APScheduler, and the IMAP thread run simultaneously.
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def close_connection() -> None:
    """Close the per-thread connection if open."""
    if hasattr(_local, "conn") and _local.conn is not None:
        _local.conn.close()
        _local.conn = None


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

    Raises:
        sqlite3.OperationalError: On SQL syntax errors or schema mismatches
            (e.g. invalid column name in UPDATE).
        sqlite3.DatabaseError: On database-level errors such as disk full.
    """
    # Single write lock serialises all INSERT/UPDATE/DELETE across threads.
    # SQLite supports only one writer at a time; this prevents IntegrityErrors
    # under concurrent access from Flask routes and the APScheduler job.
    with _write_lock:
        conn = get_connection()
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor


def _column_exists(table_name: str, column_name: str) -> bool:
    """Return whether ``table_name`` currently contains ``column_name``."""
    conn = get_connection()
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def _add_column_if_missing(table_name: str, column_name: str, ddl_fragment: str) -> None:
    """Add a column with ``ddl_fragment`` only when it is absent.

    This helper is intentionally additive-only. Callers should use it while
    holding ``_write_lock`` when applying startup compatibility changes.
    """
    if _column_exists(table_name, column_name):
        return
    conn = get_connection()
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_fragment}")


def _init_compatibility_columns() -> None:
    """Apply future additive-only compatibility columns for existing databases."""
    # Reserved for future ALTER TABLE additions. Keeping this hook under the
    # schema lock gives additive compatibility changes a single startup path.
    return None


def init_schema() -> None:
    """Create all tables and indexes if they do not yet exist.

    Safe to call on every startup — all statements use ``IF NOT EXISTS``.
    Creates the operational, audit, follow-up, review, and memory tables in a
    single ``executescript`` call under the write lock.
    """
    conn = get_connection()
    with _write_lock:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS emails (
                email_id     TEXT PRIMARY KEY,
                message_id   TEXT UNIQUE,
                thread_id    TEXT,
                subject      TEXT NOT NULL,
                from_addr    TEXT,
                to_addr      TEXT,
                received_at  TEXT NOT NULL,
                raw_body     TEXT,
                normalized_text TEXT,
                processed    INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS cases (
                case_id      TEXT PRIMARY KEY,
                case_type    TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'open',
                owner        TEXT,
                priority     TEXT NOT NULL DEFAULT 'medium',
                grouping_key TEXT UNIQUE,
                building     TEXT,
                device       TEXT,
                contractor   TEXT,
                due_date     TEXT,
                period       TEXT,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS case_events (
                event_id        TEXT PRIMARY KEY,
                case_id         TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                description     TEXT,
                source_email_id TEXT,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS extracted_fields (
                field_id         TEXT PRIMARY KEY,
                case_id          TEXT NOT NULL,
                email_id         TEXT NOT NULL,
                field_name       TEXT NOT NULL,
                field_value      TEXT,
                confidence_score REAL,
                FOREIGN KEY (case_id) REFERENCES cases(case_id),
                FOREIGN KEY (email_id) REFERENCES emails(email_id)
            );

            CREATE TABLE IF NOT EXISTS outbound_messages (
                msg_id       TEXT PRIMARY KEY,
                case_id      TEXT NOT NULL,
                intended_to  TEXT,
                intended_cc  TEXT,
                actual_to    TEXT,
                subject      TEXT,
                body         TEXT,
                status       TEXT NOT NULL DEFAULT 'draft',
                sent_at      TEXT,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS followups (
                followup_id  TEXT PRIMARY KEY,
                case_id      TEXT NOT NULL UNIQUE,
                deadline     TEXT NOT NULL,
                last_check   TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                follow_count INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS followup_actions (
                action_id        TEXT PRIMARY KEY,
                case_id          TEXT NOT NULL,
                idempotency_key  TEXT NOT NULL UNIQUE,
                followup_level   INTEGER NOT NULL,
                escalation_stage TEXT NOT NULL,
                recipient_type   TEXT NOT NULL,
                scheduled_bucket TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'created',
                outbound_msg_id  TEXT,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL,
                FOREIGN KEY (case_id) REFERENCES cases(case_id),
                FOREIGN KEY (outbound_msg_id) REFERENCES outbound_messages(msg_id)
            );

            CREATE TABLE IF NOT EXISTS manual_reviews (
                review_id  TEXT PRIMARY KEY,
                case_id    TEXT NOT NULL,
                email_id   TEXT,
                reason     TEXT,
                flagged_at TEXT NOT NULL,
                resolved   INTEGER DEFAULT 0,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS entities (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type     TEXT NOT NULL,
                canonical_name  TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                metadata_json   TEXT,
                first_seen      TEXT,
                last_seen       TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(entity_type, normalized_name)
            );

            CREATE TABLE IF NOT EXISTS entity_aliases (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id        INTEGER NOT NULL,
                alias            TEXT NOT NULL,
                normalized_alias TEXT NOT NULL,
                source           TEXT,
                confidence       REAL DEFAULT 1.0,
                first_seen       TEXT,
                last_seen        TEXT,
                created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(entity_id, normalized_alias),
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            );

            CREATE TABLE IF NOT EXISTS observations (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id          TEXT,
                email_id         TEXT,
                entity_id        INTEGER,
                observation_type TEXT NOT NULL,
                entity_type      TEXT,
                entity_value     TEXT,
                value_text       TEXT,
                value_json       TEXT,
                observed_at      TEXT,
                source           TEXT,
                confidence       REAL DEFAULT 1.0,
                created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (case_id) REFERENCES cases(case_id),
                FOREIGN KEY (email_id) REFERENCES emails(email_id),
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            );

            CREATE TABLE IF NOT EXISTS case_links (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                source_case_id TEXT NOT NULL,
                target_case_id TEXT NOT NULL,
                link_type      TEXT NOT NULL,
                reason         TEXT,
                confidence     REAL DEFAULT 1.0,
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_case_id, target_case_id, link_type),
                FOREIGN KEY (source_case_id) REFERENCES cases(case_id),
                FOREIGN KEY (target_case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS pattern_flags (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id       TEXT,
                pattern_type  TEXT NOT NULL,
                severity      TEXT NOT NULL,
                summary       TEXT NOT NULL,
                evidence_json TEXT,
                status        TEXT DEFAULT 'active',
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                resolved_at   TEXT,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS connection_hypotheses (
                hypothesis_id           TEXT PRIMARY KEY,
                hypothesis_type         TEXT NOT NULL,
                summary                 TEXT NOT NULL,
                confidence              TEXT NOT NULL,
                risk_level              TEXT NOT NULL,
                evidence_json           TEXT,
                reasoning               TEXT,
                recommended_human_review TEXT,
                status                  TEXT DEFAULT 'proposed',
                source                  TEXT DEFAULT 'ai_connection_discovery',
                created_at              TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS connection_hypothesis_cases (
                hypothesis_id TEXT NOT NULL,
                case_id       TEXT NOT NULL,
                PRIMARY KEY (hypothesis_id, case_id),
                FOREIGN KEY (hypothesis_id) REFERENCES connection_hypotheses(hypothesis_id),
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS building_issue_groups (
                group_id TEXT PRIMARY KEY,
                grouping_key TEXT UNIQUE NOT NULL,
                building TEXT NOT NULL,
                normalized_building TEXT NOT NULL,
                contractor TEXT NOT NULL,
                normalized_contractor TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                health_status TEXT,
                last_email_sent_at TEXT,
                next_email_allowed_at TEXT,
                last_response_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS building_issue_group_cases (
                group_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                added_at TEXT NOT NULL,
                included_in_email_at TEXT,
                new_since_last_email INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT NOT NULL DEFAULT 'live_pipeline',
                PRIMARY KEY (group_id, case_id),
                FOREIGN KEY (group_id) REFERENCES building_issue_groups(group_id),
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE INDEX IF NOT EXISTS idx_connection_hypotheses_status ON connection_hypotheses(status);
            CREATE INDEX IF NOT EXISTS idx_connection_hypothesis_cases_case_id ON connection_hypothesis_cases(case_id);

            CREATE INDEX IF NOT EXISTS idx_building_issue_groups_key ON building_issue_groups(grouping_key);
            CREATE INDEX IF NOT EXISTS idx_building_issue_groups_status ON building_issue_groups(status);
            CREATE INDEX IF NOT EXISTS idx_building_issue_groups_normalized
                ON building_issue_groups(normalized_building, normalized_contractor);
            CREATE INDEX IF NOT EXISTS idx_building_issue_groups_updated_at ON building_issue_groups(updated_at);
            CREATE INDEX IF NOT EXISTS idx_building_issue_group_cases_case_id ON building_issue_group_cases(case_id);
            CREATE INDEX IF NOT EXISTS idx_building_issue_group_cases_status ON building_issue_group_cases(status);
            CREATE INDEX IF NOT EXISTS idx_building_issue_group_cases_new ON building_issue_group_cases(new_since_last_email);
            CREATE INDEX IF NOT EXISTS idx_building_issue_group_cases_source ON building_issue_group_cases(source);

            CREATE INDEX IF NOT EXISTS idx_cases_grouping_key ON cases(grouping_key);
            CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
            CREATE INDEX IF NOT EXISTS idx_case_events_case_id ON case_events(case_id);
            CREATE INDEX IF NOT EXISTS idx_followups_status ON followups(status);
            CREATE INDEX IF NOT EXISTS idx_followup_actions_case_id ON followup_actions(case_id);
            CREATE INDEX IF NOT EXISTS idx_followup_actions_status ON followup_actions(status);
            CREATE INDEX IF NOT EXISTS idx_manual_reviews_resolved ON manual_reviews(resolved);
            CREATE INDEX IF NOT EXISTS idx_entities_type_name ON entities(entity_type, normalized_name);
            CREATE INDEX IF NOT EXISTS idx_observations_case_id ON observations(case_id);
            CREATE INDEX IF NOT EXISTS idx_observations_entity_lookup ON observations(entity_type, entity_value);
            CREATE INDEX IF NOT EXISTS idx_observations_type_date ON observations(observation_type, observed_at);
            CREATE INDEX IF NOT EXISTS idx_pattern_flags_case_status ON pattern_flags(case_id, status);
            CREATE INDEX IF NOT EXISTS idx_pattern_flags_type_status ON pattern_flags(pattern_type, status);
            CREATE INDEX IF NOT EXISTS idx_case_links_source_case_id ON case_links(source_case_id);
            CREATE INDEX IF NOT EXISTS idx_case_links_target_case_id ON case_links(target_case_id);
        """)
        _init_compatibility_columns()
        conn.commit()
    print("[DB] Schema initialized.")


# ---------------------------------------------------------------------------
# emails table
# ---------------------------------------------------------------------------

def insert_email(
    email_id: str,
    message_id: str,
    thread_id: Optional[str],
    subject: str,
    from_addr: str,
    to_addr: str,
    received_at: str,
    raw_body: str,
    normalized_text: str,
) -> None:
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
    _execute_write(
        """
        INSERT OR IGNORE INTO emails
            (email_id, message_id, thread_id, subject, from_addr, to_addr,
             received_at, raw_body, normalized_text, processed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (email_id, message_id, thread_id, subject, from_addr, to_addr,
         received_at, raw_body, normalized_text),
    )


def mark_email_processed(email_id: str) -> None:
    """Set the ``processed`` flag to 1 for the given email.

    Called at the end of ``case_manager.process_email`` once the email has
    been fully classified and its case created or updated.

    Args:
        email_id: UUID of the email to mark as processed.
    """
    _execute_write(
        "UPDATE emails SET processed = 1 WHERE email_id = ?",
        (email_id,),
    )


def get_unprocessed_emails() -> List[sqlite3.Row]:
    """Return all emails not yet processed by the case pipeline.

    Ordered by ``received_at`` ascending (oldest first — FIFO processing).

    Returns:
        List of ``sqlite3.Row`` objects. Empty list if all emails are processed.
    """
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM emails WHERE processed = 0 ORDER BY received_at ASC"
    ).fetchall()


def get_email_by_id(email_id: str) -> Optional[sqlite3.Row]:
    """Return a single email row by its application UUID.

    Args:
        email_id: UUID assigned when the email was inserted.

    Returns:
        Matching ``sqlite3.Row``, or None if not found.
    """
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM emails WHERE email_id = ?", (email_id,)
    ).fetchone()


def get_email_by_message_id(message_id: str) -> Optional[sqlite3.Row]:
    """Return a single email row by its unique message identifier."""
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM emails WHERE message_id = ?",
        (message_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# cases table
# ---------------------------------------------------------------------------

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
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM cases WHERE grouping_key = ?", (grouping_key,)
    ).fetchone()


def get_case_by_id(case_id: str) -> Optional[sqlite3.Row]:
    """Return a case by its UUID.

    Args:
        case_id: UUID of the case.

    Returns:
        Matching ``sqlite3.Row``, or None if not found.
    """
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM cases WHERE case_id = ?", (case_id,)
    ).fetchone()


def insert_case(
    case_id: str,
    case_type: str,
    grouping_key: str,
    building: Optional[str],
    device: Optional[str],
    contractor: Optional[str],
    due_date: Optional[str],
    period: Optional[str],
    priority: str = "medium",
) -> None:
    """Insert a new compliance case record.

    Sets ``status`` to ``'open'`` and both timestamps to the current UTC time.

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

    Raises:
        sqlite3.IntegrityError: If a case with the same ``grouping_key``
            already exists (UNIQUE constraint violation).
    """
    now = utc_now_iso()
    _execute_write(
        """
        INSERT INTO cases
            (case_id, case_type, status, priority, grouping_key,
             building, device, contractor, due_date, period, created_at, updated_at)
        VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (case_id, case_type, priority, grouping_key,
         building, device, contractor, due_date, period, now, now),
    )


def update_case(case_id: str, updates: Dict[str, Any]) -> None:
    """Update allowed mutable fields on an existing case.

    Always writes ``updated_at = <now>`` alongside the supplied fields. The
    caller's ``updates`` dict is not mutated.

    Args:
        case_id: UUID of the case to update.
        updates: Dict mapping column names to new values.

    Raises:
        ValueError: If ``updates`` contains a field outside the case update
            allowlist.
    """
    unsupported_fields = sorted(set(updates) - _ALLOWED_CASE_UPDATE_FIELDS)
    if unsupported_fields:
        raise ValueError(
            "Unsupported case update field(s): "
            + ", ".join(unsupported_fields)
        )
    values_to_update = dict(updates)
    values_to_update["updated_at"] = utc_now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in values_to_update)
    values = list(values_to_update.values()) + [case_id]
    _execute_write(
        f"UPDATE cases SET {set_clause} WHERE case_id = ?",
        tuple(values),
    )


def get_all_cases(status_filter: Optional[str] = None) -> List[sqlite3.Row]:
    """Return all cases, optionally filtered by status.

    Ordered by ``created_at`` descending (newest first).

    Args:
        status_filter: ``'open'`` or ``'closed'`` to filter, or None for all.

    Returns:
        List of ``sqlite3.Row`` objects. Empty list if no cases match.
    """
    conn = get_connection()
    if status_filter:
        return conn.execute(
            "SELECT * FROM cases WHERE status = ? ORDER BY created_at DESC",
            (status_filter,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM cases ORDER BY created_at DESC"
    ).fetchall()


# ---------------------------------------------------------------------------
# case_events table
# ---------------------------------------------------------------------------

def insert_case_event(
    event_id: str,
    case_id: str,
    event_type: str,
    description: str,
    source_email_id: Optional[str] = None,
) -> None:
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
    now = utc_now_iso()
    _execute_write(
        """
        INSERT INTO case_events
            (event_id, case_id, event_type, description, source_email_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (event_id, case_id, event_type, description, source_email_id, now),
    )


def get_events_for_case(case_id: str) -> List[sqlite3.Row]:
    """Return all events for a case ordered chronologically (oldest first).

    Args:
        case_id: UUID of the case.

    Returns:
        List of ``sqlite3.Row`` objects. Empty list if no events exist.
    """
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM case_events WHERE case_id = ? ORDER BY created_at ASC",
        (case_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# extracted_fields table
# ---------------------------------------------------------------------------

def insert_extracted_field(
    field_id: str,
    case_id: str,
    email_id: str,
    field_name: str,
    field_value: Optional[str],
    confidence_score: float,
) -> None:
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
    _execute_write(
        """
        INSERT INTO extracted_fields
            (field_id, case_id, email_id, field_name, field_value, confidence_score)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (field_id, case_id, email_id, field_name, field_value, confidence_score),
    )


def get_fields_for_case(case_id: str) -> List[sqlite3.Row]:
    """Return all extracted fields for a case, ordered alphabetically by name.

    Args:
        case_id: UUID of the case.

    Returns:
        List of ``sqlite3.Row`` objects.
    """
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM extracted_fields WHERE case_id = ? ORDER BY field_name ASC",
        (case_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# outbound_messages table
# ---------------------------------------------------------------------------

def insert_outbound_message(
    msg_id: str,
    case_id: str,
    intended_to: str,
    intended_cc: str,
    actual_to: str,
    subject: str,
    body: str,
    status: str = "draft",
) -> None:
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
    _execute_write(
        """
        INSERT INTO outbound_messages
            (msg_id, case_id, intended_to, intended_cc, actual_to,
             subject, body, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (msg_id, case_id, intended_to, intended_cc, actual_to, subject, body, status),
    )


def update_outbound_message_status(msg_id: str, status: str, sent_at: Optional[str] = None) -> None:
    """Update the delivery status of an outbound message.

    Args:
        msg_id: UUID of the message.
        status: New value — typically ``'sent'``, ``'sent_dry_run'``, or ``'failed'``.
        sent_at: ISO 8601 delivery timestamp. If None, ``sent_at`` column unchanged.
    """
    if sent_at:
        _execute_write(
            "UPDATE outbound_messages SET status = ?, sent_at = ? WHERE msg_id = ?",
            (status, sent_at, msg_id),
        )
    else:
        _execute_write(
            "UPDATE outbound_messages SET status = ? WHERE msg_id = ?",
            (status, msg_id),
        )


def get_messages_for_case(case_id: str) -> List[sqlite3.Row]:
    """Return all outbound messages for a case in insertion order.

    Args:
        case_id: UUID of the case.

    Returns:
        List of ``sqlite3.Row`` objects ordered by ``rowid`` ascending.
    """
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM outbound_messages WHERE case_id = ? ORDER BY rowid ASC",
        (case_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# followups table
# ---------------------------------------------------------------------------

def upsert_followup(
    followup_id: str,
    case_id: str,
    deadline: str,
) -> None:
    """Insert a follow-up deadline for a case, ignoring duplicates.

    Uses ``INSERT OR IGNORE`` — if a record already exists for this ``case_id``
    (UNIQUE constraint), the call is a no-op. This ensures re-processing an
    email for an existing case never resets the follow-up counter.

    Args:
        followup_id: Application UUID.
        case_id: UUID of the case.
        deadline: ISO 8601 timestamp after which the follow-up should fire.
    """
    _execute_write(
        """
        INSERT OR IGNORE INTO followups
            (followup_id, case_id, deadline, status, follow_count)
        VALUES (?, ?, ?, 'pending', 0)
        """,
        (followup_id, case_id, deadline),
    )


def get_overdue_followups() -> List[sqlite3.Row]:
    """Return all follow-up records whose deadline has passed and are not closed.

    Joins with ``cases`` to exclude follow-ups for already-closed cases.
    Ordered by deadline ascending (most overdue first).

    Returns:
        Rows with all ``followups`` columns plus ``case_status`` from the join.
    """
    now = utc_now_iso()
    conn = get_connection()
    return conn.execute(
        """
        SELECT f.*, c.status as case_status
        FROM followups f
        JOIN cases c ON f.case_id = c.case_id
        WHERE f.deadline <= ?
          AND f.status != 'closed'
          AND c.status != 'closed'
        ORDER BY f.deadline ASC
        """,
        (now,),
    ).fetchall()


def increment_followup_count(case_id: str) -> int:
    """Increment the follow-up counter and record the current check time.

    Thread-safe: acquires the write lock for the read-modify-write sequence.

    Args:
        case_id: UUID of the case.

    Returns:
        New ``follow_count`` value after incrementing, or 0 if not found.
    """
    now = utc_now_iso()
    with _write_lock:
        conn = get_connection()
        conn.execute(
            """
            UPDATE followups
            SET follow_count = follow_count + 1,
                last_check = ?
            WHERE case_id = ?
            """,
            (now, case_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT follow_count FROM followups WHERE case_id = ?", (case_id,)
        ).fetchone()
    return row["follow_count"] if row else 0


def reschedule_followup(case_id: str, deadline: str) -> None:
    """Move the next follow-up deadline forward after a successful reminder."""
    now = utc_now_iso()
    _execute_write(
        """
        UPDATE followups
        SET deadline = ?,
            last_check = ?
        WHERE case_id = ?
        """,
        (deadline, now, case_id),
    )


def close_followup(case_id: str) -> None:
    """Mark the follow-up record for a case as closed.

    Called when a case is closed so the scheduler stops generating reminders.

    Args:
        case_id: UUID of the case.
    """
    _execute_write(
        "UPDATE followups SET status = 'closed' WHERE case_id = ?",
        (case_id,),
    )


def get_followup_for_case(case_id: str) -> Optional[sqlite3.Row]:
    """Return the follow-up record for a case.

    Args:
        case_id: UUID of the case.

    Returns:
        Matching ``sqlite3.Row``, or None if no follow-up has been scheduled.
    """
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM followups WHERE case_id = ?", (case_id,)
    ).fetchone()


def reserve_followup_action(
    action_id: str,
    case_id: str,
    idempotency_key: str,
    followup_level: int,
    escalation_stage: str,
    recipient_type: str,
    scheduled_bucket: str,
) -> bool:
    """Reserve a follow-up action slot, returning False when it already exists."""
    now = utc_now_iso()
    cursor = _execute_write(
        """
        INSERT OR IGNORE INTO followup_actions
            (action_id, case_id, idempotency_key, followup_level, escalation_stage,
             recipient_type, scheduled_bucket, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)
        """,
        (
            action_id,
            case_id,
            idempotency_key,
            followup_level,
            escalation_stage,
            recipient_type,
            scheduled_bucket,
            now,
            now,
        ),
    )
    return cursor.rowcount > 0


def mark_followup_action_status(
    idempotency_key: str,
    status: str,
    outbound_msg_id: Optional[str] = None,
) -> None:
    """Update the state of a reserved follow-up action."""
    now = utc_now_iso()
    _execute_write(
        """
        UPDATE followup_actions
        SET status = ?,
            outbound_msg_id = COALESCE(?, outbound_msg_id),
            updated_at = ?
        WHERE idempotency_key = ?
        """,
        (status, outbound_msg_id, now, idempotency_key),
    )


def get_followup_action_by_key(idempotency_key: str) -> Optional[sqlite3.Row]:
    """Return a follow-up action record by its idempotency key."""
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM followup_actions WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()


def get_followup_actions_for_case(case_id: str) -> List[sqlite3.Row]:
    """Return follow-up action records for a case."""
    conn = get_connection()
    return conn.execute(
        """
        SELECT *
        FROM followup_actions
        WHERE case_id = ?
        ORDER BY created_at ASC
        """,
        (case_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# manual_reviews table
# ---------------------------------------------------------------------------

def insert_manual_review(
    review_id: str,
    case_id: str,
    email_id: Optional[str],
    reason: str,
) -> None:
    """Flag a case for manual human review.

    Used in four situations: low classification confidence, injection detected,
    reply suggests possible resolution, escalation threshold reached.

    Args:
        review_id: Application UUID.
        case_id: UUID of the case to flag.
        email_id: UUID of the triggering email, or None for system-triggered reviews.
        reason: Human-readable explanation of why review is required.
    """
    now = utc_now_iso()
    _execute_write(
        """
        INSERT INTO manual_reviews
            (review_id, case_id, email_id, reason, flagged_at, resolved)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (review_id, case_id, email_id, reason, now),
    )


def get_open_manual_reviews() -> List[sqlite3.Row]:
    """Return all unresolved manual review records with case context.

    Joins with ``cases`` for ``case_type``, ``building``, and ``status``.
    Ordered by ``flagged_at`` descending (most recently flagged first).

    Returns:
        List of ``sqlite3.Row`` objects. Empty if all reviews are resolved.
    """
    conn = get_connection()
    return conn.execute(
        """
        SELECT mr.*, c.case_type, c.building, c.status as case_status
        FROM manual_reviews mr
        JOIN cases c ON mr.case_id = c.case_id
        WHERE mr.resolved = 0
        ORDER BY mr.flagged_at DESC
        """
    ).fetchall()


def has_open_manual_review(case_id: str, reason: str) -> bool:
    """Return True when an unresolved manual review already exists for the case/reason pair."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT 1
        FROM manual_reviews
        WHERE case_id = ?
          AND reason = ?
          AND resolved = 0
        LIMIT 1
        """,
        (case_id, reason),
    ).fetchone()
    return row is not None


def resolve_manual_review(review_id: str) -> None:
    """Mark a manual review item as resolved.

    Args:
        review_id: UUID of the review record.
    """
    _execute_write(
        "UPDATE manual_reviews SET resolved = 1 WHERE review_id = ?",
        (review_id,),
    )


# ---------------------------------------------------------------------------
# memory tables
# ---------------------------------------------------------------------------

def upsert_entity_record(
    entity_type: str,
    canonical_name: str,
    normalized_name: str,
    metadata_json: Optional[str] = None,
    seen_at: Optional[str] = None,
) -> int:
    """Insert or update a canonical entity record and return its integer ID."""
    now = utc_now_iso()
    seen_at = seen_at or now
    with _write_lock:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT *
            FROM entities
            WHERE entity_type = ? AND normalized_name = ?
            """,
            (entity_type, normalized_name),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE entities
                SET canonical_name = ?,
                    metadata_json = COALESCE(?, metadata_json),
                    last_seen = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (canonical_name, metadata_json, seen_at, now, row["id"]),
            )
            conn.commit()
            return int(row["id"])

        cursor = conn.execute(
            """
            INSERT INTO entities
                (entity_type, canonical_name, normalized_name, metadata_json,
                 first_seen, last_seen, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_type, canonical_name, normalized_name, metadata_json, seen_at, seen_at, now, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_entity_by_normalized_name(entity_type: str, normalized_name: str) -> Optional[sqlite3.Row]:
    """Return a canonical entity by type and normalized name."""
    conn = get_connection()
    return conn.execute(
        """
        SELECT *
        FROM entities
        WHERE entity_type = ? AND normalized_name = ?
        """,
        (entity_type, normalized_name),
    ).fetchone()


def upsert_entity_alias_record(
    entity_id: int,
    alias: str,
    normalized_alias: str,
    source: Optional[str] = None,
    confidence: float = 1.0,
    seen_at: Optional[str] = None,
) -> int:
    """Insert or refresh an alternate name for a canonical entity."""
    now = utc_now_iso()
    seen_at = seen_at or now
    with _write_lock:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT *
            FROM entity_aliases
            WHERE entity_id = ? AND normalized_alias = ?
            """,
            (entity_id, normalized_alias),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE entity_aliases
                SET alias = ?,
                    source = COALESCE(?, source),
                    confidence = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (alias, source, confidence, seen_at, row["id"]),
            )
            conn.commit()
            return int(row["id"])

        cursor = conn.execute(
            """
            INSERT INTO entity_aliases
                (entity_id, alias, normalized_alias, source, confidence,
                 first_seen, last_seen, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_id, alias, normalized_alias, source, confidence, seen_at, seen_at, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def find_matching_observation(
    case_id: Optional[str],
    email_id: Optional[str],
    entity_id: Optional[int],
    observation_type: str,
    entity_type: Optional[str],
    entity_value: Optional[str],
    value_text: Optional[str],
    value_json: Optional[str],
    observed_at: Optional[str],
    source: Optional[str],
) -> Optional[sqlite3.Row]:
    """Return an existing observation row that matches the supplied fingerprint."""
    conn = get_connection()
    return conn.execute(
        """
        SELECT *
        FROM observations
        WHERE COALESCE(case_id, '') = COALESCE(?, '')
          AND COALESCE(email_id, '') = COALESCE(?, '')
          AND COALESCE(entity_id, -1) = COALESCE(?, -1)
          AND observation_type = ?
          AND COALESCE(entity_type, '') = COALESCE(?, '')
          AND COALESCE(entity_value, '') = COALESCE(?, '')
          AND COALESCE(value_text, '') = COALESCE(?, '')
          AND COALESCE(value_json, '') = COALESCE(?, '')
          AND COALESCE(observed_at, '') = COALESCE(?, '')
          AND COALESCE(source, '') = COALESCE(?, '')
        LIMIT 1
        """,
        (
            case_id,
            email_id,
            entity_id,
            observation_type,
            entity_type,
            entity_value,
            value_text,
            value_json,
            observed_at,
            source,
        ),
    ).fetchone()


def insert_observation_record(
    case_id: Optional[str],
    email_id: Optional[str],
    entity_id: Optional[int],
    observation_type: str,
    entity_type: Optional[str],
    entity_value: Optional[str],
    value_text: Optional[str],
    value_json: Optional[str],
    observed_at: Optional[str],
    source: str,
    confidence: float = 1.0,
) -> int:
    """Insert an observation row if it does not already exist and return its ID."""
    existing = find_matching_observation(
        case_id=case_id,
        email_id=email_id,
        entity_id=entity_id,
        observation_type=observation_type,
        entity_type=entity_type,
        entity_value=entity_value,
        value_text=value_text,
        value_json=value_json,
        observed_at=observed_at,
        source=source,
    )
    if existing:
        return int(existing["id"])

    cursor = _execute_write(
        """
        INSERT INTO observations
            (case_id, email_id, entity_id, observation_type, entity_type,
             entity_value, value_text, value_json, observed_at, source, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            case_id,
            email_id,
            entity_id,
            observation_type,
            entity_type,
            entity_value,
            value_text,
            value_json,
            observed_at,
            source,
            confidence,
        ),
    )
    return int(cursor.lastrowid)


def get_observations_for_case(case_id: str, limit: Optional[int] = None) -> List[sqlite3.Row]:
    """Return recent observations for a case, newest first."""
    conn = get_connection()
    sql = """
        SELECT *
        FROM observations
        WHERE case_id = ?
        ORDER BY COALESCE(observed_at, created_at) DESC, id DESC
    """
    params: tuple = (case_id,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (case_id, limit)
    return conn.execute(sql, params).fetchall()


def insert_case_link_record(
    source_case_id: str,
    target_case_id: str,
    link_type: str,
    reason: Optional[str] = None,
    confidence: float = 1.0,
) -> None:
    """Insert a case-to-case relationship if it has not already been recorded."""
    _execute_write(
        """
        INSERT OR IGNORE INTO case_links
            (source_case_id, target_case_id, link_type, reason, confidence)
        VALUES (?, ?, ?, ?, ?)
        """,
        (source_case_id, target_case_id, link_type, reason, confidence),
    )


def get_related_cases_for_case(case_id: str, limit: int = 10) -> List[sqlite3.Row]:
    """Return related cases linked to the given case from either direction."""
    conn = get_connection()
    return conn.execute(
        """
        SELECT *
        FROM (
            SELECT
                cl.link_type,
                cl.reason,
                cl.confidence,
                cl.created_at AS linked_at,
                c.case_id,
                c.case_type,
                c.status,
                c.priority,
                c.building,
                c.device,
                c.contractor,
                c.created_at,
                c.updated_at
            FROM case_links cl
            JOIN cases c ON c.case_id = cl.target_case_id
            WHERE cl.source_case_id = ?

            UNION ALL

            SELECT
                cl.link_type,
                cl.reason,
                cl.confidence,
                cl.created_at AS linked_at,
                c.case_id,
                c.case_type,
                c.status,
                c.priority,
                c.building,
                c.device,
                c.contractor,
                c.created_at,
                c.updated_at
            FROM case_links cl
            JOIN cases c ON c.case_id = cl.source_case_id
            WHERE cl.target_case_id = ?
        )
        ORDER BY linked_at DESC, updated_at DESC
        LIMIT ?
        """,
        (case_id, case_id, limit),
    ).fetchall()


def get_active_pattern_flags_for_case(case_id: str) -> List[sqlite3.Row]:
    """Return active pattern flags for a specific case ordered by severity/date."""
    conn = get_connection()
    return conn.execute(
        """
        SELECT *
        FROM pattern_flags
        WHERE case_id = ? AND status = 'active'
        ORDER BY
            CASE severity
                WHEN 'review' THEN 4
                WHEN 'high' THEN 3
                WHEN 'medium' THEN 2
                ELSE 1
            END DESC,
            created_at DESC
        """,
        (case_id,),
    ).fetchall()


def get_active_pattern_flags(limit: Optional[int] = None) -> List[sqlite3.Row]:
    """Return active pattern flags across all cases ordered by severity/date."""
    conn = get_connection()
    sql = """
        SELECT pf.*, c.case_type, c.building, c.device, c.contractor
        FROM pattern_flags pf
        LEFT JOIN cases c ON c.case_id = pf.case_id
        WHERE pf.status = 'active'
        ORDER BY
            CASE pf.severity
                WHEN 'review' THEN 4
                WHEN 'high' THEN 3
                WHEN 'medium' THEN 2
                ELSE 1
            END DESC,
            pf.created_at DESC
    """
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return conn.execute(sql, params).fetchall()


def upsert_pattern_flag_record(
    case_id: Optional[str],
    pattern_type: str,
    severity: str,
    summary: str,
    evidence_json: Optional[str],
    status: str = "active",
) -> Dict[str, Any]:
    """Insert or refresh a pattern flag and report whether it was created or updated."""
    now = utc_now_iso()
    with _write_lock:
        conn = get_connection()
        if case_id is None:
            row = conn.execute(
                """
                SELECT *
                FROM pattern_flags
                WHERE case_id IS NULL AND pattern_type = ? AND status = 'active'
                ORDER BY id DESC
                LIMIT 1
                """,
                (pattern_type,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT *
                FROM pattern_flags
                WHERE case_id = ? AND pattern_type = ? AND status = 'active'
                ORDER BY id DESC
                LIMIT 1
                """,
                (case_id, pattern_type),
            ).fetchone()

        if row:
            changed = any(
                row[key] != value
                for key, value in {
                    "severity": severity,
                    "summary": summary,
                    "evidence_json": evidence_json,
                    "status": status,
                }.items()
            )
            if changed:
                conn.execute(
                    """
                    UPDATE pattern_flags
                    SET severity = ?,
                        summary = ?,
                        evidence_json = ?,
                        status = ?,
                        updated_at = ?,
                        resolved_at = CASE WHEN ? = 'resolved' THEN ? ELSE resolved_at END
                    WHERE id = ?
                    """,
                    (
                        severity,
                        summary,
                        evidence_json,
                        status,
                        now,
                        status,
                        now,
                        row["id"],
                    ),
                )
                conn.commit()
            return {
                "id": int(row["id"]),
                "case_id": case_id,
                "pattern_type": pattern_type,
                "severity": severity,
                "summary": summary,
                "evidence_json": evidence_json,
                "status": status,
                "created": False,
                "updated": changed,
            }

        cursor = conn.execute(
            """
            INSERT INTO pattern_flags
                (case_id, pattern_type, severity, summary, evidence_json,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (case_id, pattern_type, severity, summary, evidence_json, status, now, now),
        )
        conn.commit()
        return {
            "id": int(cursor.lastrowid),
            "case_id": case_id,
            "pattern_type": pattern_type,
            "severity": severity,
            "summary": summary,
            "evidence_json": evidence_json,
            "status": status,
            "created": True,
            "updated": False,
        }



# ---------------------------------------------------------------------------
# Dashboard / UI read helpers (no writes)
# ---------------------------------------------------------------------------


def get_dashboard_summary() -> dict:
    """Return high-level counts for the dashboard page."""
    conn = get_connection()
    result = {}

    row = conn.execute("SELECT COUNT(*) AS n FROM emails").fetchone()
    result["total_emails"] = int(row["n"])

    row = conn.execute("SELECT COUNT(*) AS n FROM emails WHERE processed = 0").fetchone()
    result["unprocessed_emails"] = int(row["n"])

    row = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()
    result["total_cases"] = int(row["n"])

    row = conn.execute("SELECT COUNT(*) AS n FROM cases WHERE status = 'open'").fetchone()
    result["open_cases"] = int(row["n"])

    row = conn.execute("SELECT COUNT(*) AS n FROM cases WHERE priority = 'critical'").fetchone()
    result["critical_cases"] = int(row["n"])

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM manual_reviews WHERE resolved = 0"
    ).fetchone()
    result["open_reviews"] = int(row["n"])

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM pattern_flags WHERE status = 'active'"
    ).fetchone()
    result["active_patterns"] = int(row["n"])

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM pattern_flags WHERE status = 'active' AND severity IN ('review', 'high')"
    ).fetchone()
    result["high_patterns"] = int(row["n"])

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM outbound_messages WHERE status = 'sent'"
    ).fetchone()
    result["sent_messages"] = int(row["n"])

    row = conn.execute(
        "SELECT COUNT(*) AS n FROM outbound_messages WHERE status = 'draft'"
    ).fetchone()
    result["draft_messages"] = int(row["n"])

    return result


def get_case_counts_by_status() -> dict:
    """Return {status: count} for all cases."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM cases GROUP BY status"
    ).fetchall()
    return {row["status"]: int(row["n"]) for row in rows}


def get_case_counts_by_type() -> list:
    """Return [{"case_type": ..., "count": ...}] sorted descending."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT case_type, COUNT(*) AS n FROM cases GROUP BY case_type ORDER BY n DESC"
    ).fetchall()
    return [{"case_type": row["case_type"], "count": int(row["n"])} for row in rows]


def get_recent_agent_activity(limit: int = 20) -> list:
    """Return recent case events joined with case metadata for the activity timeline."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT ce.event_id, ce.case_id, ce.event_type, ce.description,
               ce.source_email_id, ce.created_at,
               c.case_type, c.building, c.status AS case_status, c.priority
        FROM case_events ce
        LEFT JOIN cases c ON c.case_id = ce.case_id
        ORDER BY ce.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_email_pipeline_summary() -> dict:
    """Return email pipeline counts for the emails page cards."""
    conn = get_connection()
    result = {}

    row = conn.execute("SELECT COUNT(*) AS n FROM emails").fetchone()
    result["total"] = int(row["n"])

    row = conn.execute("SELECT COUNT(*) AS n FROM emails WHERE processed = 0").fetchone()
    result["unprocessed"] = int(row["n"])

    row = conn.execute("SELECT COUNT(*) AS n FROM emails WHERE processed = 1").fetchone()
    result["processed"] = int(row["n"])

    created_placeholders = ", ".join("?" for _ in _CREATED_EMAIL_EVENT_TYPES)
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT e.email_id) AS n
        FROM emails e
        INNER JOIN case_events ce ON ce.source_email_id = e.email_id
        WHERE ce.event_type IN ({created_placeholders})
        """,
        _CREATED_EMAIL_EVENT_TYPES,
    ).fetchone()
    result["created_cases"] = int(row["n"])

    row = conn.execute(
        """
        SELECT COUNT(DISTINCT mr.email_id) AS n
        FROM manual_reviews mr
        WHERE mr.email_id IS NOT NULL AND mr.resolved = 0
        """
    ).fetchone()
    result["flagged_for_review"] = int(row["n"])

    return result


def get_email_backlog(limit: int = 100, status_filter: str = "") -> list:
    """
    Return emails with enriched action/status fields derived from case_events and
    manual_reviews. Each row is a plain dict with extra keys:
      linked_case_id, linked_case_type, linked_case_status, linked_case_priority,
      review_count, review_reason, action
    ``action`` is one of: new | skipped | created | updated | review | injection_flag
    ``status_filter`` filters by action value (post-derivation).
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT e.*,
               ce.case_id   AS linked_case_id,
               ce.event_type AS first_event_type,
               c.case_type  AS linked_case_type,
               c.status     AS linked_case_status,
               c.priority   AS linked_case_priority,
               mr.review_count,
               mr.first_reason AS review_reason
        FROM emails e
        LEFT JOIN (
            SELECT ce_inner.source_email_id,
                   ce_inner.case_id,
                   ce_inner.event_type
            FROM case_events ce_inner
            INNER JOIN (
                SELECT source_email_id, MIN(rowid) AS min_rid
                FROM case_events
                WHERE source_email_id IS NOT NULL
                GROUP BY source_email_id
            ) ce_first ON ce_inner.rowid = ce_first.min_rid
        ) ce ON ce.source_email_id = e.email_id
        LEFT JOIN cases c ON c.case_id = ce.case_id
        LEFT JOIN (
            SELECT email_id,
                   COUNT(*) AS review_count,
                   MIN(reason) AS first_reason
            FROM manual_reviews
            WHERE email_id IS NOT NULL
            GROUP BY email_id
        ) mr ON mr.email_id = e.email_id
        ORDER BY e.received_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    result = []
    for row in rows:
        email = dict(row)
        review_count = int(email.get("review_count") or 0)
        reason = email.get("review_reason") or ""
        first_event = email.get("first_event_type")

        if review_count > 0 and "injection" in reason.lower():
            action = "injection_flag"
        elif review_count > 0:
            action = "review"
        elif first_event in _CREATED_EMAIL_EVENT_TYPES:
            action = "created"
        elif first_event in _UPDATED_EMAIL_EVENT_TYPES or first_event is not None:
            action = "updated"
        elif email.get("processed"):
            action = "skipped"
        else:
            action = "new"

        email["action"] = action
        result.append(email)

    if status_filter:
        result = [e for e in result if e["action"] == status_filter]

    return result


def get_events_for_email(email_id: str) -> list:
    """Return case events that originated from a specific email."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT ce.*, c.case_type, c.building, c.status AS case_status
        FROM case_events ce
        LEFT JOIN cases c ON c.case_id = ce.case_id
        WHERE ce.source_email_id = ?
        ORDER BY ce.created_at ASC
        """,
        (email_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# connection_hypotheses / connection_hypothesis_cases tables
# ---------------------------------------------------------------------------

def insert_connection_hypothesis(
    hypothesis_id: str,
    hypothesis_type: str,
    summary: str,
    confidence: str,
    risk_level: str,
    evidence_json: Optional[str],
    reasoning: str,
    recommended_human_review: str,
    status: str = "proposed",
) -> None:
    """Insert a proposed AI-generated connection hypothesis.

    Args:
        hypothesis_id: Application UUID.
        hypothesis_type: Short machine-readable label for the hypothesis type.
        summary: One-sentence human-readable summary.
        confidence: One of ``'low'``, ``'medium'``, ``'high'``.
        risk_level: One of ``'info'``, ``'review'``, ``'management_review'``.
        evidence_json: JSON-encoded evidence dict, or None.
        reasoning: AI-generated reasoning for the hypothesis.
        recommended_human_review: What a human reviewer should check.
        status: Defaults to ``'proposed'``.
    """
    now = utc_now_iso()
    _execute_write(
        """
        INSERT INTO connection_hypotheses
            (hypothesis_id, hypothesis_type, summary, confidence, risk_level,
             evidence_json, reasoning, recommended_human_review, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hypothesis_id,
            hypothesis_type,
            summary,
            confidence,
            risk_level,
            evidence_json,
            reasoning,
            recommended_human_review,
            status,
            now,
        ),
    )


def insert_connection_hypothesis_case(hypothesis_id: str, case_id: str) -> None:
    """Link a case to a connection hypothesis (INSERT OR IGNORE for idempotency)."""
    _execute_write(
        """
        INSERT OR IGNORE INTO connection_hypothesis_cases (hypothesis_id, case_id)
        VALUES (?, ?)
        """,
        (hypothesis_id, case_id),
    )


def get_connection_hypotheses(
    status_filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[sqlite3.Row]:
    """Return connection hypotheses ordered by created_at descending.

    Args:
        status_filter: Optional status value to filter by (e.g. ``'proposed'``).
        limit: Optional maximum number of rows to return.

    Returns:
        List of ``sqlite3.Row`` objects.
    """
    conn = get_connection()
    sql = "SELECT * FROM connection_hypotheses"
    params: list = []
    if status_filter:
        sql += " WHERE status = ?"
        params.append(status_filter)
    sql += " ORDER BY created_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_cases_for_hypothesis(hypothesis_id: str) -> List[str]:
    """Return the case_ids linked to a hypothesis."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT case_id FROM connection_hypothesis_cases WHERE hypothesis_id = ?",
        (hypothesis_id,),
    ).fetchall()
    return [row["case_id"] for row in rows]


def get_supported_cases_for_discovery(
    building: Optional[str] = None,
    case_type: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[sqlite3.Row]:
    """Return cases of supported types only for connection discovery.

    Filters strictly to supported KPI case types. Never returns UNKNOWN or
    any other unsupported type.

    Args:
        building: Optional building name substring filter.
        case_type: Optional exact case type (must be a supported type or ignored).
        limit: Optional maximum rows to return.

    Returns:
        List of ``sqlite3.Row`` objects ordered by ``created_at`` descending.
    """
    supported_placeholders = ", ".join("?" for _ in SUPPORTED_CASE_TYPES)
    sql = f"SELECT * FROM cases WHERE case_type IN ({supported_placeholders})"
    params: list = list(SUPPORTED_CASE_TYPES)

    if building:
        sql += " AND building LIKE ?"
        params.append(f"%{building}%")

    if case_type and case_type in SUPPORTED_CASE_TYPES:
        sql += " AND case_type = ?"
        params.append(case_type)

    sql += " ORDER BY created_at DESC"

    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    conn = get_connection()
    return conn.execute(sql, params).fetchall()


def resolve_missing_pattern_flags(case_id: str, active_pattern_types: List[str]) -> int:
    """Resolve active pattern flags for a case that were not present in the latest run."""
    now = utc_now_iso()
    with _write_lock:
        conn = get_connection()
        if active_pattern_types:
            placeholders = ", ".join("?" for _ in active_pattern_types)
            cursor = conn.execute(
                f"""
                UPDATE pattern_flags
                SET status = 'resolved',
                    resolved_at = ?,
                    updated_at = ?
                WHERE case_id = ?
                  AND status = 'active'
                  AND pattern_type NOT IN ({placeholders})
                """,
                (now, now, case_id, *active_pattern_types),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE pattern_flags
                SET status = 'resolved',
                    resolved_at = ?,
                    updated_at = ?
                WHERE case_id = ?
                  AND status = 'active'
                """,
                (now, now, case_id),
            )
        conn.commit()
        return int(cursor.rowcount)
