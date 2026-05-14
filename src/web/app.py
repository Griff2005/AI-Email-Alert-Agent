"""
web/app.py — Flask case management web UI.

Routes:
  GET  /                       -> dashboard (metrics, pipeline, activity)
  GET  /cases                  -> case table view
  GET  /cases/<case_id>        -> case detail page
  GET  /reviews                -> manual review queue
  GET  /events                 -> recent events feed
  GET  /patterns               -> memory/intelligence flags
  GET  /observability.json     -> local metrics and safety snapshot
  GET  /emails                 -> email work queue / backlog
  GET  /emails/<email_id>      -> single email detail
  POST /cases/<case_id>/close          -> mark case closed (manual only)
  POST /cases/<case_id>/resolve-review -> resolve a manual review item
"""

import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional

# Allow imports from src/ when running from src/web/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, redirect, render_template, request, url_for, flash
from config import config, PROJECT_ROOT
import building_groups as group_service
import database as db
import memory
from constants import GROUP_STATUSES
from observability import build_metrics_snapshot
from runtime_options import runtime_options

app = Flask(__name__, template_folder="templates")
app.secret_key = "solucore-demo-secret-not-for-production"


_SEVERITY_RANK = {
    "info": 1,
    "medium": 2,
    "high": 3,
    "review": 4,
}


_PATTERN_LABELS = {
    "repeated_building_issue": "Repeated Building",
    "repeated_device_issue": "Repeated Device",
    "repeated_contractor_issue": "Contractor Pattern",
    "repeated_no_response": "No Response",
    "repeated_data_absence": "Repeated Data Absence",
    "repeated_major_work_overdue": "Major Work Pattern",
    "repeated_maintenance_shortfall": "Shortfall Pattern",
    "mechanic_recurrence": "Mechanic Pattern",
    "mechanic_rotation": "Mechanic Pattern",
}


def _humanize(value: Optional[str]) -> str:
    if not value:
        return ""
    return str(value).replace("_", " ").replace("-", " ").title()


def _parse_evidence(evidence_json: Optional[str]) -> Dict[str, Any]:
    if not evidence_json:
        return {}
    try:
        parsed = json.loads(evidence_json)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _case_ids_from_evidence(evidence: Dict[str, Any]) -> List[str]:
    case_ids: List[str] = []
    for key in ("supporting_case_ids", "related_case_ids"):
        values = evidence.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if value and str(value) not in case_ids:
                case_ids.append(str(value))
    return case_ids


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _evidence_why(evidence: Dict[str, Any]) -> Optional[str]:
    observed_count = evidence.get("observed_count")
    entity_type = evidence.get("entity_type")
    entity_value = evidence.get("entity_value")
    window_days = evidence.get("time_window_days")
    threshold = evidence.get("threshold")
    supporting_cases = _case_ids_from_evidence(evidence)

    if observed_count and entity_type and entity_value and window_days:
        item_label = "cases" if supporting_cases else "observations"
        why = (
            f"{observed_count} {item_label} observed for "
            f"{str(entity_type).replace('_', ' ')} {entity_value} over {window_days} days"
        )
        if threshold:
            why += f" (threshold: {threshold})"
        return why + "."
    if evidence.get("rule"):
        return f"Rule matched: {evidence['rule']}."
    return None


