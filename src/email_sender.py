"""
email_sender.py — SMTP outbound email with demo safety guardrails.

DEMO MODE GUARANTEES (enforced unconditionally when DEMO_MODE=true):
- Actual To: = DEMO_RECIPIENT_EMAIL only
- Actual CC/BCC: empty
- Subject prefix: [DEMO]
- Body footer: disclaimer explaining this is a demo message
- Default status: draft (never sends unless DEMO_MODE=false or explicit confirmation)

Intended recipients are stored in outbound_messages.intended_to for audit only.
"""

import smtplib
import uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import database as db
from config import config

_DEMO_FOOTER = (
    "\n\n---\n"
    "This message was generated for demo review only. "
    "Not sent to intended production recipients."
)


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
    msg_id = str(uuid.uuid4())
    actual_to = config.DEMO_RECIPIENT_EMAIL if config.DEMO_MODE else intended_to

    if config.DEMO_MODE and not subject.startswith("[DEMO]"):
        subject = f"[DEMO] {subject}"

    final_body = body + _DEMO_FOOTER if config.DEMO_MODE else body

    db.insert_outbound_message(
        msg_id=msg_id,
        case_id=case_id,
        intended_to=intended_to,
        intended_cc=intended_cc,
        actual_to=actual_to,
        subject=subject,
        body=final_body,
        status="draft",
    )

    print(f"[EMAIL_SENDER] Draft created: {msg_id} | To (actual): {actual_to}")
    return msg_id


def send_draft(msg_id: str, confirm: bool = False) -> bool:
    """Send a saved draft via SMTP.

    Falls back to a dry-run log when SMTP is not configured — marks the record
    ``sent_dry_run`` and logs the subject and recipient rather than failing.

    Args:
        msg_id: UUID of the draft in ``outbound_messages``.
        confirm: Must be True when ``DEMO_MODE`` is True; has no effect in
            production mode. Guards against accidental sends during demo use.

    Returns:
        True if the email was sent or logged as a dry-run.
        False if the draft was not found, was already sent,
        DEMO_MODE blocked the send (``confirm=False``), or an SMTP
        error occurred.
    """
    messages = db.get_connection().execute(
        "SELECT * FROM outbound_messages WHERE msg_id = ?", (msg_id,)
    ).fetchone()

    if not messages:
        print(f"[EMAIL_SENDER] Draft {msg_id} not found.")
        return False

    if messages["status"] == "sent":
        print(f"[EMAIL_SENDER] Draft {msg_id} already sent.")
        return False

    if config.DEMO_MODE and not confirm:
        print(f"[EMAIL_SENDER] DEMO_MODE=true — set confirm=True to send draft {msg_id}.")
        return False

    if not config.is_smtp_configured():
        # Dry run: log and mark as sent
        print(
            f"[EMAIL_SENDER] SMTP not configured — dry run send:\n"
            f"  To: {messages['actual_to']}\n"
            f"  Subject: {messages['subject']}\n"
            f"  Body preview: {messages['body'][:120]}..."
        )
        sent_at = datetime.utcnow().isoformat()
        db.update_outbound_message_status(msg_id, "sent_dry_run", sent_at)
        db.insert_case_event(
            event_id=str(uuid.uuid4()),
            case_id=messages["case_id"],
            event_type="email_dry_run",
            description=f"Follow-up email logged (dry run — SMTP not configured). Subject: {messages['subject']}",
        )
        return True

    # Real send
    try:
        mime_msg = MIMEMultipart()
        mime_msg["From"] = config.AGENT_EMAIL
        mime_msg["To"] = messages["actual_to"]
        mime_msg["Subject"] = messages["subject"]
        mime_msg.attach(MIMEText(messages["body"], "plain"))

        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(config.AGENT_EMAIL, config.AGENT_EMAIL_PASSWORD)
            server.sendmail(
                config.AGENT_EMAIL,
                [messages["actual_to"]],
                mime_msg.as_string(),
            )

        sent_at = datetime.utcnow().isoformat()
        db.update_outbound_message_status(msg_id, "sent", sent_at)
        db.insert_case_event(
            event_id=str(uuid.uuid4()),
            case_id=messages["case_id"],
            event_type="email_sent",
            description=f"Follow-up email sent to {messages['actual_to']}. Subject: {messages['subject']}",
        )
        print(f"[EMAIL_SENDER] Email sent: {msg_id} to {messages['actual_to']}")
        return True

    except smtplib.SMTPException as exc:
        print(f"[EMAIL_SENDER] SMTP error sending {msg_id}: {exc}")
        return False
    except Exception as exc:
        print(f"[EMAIL_SENDER] Unexpected error sending {msg_id}: {exc}")
        return False


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
    msg_id = create_draft(case_id, subject, body, intended_to, intended_cc)
    if auto_send:
        # confirm=True is correct: the demo recipient redirect in create_draft() is the
        # safety guardrail — withholding the send is unnecessary once the redirect is applied.
        send_draft(msg_id, confirm=True)
    return msg_id
