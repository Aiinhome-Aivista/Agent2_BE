# """SMTP email service.

# Sends incident escalation emails to on-call engineers when the system can't
# auto-resolve a high-priority (P1/P2) incident.

# Configuration (env / settings):
#     SMTP_HOST           e.g. smtp.gmail.com
#     SMTP_PORT           587 (STARTTLS) or 465 (SSL)
#     SMTP_USERNAME       full email address used for auth
#     SMTP_PASSWORD       app password (for Gmail this is an app-specific password)
#     SMTP_FROM_ADDRESS   from header; falls back to SMTP_USERNAME
#     SMTP_FROM_NAME      display name in From header
#     SMTP_USE_TLS        true → STARTTLS (port 587), false → implicit SSL (465)

# If SMTP_HOST is not set, `send_email()` records the attempt as `skipped`
# in the email_logs table and returns False — the system stays functional
# without email being configured.
# """
# from __future__ import annotations

# import smtplib
# import ssl
# import uuid
# from datetime import datetime
# from email.message import EmailMessage
# from email.utils import formataddr
# from typing import Any, Dict, List, Optional

# from app.core.config import settings
# from app.core.logger import logger
# from app.db import get_db


# # ===========================================================================
# # Public API
# # ===========================================================================
# def send_email(
#     *,
#     to: str,
#     subject: str,
#     body_text: str,
#     body_html: Optional[str] = None,
#     cc: Optional[List[str]] = None,
#     template: Optional[str] = None,
#     related_id: Optional[str] = None,
#     related_type: Optional[str] = None,
# ) -> bool:
#     """Send an email. Returns True on success, False on skip or failure.

#     Always logs to `email_logs` so operators can audit deliveries from the UI.
#     """
#     log_id = _create_log_entry(
#         to=to, cc=cc or [], subject=subject, body_preview=body_text[:1000],
#         template=template, related_id=related_id, related_type=related_type,
#     )

#     if not _is_configured():
#         logger.info(f"[email] SMTP not configured — skipping send to {to}")
#         _update_log(log_id, status="skipped", error="SMTP_HOST is not configured")
#         return False

#     try:
#         msg = _build_message(
#             to=to, cc=cc or [], subject=subject,
#             body_text=body_text, body_html=body_html,
#         )
#         smtp_response = _send_via_smtp(msg)
#         _update_log(log_id, status="sent", smtp_response=smtp_response, sent_at=datetime.now())
#         logger.info(f"[email] sent to {to}: {subject[:60]}")
#         return True
#     except Exception as e:  # noqa: BLE001
#         logger.exception(f"[email] failed to send to {to}: {e}")
#         _update_log(log_id, status="failed", error=str(e)[:500])
#         return False


# def is_configured() -> bool:
#     """Public helper — agents check before composing email."""
#     return _is_configured()


# # ===========================================================================
# # High-priority escalation template
# # ===========================================================================
# def send_high_priority_escalation(
#     *,
#     incident: Dict[str, Any],
#     engineer_email: str,
#     engineer_name: Optional[str],
#     llm_summary: Optional[str] = None,
#     runbook_attempted: Optional[str] = None,
#     failure_output: Optional[str] = None,
#     incident_url: Optional[str] = None,
#     extra_message: Optional[str] = None,
# ) -> bool:
#     """Compose and send a P1/P2 escalation email."""

#     priority = incident.get("priority", "P3")

#     subject = f"[{priority}] {incident.get('subject', '(no subject)')[:120]}"

#     greeting = f"Hi {engineer_name}," if engineer_name else "Hi,"

#     url = incident_url or (
#         f"{settings.frontend_url.rstrip('/')}/incidents/{incident.get('id')}"
#     )

#     text_lines: List[str] = []

#     if extra_message:
#         text_lines += [
#             "=" * 70,
#             extra_message,
#             "=" * 70,
#             "",
#         ]

#     text_lines += [
#         greeting,
#         "",
#         f"A {priority} incident requires human review. The agent has attempted automated remediation but cannot resolve it safely.",
#         "",
#         "─" * 56,
#         f"Incident:    {incident.get('id')}",
#         f"Subject:     {incident.get('subject', '')}",
#         f"Caller:      {incident.get('caller', 'unknown')} <{incident.get('caller_email', '')}>",
#         f"Category:    {incident.get('category', 'unknown')}",
#         f"Priority:    {priority} (severity: {incident.get('severity', '')})",
#         f"Status:      {incident.get('status', '')}",
#         f"Created:     {incident.get('created_at', '')}",
#         "─" * 56,
#         "",
#         "DESCRIPTION",
#         incident.get("description", "(no description)"),
#         "",
#     ]