def _enrich_pattern_flag(flag: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(flag)
    evidence = _parse_evidence(enriched.get("evidence_json"))
    case_ids = _case_ids_from_evidence(evidence)
    observation_ids = evidence.get("supporting_observation_ids")
    if not isinstance(observation_ids, list):
        observation_ids = []
    enriched.update(
        {
            "pattern_label": _PATTERN_LABELS.get(enriched.get("pattern_type"), _humanize(enriched.get("pattern_type"))),
            "evidence": evidence,
            "evidence_why": _evidence_why(evidence),
            "supporting_case_ids": case_ids,
            "supporting_observation_ids": observation_ids,
            "evidence_count": max(len(case_ids), len(observation_ids), _safe_int(evidence.get("observed_count"))),
            "entity_type": evidence.get("entity_type"),
            "entity_value": evidence.get("entity_value"),
        }
    )
    return enriched


def _pattern_indicator(case_id: str) -> Optional[Dict[str, Any]]:
    flags = [dict(row) for row in db.get_active_pattern_flags_for_case(case_id)]
    if not flags:
        return None
    highest = max(flags, key=lambda flag: _SEVERITY_RANK.get(flag["severity"], 0))
    severity_label = {
        "review": "Review",
        "high": "Repeated",
        "medium": "Pattern",
        "info": "Memory",
    }.get(highest["severity"], "Pattern")
    pattern_labels = [
        _PATTERN_LABELS.get(flag["pattern_type"], _humanize(flag["pattern_type"]))
        for flag in flags
    ]
    return {
        "count": len(flags),
        "severity": highest["severity"],
        "label": severity_label,
        "pattern_labels": pattern_labels,
        "primary_pattern_label": pattern_labels[0] if pattern_labels else severity_label,
    }


def _case_count_map(sql: str, params: Iterable[Any] = ()) -> Dict[str, int]:
    conn = db.get_connection()
    return {
        row["case_id"]: int(row["count"])
        for row in conn.execute(sql, tuple(params)).fetchall()
    }


def _related_case_counts() -> Dict[str, int]:
    return _case_count_map(
        """
        SELECT case_id, COUNT(DISTINCT related_case_id) AS count
        FROM (
            SELECT source_case_id AS case_id, target_case_id AS related_case_id FROM case_links
            UNION ALL
            SELECT target_case_id AS case_id, source_case_id AS related_case_id FROM case_links
        )
        GROUP BY case_id
        """
    )


def _open_review_counts() -> Dict[str, int]:
    return _case_count_map(
        """
        SELECT case_id, COUNT(*) AS count
        FROM manual_reviews
        WHERE resolved = 0
        GROUP BY case_id
        """
    )


def _case_type_options() -> List[str]:
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT DISTINCT case_type FROM cases ORDER BY case_type"
    ).fetchall()
    return [row["case_type"] for row in rows]


