"""
database.py — SQLite schema creation and all database query helpers.
Uses a module-level write lock for thread safety with Flask + APScheduler.
"""

import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional

from config import config
from constants import (
    DRAFT_STATUSES,
    EVENT_BACKLOG_CASE_CREATED,
    EVENT_BACKLOG_CASE_UPDATED,
    EVENT_BACKLOG_EMAIL_IMPORTED,
    EVENT_CASE_CREATED,
    EVENT_EMAIL_RECEIVED,
    QUEUE_STATUSES,
    SUPPORTED_CASE_TYPES,
    SUPPORTED_CASE_TYPES_SET,
)
from time_utils import utc_now_iso

# Thread-local storage for connections — each thread gets its own connection
_local = threading.local()
_write_lock = threading.RLock()

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
    with _write_lock:
        _add_column_if_missing("emails", "cc_addrs", "TEXT DEFAULT ''")
        _add_column_if_missing("emails", "bcc_addrs", "TEXT DEFAULT ''")
        _add_column_if_missing("emails", "reply_to", "TEXT DEFAULT ''")
        _add_column_if_missing("manual_reviews", "review_category", "TEXT")
        _add_column_if_missing("manual_reviews", "blocking", "INTEGER DEFAULT 1")
        _add_column_if_missing("manual_reviews", "context_json", "TEXT")


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
                cc_addrs    TEXT DEFAULT '',
                bcc_addrs   TEXT DEFAULT '',
                reply_to    TEXT DEFAULT '',
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

            CREATE TABLE IF NOT EXISTS connection_discovery_runs (
                run_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                max_ai_calls INTEGER NOT NULL,
                ai_calls_used INTEGER DEFAULT 0,
                packets_created INTEGER DEFAULT 0,
                packets_analyzed INTEGER DEFAULT 0,
                hypotheses_created INTEGER DEFAULT 0,
                hypotheses_rejected INTEGER DEFAULT 0,
                unsupported_records_included INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                config_json TEXT
            );

            CREATE TABLE IF NOT EXISTS connection_discovery_packets (
                packet_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                packet_type TEXT NOT NULL,
                entity_type TEXT,
                entity_value TEXT,
                case_count INTEGER DEFAULT 0,
                pattern_count INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                ai_call_used INTEGER DEFAULT 0,
                hypotheses_created INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                error TEXT
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

            CREATE TABLE IF NOT EXISTS building_group_emails (
                group_email_id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                outbound_msg_id TEXT,
                email_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft_generated',
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                intended_to TEXT,
                intended_cc TEXT DEFAULT '',
                actual_to TEXT,
                summary_json TEXT,
                quality_check_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                approved_at TEXT,
                rejected_at TEXT,
                sent_at TEXT,
                review_notes TEXT,
                FOREIGN KEY (group_id) REFERENCES building_issue_groups(group_id),
                FOREIGN KEY (outbound_msg_id) REFERENCES outbound_messages(msg_id)
            );

            CREATE TABLE IF NOT EXISTS communication_queue (
                queue_id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                case_id TEXT,
                queue_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reason TEXT,
                suppression_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (group_id) REFERENCES building_issue_groups(group_id),
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS case_data_requirements (
                requirement_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                requirement_key TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'missing',
                required INTEGER NOT NULL DEFAULT 1,
                source TEXT,
                evidence_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(case_id, requirement_key),
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );

            CREATE TABLE IF NOT EXISTS reply_case_mappings (
                mapping_id TEXT PRIMARY KEY,
                reply_email_id TEXT NOT NULL,
                case_id TEXT,
                group_id TEXT,
                mapping_source TEXT NOT NULL,
                confidence TEXT,
                status TEXT NOT NULL DEFAULT 'proposed',
                completeness_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (reply_email_id) REFERENCES emails(email_id),
                FOREIGN KEY (case_id) REFERENCES cases(case_id),
                FOREIGN KEY (group_id) REFERENCES building_issue_groups(group_id)
            );

            CREATE TABLE IF NOT EXISTS case_field_suggestions (
                suggestion_id          TEXT PRIMARY KEY,
                case_id                TEXT NOT NULL,
                field_name             TEXT NOT NULL,
                suggested_value        TEXT NOT NULL,
                confidence             TEXT NOT NULL,
                confidence_score       REAL,
                rationale              TEXT,
                evidence_json          TEXT,
                source_email_id        TEXT,
                source                 TEXT NOT NULL DEFAULT 'ai_missing_data_enrichment',
                status                 TEXT NOT NULL DEFAULT 'proposed',
                run_id                 TEXT,
                packet_id              TEXT,
                model_name             TEXT,
                prompt_schema_version  TEXT,
                created_at             TEXT NOT NULL,
                reviewed_at            TEXT,
                reviewed_by            TEXT,
                review_notes           TEXT,
                accepted_at            TEXT,
                rejected_at            TEXT,
                FOREIGN KEY (case_id) REFERENCES cases(case_id),
                FOREIGN KEY (source_email_id) REFERENCES emails(email_id)
            );

            CREATE INDEX IF NOT EXISTS idx_connection_hypotheses_status ON connection_hypotheses(status);
            CREATE INDEX IF NOT EXISTS idx_connection_hypothesis_cases_case_id ON connection_hypothesis_cases(case_id);
            CREATE INDEX IF NOT EXISTS idx_connection_discovery_runs_started
                ON connection_discovery_runs(started_at);
            CREATE INDEX IF NOT EXISTS idx_connection_discovery_packets_run
                ON connection_discovery_packets(run_id, status);

            CREATE INDEX IF NOT EXISTS idx_building_issue_groups_key ON building_issue_groups(grouping_key);
            CREATE INDEX IF NOT EXISTS idx_building_issue_groups_status ON building_issue_groups(status);
            CREATE INDEX IF NOT EXISTS idx_building_issue_groups_normalized
                ON building_issue_groups(normalized_building, normalized_contractor);
            CREATE INDEX IF NOT EXISTS idx_building_issue_groups_updated_at ON building_issue_groups(updated_at);
            CREATE INDEX IF NOT EXISTS idx_building_issue_group_cases_case_id ON building_issue_group_cases(case_id);
            CREATE INDEX IF NOT EXISTS idx_building_issue_group_cases_status ON building_issue_group_cases(status);
            CREATE INDEX IF NOT EXISTS idx_building_issue_group_cases_new ON building_issue_group_cases(new_since_last_email);
            CREATE INDEX IF NOT EXISTS idx_building_issue_group_cases_source ON building_issue_group_cases(source);
            CREATE INDEX IF NOT EXISTS idx_group_emails_group_status ON building_group_emails(group_id, status);
            CREATE INDEX IF NOT EXISTS idx_comm_queue_status ON communication_queue(status);

            CREATE INDEX IF NOT EXISTS idx_case_requirements_case_status ON case_data_requirements(case_id, status);
            CREATE INDEX IF NOT EXISTS idx_case_requirements_created_at ON case_data_requirements(created_at);
            CREATE INDEX IF NOT EXISTS idx_reply_mappings_reply ON reply_case_mappings(reply_email_id);
            CREATE INDEX IF NOT EXISTS idx_reply_mappings_case ON reply_case_mappings(case_id);
            CREATE INDEX IF NOT EXISTS idx_reply_mappings_group ON reply_case_mappings(group_id);
            CREATE INDEX IF NOT EXISTS idx_reply_mappings_status ON reply_case_mappings(status);

            CREATE INDEX IF NOT EXISTS idx_case_field_suggestions_case_status
                ON case_field_suggestions(case_id, status);
            CREATE INDEX IF NOT EXISTS idx_case_field_suggestions_field_status
                ON case_field_suggestions(field_name, status);
            CREATE INDEX IF NOT EXISTS idx_case_field_suggestions_created_at
                ON case_field_suggestions(created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_case_field_suggestions_proposed_unique
                ON case_field_suggestions(case_id, field_name, suggested_value)
                WHERE status = 'proposed';

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
    cc_addrs: str = "",
    bcc_addrs: str = "",
    reply_to: str = "",
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
        cc_addrs: Semicolon-separated CC recipients preserved for review.
        bcc_addrs: Semicolon-separated BCC recipients preserved for review.
        reply_to: Reply-To header value preserved for review.
    """
    _execute_write(
        """
        INSERT OR IGNORE INTO emails
            (email_id, message_id, thread_id, subject, from_addr, to_addr,
             received_at, raw_body, normalized_text, cc_addrs, bcc_addrs,
             reply_to, processed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (email_id, message_id, thread_id, subject, from_addr, to_addr,
         received_at, raw_body, normalized_text, cc_addrs, bcc_addrs, reply_to),
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
    device: Optional[str] = None,
    contractor: Optional[str] = None,
    due_date: Optional[str] = None,
    period: Optional[str] = None,
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
    caller ``updates`` dict is not mutated.

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


def get_recent_events(limit: int = 100) -> List[sqlite3.Row]:
    """Return the most recent case events joined with case metadata."""
    return get_connection().execute(
        """
        SELECT ce.*, c.case_type, c.building
        FROM case_events ce
        LEFT JOIN cases c ON ce.case_id = c.case_id
        ORDER BY ce.created_at DESC
        LIMIT ?
        """,
        (limit,),
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
# building_group_emails table
# ---------------------------------------------------------------------------

def insert_building_group_email(
    group_email_id: str,
    group_id: str,
    email_type: str,
    subject: str,
    body: str,
    intended_to: str,
    intended_cc: str,
    actual_to: str,
    summary_json: Optional[str] = None,
    quality_check_json: Optional[str] = None,
    outbound_msg_id: Optional[str] = None,
    status: str = "draft_generated",
) -> None:
    """Insert a review-only consolidated building-group draft row."""
    if status not in DRAFT_STATUSES:
        raise ValueError(f"Unsupported group email status: {status}")
    now = utc_now_iso()
    _execute_write(
        """
        INSERT INTO building_group_emails
            (group_email_id, group_id, outbound_msg_id, email_type, status,
             subject, body, intended_to, intended_cc, actual_to, summary_json,
             quality_check_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            group_email_id,
            group_id,
            outbound_msg_id,
            email_type,
            status,
            subject,
            body,
            intended_to,
            intended_cc,
            actual_to,
            summary_json,
            quality_check_json,
            now,
            now,
        ),
    )


def get_building_group_email(group_email_id: str) -> Optional[sqlite3.Row]:
    """Return a consolidated building-group email row by ID."""
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM building_group_emails WHERE group_email_id = ?",
        (group_email_id,),
    ).fetchone()


def list_building_group_emails(
    group_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[sqlite3.Row]:
    """Return consolidated building-group email rows, newest first."""
    clauses = []
    params: list[Any] = []
    if group_id:
        clauses.append("group_id = ?")
        params.append(group_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    safe_limit = max(1, int(limit))
    conn = get_connection()
    return conn.execute(
        f"""
        SELECT *
        FROM building_group_emails
        {where_sql}
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        tuple(params + [safe_limit]),
    ).fetchall()


def update_building_group_email_status(
    group_email_id: str,
    status: str,
    notes: Optional[str] = None,
    outbound_msg_id: Optional[str] = None,
) -> Optional[sqlite3.Row]:
    """Update a consolidated draft status and return the updated row."""
    if status not in DRAFT_STATUSES:
        raise ValueError(f"Unsupported group email status: {status}")
    now = utc_now_iso()
    approved_at = now if status == "approved" else None
    rejected_at = now if status == "rejected" else None
    sent_at = now if status == "sent" else None
    _execute_write(
        """
        UPDATE building_group_emails
        SET status = ?,
            outbound_msg_id = COALESCE(?, outbound_msg_id),
            updated_at = ?,
            approved_at = COALESCE(?, approved_at),
            rejected_at = COALESCE(?, rejected_at),
            sent_at = COALESCE(?, sent_at),
            review_notes = COALESCE(?, review_notes)
        WHERE group_email_id = ?
        """,
        (
            status,
            outbound_msg_id,
            now,
            approved_at,
            rejected_at,
            sent_at,
            notes,
            group_email_id,
        ),
    )
    return get_building_group_email(group_email_id)


# ---------------------------------------------------------------------------
# communication_queue table
# ---------------------------------------------------------------------------

def get_communication_queue_item(queue_id: str) -> Optional[sqlite3.Row]:
    """Return one communication queue row by ID."""
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM communication_queue WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()


def list_communication_queue(
    status: Optional[str] = None,
    group_id: Optional[str] = None,
    limit: int = 100,
) -> List[sqlite3.Row]:
    """Return communication queue rows, optionally filtered by status or group."""
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if group_id:
        clauses.append("group_id = ?")
        params.append(group_id)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    safe_limit = max(1, int(limit))
    conn = get_connection()
    return conn.execute(
        f"""
        SELECT *
        FROM communication_queue
        {where_sql}
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        tuple(params + [safe_limit]),
    ).fetchall()


def upsert_communication_queue_item(
    group_id: str,
    queue_type: str,
    case_id: Optional[str] = None,
    reason: Optional[str] = None,
    status: str = "pending",
    suppression_reason: Optional[str] = None,
    queue_id: Optional[str] = None,
) -> str:
    """Insert or update a non-completed communication queue row."""
    if status not in QUEUE_STATUSES:
        raise ValueError(f"Unsupported communication queue status: {status}")
    now = utc_now_iso()
    with _write_lock:
        conn = get_connection()
        existing = conn.execute(
            """
            SELECT queue_id
            FROM communication_queue
            WHERE group_id = ?
              AND queue_type = ?
              AND COALESCE(case_id, '') = COALESCE(?, '')
              AND status != 'completed'
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (group_id, queue_type, case_id),
        ).fetchone()
        if existing:
            existing_queue_id = existing["queue_id"]
            conn.execute(
                """
                UPDATE communication_queue
                SET status = ?,
                    reason = COALESCE(?, reason),
                    suppression_reason = COALESCE(?, suppression_reason),
                    updated_at = ?
                WHERE queue_id = ?
                """,
                (status, reason, suppression_reason, now, existing_queue_id),
            )
            conn.commit()
            return existing_queue_id

        new_queue_id = queue_id or str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO communication_queue
                (queue_id, group_id, case_id, queue_type, status, reason,
                 suppression_reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_queue_id,
                group_id,
                case_id,
                queue_type,
                status,
                reason,
                suppression_reason,
                now,
                now,
            ),
        )
        conn.commit()
        return new_queue_id


def update_communication_queue_status(
    queue_id: str,
    status: str,
    suppression_reason: Optional[str] = None,
) -> Optional[sqlite3.Row]:
    """Update a communication queue row status and return the updated row."""
    if status not in QUEUE_STATUSES:
        raise ValueError(f"Unsupported communication queue status: {status}")
    now = utc_now_iso()
    _execute_write(
        """
        UPDATE communication_queue
        SET status = ?,
            suppression_reason = COALESCE(?, suppression_reason),
            updated_at = ?
        WHERE queue_id = ?
        """,
        (status, suppression_reason, now, queue_id),
    )
    return get_communication_queue_item(queue_id)


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
# Missing-data review helpers
# ---------------------------------------------------------------------------

_MISSING_CASE_FIELD_NAMES = ("contractor", "building", "device", "due_date", "period")
_CASE_FIELD_SUGGESTION_STATUSES = frozenset(
    {"proposed", "accepted", "rejected", "superseded"}
)
_ENRICHMENT_FIELD_COLUMNS = frozenset({"contractor"})


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _missing_case_fields(case_row: Dict[str, Any]) -> List[str]:
    return [
        field_name
        for field_name in _MISSING_CASE_FIELD_NAMES
        if _is_blank(case_row.get(field_name))
    ]


def _manual_review_status(case_row: Dict[str, Any]) -> str:
    open_count = int(case_row.get("open_review_count") or 0)
    total_count = int(case_row.get("total_review_count") or 0)
    if open_count > 0:
        return "open"
    if total_count > 0:
        return "resolved"
    return "none"


def _missing_required_evidence_for_case(case_id: str, case_type: str) -> List[str]:
    if case_type not in SUPPORTED_CASE_TYPES_SET:
        return []

    # Keep the read helper side-effect free; build_case_requirements() writes rows.
    from response_requirements import get_required_response_items

    existing = {
        requirement["requirement_key"]: requirement
        for requirement in get_case_data_requirements(case_id)
    }
    missing = []
    for item in get_required_response_items(case_type):
        key = item["key"]
        current = existing.get(key)
        if current is None or current.get("status") in {"missing", "partial"}:
            missing.append(key)
    return missing


def _include_missing_data_case(case_row: Dict[str, Any], field_filter: Optional[str]) -> bool:
    if not field_filter:
        return True
    normalized = field_filter.strip().lower()
    if normalized == "client":
        return _is_blank(case_row.get("client"))
    if normalized in {"evidence", "required_evidence", "missing_required_evidence"}:
        return bool(case_row.get("missing_required_evidence"))
    return normalized in case_row.get("missing_fields", [])


def get_source_emails_for_case(case_id: str) -> List[sqlite3.Row]:
    """Return source emails linked to a case through events, fields, or reviews."""
    conn = get_connection()
    return conn.execute(
        """
        WITH linked_email_ids AS (
            SELECT source_email_id AS email_id
            FROM case_events
            WHERE case_id = ? AND source_email_id IS NOT NULL
            UNION
            SELECT email_id
            FROM extracted_fields
            WHERE case_id = ?
            UNION
            SELECT email_id
            FROM manual_reviews
            WHERE case_id = ? AND email_id IS NOT NULL
        )
        SELECT e.*
        FROM emails e
        JOIN linked_email_ids l ON l.email_id = e.email_id
        ORDER BY e.received_at ASC, e.email_id ASC
        """,
        (case_id, case_id, case_id),
    ).fetchall()


def get_latest_field_values_for_case(case_id: str) -> Dict[str, str]:
    """Return the latest non-empty extracted value for each field on a case."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT field_name, field_value
        FROM extracted_fields
        WHERE case_id = ?
        ORDER BY rowid ASC
        """,
        (case_id,),
    ).fetchall()
    values: Dict[str, str] = {}
    for row in rows:
        field_name = row["field_name"]
        field_value = row["field_value"]
        if _is_blank(field_name) or _is_blank(field_value):
            continue
        values[str(field_name)] = str(field_value)
    return values


def list_missing_data_cases(
    status_filter: str = "open",
    field_filter: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Return cases with blank core fields, client data, or required evidence."""
    status_filter = (status_filter or "open").strip().lower()
    params: List[Any] = []
    if status_filter == "all":
        status_sql = "1=1"
    elif status_filter == "open":
        status_sql = "c.status != 'closed'"
    else:
        status_sql = "c.status = ?"
        params.append(status_filter)

    safe_limit = max(1, int(limit))
    params.append(safe_limit)
    conn = get_connection()
    rows = conn.execute(
        f"""
        WITH field_values AS (
            SELECT
                case_id,
                MAX(CASE
                    WHEN field_name IN ('client', 'customer', 'property_manager')
                     AND NULLIF(TRIM(COALESCE(field_value, '')), '') IS NOT NULL
                    THEN field_value
                END) AS client
            FROM extracted_fields
            GROUP BY case_id
        ),
        missing_requirements AS (
            SELECT
                case_id,
                COUNT(*) AS missing_evidence_count,
                GROUP_CONCAT(requirement_key, ', ') AS missing_evidence_keys
            FROM case_data_requirements
            WHERE required = 1 AND status IN ('missing', 'partial')
            GROUP BY case_id
        ),
        review_status AS (
            SELECT
                case_id,
                COUNT(*) AS total_review_count,
                SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) AS open_review_count,
                GROUP_CONCAT(CASE WHEN resolved = 0 THEN reason END, ' | ') AS open_review_reasons
            FROM manual_reviews
            GROUP BY case_id
        )
        SELECT
            c.*,
            fv.client,
            COALESCE(mr.missing_evidence_count, 0) AS missing_evidence_count,
            mr.missing_evidence_keys,
            COALESCE(rv.total_review_count, 0) AS total_review_count,
            COALESCE(rv.open_review_count, 0) AS open_review_count,
            rv.open_review_reasons
        FROM cases c
        LEFT JOIN field_values fv ON fv.case_id = c.case_id
        LEFT JOIN missing_requirements mr ON mr.case_id = c.case_id
        LEFT JOIN review_status rv ON rv.case_id = c.case_id
        WHERE {status_sql}
          AND (
              NULLIF(TRIM(COALESCE(c.contractor, '')), '') IS NULL
           OR NULLIF(TRIM(COALESCE(c.building, '')), '') IS NULL
           OR NULLIF(TRIM(COALESCE(c.device, '')), '') IS NULL
           OR NULLIF(TRIM(COALESCE(c.due_date, '')), '') IS NULL
           OR NULLIF(TRIM(COALESCE(c.period, '')), '') IS NULL
           OR NULLIF(TRIM(COALESCE(fv.client, '')), '') IS NULL
           OR COALESCE(mr.missing_evidence_count, 0) > 0
          )
        ORDER BY COALESCE(rv.open_review_count, 0) DESC, c.updated_at DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()

    cases = []
    for row in rows:
        case = dict(row)
        case["missing_fields"] = _missing_case_fields(case)
        case["missing_required_evidence"] = _missing_required_evidence_for_case(
            case["case_id"],
            case["case_type"],
        )
        case["manual_review_status"] = _manual_review_status(case)
        if _include_missing_data_case(case, field_filter):
            cases.append(case)
    return cases


def get_missing_data_case_detail(case_id: str) -> Optional[Dict[str, Any]]:
    """Return all read-only review data needed by the missing-data detail view."""
    case_row = get_case_by_id(case_id)
    if case_row is None:
        return None

    case = dict(case_row)
    missing_fields = _missing_case_fields(case)
    missing_required_evidence = _missing_required_evidence_for_case(
        case_id,
        case["case_type"],
    )
    manual_review_helper = globals().get("get_manual_reviews_for_case")
    manual_reviews = (
        manual_review_helper(case_id)
        if callable(manual_review_helper)
        else []
    )
    return {
        "case": case,
        "source_emails": [dict(row) for row in get_source_emails_for_case(case_id)],
        "field_values": get_latest_field_values_for_case(case_id),
        "missing_fields": missing_fields,
        "missing_required_evidence": missing_required_evidence,
        "manual_reviews": manual_reviews,
        "proposed_suggestions": [
            dict(row)
            for row in list_case_field_suggestions(
                case_id=case_id,
                status_filter="proposed",
            )
        ],
    }


def insert_case_field_suggestion(
    suggestion_id: str,
    case_id: str,
    field_name: str,
    suggested_value: str,
    confidence: str,
    confidence_score: Optional[float] = None,
    rationale: Optional[str] = None,
    evidence_json: Optional[str] = None,
    source_email_id: Optional[str] = None,
    source: str = "ai_missing_data_enrichment",
    run_id: Optional[str] = None,
    packet_id: Optional[str] = None,
    model_name: Optional[str] = None,
    prompt_schema_version: Optional[str] = None,
    created_at: Optional[str] = None,
) -> None:
    """Insert a proposed field-value suggestion for later human review."""
    _execute_write(
        """
        INSERT INTO case_field_suggestions
            (suggestion_id, case_id, field_name, suggested_value, confidence,
             confidence_score, rationale, evidence_json, source_email_id,
             source, status, run_id, packet_id, model_name,
             prompt_schema_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?)
        """,
        (
            suggestion_id,
            case_id,
            field_name,
            suggested_value,
            confidence,
            confidence_score,
            rationale,
            evidence_json,
            source_email_id,
            source,
            run_id,
            packet_id,
            model_name,
            prompt_schema_version,
            created_at or utc_now_iso(),
        ),
    )


def get_case_field_suggestion(suggestion_id: str) -> Optional[sqlite3.Row]:
    """Return one field suggestion row by ID, or None when absent."""
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM case_field_suggestions WHERE suggestion_id = ?",
        (suggestion_id,),
    ).fetchone()


def list_case_field_suggestions(
    status_filter: str = "proposed",
    case_id: Optional[str] = None,
    field_name: Optional[str] = None,
    limit: int = 100,
) -> List[sqlite3.Row]:
    """Return field suggestions filtered by status, case, or field."""
    clauses: List[str] = []
    params: List[Any] = []
    normalized_status = (status_filter or "proposed").strip().lower()
    if normalized_status != "all":
        clauses.append("status = ?")
        params.append(normalized_status)
    if case_id:
        clauses.append("case_id = ?")
        params.append(case_id)
    if field_name:
        clauses.append("field_name = ?")
        params.append(field_name)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    safe_limit = max(1, int(limit))
    conn = get_connection()
    return conn.execute(
        f"""
        SELECT *
        FROM case_field_suggestions
        {where_sql}
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        tuple(params + [safe_limit]),
    ).fetchall()


def update_case_field_suggestion_status(
    suggestion_id: str,
    status: str,
    *,
    reviewed_by: Optional[str] = None,
    review_notes: Optional[str] = None,
    accepted_at: Optional[str] = None,
    rejected_at: Optional[str] = None,
) -> None:
    """Update a field suggestion review status and optional review metadata."""
    if status not in _CASE_FIELD_SUGGESTION_STATUSES:
        raise ValueError(f"Unsupported case field suggestion status: {status}")
    now = utc_now_iso()
    _execute_write(
        """
        UPDATE case_field_suggestions
        SET status = ?,
            reviewed_at = ?,
            reviewed_by = COALESCE(?, reviewed_by),
            review_notes = COALESCE(?, review_notes),
            accepted_at = COALESCE(?, accepted_at),
            rejected_at = COALESCE(?, rejected_at)
        WHERE suggestion_id = ?
        """,
        (
            status,
            now,
            reviewed_by,
            review_notes,
            accepted_at,
            rejected_at,
            suggestion_id,
        ),
    )


def mark_other_field_suggestions_superseded(
    case_id: str,
    field_name: str,
    accepted_suggestion_id: str,
) -> int:
    """Mark competing proposed suggestions for the same case field superseded."""
    now = utc_now_iso()
    cursor = _execute_write(
        """
        UPDATE case_field_suggestions
        SET status = 'superseded',
            reviewed_at = ?
        WHERE case_id = ?
          AND field_name = ?
          AND suggestion_id != ?
          AND status = 'proposed'
        """,
        (now, case_id, field_name, accepted_suggestion_id),
    )
    return int(cursor.rowcount)


def list_cases_missing_field_for_enrichment(
    field_name: str = "contractor",
    limit: Optional[int] = None,
    case_id: Optional[str] = None,
    building: Optional[str] = None,
    case_type: Optional[str] = None,
) -> List[sqlite3.Row]:
    """Return supported open cases missing ``field_name`` and lacking proposals."""
    if field_name not in _ENRICHMENT_FIELD_COLUMNS:
        raise ValueError(f"Unsupported enrichment field: {field_name}")

    supported_placeholders = ", ".join("?" for _ in SUPPORTED_CASE_TYPES)
    sql = f"""
        SELECT c.*
        FROM cases c
        WHERE c.status != 'closed'
          AND c.case_type IN ({supported_placeholders})
          AND (NULLIF(TRIM(COALESCE(c.{field_name}, '')), '') IS NULL)
          AND NOT EXISTS (
              SELECT 1 FROM case_field_suggestions s
              WHERE s.case_id = c.case_id
                AND s.field_name = ?
                AND s.status = 'proposed'
          )
    """
    params: List[Any] = list(SUPPORTED_CASE_TYPES)
    params.append(field_name)

    if case_id:
        sql += " AND c.case_id = ?"
        params.append(case_id)
    if building:
        sql += " AND lower(COALESCE(c.building, '')) LIKE ?"
        params.append(f"%{building.lower()}%")
    if case_type:
        sql += " AND c.case_type = ?"
        params.append(case_type)

    sql += " ORDER BY c.updated_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))

    conn = get_connection()
    return conn.execute(sql, tuple(params)).fetchall()


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


def update_connection_hypothesis(hypothesis_id: str, updates: Dict[str, Any]) -> None:
    """Update additive review fields on a connection hypothesis."""
    allowed_fields = {
        "summary",
        "confidence",
        "risk_level",
        "evidence_json",
        "reasoning",
        "recommended_human_review",
        "status",
    }
    unsupported_fields = sorted(set(updates) - allowed_fields)
    if unsupported_fields:
        raise ValueError(
            "Unsupported connection hypothesis update field(s): "
            + ", ".join(unsupported_fields)
        )
    if not updates:
        return
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    _execute_write(
        f"UPDATE connection_hypotheses SET {set_clause} WHERE hypothesis_id = ?",
        tuple(updates.values()) + (hypothesis_id,),
    )


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


# ---------------------------------------------------------------------------
# connection_discovery_runs / connection_discovery_packets tables
# ---------------------------------------------------------------------------

_DISCOVERY_RUN_UPDATE_FIELDS = frozenset(
    {
        "scope",
        "status",
        "started_at",
        "completed_at",
        "max_ai_calls",
        "ai_calls_used",
        "packets_created",
        "packets_analyzed",
        "hypotheses_created",
        "hypotheses_rejected",
        "unsupported_records_included",
        "error_count",
        "config_json",
    }
)

_DISCOVERY_PACKET_UPDATE_FIELDS = frozenset(
    {
        "packet_type",
        "entity_type",
        "entity_value",
        "case_count",
        "pattern_count",
        "status",
        "ai_call_used",
        "hypotheses_created",
        "created_at",
        "completed_at",
        "error",
    }
)


def insert_discovery_run(
    run_id: str,
    scope: str,
    status: str,
    max_ai_calls: int,
    started_at: Optional[str] = None,
    config_json: Optional[str] = None,
    ai_calls_used: int = 0,
    packets_created: int = 0,
    packets_analyzed: int = 0,
    hypotheses_created: int = 0,
    hypotheses_rejected: int = 0,
    unsupported_records_included: int = 0,
    error_count: int = 0,
    completed_at: Optional[str] = None,
) -> None:
    """Insert a connection discovery run tracking row."""
    _execute_write(
        """
        INSERT INTO connection_discovery_runs
            (run_id, scope, status, started_at, completed_at, max_ai_calls,
             ai_calls_used, packets_created, packets_analyzed,
             hypotheses_created, hypotheses_rejected,
             unsupported_records_included, error_count, config_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            scope,
            status,
            started_at or utc_now_iso(),
            completed_at,
            int(max_ai_calls),
            int(ai_calls_used),
            int(packets_created),
            int(packets_analyzed),
            int(hypotheses_created),
            int(hypotheses_rejected),
            int(unsupported_records_included),
            int(error_count),
            config_json,
        ),
    )


def update_discovery_run(run_id: str, updates: Dict[str, Any]) -> None:
    """Update fields on a connection discovery run row."""
    unsupported_fields = sorted(set(updates) - _DISCOVERY_RUN_UPDATE_FIELDS)
    if unsupported_fields:
        raise ValueError(
            "Unsupported discovery run update field(s): "
            + ", ".join(unsupported_fields)
        )
    if not updates:
        return
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    _execute_write(
        f"UPDATE connection_discovery_runs SET {set_clause} WHERE run_id = ?",
        tuple(updates.values()) + (run_id,),
    )


def insert_discovery_packet(
    packet_id: str,
    run_id: str,
    packet_type: str,
    status: str = "pending",
    entity_type: Optional[str] = None,
    entity_value: Optional[str] = None,
    case_count: int = 0,
    pattern_count: int = 0,
    ai_call_used: int = 0,
    hypotheses_created: int = 0,
    created_at: Optional[str] = None,
    completed_at: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Insert a connection discovery packet tracking row."""
    _execute_write(
        """
        INSERT INTO connection_discovery_packets
            (packet_id, run_id, packet_type, entity_type, entity_value,
             case_count, pattern_count, status, ai_call_used,
             hypotheses_created, created_at, completed_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            packet_id,
            run_id,
            packet_type,
            entity_type,
            entity_value,
            int(case_count),
            int(pattern_count),
            status,
            int(ai_call_used),
            int(hypotheses_created),
            created_at or utc_now_iso(),
            completed_at,
            error,
        ),
    )


def update_discovery_packet(packet_id: str, updates: Dict[str, Any]) -> None:
    """Update fields on a connection discovery packet row."""
    unsupported_fields = sorted(set(updates) - _DISCOVERY_PACKET_UPDATE_FIELDS)
    if unsupported_fields:
        raise ValueError(
            "Unsupported discovery packet update field(s): "
            + ", ".join(unsupported_fields)
        )
    if not updates:
        return
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    _execute_write(
        f"UPDATE connection_discovery_packets SET {set_clause} WHERE packet_id = ?",
        tuple(updates.values()) + (packet_id,),
    )


def get_discovery_runs(
    status_filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[sqlite3.Row]:
    """Return discovery runs ordered newest first."""
    conn = get_connection()
    sql = "SELECT * FROM connection_discovery_runs"
    params: list[Any] = []
    if status_filter:
        sql += " WHERE status = ?"
        params.append(status_filter)
    sql += " ORDER BY started_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_discovery_packets_for_run(
    run_id: str,
    status_filter: Optional[str] = None,
) -> List[sqlite3.Row]:
    """Return packet tracking rows for a discovery run."""
    conn = get_connection()
    sql = "SELECT * FROM connection_discovery_packets WHERE run_id = ?"
    params: list[Any] = [run_id]
    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter)
    sql += " ORDER BY created_at ASC, packet_id ASC"
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


# ---------------------------------------------------------------------------
# case_data_requirements table
# ---------------------------------------------------------------------------

def upsert_case_data_requirement(
    requirement_id: str,
    case_id: str,
    requirement_key: str,
    label: str,
    status: str = "missing",
    required: int = 1,
    source: Optional[str] = None,
    evidence_json: Optional[str] = None,
) -> None:
    """Upsert a case data requirement row.

    Args:
        requirement_id: UUID for this requirement.
        case_id: Case this requirement belongs to.
        requirement_key: Unique key within case (e.g. 'upload_confirmation').
        label: Human-readable label for the requirement.
        status: One of REQUIREMENT_STATUSES.
        required: 1 if required, 0 if optional.
        source: Where the value came from (e.g. 'email_body', 'manual_entry').
        evidence_json: JSON object with supporting evidence.
    """
    now = utc_now_iso()
    _execute_write(
        """
        INSERT INTO case_data_requirements
            (requirement_id, case_id, requirement_key, label, status, required, source, evidence_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(case_id, requirement_key) DO UPDATE SET
            status = CASE WHEN status IN ('provided', 'partial') THEN status ELSE excluded.status END,
            source = excluded.source,
            evidence_json = excluded.evidence_json,
            updated_at = excluded.updated_at
        """,
        (requirement_id, case_id, requirement_key, label, status, required, source, evidence_json, now, now),
    )


def get_case_data_requirements(case_id: str) -> List[Dict[str, Any]]:
    """Fetch all data requirements for a case.

    Args:
        case_id: Case to fetch requirements for.

    Returns:
        List of requirement dicts with keys: requirement_id, case_id, requirement_key, label,
        status, required, source, evidence_json, created_at, updated_at.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM case_data_requirements WHERE case_id = ? ORDER BY created_at ASC",
        (case_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def update_case_requirement_status(
    case_id: str,
    requirement_key: str,
    status: str,
    source: Optional[str] = None,
    evidence_json: Optional[str] = None,
) -> None:
    """Update the status of a specific requirement within a case.

    Args:
        case_id: Case owning the requirement.
        requirement_key: Requirement key within that case.
        status: New status (one of REQUIREMENT_STATUSES).
        source: Optional source/reason for the update.
        evidence_json: Optional evidence for the update.
    """
    now = utc_now_iso()
    _execute_write(
        """
        UPDATE case_data_requirements
        SET status = ?, source = ?, evidence_json = ?, updated_at = ?
        WHERE case_id = ? AND requirement_key = ?
        """,
        (status, source, evidence_json, now, case_id, requirement_key),
    )


# ---------------------------------------------------------------------------
# reply_case_mappings table
# ---------------------------------------------------------------------------

def insert_reply_case_mapping(
    mapping_id: str,
    reply_email_id: str,
    case_id: Optional[str] = None,
    group_id: Optional[str] = None,
    mapping_source: str = "manual",
    confidence: Optional[str] = None,
    status: str = "proposed",
) -> None:
    """Insert a mapping from a reply email to a case or group.

    At least one of case_id or group_id must be provided.

    Args:
        mapping_id: UUID for this mapping.
        reply_email_id: The inbound reply email being mapped.
        case_id: Target case ID (optional if group_id provided).
        group_id: Target building group ID (optional if case_id provided).
        mapping_source: One of REPLY_MAPPING_SOURCES.
        confidence: Confidence level (e.g. 'low', 'medium', 'high').
        status: One of REPLY_MAPPING_STATUSES (default 'proposed').
    """
    now = utc_now_iso()
    _execute_write(
        """
        INSERT INTO reply_case_mappings
            (mapping_id, reply_email_id, case_id, group_id, mapping_source, confidence, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mapping_id, reply_email_id, case_id, group_id, mapping_source, confidence, status, now, now),
    )