#     if runbook_attempted:
#         text_lines += [
#             "AUTOMATED ATTEMPT",
#             f"Runbook tried: {runbook_attempted}",
#         ]

#         if failure_output:
#             text_lines += [
#                 "Failure output:",
#                 failure_output,
#             ]

#         text_lines.append("")

#     if llm_summary:
#         text_lines += [
#             "AI ANALYSIS",
#             llm_summary,
#             "",
#         ]

#     text_lines += [
#         "NEXT STEPS",
#         f"Open the incident: {url}",
#         "Please investigate and resolve the issue.",
#         "",
#         "— Intelligent Incident Agent",
#     ]

#     body_text = "\n".join(text_lines)

#     return send_email(
#         to=engineer_email,
#         subject=subject,
#         body_text=body_text,
#         body_html=None,
#         template="high_priority_escalation",
#         related_id=incident.get("id"),
#         related_type="incident",
#     )

# # ===========================================================================
# # Internals
# # ===========================================================================
# def _is_configured() -> bool:
#     return bool((settings.smtp_host or "").strip())


# def _from_address() -> str:
#     addr = (settings.smtp_from_address or settings.smtp_username or "").strip()
#     name = (settings.smtp_from_name or "Incident Agent").strip()
#     if not addr:
#         return ""
#     return formataddr((name, addr))


# def _build_message(
#     *,
#     to: str,
#     cc: List[str],
#     subject: str,
#     body_text: str,
#     body_html: Optional[str],
# ) -> EmailMessage:
#     msg = EmailMessage()
#     msg["Subject"] = subject
#     msg["From"] = _from_address()
#     msg["To"] = to
#     if cc:
#         msg["Cc"] = ", ".join(cc)
#     msg.set_content(body_text, charset="utf-8")
#     if body_html:
#         msg.add_alternative(body_html, subtype="html")
#     return msg


# def _send_via_smtp(msg: EmailMessage) -> str:
#     """Connect to the configured SMTP server, send, and return the server reply."""
#     host = settings.smtp_host
#     port = int(settings.smtp_port or 587)
#     username = settings.smtp_username
#     password = settings.smtp_password
#     use_tls = settings.smtp_use_tls if hasattr(settings, "smtp_use_tls") else True

#     if port == 465 or (not use_tls and port == 465):
#         # Implicit SSL
#         ctx = ssl.create_default_context()
#         with smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) as server:
#             if username and password:
#                 server.login(username, password)
#             refused = server.send_message(msg)
#         return f"OK; refused={refused}"

#     # STARTTLS (default 587)
#     with smtplib.SMTP(host, port, timeout=30) as server:
#         server.ehlo()
#         if use_tls:
#             ctx = ssl.create_default_context()
#             server.starttls(context=ctx)
#             server.ehlo()
#         if username and password:
#             server.login(username, password)
#         refused = server.send_message(msg)
#     return f"OK; refused={refused}"


# def _build_html_escalation(
#     *,
#     incident: Dict[str, Any],
#     engineer_name: Optional[str],
#     llm_summary: Optional[str],
#     runbook_attempted: Optional[str],
#     failure_output: Optional[str],
#     url: str,
# ) -> str:
#     """Minimal HTML escalation body — single-file, no external CSS, mobile-safe."""
#     priority = incident.get("priority", "P3")
#     priority_color = {"P1": "#dc2626", "P2": "#ea580c", "P3": "#0891b2", "P4": "#65a30d"}.get(
#         priority, "#0891b2"
#     )

#     def esc(s: Any) -> str:
#         return (
#             str(s or "")
#             .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
#             .replace('"', "&quot;")
#         )

