# utils/notify.py
# Customer notifications: SMS (Twilio) + Email (SMTP)
# Safe-by-default: if env is missing, it no-ops and returns (False, "reason")

import os
import re
import smtplib
from email.message import EmailMessage
from typing import Tuple


def _bool_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "y")


def _twilio_enabled() -> bool:
    return _bool_env("TWILIO_ENABLED", "0")


def _email_enabled() -> bool:
    return _bool_env("EMAIL_ENABLED", "0")


def _to_e164(phone: str, default_country: str = "US") -> str:
    """
    Convert common phone inputs to E.164 for Twilio.
    - "316-730-6790" -> "+13167306790"
    - "(316) 730-6790" -> "+13167306790"
    - "+13167306790" -> "+13167306790"
    - "1 316 730 6790" -> "+13167306790"

    NOTE: This assumes US/CA numbers when 10 digits are provided.
    """
    p = (phone or "").strip()
    if not p:
        return ""

    # keep leading + if present, strip everything else non-digit
    if p.startswith("+"):
        digits = "+" + re.sub(r"\D+", "", p[1:])
        return digits if len(digits) > 1 else ""

    digits = re.sub(r"\D+", "", p)

    # US/CA default: 10 digits -> +1XXXXXXXXXX
    if default_country.upper() in ("US", "CA"):
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits

    # Fallback: if someone stored already as countrycode+number without '+'
    if len(digits) >= 11:
        return "+" + digits

    # Can't confidently format
    return ""


def send_sms(to_phone: str, body: str) -> Tuple[bool, str]:
    """
    Env:
      TWILIO_ENABLED=1
      TWILIO_ACCOUNT_SID=...
      TWILIO_AUTH_TOKEN=...
      TWILIO_FROM=+15551234567
    """
    to_phone = (to_phone or "").strip()
    body = (body or "").strip()
    if not to_phone or not body:
        return False, "missing to_phone/body"

    if not _twilio_enabled():
        return False, "twilio disabled"

    sid = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    tw_from = (os.getenv("TWILIO_FROM") or "").strip()

    if not (sid and token and tw_from):
        return False, "missing twilio env"

    to_e164 = _to_e164(to_phone, default_country=os.getenv("DEFAULT_COUNTRY", "US"))
    if not to_e164:
        return False, f"invalid phone for sms: {to_phone}"

    try:
        # Lazy import so app runs even without twilio installed
        from twilio.rest import Client  # type: ignore
        client = Client(sid, token)
        msg = client.messages.create(
            body=body,
            from_=tw_from,
            to=to_e164,
        )
        return True, f"sent ({msg.sid})"
    except Exception as e:
        return False, f"twilio error: {e}"


def send_email(to_email: str, subject: str, body: str) -> Tuple[bool, str]:
    """
    Env:
      EMAIL_ENABLED=1
      SMTP_HOST=smtp.gmail.com
      SMTP_PORT=587
      SMTP_USERNAME=...
      SMTP_PASSWORD=...
      SMTP_FROM=Dispatch <dispatch@yourdomain.com>   (or just dispatch@...)
      SMTP_TLS=1
    """
    to_email = (to_email or "").strip()
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not to_email or not subject or not body:
        return False, "missing to_email/subject/body"

    if not _email_enabled():
        return False, "email disabled"

    host = (os.getenv("SMTP_HOST") or "").strip()
    port = int((os.getenv("SMTP_PORT") or "587").strip() or "587")
    user = (os.getenv("SMTP_USERNAME") or "").strip()
    pw = (os.getenv("SMTP_PASSWORD") or "").strip()
    frm = (os.getenv("SMTP_FROM") or user or "").strip()
    tls = _bool_env("SMTP_TLS", "1")

    if not (host and port and user and pw and frm):
        return False, "missing smtp env"

    try:
        msg = EmailMessage()
        msg["From"] = frm
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP(host, port, timeout=20) as s:
            if tls:
                s.starttls()
            s.login(user, pw)
            s.send_message(msg)

        return True, "sent"
    except Exception as e:
        return False, f"smtp error: {e}"


def build_message(event_key: str, ticket: dict) -> Tuple[str, str]:
    """
    Returns (subject, body) for email. SMS uses the body (short).
    """
    tid = (ticket.get("id") or "").strip()
    svc = (ticket.get("service") or "service").title()
    status = (ticket.get("status") or "").replace("_", " ").title()
    tech = (ticket.get("assigned_tech_name") or "").strip()
    when = (ticket.get("scheduled_start") or "").strip()
    addr = (ticket.get("address") or "").strip()

    if event_key == "intake_confirmed":
        subject = f"Request received — Ticket {tid}"
        body = (
            f"✅ We received your request.\n"
            f"Ticket: {tid}\n"
            f"Service: {svc}\n"
            f"Location: {addr or '—'}\n"
            f"We’ll follow up shortly."
        )
        return subject, body

    if event_key == "assigned":
        subject = f"Technician assigned — Ticket {tid}"
        body = (
            f"👷 Technician assigned.\n"
            f"Ticket: {tid}\n"
            f"Service: {svc}\n"
            f"Tech: {tech or 'Assigned'}\n"
            f"Status: {status or 'Open'}"
        )
        return subject, body

    if event_key == "scheduled":
        subject = f"Appointment scheduled — Ticket {tid}"
        body = (
            f"🗓️ Appointment scheduled.\n"
            f"Ticket: {tid}\n"
            f"When: {when or 'Scheduled'}\n"
            f"Tech: {tech or '—'}\n"
            f"Address: {addr or '—'}"
        )
        return subject, body

    if event_key == "en_route":
        subject = f"On the way — Ticket {tid}"
        body = (
            f"🚗 Your technician is on the way.\n"
            f"Ticket: {tid}\n"
            f"Tech: {tech or '—'}"
        )
        return subject, body

    if event_key == "onsite":
        subject = f"Arrived — Ticket {tid}"
        body = (
            f"📍 Technician arrived.\n"
            f"Ticket: {tid}\n"
            f"Tech: {tech or '—'}"
        )
        return subject, body

    if event_key == "completed":
        subject = f"Completed — Ticket {tid}"
        body = (
            f"✅ Job completed.\n"
            f"Ticket: {tid}\n"
            f"Service: {svc}\n"
            f"Thanks for choosing us."
        )
        return subject, body

    if event_key == "canceled":
        subject = f"Canceled — Ticket {tid}"
        body = (
            f"❌ Ticket canceled.\n"
            f"Ticket: {tid}\n"
            f"If this is a mistake, reply and we’ll help."
        )
        return subject, body

    # fallback
    subject = f"Update — Ticket {tid}"
    body = f"Update on your ticket {tid}. Status: {status or '—'}"
    return subject, body


def notify_customer(event_key: str, ticket: dict) -> Tuple[bool, str]:
    """
    Uses ticket['phone'] and/or ticket['customer_email'] (optional).
    Sends whatever is available/enabled. Returns success if at least one send worked.
    """
    subject, body = build_message(event_key, ticket)

    sent_any = False
    notes = []

    # SMS
    phone = (ticket.get("phone") or "").strip()
    if phone:
        ok, msg = send_sms(phone, body)
        notes.append(f"sms={ok}:{msg}")
        sent_any = sent_any or ok
    else:
        notes.append("sms=skip:no phone")

    # Email
    email = (ticket.get("customer_email") or "").strip()
    if email:
        ok, msg = send_email(email, subject, body)
        notes.append(f"email={ok}:{msg}")
        sent_any = sent_any or ok
    else:
        notes.append("email=skip:no email")

    return sent_any, "; ".join(notes)
