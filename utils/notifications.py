# utils/notifications.py
import os
import json
from datetime import datetime

# Toggle to "true" later when Twilio is fully approved
TWILIO_ENABLED = os.getenv("TWILIO_ENABLED", "false").lower() == "true"

# Optional: store debug logs to disk
BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # project root
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_FILE = os.path.join(DATA_DIR, "sms_log.jsonl")


def _now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_data_dir():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        pass


def log_sms(client_id: str, to: str, body: str, meta: dict | None = None):
    """Write SMS attempt to a local log (works even without Twilio)."""
    _ensure_data_dir()
    rec = {
        "ts": _now_iso(),
        "client_id": client_id,
        "to": to,
        "body": body,
        "meta": meta or {},
        "twilio_enabled": TWILIO_ENABLED,
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        # Worst case: just print
        print("[SMS LOG FAIL]", rec)


def send_sms(client_id: str, to: str, body: str, meta: dict | None = None) -> bool:
    """
    DEV SAFE:
    - If TWILIO_ENABLED=false -> logs + prints, returns True.
    - When TWILIO_ENABLED=true -> we'll replace with real Twilio send.
    """
    # normalize phone a bit (very light)
    to = (to or "").strip()
    body = (body or "").strip()

    if not to or not body:
        return False

    if not TWILIO_ENABLED:
        print(f"[SMS DISABLED] ({client_id}) to={to} body={body}")
        log_sms(client_id, to, body, meta=meta)
        return True

    # -------- REAL TWILIO SEND (we'll enable tomorrow) --------
    # This code won't run until TWILIO_ENABLED=true, so it's safe to keep here.
    from twilio.rest import Client as TwilioClient

    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_FROM_NUMBER", "").strip()

    if not account_sid or not auth_token or not from_number:
        # Still log so we can debug config
        log_sms(client_id, to, body, meta={"error": "missing twilio env vars", **(meta or {})})
        return False

    try:
        client = TwilioClient(account_sid, auth_token)
        msg = client.messages.create(
            from_=from_number,
            to=to,
            body=body,
        )
        log_sms(client_id, to, body, meta={"sid": getattr(msg, "sid", None), **(meta or {})})
        return True
    except Exception as e:
        log_sms(client_id, to, body, meta={"error": str(e), **(meta or {})})
        return False


def sms_for_ticket_event(event: str, t: dict, tech_name: str | None = None) -> str:
    """
    Simple templating for common events.
    Keep it short + transactional (better deliverability).
    """
    service = (t.get("service") or "service").title()
    addr = (t.get("address") or "").strip()
    when = (t.get("availability") or t.get("schedule", {}).get("start") or "").strip()
    ticket_id = t.get("id") or t.get("ticket_id") or ""

    if event == "urgent":
        return f"Turbo Dispatch: URGENT {service}. If danger/smoke, call 911. We’re dispatching now. Ticket {ticket_id}"
    if event == "tech_assigned":
        who = tech_name or (t.get("assigned_to") or "a technician")
        return f"Turbo Dispatch: {who} assigned for {service}. {addr} {(' | ' + when) if when else ''}"
    if event == "en_route":
        who = tech_name or (t.get("assigned_to") or "Your tech")
        return f"Turbo Dispatch: {who} is en route. {addr}"
    if event == "onsite":
        who = tech_name or (t.get("assigned_to") or "Your tech")
        return f"Turbo Dispatch: {who} is on site. {addr}"
    if event == "completed":
        return f"Turbo Dispatch: Job completed for {service}. Reply STOP to opt out."
    # default
    return f"Turbo Dispatch update for {service}. Ticket {ticket_id}"