#     runbook_block = (
#         f"<p><strong>Automated attempt:</strong> {esc(runbook_attempted)}</p>"
#         + (f"<pre style='white-space:pre-wrap;background:#f3f4f6;padding:8px;border-radius:4px;font-size:12px'>"
#            f"{esc(failure_output)}</pre>" if failure_output else "")
#         if runbook_attempted else ""
#     )
#     ai_block = (
#         f"<div style='margin:16px 0;padding:12px;background:#fefce8;border-left:3px solid #ca8a04;border-radius:4px'>"
#         f"<strong>AI analysis</strong><br/>{esc(llm_summary)}</div>"
#         if llm_summary else ""
#     )

#     return f"""<!doctype html>
# <html><body style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#111;line-height:1.5;max-width:640px;margin:0 auto;padding:24px">
#   <p>Hi {esc(engineer_name) if engineer_name else 'team'},</p>
#   <p>A <strong style="color:{priority_color}">{esc(priority)}</strong> incident requires human review.
#      The agent attempted automated remediation but cannot resolve it safely.</p>

#   <table style="border-collapse:collapse;width:100%;margin:16px 0;font-size:14px">
#     <tr><td style="padding:4px 8px;color:#6b7280">Incident</td>
#         <td style="padding:4px 8px"><code>{esc(incident.get('id'))}</code></td></tr>
#     <tr><td style="padding:4px 8px;color:#6b7280">Subject</td>
#         <td style="padding:4px 8px"><strong>{esc(incident.get('subject', ''))}</strong></td></tr>
#     <tr><td style="padding:4px 8px;color:#6b7280">Caller</td>
#         <td style="padding:4px 8px">{esc(incident.get('caller', ''))} &lt;{esc(incident.get('caller_email', ''))}&gt;</td></tr>
#     <tr><td style="padding:4px 8px;color:#6b7280">Category</td>
#         <td style="padding:4px 8px">{esc(incident.get('category', ''))}</td></tr>
#     <tr><td style="padding:4px 8px;color:#6b7280">Priority</td>
#         <td style="padding:4px 8px"><span style="color:{priority_color};font-weight:600">{esc(priority)}</span></td></tr>
#   </table>

#   <p style="margin-top:12px;color:#374151"><strong>Description:</strong><br/>
#     {esc(incident.get('description', '(no description)'))}</p>

#   {runbook_block}
#   {ai_block}

#   <p style="margin-top:24px">
#     <a href="{esc(url)}"
#        style="display:inline-block;background:#2563eb;color:#fff;padding:10px 20px;
#               border-radius:6px;text-decoration:none;font-weight:600">
#       Open incident →
#     </a>
#   </p>

#   <p style="margin-top:24px;color:#9ca3af;font-size:12px">
#     When you resolve this incident, the system will record your steps and
#     add them to the knowledge graph so similar issues can be auto-resolved
#     next time. — Intelligent Incident Agent
#   </p>
# </body></html>"""


# # ===========================================================================
# # Email log persistence
# # ===========================================================================
# def _new_id() -> str:
#     return f"EML-{uuid.uuid4().hex[:12]}"


# def _create_log_entry(
#     *,
#     to: str,
#     cc: List[str],
#     subject: str,
#     body_preview: str,
#     template: Optional[str],
#     related_id: Optional[str],
#     related_type: Optional[str],
# ) -> str:
#     import json as _json
#     log_id = _new_id()
#     try:
#         with get_db() as conn:
#             with conn.cursor() as cur:
#                 cur.execute(
#                     """
#                     INSERT INTO email_logs
#                       (id, to_address, cc_addresses, subject, body_preview,
#                        template, related_id, related_type, status)
#                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
#                     """,
#                     (log_id, to, _json.dumps(cc), subject, body_preview,
#                      template, related_id, related_type),
#                 )
#             conn.commit()
#     except Exception as e:  # noqa: BLE001
#         logger.warning(f"[email] could not write to email_logs: {e}")
#     return log_id


# def _update_log(
#     log_id: str,
#     *,
#     status: str,
#     smtp_response: Optional[str] = None,
#     error: Optional[str] = None,
#     sent_at: Optional[datetime] = None,
# ) -> None:
#     try:
#         with get_db() as conn:
#             with conn.cursor() as cur:
#                 cur.execute(
#                     """
#                     UPDATE email_logs
#                     SET status = %s, smtp_response = %s, error = %s, sent_at = %s
#                     WHERE id = %s
#                     """,
#                     (status, smtp_response, error, sent_at, log_id),
#                 )
#             conn.commit()
#     except Exception as e:  # noqa: BLE001
#         logger.warning(f"[email] could not update email_log: {e}")


