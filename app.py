from flask import Flask, render_template, request, jsonify
import os, json, traceback, re
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

import smtplib
from email.message import EmailMessage

# Optional Twilio (safe if missing)
try:
    from twilio.rest import Client as TwilioClient
except Exception:
    TwilioClient = None

app = Flask(__name__)

BASE = os.path.dirname(os.path.abspath(__file__))
CLIENTS_DIR = os.path.join(BASE, "clients")
DATA_DIR = os.path.join(BASE, "data")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CLIENTS_DIR, exist_ok=True)

CHATS_PATH = os.path.join(DATA_DIR, "chats.json")


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


DEFAULT_CLIENT = {
    "client_id": "default",
    "business_name": "Turbo Desk",
    "service_category": "home_services",
    "emergency_keywords": [
        "sparking", "smoke", "fire", "gas", "flood", "overflow", "burst",
        "burning smell", "no power", "carbon monoxide"
    ],
    "lead_questions": [
        "Your name",
        "Service address",
        "Best callback number",
        "Is it actively happening right now?",
        "Any photos/video? (optional)"
    ],
    "alert_email_to": ""
}


def _read_json(path, fallback):
    try:
        if not os.path.exists(path):
            return fallback
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return fallback
        return json.loads(raw)
    except Exception:
        return fallback


def _write_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
    except Exception:
        pass


def list_client_ids():
    ids = []
    for fn in os.listdir(CLIENTS_DIR):
        if fn.endswith(".json"):
            ids.append(fn[:-5])
    ids.sort()
    return ids


def load_client(client_id: str):
    cid = (client_id or "default").strip() or "default"
    specific = os.path.join(CLIENTS_DIR, f"{cid}.json")
    fallback = os.path.join(CLIENTS_DIR, "default.json")

    cfg = _read_json(specific, None)
    if not isinstance(cfg, dict):
        cfg = _read_json(fallback, DEFAULT_CLIENT)

    for k, v in DEFAULT_CLIENT.items():
        cfg.setdefault(k, v)

    cfg["client_id"] = cid
    return cfg


def append_lead(client_id: str, customer_message: str, priority: str):
    chats = _read_json(CHATS_PATH, [])
    if not isinstance(chats, list):
        chats = []
    chats.append({
        "time": now_iso(),
        "client_id": client_id,
        "priority": priority,
        "message": customer_message
    })
    _write_json(CHATS_PATH, chats)


def sanitize_client_id(cid: str) -> str:
    cid = (cid or "").strip().lower()
    cid = re.sub(r"[^a-z0-9_]+", "_", cid)
    cid = re.sub(r"_+", "_", cid).strip("_")
    return cid or "client"


# -------------------
# Email Alerts (Free)
# -------------------
def email_ready():
    enabled = os.getenv("EMAIL_ALERTS_ENABLED", "false").lower() == "true"
    host = os.getenv("SMTP_HOST", "").strip()
    port = os.getenv("SMTP_PORT", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    pw = os.getenv("SMTP_PASS", "").strip()
    to = os.getenv("ALERT_EMAIL_TO", "").strip()
    from_ = os.getenv("ALERT_EMAIL_FROM", "").strip()
    return bool(enabled and host and port and user and pw and to and from_)


def send_urgent_email(client_cfg: dict, customer_message: str):
    try:
        if not email_ready():
            return False, "email_not_configured"

        host = os.getenv("SMTP_HOST").strip()
        port = int(os.getenv("SMTP_PORT").strip())
        user = os.getenv("SMTP_USER").strip()
        pw = os.getenv("SMTP_PASS").strip()
        from_ = os.getenv("ALERT_EMAIL_FROM").strip()

        # per-client override if present
        to = (client_cfg.get("alert_email_to") or "").strip() or os.getenv("ALERT_EMAIL_TO").strip()

        business = client_cfg.get("business_name", "Turbo Desk")
        cid = client_cfg.get("client_id", "default")

        subject = f"URGENT lead ({business}) — {customer_message[:60]}"
        body = (
            f"⚠️ URGENT LEAD\n\n"
            f"Business: {business}\n"
            f"Client ID: {cid}\n"
            f"Time: {now_iso()}\n\n"
            f"Customer message:\n{customer_message}\n"
        )

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_
        msg["To"] = to
        msg.set_content(body)

        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, pw)
            server.send_message(msg)

        return True, "sent"
    except Exception as e:
        print("⚠️ Email alert failed:", str(e))
        return False, f"failed:{e}"


# -------------------
# Twilio (Optional)
# -------------------
def twilio_ready():
    return bool(
        TwilioClient is not None and
        os.getenv("TWILIO_ACCOUNT_SID") and
        os.getenv("TWILIO_AUTH_TOKEN") and
        os.getenv("TWILIO_FROM_NUMBER") and
        os.getenv("ALERT_TO_NUMBER")
    )