def get_reply_mappings_for_email(reply_email_id: str) -> List[Dict[str, Any]]:
    """Fetch all mappings for a reply email.

    Args:
        reply_email_id: Email to fetch mappings for.

    Returns:
        List of mapping dicts.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reply_case_mappings WHERE reply_email_id = ? ORDER BY created_at ASC",
        (reply_email_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_reply_mappings_for_case(case_id: str) -> List[Dict[str, Any]]:
    """Fetch all mappings for a case.

    Args:
        case_id: Case to fetch mappings for.

    Returns:
        List of mapping dicts.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reply_case_mappings WHERE case_id = ? ORDER BY created_at ASC",
        (case_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_reply_mappings_for_group(group_id: str) -> List[Dict[str, Any]]:
    """Fetch all mappings for a building group.

    Args:
        group_id: Group to fetch mappings for.

    Returns:
        List of mapping dicts.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM reply_case_mappings WHERE group_id = ? ORDER BY created_at ASC",
        (group_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def update_reply_mapping_completeness(
    mapping_id: str,
    completeness_json: Optional[str],
) -> None:
    """Update the completeness analysis for a reply mapping.

    Args:
        mapping_id: Mapping to update.
        completeness_json: JSON object with completeness analysis results.
    """
    now = utc_now_iso()
    _execute_write(
        """
        UPDATE reply_case_mappings
        SET completeness_json = ?, updated_at = ?
        WHERE mapping_id = ?
        """,
        (completeness_json, now, mapping_id),
    )


# Additional helpers for manual reviews and building groups

def insert_manual_review(
    review_id: str,
    case_id: str,
    email_id: Optional[str] = None,
    reason: Optional[str] = None,
    review_category: Optional[str] = None,
    blocking: int = 1,
) -> None:
    """Insert a manual review record.

    Args:
        review_id: UUID for this review.
        case_id: Case to review.
        email_id: Source email ID (optional).
        reason: Human-readable reason for review.
        review_category: Category of review (one of REVIEW_CATEGORIES).
        blocking: 1 if this blocks action, 0 if informational.
    """
    now = utc_now_iso()
    _execute_write(
        """
        INSERT INTO manual_reviews
            (review_id, case_id, email_id, reason, review_category, blocking, flagged_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (review_id, case_id, email_id, reason, review_category, blocking, now),
    )


def get_manual_reviews_for_case(case_id: str) -> List[Dict[str, Any]]:
    """Fetch all manual reviews for a case.

    Args:
        case_id: Case to fetch reviews for.

    Returns:
        List of review dicts.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM manual_reviews WHERE case_id = ? ORDER BY flagged_at ASC",
        (case_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_building_issue_group_cases(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Fetch building_issue_group_cases rows with optional filters.

    Args:
        filters: Dict with optional filters: {case_id, group_id, status}

    Returns:
        List of dicts.
    """
    conn = get_connection()
    sql = "SELECT * FROM building_issue_group_cases WHERE 1=1"
    params = []

    if filters:
        if "case_id" in filters:
            sql += " AND case_id = ?"
            params.append(filters["case_id"])
        if "group_id" in filters:
            sql += " AND group_id = ?"
            params.append(filters["group_id"])
        if "status" in filters:
            sql += " AND status = ?"
            params.append(filters["status"])

    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_building_group(group_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a building group by ID.

    Args:
        group_id: Group to fetch.

    Returns:
        Dict or None if not found.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM building_issue_groups WHERE group_id = ?",
        (group_id,),
    ).fetchone()
    return dict(row) if row else None


def list_building_groups(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Fetch building_issue_groups rows with optional filters.

    Args:
        filters: Dict with optional filters: {status, building}

    Returns:
        List of dicts.
    """
    conn = get_connection()
    sql = "SELECT * FROM building_issue_groups WHERE 1=1"
    params = []

    if filters:
        if "status" in filters:
            sql += " AND status = ?"
            params.append(filters["status"])
        if "building" in filters:
            sql += " AND normalized_building LIKE ?"
            params.append(f"%{filters['building']}%")

    sql += " ORDER BY updated_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def update_draft_status(
    draft_id: str,
    status: str,
    approved_at: Optional[str] = None,
    rejected_at: Optional[str] = None,
    review_notes: Optional[str] = None,
) -> None:
    """Update the status of a building_group_emails draft.

    Args:
        draft_id: Draft to update.
        status: New status (one of DRAFT_STATUSES).
        approved_at: Approval timestamp (if approving).
        rejected_at: Rejection timestamp (if rejecting).
        review_notes: Notes for rejection.
    """
    now = utc_now_iso()
    _execute_write(
        """
        UPDATE building_group_emails
        SET status = ?, approved_at = ?, rejected_at = ?, review_notes = ?, updated_at = ?
        WHERE group_email_id = ?
        """,
        (status, approved_at, rejected_at, review_notes, now, draft_id),
    )