# def list_recent_emails(limit: int = 50) -> List[Dict[str, Any]]:
#     """Used by the admin UI to show recent email activity."""
#     with get_db() as conn:
#         with conn.cursor(dictionary=True) as cur:
#             cur.execute(
#                 """
#                 SELECT id, to_address, subject, template, related_id,
#                        status, error, retry_count, sent_at, created_at
#                 FROM email_logs
#                 ORDER BY created_at DESC
#                 LIMIT %s
#                 """,
#                 (limit,),
#             )
#             return cur.fetchall()

"""SMTP email service.

Sends incident escalation emails to on-call engineers when the system can't
auto-resolve a high-priority (P1/P2) incident.
"""
from __future__ import annotations

import smtplib
import ssl
import uuid
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logger import logger
from app.db import get_db


# ===========================================================================
# Public API
# ===========================================================================
def send_email(
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    cc: Optional[List[str]] = None,
    template: Optional[str] = None,
    related_id: Optional[str] = None,
    related_type: Optional[str] = None,
    incident_id: Optional[str] = None,
) -> bool:
    """Send an email. Returns True on success, False on skip or failure."""

    log_id = _create_log_entry(
        to=to,
        cc=cc or [],
        subject=subject,
        body_preview=body_text[:1000],
        template=template,
        related_id=related_id,
        related_type=related_type,
        incident_id=incident_id,
    )

    if not _is_configured():
        logger.info(f"[email] SMTP not configured — skipping send to {to}")
        _update_log(log_id, status="skipped", error="SMTP_HOST is not configured")
        return False

    try:
        msg = _build_message(
            to=to,
            cc=cc or [],
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )

        smtp_response = _send_via_smtp(msg)

        _update_log(
            log_id,
            status="sent",
            smtp_response=smtp_response,
            sent_at=datetime.now(),
        )

        logger.info(f"[email] sent to {to}: {subject[:60]}")
        return True

    except Exception as e:  # noqa: BLE001
        logger.exception(f"[email] failed to send to {to}: {e}")
        _update_log(log_id, status="failed", error=str(e)[:500])
        return False


def is_configured() -> bool:
    return _is_configured()


# ===========================================================================
# High-priority escalation template
# ===========================================================================
def send_high_priority_escalation(
    *,
    incident: Dict[str, Any],
    engineer_email: str,
    engineer_name: Optional[str],
    llm_summary: Optional[str] = None,
    runbook_attempted: Optional[str] = None,
    failure_output: Optional[str] = None,
    incident_url: Optional[str] = None,
    extra_message: Optional[str] = None,
) -> bool:
    """Compose and send a P1/P2 escalation email."""

    priority = incident.get("priority", "P3")

    subject = f"[{priority}] {incident.get('subject', '(no subject)')[:120]}"

    greeting = f"Hi {engineer_name}," if engineer_name else "Hi,"

    url = incident_url or (
        f"{settings.frontend_url.rstrip('/')}/incidents/{incident.get('id')}"
    )

    text_lines: List[str] = []

    if extra_message:
        text_lines += [
            "=" * 70,
            extra_message,
            "=" * 70,
            "",
        ]

    text_lines += [
        greeting,
        "",
        f"A {priority} incident requires human review. The agent has attempted automated remediation but cannot resolve it safely.",
        "",
        "─" * 56,
        f"Incident:    {incident.get('id')}",
        f"Subject:     {incident.get('subject', '')}",
        f"Caller:      {incident.get('caller', 'unknown')} <{incident.get('caller_email', '')}>",
        f"Category:    {incident.get('category', 'unknown')}",
        f"Priority:    {priority} (severity: {incident.get('severity', '')})",
        f"Status:      {incident.get('status', '')}",
        f"Created:     {incident.get('created_at', '')}",
        "─" * 56,
        "",
        "DESCRIPTION",
        incident.get("description", "(no description)"),
        "",
    ]

    if runbook_attempted:
        text_lines += [
            "AUTOMATED ATTEMPT",
            f"Runbook tried: {runbook_attempted}",
        ]

        if failure_output:
            text_lines += [
                "Failure output:",
                failure_output,
            ]

        text_lines.append("")

    if llm_summary:
        text_lines += [
            "AI ANALYSIS",
            llm_summary,
            "",
        ]

    text_lines += [
        "NEXT STEPS",
        f"Open the incident: {url}",
        "Please investigate and resolve the issue.",
        "",
        "— Intelligent Incident Agent",
    ]

    body_text = "\n".join(text_lines)

    return send_email(
        to=engineer_email,
        subject=subject,
        body_text=body_text,
        body_html=None,
        template="high_priority_escalation",
        related_id=incident.get("id"),
        related_type="incident",
        incident_id=incident.get("id"),
    )


