"""Shared string constants for safety-critical demo behavior.

This module intentionally stays small. It centralizes only strings that are
used across modules or are easy to mistype in safety-sensitive paths.
"""

from __future__ import annotations

CASE_TYPE_CAT1_COMPLIANCE = "CAT1_COMPLIANCE"
CASE_TYPE_CAT5_COMPLIANCE = "CAT5_COMPLIANCE"
CASE_TYPE_DATA_ABSENCE = "DATA_ABSENCE"
CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL = "MAINTENANCE_HOURS_SHORTFALL"
CASE_TYPE_MAJOR_WORK_OVERDUE = "MAJOR_WORK_OVERDUE"
CASE_TYPE_GOVERNMENT_DIRECTIVE = "GOVERNMENT_DIRECTIVE"
CASE_TYPE_UNKNOWN = "UNKNOWN"

SUPPORTED_CASE_TYPES = (
    CASE_TYPE_CAT1_COMPLIANCE,
    CASE_TYPE_CAT5_COMPLIANCE,
    CASE_TYPE_DATA_ABSENCE,
    CASE_TYPE_MAINTENANCE_HOURS_SHORTFALL,
    CASE_TYPE_MAJOR_WORK_OVERDUE,
    CASE_TYPE_GOVERNMENT_DIRECTIVE,
)
CLASSIFIABLE_CASE_TYPES = (*SUPPORTED_CASE_TYPES, CASE_TYPE_UNKNOWN)

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_DRAFT = "draft"
STATUS_SENT = "sent"
STATUS_SENT_DRY_RUN = "sent_dry_run"
STATUS_ACTIVE = "active"
STATUS_RESOLVED = "resolved"

EVENT_ACTION_INDICATED = "action_indicated"
EVENT_BACKLOG_CASE_CREATED = "backlog_case_created"
EVENT_BACKLOG_CASE_UPDATED = "backlog_case_updated"
EVENT_BACKLOG_EMAIL_IMPORTED = "backlog_email_imported"
EVENT_BACKLOG_MEMORY_UPDATED = "backlog_memory_updated"
EVENT_CASE_CLOSED = "case_closed"
EVENT_CASE_CREATED = "case_created"
EVENT_EMAIL_DRY_RUN = "email_dry_run"
EVENT_EMAIL_RECEIVED = "email_received"
EVENT_EMAIL_SENT = "email_sent"
EVENT_ESCALATED = "escalated"
EVENT_FLAGGED_FOR_REVIEW = "flagged_for_review"
EVENT_FOLLOWUP_TRIGGERED = "followup_triggered"
EVENT_MEMORY_UPDATED = "memory_updated"
EVENT_REPLY_RECEIVED = "reply_received"

REVIEW_REASON_PROMPT_INJECTION = "Possible prompt injection content detected in email body."
REVIEW_REASON_REPLY_POSSIBLE_RESOLUTION = (
    "Reply indicates possible resolution. Manual confirmation required before case closure."
)

HYPOTHESIS_STATUS_PROPOSED = "proposed"

HYPOTHESIS_CONFIDENCE_LOW = "low"
HYPOTHESIS_CONFIDENCE_MEDIUM = "medium"
HYPOTHESIS_CONFIDENCE_HIGH = "high"

HYPOTHESIS_RISK_INFO = "info"
HYPOTHESIS_RISK_REVIEW = "review"
HYPOTHESIS_RISK_MANAGEMENT_REVIEW = "management_review"

VALID_HYPOTHESIS_CONFIDENCES = (
    HYPOTHESIS_CONFIDENCE_LOW,
    HYPOTHESIS_CONFIDENCE_MEDIUM,
    HYPOTHESIS_CONFIDENCE_HIGH,
)

VALID_HYPOTHESIS_RISK_LEVELS = (
    HYPOTHESIS_RISK_INFO,
    HYPOTHESIS_RISK_REVIEW,
    HYPOTHESIS_RISK_MANAGEMENT_REVIEW,
)

GROUP_STATUSES = (
    "open",
    "updated_since_last_email",
    "closed",
    "blocked",
)

CASE_GROUP_SOURCE = (
    "live_pipeline",
    "backlog_import",
    "manual",
)

GROUP_CASE_STATUS = (
    "active",
    "closed",
    "removed",
)

DRAFT_STATUSES = (
    "draft_generated",
    "needs_review",
    "approved",
    "sent",
    "rejected",
    "revised",
)

EMAIL_TYPES = (
    "initial",
    "followup",
    "clarification",
)

QUEUE_STATUSES = (
    "pending",
    "ready",
    "suppressed",
    "completed",
)

QUEUE_TYPES = (
    "initial_outreach",
    "followup",
    "clarification",
)

# Phase 3: Data requirements and reply mapping statuses
REQUIREMENT_STATUSES = (
    "missing",
    "provided",
    "partial",
    "not_applicable",
)

REPLY_MAPPING_SOURCES = (
    "manual",
    "deterministic",
    "ai_assisted",
)

REPLY_MAPPING_STATUSES = (
    "proposed",
    "confirmed",
    "rejected",
)

REPLY_COMPLETENESS_RESULTS = (
    "complete",
    "partial",
    "vague",
    "completion_claimed_no_evidence",
    "future_action",
    "clarification_needed",
    "unrelated",
)

REVIEW_CATEGORIES = (
    "missing_required_field",
    "ambiguous_building",
    "ambiguous_contractor",
    "duplicate_uncertainty",
    "prompt_injection",
    "reply_claims_completion",
    "evidence_missing",
    "unsupported_format",
    "ai_hypothesis_review",
    "communication_blocked",
    "draft_quality_failure",
    "reply_mapping_needed",
    "data_requirement_incomplete",
)
