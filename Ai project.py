# utils/notify.py
# Customer notifications: SMS (Twilio) + Email (SMTP)
# Safe-by-default: if env is missing, it no-ops and returns (False, "reason")

import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Tuple, Optional, Dict, Any


# -----------------------------
# Helpers
# -----------------------------
def _bool_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "y")


def _twilio_enabled() -> bool:
    return _bool_env("TWILIO_ENABLED", "0")


def _email_enabled() -> bool:
    return _bool_env("EMAIL_ENABLED", "0")


def _public_base_url() -> str:
    """
    Used to build tracking links inside notify.py.
    Set this in .env for production-like links:
      PUBLIC_BASE_URL=https://yourdomain.com
    Local dev example:
      PUBLIC_BASE_URL=http://127.0.0.1:5000
    """
    return (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")


def _ticket_track_url(ticket: dict) -> str:
    """
    Prefer a precomputed track_url if app.py put it on ticket.
    Otherwise build from PUBLIC_BASE_URL + track_token if possible.
    """
    u = (ticket.get("track_url") or "").strip()
    if u:
        return u

    tok = (ticket.get("track_token") or "").strip()
    base = _public_base_url()
    if base and tok:
        return f"{base}/track/{tok}"
    return ""


def _to_e164(phone: str, default_country: str = "US") -> str:
    """
    Convert common phone inputs to E.164 for Twilio.
    NOTE: Assumes US/CA when 10 digits are provided.
    """
    p = (phone or "").strip()
    if not p:
        return ""

    if p.startswith("+"):
        digits = "+" + re.sub(r"\D+", "", p[1:])
        return digits if len(digits) > 1 else ""

    digits = re.sub(r"\D+", "", p)

    if default_country.upper() in ("US", "CA"):
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits

    if len(digits) >= 11:
        return "+" + digits

    return ""


def _escape_html(s: str) -> str:
    s = (s or "")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s


def _safe_brand(cfg: Optional[dict]) -> Dict[str, Any]:
    """
    Brand defaults are env-driven, but can be overridden by cfg['brand'] (optional).
    This is SAFE: if cfg missing, uses env defaults.

    Optional env:
      APP_NAME=Turbo Dispatch
      BRAND_PRIMARY=#111827
      BRAND_ACCENT=#2563eb
      BRAND_LOGO_URL=https://...
      BRAND_SUPPORT_EMAIL=support@...
      BRAND_REPLY_TO=support@...
      BRAND_FOOTER=...
    """
    brand = {
        "app_name": (os.getenv("APP_NAME") or "Turbo Dispatch").strip(),
        "primary": (os.getenv("BRAND_PRIMARY") or "#111827").strip(),
        "accent": (os.getenv("BRAND_ACCENT") or "#2563eb").strip(),
        "logo_url": (os.getenv("BRAND_LOGO_URL") or "").strip(),
        "support_email": (os.getenv("BRAND_SUPPORT_EMAIL") or "").strip(),
        "reply_to": (os.getenv("BRAND_REPLY_TO") or "").strip(),
        "footer": (os.getenv("BRAND_FOOTER") or "This is an automated message.").strip(),
    }

    if isinstance(cfg, dict):
        b = cfg.get("brand") or {}
        if isinstance(b, dict):
            brand["app_name"] = (b.get("app_name") or brand["app_name"]).strip()
            brand["primary"] = (b.get("primary") or brand["primary"]).strip()
            brand["accent"] = (b.get("accent") or brand["accent"]).strip()
            brand["logo_url"] = (b.get("logo_url") or brand["logo_url"]).strip()
            brand["support_email"] = (b.get("support_email") or brand["support_email"]).strip()
            brand["reply_to"] = (b.get("reply_to") or brand["reply_to"]).strip()
            brand["footer"] = (b.get("footer") or brand["footer"]).strip()

    return brand


# -----------------------------
# SMS (Twilio)
# -----------------------------
def send_sms(to_phone: str, body: str) -> Tuple[bool, str]:
    """
    Env:
      TWILIO_ENABLED=1
      TWILIO_SID=...
      TWILIO_TOKEN=...
      TWILIO_FROM=+15551234567
    """
    to_phone = (to_phone or "").strip()
    body = (body or "").strip()
    if not to_phone or not body:
        return False, "missing to_phone/body"

    if not _twilio_enabled():
        return False, "twilio disabled"

    sid = (os.getenv("TWILIO_SID") or "").strip()
    token = (os.getenv("TWILIO_TOKEN") or "").strip()
    tw_from = (os.getenv("TWILIO_FROM") or "").strip()

    if not (sid and token and tw_from):
        return False, "missing twilio env"

    to_e164 = _to_e164(to_phone, default_country=os.getenv("DEFAULT_COUNTRY", "US"))
    if not to_e164:
        return False, f"invalid phone for sms: {to_phone}"

    try:
        from twilio.rest import Client  # type: ignore
        client = Client(sid, token)
        msg = client.messages.create(body=body, from_=tw_from, to=to_e164)
        return True, f"sent ({msg.sid})"
    except Exception as e:
        return False, f"twilio error: {e}"


# -----------------------------
# Email (SMTP)
# -----------------------------
def _smtp_conn(host: str, port: int, tls: bool):
    """
    Gmail-friendly SMTP connection helper.
    Uses STARTTLS when tls=True (recommended for port 587).
    """
    timeout = int((os.getenv("SMTP_TIMEOUT") or "20").strip() or "20")
    s = smtplib.SMTP(host, port, timeout=timeout)
    s.ehlo()
    if tls:
        ctx = ssl.create_default_context()
        s.starttls(context=ctx)
        s.ehlo()
    return s


def _build_email_html(subject: str, body_text: str, ticket: dict, brand: dict) -> str:
    """
    Enterprise-ish HTML wrapper.
    - clean header
    - highlights
    - optional tracking button
    - footer
    """
    track = _ticket_track_url(ticket)
    app_name = _escape_html(brand.get("app_name", "Turbo Dispatch"))
    primary = _escape_html(brand.get("primary", "#111827"))
    accent = _escape_html(brand.get("accent", "#2563eb"))
    logo_url = (brand.get("logo_url") or "").strip()
    footer = _escape_html(brand.get("footer") or "This is an automated message.")

    tid = _escape_html((ticket.get("id") or "").strip())
    status = _escape_html((ticket.get("status") or "").replace("_", " ").title())
    service = _escape_html((ticket.get("service") or "service").title())
    tech = _escape_html((ticket.get("assigned_tech_name") or "").strip() or "—")
    addr = _escape_html((ticket.get("address") or "").strip() or "—")

    # Turn plain text into safe HTML
    safe_body = _escape_html(body_text).replace("\n", "<br/>")

    logo_html = ""
    if logo_url:
        logo_html = f"""
          <div style="margin-bottom:12px;">
            <img src="{logo_url}" alt="{app_name}" style="height:34px; width:auto; display:block;" />
          </div>
        """.strip()

    button_html = ""
    if track:
        t = _escape_html(track)
        button_html = f"""
          <div style="margin-top:16px;">
            <a href="{t}"
               style="display:inline-block; background:{accent}; color:white; text-decoration:none;
                      padding:10px 14px; border-radius:10px; font-weight:700; font-size:14px;">
              View Tracking
            </a>
          </div>
        """.strip()

    # Preheader (hidden)
    preheader = _escape_html(subject)

    return f"""
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>{_escape_html(subject)}</title>
  </head>
  <body style="margin:0; padding:0; background:#f3f4f6;">
    <div style="display:none; max-height:0; overflow:hidden; opacity:0; color:transparent;">
      {preheader}
    </div>

    <div style="max-width:640px; margin:0 auto; padding:24px;">
      <div style="background:#ffffff; border:1px solid #e5e7eb; border-radius:16px; overflow:hidden;">
        <div style="padding:18px 18px 12px 18px; border-bottom:1px solid #e5e7eb;">
          {logo_html}
          <div style="font-size:18px; font-weight:800; color:{primary};">
            {app_name}
          </div>
          <div style="margin-top:6px; font-size:13px; color:#6b7280;">
            {_escape_html(subject)}
          </div>
        </div>

        <div style="padding:18px;">
          <div style="font-size:14px; color:#111827; line-height:1.55;">
            {safe_body}
          </div>

          {button_html}

          <div style="margin-top:18px; padding:14px; background:#f9fafb; border:1px solid #e5e7eb; border-radius:12px;">
            <div style="font-weight:800; font-size:13px; color:{primary}; margin-bottom:10px;">
              Ticket Summary
            </div>
            <table style="width:100%; border-collapse:collapse; font-size:13px; color:#111827;">
              <tr>
                <td style="padding:6px 0; color:#6b7280; width:140px;">Ticket</td>
                <td style="padding:6px 0; font-weight:700;">{tid or "—"}</td>
              </tr>
              <tr>
                <td style="padding:6px 0; color:#6b7280;">Status</td>
                <td style="padding:6px 0; font-weight:700;">{status or "—"}</td>
              </tr>
              <tr>
                <td style="padding:6px 0; color:#6b7280;">Service</td>
                <td style="padding:6px 0; font-weight:700;">{service}</td>
              </tr>
              <tr>
                <td style="padding:6px 0; color:#6b7280;">Technician</td>
                <td style="padding:6px 0; font-weight:700;">{tech}</td>
              </tr>
              <tr>
                <td style="padding:6px 0; color:#6b7280;">Address</td>
                <td style="padding:6px 0; font-weight:700;">{addr}</td>
              </tr>
            </table>
          </div>

          <div style="margin-top:14px; font-size:12px; color:#6b7280;">
            {footer}
          </div>
        </div>
      </div>

      <div style="margin-top:12px; text-align:center; font-size:11px; color:#9ca3af;">
        © {app_name}
      </div>
    </div>
  </body>
</html>
""".strip()


def send_email(to_email: str, subject: str, body: str, ticket: dict = None, cfg: dict = None) -> Tuple[bool, str]:
    """
    Env:
      EMAIL_ENABLED=1
      SMTP_HOST=smtp.gmail.com
      SMTP_PORT=587
      SMTP_USERNAME=...
      SMTP_PASSWORD=...   (GMAIL: use an App Password, NOT your normal password)
      SMTP_FROM=Turbo Dispatch <your_email@gmail.com>
      SMTP_TLS=1

    Optional:
      SMTP_DEBUG=1
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
    debug = _bool_env("SMTP_DEBUG", "0")

    if not (host and port and user and pw and frm):
        return False, "missing smtp env"

    try:
        t = ticket or {}
        brand = _safe_brand(cfg)

        msg = EmailMessage()
        msg["From"] = frm
        msg["To"] = to_email
        msg["Subject"] = subject

        # Optional Reply-To for professional replies
        reply_to = (brand.get("reply_to") or "").strip()
        if reply_to:
            msg["Reply-To"] = reply_to

        # Plain text always
        msg.set_content(body)

        # HTML version (branded)
        html = _build_email_html(subject, body, t, brand)
        msg.add_alternative(html, subtype="html")

        with _smtp_conn(host, port, tls) as s:
            if debug:
                s.set_debuglevel(1)
            s.login(user, pw)
            s.send_message(msg)

        return True, "sent"
    except Exception as e:
        return False, f"smtp error: {e}"


# -----------------------------
# Message content (existing behavior preserved)
# -----------------------------
def build_message(event_key: str, ticket: dict) -> Tuple[str, str]:
    """
    Returns (subject, body) for email. SMS uses the body (short-ish).
    Adds tracking link if available.
    """
    tid = (ticket.get("id") or "").strip()
    svc = (ticket.get("service") or "service").title()
    status = (ticket.get("status") or "").replace("_", " ").title()
    tech = (ticket.get("assigned_tech_name") or "").strip()
    when = (ticket.get("scheduled_start") or "").strip()
    addr = (ticket.get("address") or "").strip()

    track = _ticket_track_url(ticket)
    track_line = f"\nTrack: {track}" if track else ""

    if event_key == "intake_confirmed":
        subject = f"Request received — Ticket {tid}"
        body = (
            f"✅ We received your request.\n"
            f"Ticket: {tid}\n"
            f"Service: {svc}\n"
            f"Location: {addr or '—'}\n"
            f"We’ll follow up shortly."
            f"{track_line}"
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
            f"{track_line}"
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
            f"{track_line}"
        )
        return subject, body

    if event_key == "en_route":
        subject = f"On the way — Ticket {tid}"
        body = (
            f"🚗 Your technician is on the way.\n"
            f"Ticket: {tid}\n"
            f"Tech: {tech or '—'}"
            f"{track_line}"
        )
        return subject, body

    if event_key == "eta":
        subject = f"ETA updated — Ticket {tid}"
        mins = ticket.get("eta_minutes", None)
        mins_txt = f"{mins} min" if mins is not None else "updated"
        body = (
            f"⏱️ ETA {mins_txt}.\n"
            f"Ticket: {tid}\n"
            f"Tech: {tech or '—'}"
            f"{track_line}"
        )
        return subject, body

    if event_key == "onsite":
        subject = f"Arrived — Ticket {tid}"
        body = (
            f"📍 Technician arrived.\n"
            f"Ticket: {tid}\n"
            f"Tech: {tech or '—'}"
            f"{track_line}"
        )
        return subject, body

    if event_key == "completed":
        subject = f"Completed — Ticket {tid}"
        body = (
            f"✅ Job completed.\n"
            f"Ticket: {tid}\n"
            f"Service: {svc}\n"
            f"Thanks for choosing us."
            f"{track_line}"
        )
        return subject, body

    if event_key == "canceled":
        subject = f"Canceled — Ticket {tid}"
        body = (
            f"❌ Ticket canceled.\n"
            f"Ticket: {tid}\n"
            f"If this is a mistake, reply and we’ll help."
            f"{track_line}"
        )
        return subject, body

    subject = f"Update — Ticket {tid}"
    body = f"Update on your ticket {tid}. Status: {status or '—'}{track_line}"
    return subject, body


# -----------------------------
# Main notifier (existing behavior preserved)
# -----------------------------
def notify_customer(event_key: str, ticket: dict, cfg: dict = None) -> Tuple[bool, str]:
    """
    Uses ticket['phone'] and/or ticket['customer_email'] (optional).
    Sends whatever is available/enabled. Returns success if at least one send worked.

    cfg: client config dict (optional). If provided, enforces cfg['notify'] settings.

    Optional env:
      SMS_OPT_OUT_LINE=1  (default 1) -> appends "Reply STOP to opt out."

    Optional env (OFF by default):
      NOTIFY_FALLBACK_EMAIL=you@domain.com   -> if ticket has no email, send to this (good for dev)
    """
    # ---------- client-level gating ----------
    if isinstance(cfg, dict):
        ncfg = cfg.get("notify") or {}
        if not ncfg.get("enabled", True):
            return False, "notify=skip:client_disabled"

        # event toggle
        events = (ncfg.get("events") or {})
        if events and (events.get(event_key) is False):
            return False, f"notify=skip:event_off:{event_key}"

        # channels toggle
        ch = (ncfg.get("channels") or {})
        allow_sms = ch.get("sms", True)
        allow_email = ch.get("email", True)
    else:
        allow_sms = True
        allow_email = True

    subject, body = build_message(event_key, ticket)

    sent_any = False
    notes = []

    # ---------- SMS ----------
    if allow_sms:
        phone = (ticket.get("phone") or "").strip()
        if phone:
            sms_body = body
            if _bool_env("SMS_OPT_OUT_LINE", "1"):
                if "reply stop" not in sms_body.lower():
                    sms_body = sms_body.rstrip() + "\n\nReply STOP to opt out."
            ok, msg = send_sms(phone, sms_body)
            notes.append(f"sms={ok}:{msg}")
            sent_any = sent_any or ok
        else:
            notes.append("sms=skip:no phone")
    else:
        notes.append("sms=skip:channel_off")

    # ---------- Email ----------
    if allow_email:
        email = (ticket.get("customer_email") or ticket.get("email") or "").strip()
        fallback = (os.getenv("NOTIFY_FALLBACK_EMAIL") or "").strip()
        to_email = email or fallback

        if to_email and not ticket.get("email_opt_out"):
            ok, msg = send_email(to_email, subject, body, ticket=ticket, cfg=cfg)
            notes.append(f"email={ok}:{msg}:{to_email}")
            sent_any = sent_any or ok
        elif ticket.get("email_opt_out"):
            notes.append("email=skip:opted_out")
        else:
            notes.append("email=skip:no email")
    else:
        notes.append("email=skip:channel_off")

    return sent_any, "; ".join(notes)