# ===========================================================================
# Internals
# ===========================================================================
def _is_configured() -> bool:
    return bool((settings.smtp_host or "").strip())


def _from_address() -> str:
    addr = (settings.smtp_from_address or settings.smtp_username or "").strip()
    name = (settings.smtp_from_name or "Incident Agent").strip()

    if not addr:
        return ""

    return formataddr((name, addr))


def _build_message(
    *,
    to: str,
    cc: List[str],
    subject: str,
    body_text: str,
    body_html: Optional[str],
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _from_address()
    msg["To"] = to

    if cc:
        msg["Cc"] = ", ".join(cc)

    msg.set_content(body_text, charset="utf-8")

    if body_html:
        msg.add_alternative(body_html, subtype="html")

    return msg


def _send_via_smtp(msg: EmailMessage) -> str:
    host = settings.smtp_host
    port = int(settings.smtp_port or 587)
    username = settings.smtp_username
    password = settings.smtp_password
    use_tls = settings.smtp_use_tls if hasattr(settings, "smtp_use_tls") else True

    if port == 465 or (not use_tls and port == 465):
        ctx = ssl.create_default_context()

        with smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) as server:
            if username and password:
                server.login(username, password)

            refused = server.send_message(msg)

        return f"OK; refused={refused}"

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()

        if use_tls:
            ctx = ssl.create_default_context()
            server.starttls(context=ctx)
            server.ehlo()

        if username and password:
            server.login(username, password)

        refused = server.send_message(msg)

    return f"OK; refused={refused}"


# ===========================================================================
# Email log persistence
# ===========================================================================
def _new_id() -> str:
    return f"EML-{uuid.uuid4().hex[:12]}"


def _create_log_entry(
    *,
    to: str,
    cc: List[str],
    subject: str,
    body_preview: str,
    template: Optional[str],
    related_id: Optional[str],
    related_type: Optional[str],
    incident_id: Optional[str] = None,
) -> str:
    import json as _json

    log_id = _new_id()

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO email_logs
                      (
                        id,
                        incident_id,
                        to_address,
                        cc_addresses,
                        subject,
                        body_preview,
                        template,
                        related_id,
                        related_type,
                        status
                      )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        'pending'
                    )
                    """,
                    (
                        log_id,
                        incident_id,
                        to,
                        _json.dumps(cc),
                        subject,
                        body_preview,
                        template,
                        related_id,
                        related_type,
                    ),
                )

            conn.commit()

    except Exception as e:  # noqa: BLE001
        logger.warning(f"[email] could not write to email_logs: {e}")

    return log_id


def _update_log(
    log_id: str,
    *,
    status: str,
    smtp_response: Optional[str] = None,
    error: Optional[str] = None,
    sent_at: Optional[datetime] = None,
) -> None:
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE email_logs
                    SET status = %s,
                        smtp_response = %s,
                        error = %s,
                        sent_at = %s
                    WHERE id = %s
                    """,
                    (status, smtp_response, error, sent_at, log_id),
                )

            conn.commit()

    except Exception as e:  # noqa: BLE001
        logger.warning(f"[email] could not update email_log: {e}")


def list_recent_emails(limit: int = 50) -> List[Dict[str, Any]]:
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                """
                SELECT id,
                       incident_id,
                       to_address,
                       subject,
                       template,
                       related_id,
                       status,
                       error,
                       retry_count,
                       sent_at,
                       created_at
                FROM email_logs
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )

            return cur.fetchall()


def list_incident_emails(incident_id: str) -> List[Dict[str, Any]]:
    with get_db() as conn:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                """
                SELECT *
                FROM email_logs
                WHERE incident_id = %s
                ORDER BY created_at ASC
                """,
                (incident_id,),
            )

            return cur.fetchall()