def _entity_connections(case: Dict[str, Any], fields: List[Dict[str, Any]], memory_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    counts = memory_context.get("counts", {})
    field_values = {
        row["field_name"]: row.get("field_value")
        for row in fields
        if row.get("field_value")
    }
    client = (
        field_values.get("client")
        or field_values.get("customer")
        or field_values.get("property_manager")
    )
    connections = [
        {
            "label": "Building",
            "value": case.get("building"),
            "count_label": "Cases with same building",
            "count": counts.get("building_cases_60_days", 0),
            "window": "last 60 days",
        },
        {
            "label": "Device",
            "value": case.get("device"),
            "count_label": "Cases with same device",
            "count": counts.get("device_cases_90_days", 0),
            "window": "last 90 days",
        },
        {
            "label": "Contractor",
            "value": case.get("contractor"),
            "count_label": "Cases with same contractor",
            "count": counts.get("contractor_cases_60_days", 0),
            "window": "last 60 days",
        },
        {
            "label": "Client",
            "value": client,
            "count_label": "Client observations",
            "count": 1 if client else 0,
            "window": None,
        },
    ]
    return connections


def _memory_summary_cards(patterns: List[Dict[str, Any]]) -> Dict[str, int]:
    def distinct_values(entity_type: str, pattern_fragment: Optional[str] = None) -> set:
        values = set()
        for pattern in patterns:
            matches_entity = pattern.get("entity_type") == entity_type
            matches_type = pattern_fragment and pattern_fragment in (pattern.get("pattern_type") or "")
            if (matches_entity or matches_type) and pattern.get("entity_value"):
                values.add(pattern["entity_value"])
        return values

    return {
        "active_patterns": len(patterns),
        "review_or_high_patterns": len([p for p in patterns if p.get("severity") in ("review", "high")]),
        "repeated_buildings": len(distinct_values("building", "building")),
        "repeated_devices": len(distinct_values("device", "device")),
        "repeated_contractors": len(distinct_values("contractor", "contractor")),
        "no_response_patterns": len([p for p in patterns if p.get("pattern_type") == "repeated_no_response"]),
        "mechanic_patterns": len([p for p in patterns if (p.get("pattern_type") or "").startswith("mechanic_")]),
    }


def _event_badge_class(event_type: Optional[str]) -> str:
    if event_type in {"memory_updated", "pattern_detected"}:
        return "badge-medium"
    if event_type == "manual_review_created":
        return "badge-review"
    if event_type == "reply_received":
        return "badge-info"
    if event_type == "followup_triggered":
        return "badge-high"
    return "badge-low"


@app.context_processor
def inject_runtime_badges():
    options = runtime_options.get()
    return {
        "demo_mode": config.DEMO_MODE,
        "demo_recipient": config.DEMO_RECIPIENT_EMAIL,
        "ai_runtime_label": "Budgeted" if options.ai_enabled else "Disabled",
    }


@app.route("/")
def index():
    """Render the dashboard page with high-level demo metrics."""
    summary = db.get_dashboard_summary()
    recent_activity = db.get_recent_agent_activity(limit=15)
    for ev in recent_activity:
        ev["badge_class"] = _event_badge_class(ev.get("event_type"))
    case_type_breakdown = db.get_case_counts_by_type()
    pipeline_summary = db.get_email_pipeline_summary()
    return render_template(
        "dashboard.html",
        summary=summary,
        recent_activity=recent_activity,
        case_type_breakdown=case_type_breakdown,
        pipeline_summary=pipeline_summary,
    )


@app.route("/cases")
def cases():
    """Render the case list table.

    Accepts ``?status=open`` or ``?status=closed`` query parameter for filtering.
    """
    status_filter = request.args.get("status")
    case_type_filter = request.args.get("case_type")
    patterned_only = request.args.get("patterned") == "1"
    review_required_only = request.args.get("review") == "1"
    related_counts = _related_case_counts()
    review_counts = _open_review_counts()
    all_cases = db.get_all_cases(status_filter=status_filter)
    cases_list = []
    for case in all_cases:
        case_dict = dict(case)
        if case_type_filter and case_dict["case_type"] != case_type_filter:
            continue
        memory_indicator = _pattern_indicator(case_dict["case_id"])
        case_dict["memory_indicator"] = memory_indicator
        case_dict["pattern_count"] = memory_indicator["count"] if memory_indicator else 0
        case_dict["related_case_count"] = related_counts.get(case_dict["case_id"], 0)
        case_dict["open_review_count"] = review_counts.get(case_dict["case_id"], 0)
        if patterned_only and not case_dict["pattern_count"]:
            continue
        if review_required_only and not case_dict["open_review_count"]:
            continue
        cases_list.append(case_dict)
    return render_template(
        "cases.html",
        cases=cases_list,
        status_filter=status_filter,
        case_type_filter=case_type_filter,
        patterned_only=patterned_only,
        review_required_only=review_required_only,
        case_type_options=_case_type_options(),
    )


@app.route("/building-groups")
def building_groups():
    """Render the building issue group list page."""
    filters = {
        "status": request.args.get("status", "").strip(),
        "building": request.args.get("building", "").strip(),
        "contractor": request.args.get("contractor", "").strip(),
    }
    groups = group_service.list_building_groups(filters)
    return render_template(
        "building_groups.html",
        groups=groups,
        filters=filters,
        group_statuses=GROUP_STATUSES,
    )


@app.route("/building-groups/<group_id>")
def building_group_detail(group_id):
    """Render a read-only detail page for one building issue group."""
    summary = group_service.get_group_summary(group_id)
    if not summary:
        flash(f"Building group {group_id} not found.", "error")
        return redirect(url_for("building_groups"))
    latest_rows = db.list_building_group_emails(group_id=group_id, limit=1)
    latest_draft = dict(latest_rows[0]) if latest_rows else None
    if latest_draft:
        latest_draft["quality_check"] = _parse_evidence(latest_draft.get("quality_check_json"))
    return render_template(
        "building_group_detail.html",
        group=summary["group"],
        cases=summary["cases"],
        counts=summary["counts"],
        timeline=summary["timeline"],
        latest_draft=latest_draft,
    )


@app.route("/building-groups/<group_id>/generate-draft", methods=["POST"])
def generate_building_group_draft(group_id):
    """Generate a review-only consolidated draft for a building issue group."""
    import group_email_builder

    email_type = request.form.get("email_type", "initial")
    try:
        draft_id = group_email_builder.create_group_email_draft(group_id, email_type=email_type)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash(f"Group draft generated for review: {draft_id}", "success")
    return redirect(url_for("building_group_detail", group_id=group_id))


@app.route("/cases/<case_id>")
def case_detail(case_id):
    """Render the detail page for a single case.

    Loads case row, event timeline, outbound messages, extracted fields, and
    follow-up status. Redirects with a flash error if the case is not found.
    """
    case = db.get_case_by_id(case_id)
    if not case:
        flash(f"Case {case_id} not found.", "error")
        return redirect(url_for("cases"))

    events = db.get_events_for_case(case_id)
    messages = db.get_messages_for_case(case_id)
    fields = [dict(f) for f in db.get_fields_for_case(case_id)]
    followup = db.get_followup_for_case(case_id)
    memory_context = memory.get_memory_context_for_case(case_id)
    memory_context["active_pattern_flags"] = [
        _enrich_pattern_flag(dict(flag))
        for flag in memory_context.get("active_pattern_flags", [])
    ]
    memory_context["recent_observations"] = [
        dict(row) for row in db.get_observations_for_case(case_id, limit=12)
    ]
    memory_context["related_cases"] = [
        dict(row) for row in db.get_related_cases_for_case(case_id, limit=12)
    ]

    return render_template(
        "case_detail.html",
        case=dict(case),
        events=[dict(e) for e in events],
        messages=[dict(m) for m in messages],
        fields=fields,
        followup=dict(followup) if followup else None,
        memory_context=memory_context,
        memory_summary=memory_context["summary"],
        entity_connections=_entity_connections(dict(case), fields, memory_context),
    )


@app.route("/cases/<case_id>/close", methods=["POST"])
def close_case(case_id):
    """Manually close a case.

    Updates ``status`` to ``'closed'``, closes the follow-up record, and logs
    a ``case_closed`` event. Only reachable via explicit human form submission.
    """
    case = db.get_case_by_id(case_id)
    if not case:
        flash(f"Case {case_id} not found.", "error")
        return redirect(url_for("cases"))

    db.update_case(case_id, {"status": "closed"})
    db.close_followup(case_id)
    import uuid
    db.insert_case_event(
        event_id=str(uuid.uuid4()),
        case_id=case_id,
        event_type="case_closed",
        description="Case manually closed via web UI.",
    )
    flash(f"Case {case_id} closed.", "success")
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/cases/<case_id>/resolve-review", methods=["POST"])
def resolve_review_for_case(case_id):
    """Mark a specific manual review item as resolved.

    Expects ``review_id`` in the POST form body.
    """
    review_id = request.form.get("review_id")
    if review_id:
        db.resolve_manual_review(review_id)
        flash("Review item resolved.", "success")
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/reviews")
def reviews():
    """Render the manual review queue with case context."""
    open_reviews = []
    for row in db.get_open_manual_reviews():
        review = dict(row)
        flags = [
            _enrich_pattern_flag(dict(flag))
            for flag in db.get_active_pattern_flags_for_case(review["case_id"])
        ]
        review["is_pattern_review"] = (review.get("reason") or "").lower().startswith("pattern review")
        review["pattern_context"] = flags[0] if flags else None
        open_reviews.append(review)
    return render_template("reviews.html", reviews=open_reviews)


@app.route("/events")
def events():
    """Render a global feed of the 100 most recent case events."""
    conn = db.get_connection()
    recent_events = conn.execute(
        """
        SELECT ce.*, c.case_type, c.building
        FROM case_events ce
        LEFT JOIN cases c ON ce.case_id = c.case_id
        ORDER BY ce.created_at DESC
        LIMIT 100
        """
    ).fetchall()
    events_list = []
    for row in recent_events:
        event = dict(row)
        event["badge_class"] = _event_badge_class(event.get("event_type"))
        events_list.append(event)
    return render_template("events.html", events=events_list)


@app.route("/observability.json")
def observability_json():
    """Return a read-only JSON metrics and safety snapshot."""
    return jsonify(build_metrics_snapshot())


@app.route("/connection-hypotheses.json")
def connection_hypotheses_json():
    """Return proposed connection hypotheses as read-only JSON.

    Never triggers AI, sends email, mutates cases, or schedules work.
    """
    status_filter = request.args.get("status", "proposed")
    rows = db.get_connection_hypotheses(status_filter=status_filter or None)
    hypotheses = []
    for row in rows:
        hyp = dict(row)
        hyp["case_ids"] = db.get_cases_for_hypothesis(hyp["hypothesis_id"])
        hypotheses.append(hyp)
    return jsonify({
        "hypotheses": hypotheses,
        "count": len(hypotheses),
        "status_filter": status_filter,
    })


@app.route("/patterns")
def patterns():
    """Render active memory/pattern flags across all cases."""
    severity_filter = request.args.get("severity")
    pattern_type_filter = request.args.get("pattern_type")
    active_patterns = [
        _enrich_pattern_flag(dict(row))
        for row in db.get_active_pattern_flags()
    ]
    summary = _memory_summary_cards(active_patterns)
    pattern_type_options = sorted({pattern["pattern_type"] for pattern in active_patterns})

    filtered_patterns = []
    for pattern in active_patterns:
        if severity_filter and pattern["severity"] != severity_filter:
            continue
        if pattern_type_filter and pattern["pattern_type"] != pattern_type_filter:
            continue
        filtered_patterns.append(pattern)

    return render_template(
        "patterns.html",
        patterns=filtered_patterns,
        summary=summary,
        severity_filter=severity_filter,
        pattern_type_filter=pattern_type_filter,
        pattern_type_options=pattern_type_options,
    )


@app.route("/emails")
def emails():
    """Render the email work queue showing all ingested emails and their pipeline status."""
    status_filter = request.args.get("status", "")
    pipeline_summary = db.get_email_pipeline_summary()
    email_list = db.get_email_backlog(limit=200, status_filter=status_filter)
    return render_template(
        "emails.html",
        emails=email_list,
        pipeline_summary=pipeline_summary,
        status_filter=status_filter,
    )


@app.route("/ingest", methods=["POST"])
def ingest():
    """Run the email ingest pipeline from data/sample_emails.json.

    Loads every sample email, stores it in the database, and runs the full
    case-manager pipeline for each one. Safe to run multiple times — existing
    emails and cases are updated rather than duplicated.
    """
    import json
    import uuid as _uuid
    from case_manager import process_email as _process_email
    from content_safety import sanitize_email_content

    sample_path = PROJECT_ROOT / "data" / "sample_emails.json"
    if not sample_path.exists():
        flash("sample_emails.json not found in data/. Cannot ingest.", "error")
        return redirect(url_for("index"))

    db.init_schema()

    with open(sample_path, "r", encoding="utf-8") as f:
        emails = json.load(f)

    created = updated = skipped = reviewed = 0
    for em in emails:
        email_id = em.get("id") or str(_uuid.uuid4())
        normalized = sanitize_email_content(em.get("body", ""))
        db.insert_email(
            email_id=email_id,
            message_id=em.get("id", email_id),
            thread_id=None,
            subject=em.get("subject", ""),
            from_addr=em.get("from", ""),
            to_addr=em.get("to", ""),
            received_at=em.get("date", ""),
            raw_body=em.get("body", ""),
            normalized_text=normalized,
        )
        result = _process_email(
            email_id=email_id,
            subject=em.get("subject", ""),
            body=em.get("body", ""),
            from_addr=em.get("from", ""),
            received_at=em.get("date", ""),
            verbose=False,
        )
        action = result.get("action", "")
        if action == "created":
            created += 1
        elif action == "updated":
            updated += 1
        elif action == "skipped":
            skipped += 1
        elif action == "review_flagged":
            reviewed += 1

    flash(
        f"Ingest complete: {created} created, {updated} updated, "
        f"{skipped} skipped, {reviewed} flagged for review.",
        "success",
    )
    return redirect(url_for("index"))


@app.route("/emails/<email_id>")
def email_detail(email_id):
    """Render the detail page for a single ingested email."""
    email = db.get_email_by_id(email_id)
    if not email:
        flash(f"Email {email_id} not found.", "error")
        return redirect(url_for("emails"))
    email_dict = dict(email)
    events = db.get_events_for_email(email_id)
    for ev in events:
        ev["badge_class"] = _event_badge_class(ev.get("event_type"))
    return render_template(
        "email_detail.html",
        email=email_dict,
        events=events,
    )


def create_app():
    """Return the configured Flask app. Call this from agent.py."""
    return app