def send_urgent_sms(client_cfg: dict, customer_message: str):
    try:
        if not twilio_ready():
            return False, "twilio_not_configured"

        sid = os.getenv("TWILIO_ACCOUNT_SID")
        token = os.getenv("TWILIO_AUTH_TOKEN")
        from_num = os.getenv("TWILIO_FROM_NUMBER")
        to_num = os.getenv("ALERT_TO_NUMBER")

        business = client_cfg.get("business_name", "Turbo Desk")
        body = f"URGENT ({business}): {customer_message[:120]}"

        tw = TwilioClient(sid, token)
        tw.messages.create(body=body, from_=from_num, to=to_num)
        return True, "sent"
    except Exception as e:
        print("⚠️ Twilio SMS failed:", str(e))
        return False, f"failed:{e}"


# -------------------
# Message handling
# -------------------
def is_urgent(client_cfg: dict, message: str) -> bool:
    msg = (message or "").lower()
    return any((k or "").lower() in msg for k in client_cfg.get("emergency_keywords", []))


def build_reply(client_cfg: dict, urgent: bool) -> str:
    business = client_cfg.get("business_name", "Turbo Desk")
    questions = client_cfg.get("lead_questions", DEFAULT_CLIENT["lead_questions"])

    header = f"{business}: ⚠️ This sounds urgent.\n\n" if urgent else f"{business}: Thanks for reaching out.\n\n"
    qlines = "\n".join([f"{i+1}) {q}" for i, q in enumerate(questions)])
    footer = "\n\n(Reply with the info above and we’ll get you scheduled ASAP.)" if urgent else "\n\n(Reply with the info above and we’ll get you booked.)"
    return header + "Please reply with:\n" + qlines + footer


# -------------------
# Routes
# -------------------
@app.route("/")
def index():
    return render_template("index.html", cache_bust=int(datetime.utcnow().timestamp()))


@app.route("/admin")
def admin():
    return render_template("admin.html", cache_bust=int(datetime.utcnow().timestamp()))


@app.route("/onboard", methods=["GET", "POST"])
def onboard():
    if request.method == "GET":
        return render_template("onboard.html", created=False)

    business_name = (request.form.get("business_name") or "").strip()
    client_id_raw = (request.form.get("client_id") or "").strip()
    alert_email_to = (request.form.get("alert_email_to") or "").strip()
    service_category = (request.form.get("service_category") or "general").strip()

    if not business_name or not client_id_raw or not alert_email_to:
        return render_template("onboard.html", created=False, error="Missing required fields.")

    cid = sanitize_client_id(client_id_raw)
    path = os.path.join(CLIENTS_DIR, f"{cid}.json")

    if os.path.exists(path):
        return render_template("onboard.html", created=False, error=f"Client ID '{cid}' already exists.")

    emergency_defaults = {
        "electrical": ["sparking", "smoke", "fire", "burning smell", "no power", "shock"],
        "plumbing": ["overflow", "flood", "burst", "sewage", "backup", "leak spraying"],
        "hvac": ["gas", "smoke", "carbon monoxide", "no heat", "no ac", "burning smell"],
        "general": ["fire", "smoke", "gas", "flood"]
    }

    cfg = {
        "client_id": cid,
        "business_name": business_name,
        "service_category": service_category,
        "emergency_keywords": emergency_defaults.get(service_category, emergency_defaults["general"]),
        "lead_questions": DEFAULT_CLIENT["lead_questions"],
        "alert_email_to": alert_email_to
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    created_link = f"{request.host_url}?client_id={cid}".rstrip("/")
    return render_template("onboard.html", created=True, created_link=created_link)


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "time": now_iso(),
        "twilio_ready": twilio_ready(),
        "email_ready": email_ready(),
        "clients": list_client_ids()
    })


@app.route("/clients")
def clients():
    return jsonify({"clients": list_client_ids()})


@app.route("/admin/data")
def admin_data():
    chats = _read_json(CHATS_PATH, [])
    if not isinstance(chats, list):
        chats = []
    chats = list(reversed(chats))[:200]
    return jsonify({"leads": chats})


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/chat", methods=["POST"])
def chat():
    try:
        payload = request.get_json(silent=True) or {}
        message = (payload.get("message") or "").strip()
        client_id = (payload.get("client_id") or "default").strip() or "default"

        if not message:
            return jsonify({"error": "message_required"}), 400

        cfg = load_client(client_id)
        urgent = is_urgent(cfg, message)

        priority = "URGENT" if urgent else "NORMAL"
        reply = build_reply(cfg, urgent)

        append_lead(client_id, message, priority)

        email_status = None
        sms_status = None

        if urgent:
            ok_e, st_e = send_urgent_email(cfg, message)
            email_status = st_e if ok_e else st_e

            ok_s, st_s = send_urgent_sms(cfg, message)
            sms_status = st_s if ok_s else st_s

        return jsonify({
            "response": reply,
            "priority": priority,
            "email_status": email_status,
            "sms_status": sms_status
        }), 200

    except Exception as e:
        print("🔥 /chat crashed:")
        traceback.print_exc()
        return jsonify({"error": "internal_server_error", "details": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
