"""
web/app.py — Flask case management web UI.

Routes:
  GET  /                  -> redirect to /cases
  GET  /cases             -> case table view
  GET  /cases/<case_id>   -> case detail page
  GET  /reviews           -> manual review queue
  GET  /events            -> recent events feed
  POST /cases/<case_id>/close   -> mark case closed (manual only)
  POST /cases/<case_id>/resolve-review  -> resolve a manual review item
"""

import sys
import os

# Allow imports from src/ when running from src/web/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, redirect, render_template, request, url_for, flash
import database as db
import memory

app = Flask(__name__, template_folder="templates")
app.secret_key = "solucore-demo-secret-not-for-production"


_SEVERITY_RANK = {
    "info": 1,
    "medium": 2,
    "high": 3,
    "review": 4,
}


def _pattern_indicator(case_id: str):
    flags = [dict(row) for row in db.get_active_pattern_flags_for_case(case_id)]
    if not flags:
        return None
    highest = max(flags, key=lambda flag: _SEVERITY_RANK.get(flag["severity"], 0))
    label = {
        "review": "Review",
        "high": "Repeated",
        "medium": "Pattern",
        "info": "Memory",
    }.get(highest["severity"], "Pattern")
    return {
        "count": len(flags),
        "severity": highest["severity"],
        "label": label,
    }


@app.route("/")
def index():
    """Redirect the root URL to the cases list."""
    return redirect(url_for("cases"))


@app.route("/cases")
def cases():
    """Render the case list table.

    Accepts ``?status=open`` or ``?status=closed`` query parameter for filtering.
    """
    status_filter = request.args.get("status")
    all_cases = db.get_all_cases(status_filter=status_filter)
    cases_list = []
    for case in all_cases:
        case_dict = dict(case)
        case_dict["memory_indicator"] = _pattern_indicator(case_dict["case_id"])
        cases_list.append(case_dict)
    return render_template("cases.html", cases=cases_list, status_filter=status_filter)


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
    fields = db.get_fields_for_case(case_id)
    followup = db.get_followup_for_case(case_id)
    memory_context = memory.get_memory_context_for_case(case_id)

    return render_template(
        "case_detail.html",
        case=dict(case),
        events=[dict(e) for e in events],
        messages=[dict(m) for m in messages],
        fields=[dict(f) for f in fields],
        followup=dict(followup) if followup else None,
        memory_context=memory_context,
        memory_summary=memory_context["summary"],
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
    open_reviews = db.get_open_manual_reviews()
    return render_template("reviews.html", reviews=[dict(r) for r in open_reviews])


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
    return render_template("events.html", events=[dict(e) for e in recent_events])


@app.route("/patterns")
def patterns():
    """Render active memory/pattern flags across all cases."""
    active_patterns = [dict(row) for row in db.get_active_pattern_flags()]
    return render_template("patterns.html", patterns=active_patterns)


def create_app():
    """Return the configured Flask app. Call this from agent.py."""
    return app
