# =========================
# app.py — PART 1 / 6
# =========================
# CLEAN SINGLE-COPY FULL VERSION (fixes endpoint overwrite + 404 aliases)
# Includes:
# - Admin login + dispatcher UI routes
# - Tickets API (assign, auto-assign, status, seen, meta, delete/restore, notify_test)
# - Chat intake engine (professional flow + safety)
# - Public tracking (/track/<token>)
# - Technician portal (login + portal UI + API + uploads)
# - Google OAuth + Calendar event sync endpoints
# - Maps endpoint /c/<client_id>/api/tickets_map
# - ✅ Compatibility aliases: /api/session /api/chat /api/reset (fix 404s when frontend calls /api/*)
# - ✅ Monetization upgrades: Stripe Checkout + Webhook + plan enforcement per-client

from flask import (
    Flask, render_template, request, jsonify, session, redirect, url_for,
    render_template_string, send_from_directory, abort
)
from functools import wraps
import os, json, time, secrets, re, hmac, hashlib
from datetime import datetime, timezone, timedelta

# Optional .env support
try:
    from dotenv import load_dotenv
    # ✅ More reliable: load .env from same folder as this file
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

# Optional notifications module (safe no-op if missing)
try:
    from utils.notify import notify_customer
    NOTIFY_ENABLED = True
except Exception:
    NOTIFY_ENABLED = False
    def notify_customer(*args, **kwargs):
        return (False, "notify module missing")

# Google libs (optional)
GOOGLE_LIBS_OK = True
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
except Exception:
    GOOGLE_LIBS_OK = False
    Credentials = None
    Flow = None
    Request = None
    build = None

# Stripe (optional)
STRIPE_OK = True
try:
    import stripe
except Exception:
    STRIPE_OK = False
    stripe = None

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,  # set True behind HTTPS
)

BASE_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CLIENTS_FILE = os.path.join(BASE_DATA_DIR, "clients.json")

LOGIN_WINDOW_SECONDS = 10 * 60
LOGIN_MAX_ATTEMPTS = 8
_login_attempts = {}  # ip -> list[timestamps]

UPLOADS_DIRNAME = "uploads"
ALLOWED_UPLOAD_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}

# =========================
# Monetization / Stripe config
# =========================
STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
STRIPE_WEBHOOK_SECRET = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
STRIPE_PRICE_STARTER = (os.getenv("STRIPE_PRICE_STARTER") or "").strip()
STRIPE_PRICE_PRO = (os.getenv("STRIPE_PRICE_PRO") or "").strip()

APP_NAME = os.getenv("APP_NAME", "Turbo Dispatch")
DEFAULT_PLAN = "starter"
PAID_PLANS = {"pro", "elite"}  # per your usage

# =========================================================
# Utilities
# =========================================================
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _parse_iso_z(s: str):
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _external_base_url():
    try:
        return request.host_url.rstrip("/")
    except Exception:
        return "http://127.0.0.1:5000"

def client_dir(client_id: str) -> str:
    return os.path.join(BASE_DATA_DIR, client_id)

def ensure_client_dirs(client_id: str):
    os.makedirs(client_dir(client_id), exist_ok=True)

def tickets_path(client_id: str) -> str:
    return os.path.join(client_dir(client_id), "tickets.json")

def sessions_path(client_id: str) -> str:
    return os.path.join(client_dir(client_id), "sessions.json")

def config_path(client_id: str) -> str:
    return os.path.join(client_dir(client_id), "config.json")

def uploads_root(client_id: str) -> str:
    return os.path.join(client_dir(client_id), UPLOADS_DIRNAME)

def uploads_ticket_dir(client_id: str, ticket_id: str) -> str:
    return os.path.join(uploads_root(client_id), ticket_id)

def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def write_json(path, data):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def get_clients():
    clients = read_json(CLIENTS_FILE, [])
    return clients if isinstance(clients, list) else []

def get_client(client_id: str):
    for c in get_clients():
        if c.get("client_id") == client_id:
            return c
    return None

def new_id(prefix="m"):
    return f"{prefix}_{secrets.token_urlsafe(8).replace('-', '_')}"

def add_timeline(t, label, by="system", detail=None):
    entry = {"by": by, "at": now_iso(), "label": label}
    if detail:
        entry["detail"] = detail
    t.setdefault("timeline", []).insert(0, entry)

def push_message(srec: dict, role: str, text: str, ui: dict = None, turn_id: str = None):
    srec.setdefault("messages", []).append({
        "id": new_id("msg"),
        "role": role,
        "text": text,
        "at": now_iso(),
        "ui": ui,
        "turn_id": turn_id,
    })

def ensure_track_token(t: dict) -> str:
    tok = (t.get("track_token") or "").strip()
    if tok:
        return tok
    tok = secrets.token_urlsafe(24).replace("-", "_")
    t["track_token"] = tok
    return tok

def build_track_url(token: str) -> str:
    base = _external_base_url()
    return f"{base}/track/{token}"

def _notify_and_log(t: dict, event_key: str):
    if not NOTIFY_ENABLED:
        return
    try:
        ok, info = notify_customer(event_key, t)
        add_timeline(
            t,
            f"Customer notified ({event_key})" if ok else f"Notify skipped/failed ({event_key})",
            by="system",
            detail=info
        )
    except Exception as e:
        add_timeline(t, f"Notify error ({event_key})", by="system", detail=str(e))

# =========================================================
# Per-client config (includes billing/plan)
# =========================================================
def get_client_config(client_id: str):
    ensure_client_dirs(client_id)

    # ✅ Fixed: single coherent default schema (no stray duplicate keys)
    default = {
        "plan_tier": DEFAULT_PLAN,  # "starter" | "pro" | ...
        "default_plan_tier": DEFAULT_PLAN,

        "billing": {
            "status": "unpaid",          # stripe raw: unpaid|active|trialing|past_due|canceled|incomplete|...
            "provider": "stripe",
            "plan": DEFAULT_PLAN,        # mapped from price_id
            "customer_id": "",
            "subscription_id": "",
            "price_id": "",
            "current_period_end": "",    # ISO
            "updated_at": "",            # ISO
        },

        "service_states_allowlist": ["ks"],
        "service_zip3_allowlist": ["670", "671"],
        "techs": [
            {"id": "t1", "name": "A. Rivera", "skills": ["electrical", "plumbing"], "enabled": True, "status": "available"},
            {"id": "t2", "name": "M. Chen", "skills": ["hvac"], "enabled": True, "status": "available"},
            {"id": "t3", "name": "J. Patel", "skills": ["electrical", "hvac"], "enabled": True, "status": "available"},
        ],
        "service_category": "plumbing",
        "emergency_keywords": [
            "flood", "overflow", "burst", "sewage", "backup", "water everywhere", "pipe burst", "toilet overflowing",
            "sparking", "smell burning", "burning", "smoke", "smoking", "arcing", "crackling"
        ],
        "google_calendar_id": "primary",
        "google_maps_public_key": "",
        "admin_password": "",
        "tracking_hide_street": False,
        "tech_users": "",  # "t1:1234,t2:2222"
    }

    cfg = read_json(config_path(client_id), default)
    if not isinstance(cfg, dict):
        cfg = default

    # Merge top-level defaults
    for k, v in default.items():
        cfg.setdefault(k, v)

    # Billing defaults/shape
    if not isinstance(cfg.get("billing"), dict):
        cfg["billing"] = default["billing"].copy()
    for k, v in default["billing"].items():
        cfg["billing"].setdefault(k, v)

    techs = cfg.get("techs") or []
    if not isinstance(techs, list):
        techs = []
        cfg["techs"] = techs

    for t in techs:
        if not isinstance(t, dict):
            continue
        t.setdefault("enabled", True)
        t.setdefault("status", "available")
        t.setdefault("skills", [])
        t.setdefault("last_assigned_at", "")
        t.setdefault("last_assigned_ticket_id", "")

    return cfg

def set_client_billing(client_id: str, **updates):
    cfg = get_client_config(client_id)
    cfg.setdefault("billing", {})
    b = cfg["billing"]
    for k, v in updates.items():
        b[k] = v
    b["updated_at"] = now_iso()

    # Keep plan_tier in sync if plan is present
    if b.get("plan"):
        cfg["plan_tier"] = b.get("plan")

    write_json(config_path(client_id), cfg)
    return cfg

def is_client_paid(cfg: dict) -> bool:
    cfg = cfg or {}
    b = cfg.get("billing") or {}

    status = (b.get("status") or "").lower().strip()
    plan = (b.get("plan") or cfg.get("plan_tier") or cfg.get("default_plan_tier") or "").lower().strip()

    if plan not in PAID_PLANS:
        return False

    return status in ("active", "trialing")

# =========================================================
# Tickets + sessions storage
# =========================================================
def get_tickets(client_id: str):
    ensure_client_dirs(client_id)
    data = read_json(tickets_path(client_id), [])
    return data if isinstance(data, list) else []

def save_tickets(client_id: str, tickets):
    write_json(tickets_path(client_id), tickets)

def get_sessions(client_id: str):
    ensure_client_dirs(client_id)
    data = read_json(sessions_path(client_id), {})
    return data if isinstance(data, dict) else {}

def save_sessions(client_id: str, sessions):
    write_json(sessions_path(client_id), sessions)

def find_ticket(tickets, tid: str):
    for t in tickets:
        if t.get("id") == tid:
            return t
    return None

def find_draft_ticket_by_session(tickets, sid: str):
    for t in tickets:
        if t.get("draft") and t.get("session_id") == sid and not t.get("deleted"):
            return t
    return None

# =========================
# app.py — PART 2 / 6
# =========================

# =========================================================
# Auth (admin/dispatcher)
# =========================================================
def prune_attempts(ip: str):
    now = time.time()
    arr = _login_attempts.get(ip, [])
    arr = [ts for ts in arr if now - ts < LOGIN_WINDOW_SECONDS]
    _login_attempts[ip] = arr
    return arr

def parse_admin_users(raw: str) -> dict:
    out = {}
    raw = (raw or "").strip()
    if not raw:
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        user, pw = part.split(":", 1)
        user = user.strip()
        pw = pw.strip()
        if user and pw:
            out[user] = pw
    return out

def _get_client_admin_password(client_id: str) -> str:
    env_pw = (os.getenv("ADMIN_PASSWORD") or "").strip()
    if env_pw:
        return env_pw

    c = get_client(client_id) or {}
    pw = (c.get("admin_password") or "").strip()
    if pw:
        return pw

    cfg = get_client_config(client_id) or {}
    pw2 = (cfg.get("admin_password") or "").strip()
    if pw2:
        return pw2

    return "admin"

def _check_login(client_id: str, username: str, password: str) -> bool:
    users_map = parse_admin_users(os.getenv("ADMIN_USERS") or "")
    if users_map:
        expected = users_map.get(username or "")
        return bool(expected) and password == expected
    expected = _get_client_admin_password(client_id)
    return password == expected

def login_required(fn):
    @wraps(fn)
    def wrapper(client_id, *args, **kwargs):
        if session.get("admin_client_id") != client_id:
            next_url = request.args.get("next") or url_for("dispatcher", client_id=client_id)
            return redirect(url_for("admin_login", client_id=client_id, next=next_url))
        return fn(client_id, *args, **kwargs)
    return wrapper

def paid_required(fn):
    """Locks dispatcher/admin APIs behind paid plan (set billing.status=active/trialing)."""
    @wraps(fn)
    def wrapper(client_id, *args, **kwargs):
        cfg = get_client_config(client_id)
        if not is_client_paid(cfg):
            return redirect(url_for("pricing", client_id=client_id))
        return fn(client_id, *args, **kwargs)
    return wrapper

# =========================================================
# Technician auth
# =========================================================
def parse_tech_users(raw: str) -> dict:
    out = {}
    raw = (raw or "").strip()
    if not raw:
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        tid, pw = part.split(":", 1)
        tid = tid.strip()
        pw = pw.strip()
        if tid and pw:
            out[tid] = pw
    return out

def _get_tech_users_map(cfg: dict) -> dict:
    env_raw = (os.getenv("TECH_USERS") or "").strip()
    if env_raw:
        return parse_tech_users(env_raw)
    cfg_raw = (cfg.get("tech_users") or "").strip() if isinstance(cfg, dict) else ""
    return parse_tech_users(cfg_raw)

def tech_required(fn):
    @wraps(fn)
    def wrapper(client_id, *args, **kwargs):
        if session.get("tech_client_id") != client_id or not session.get("tech_id"):
            next_url = request.args.get("next") or url_for("tech_portal", client_id=client_id)
            return redirect(url_for("tech_login", client_id=client_id, next=next_url))
        return fn(client_id, *args, **kwargs)
    return wrapper

def _tech_identity():
    return {
        "tech_id": (session.get("tech_id") or "").strip(),
        "tech_name": (session.get("tech_name") or "").strip(),
    }

# =========================================================
# Google OAuth + token storage
# =========================================================
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

def google_dir(client_id: str) -> str:
    return os.path.join(client_dir(client_id), "google")

def google_token_path(client_id: str) -> str:
    return os.path.join(google_dir(client_id), "token.json")

def google_client_secret_path() -> str:
    return os.path.join(BASE_DATA_DIR, "google_oauth_client.json")

def load_google_oauth_client_config() -> dict:
    path = google_client_secret_path()
    if os.path.exists(path):
        cfg = read_json(path, None)
        if isinstance(cfg, dict):
            return cfg

    raw = (os.getenv("GOOGLE_OAUTH_CLIENT_JSON") or "").strip()
    if raw:
        try:
            cfg = json.loads(raw)
            if isinstance(cfg, dict):
                return cfg
        except Exception:
            pass

    return {}

def _ensure_google_dir(client_id: str) -> str:
    d = google_dir(client_id)
    if os.path.exists(d) and not os.path.isdir(d):
        try:
            os.remove(d)
        except Exception as e:
            raise Exception(f"Google token path is not a folder: {d}. Delete/rename it. ({e})")
    os.makedirs(d, exist_ok=True)
    return d

def save_google_token(client_id: str, token_dict: dict):
    _ensure_google_dir(client_id)
    write_json(google_token_path(client_id), token_dict)

def load_google_token(client_id: str) -> dict:
    tok = read_json(google_token_path(client_id), None)
    return tok if isinstance(tok, dict) else {}

def google_is_connected(client_id: str) -> bool:
    tok = load_google_token(client_id)
    return bool((tok.get("token") or "").strip() or (tok.get("refresh_token") or "").strip())

def get_default_calendar_id(cfg: dict) -> str:
    if isinstance(cfg, dict):
        cal_id = (cfg.get("google_calendar_id") or "").strip()
        if cal_id:
            return cal_id
    return "primary"

def get_gcal_service(client_id: str):
    if not GOOGLE_LIBS_OK:
        raise Exception("Google libs not installed. Install google-auth/google-api-python-client packages.")

    token = load_google_token(client_id)
    if not isinstance(token, dict) or not (token.get("token") or token.get("refresh_token")):
        raise Exception("Google Calendar not connected (missing token).")

    creds = Credentials(
        token=token.get("token"),
        refresh_token=token.get("refresh_token"),
        token_uri=token.get("token_uri"),
        client_id=token.get("client_id"),
        client_secret=token.get("client_secret"),
        scopes=token.get("scopes") or GOOGLE_SCOPES,
    )

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_google_token(client_id, {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or GOOGLE_SCOPES),
        })

    return build("calendar", "v3", credentials=creds)

# =========================================================
# Stripe helpers
# =========================================================
def stripe_enabled() -> bool:
    return STRIPE_OK and bool(STRIPE_SECRET_KEY)

def _stripe_init():
    if not stripe_enabled():
        raise Exception("Stripe not configured. Set STRIPE_SECRET_KEY.")
    stripe.api_key = STRIPE_SECRET_KEY

def _plan_to_price_id(plan: str) -> str:
    p = (plan or "").lower().strip()
    if p == "starter":
        return STRIPE_PRICE_STARTER
    if p == "pro":
        return STRIPE_PRICE_PRO
    return ""

def _price_id_to_plan(price_id: str) -> str:
    pid = (price_id or "").strip()
    if pid and STRIPE_PRICE_PRO and pid == STRIPE_PRICE_PRO:
        return "pro"
    if pid and STRIPE_PRICE_STARTER and pid == STRIPE_PRICE_STARTER:
        return "starter"
    return ""

def _billing_success_url(client_id: str) -> str:
    return _external_base_url() + url_for("dispatcher", client_id=client_id)

def _billing_cancel_url(client_id: str) -> str:
    return _external_base_url() + url_for("pricing", client_id=client_id)

def _iso_from_unix(ts: int | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""

# =========================
# app.py — PART 3 / 6
# =========================

# =========================================================
# Google OAuth routes (admin-only) — UNIQUE function names
# =========================================================
@app.route("/c/<client_id>/integrations/google/start", methods=["GET"])
@login_required
def google_oauth_start(client_id):
    if not GOOGLE_LIBS_OK:
        return jsonify({"error": "Google libs not installed."}), 400

    oauth_cfg = load_google_oauth_client_config()
    if not oauth_cfg:
        return jsonify({"error": "Missing OAuth config. Put data/google_oauth_client.json"}), 400

    redirect_uri = _external_base_url() + url_for("google_oauth_callback")
    flow = Flow.from_client_config(oauth_cfg, scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri)

    state = new_id("gstate") + "|" + client_id
    session["google_oauth_state"] = state

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return redirect(auth_url)

@app.route("/integrations/google/callback", methods=["GET"])
def google_oauth_callback():
    if not GOOGLE_LIBS_OK:
        return "Google libs not installed.", 400

    state = (request.args.get("state") or "").strip()
    code = (request.args.get("code") or "").strip()
    err = (request.args.get("error") or "").strip()

    if err:
        return f"Google authorization failed: {err}", 400
    if not state or "|" not in state:
        return "Invalid OAuth state.", 400

    sess_state = session.get("google_oauth_state")
    if sess_state and sess_state != state:
        return "OAuth state mismatch.", 400

    _, client_id = state.split("|", 1)
    client_id = (client_id or "").strip()
    if not client_id:
        return "Missing client id in state.", 400

    oauth_cfg = load_google_oauth_client_config()
    if not oauth_cfg:
        return "Server missing OAuth client config.", 500

    redirect_uri = _external_base_url() + url_for("google_oauth_callback")
    flow = Flow.from_client_config(oauth_cfg, scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri, state=state)

    flow.fetch_token(code=code)
    creds = flow.credentials

    save_google_token(client_id, {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    })

    session.pop("google_oauth_state", None)
    return redirect(url_for("dispatcher", client_id=client_id))

@app.route("/c/<client_id>/integrations/google/status", methods=["GET"])
@login_required
def google_oauth_status(client_id):
    cfg = get_client_config(client_id)
    connected = google_is_connected(client_id)
    return jsonify({
        "ok": True,
        "connected": connected,
        "calendar_id": get_default_calendar_id(cfg) if connected else "",
        "google_libs_ok": GOOGLE_LIBS_OK,
    })

@app.route("/c/<client_id>/integrations/google/disconnect", methods=["POST"])
@login_required
def google_oauth_disconnect(client_id):
    try:
        path = google_token_path(client_id)
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass
    session.pop("google_oauth_state", None)
    return jsonify({"ok": True, "connected": False})

# =========================================================
# Google Calendar event helpers
# =========================================================
def _parse_any_dt(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s2)
        else:
            try:
                dt = datetime.fromisoformat(s)
            except Exception:
                try:
                    dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
                except Exception:
                    dt = datetime.strptime(s, "%Y-%m-%d")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _ticket_calendar_summary(t: dict) -> str:
    svc = (t.get("service") or "Service").title()
    urg = (t.get("urgency") or "normal").lower()
    tag = "URGENT" if urg == "urgent" else "Job"
    return f"{tag}: {svc} — Ticket {t.get('id','')}".strip()

def _ticket_calendar_description(t: dict) -> str:
    lines = []
    lines.append(f"Ticket: {t.get('id','')}")
    lines.append(f"Service: {t.get('service','')}")
    lines.append(f"Urgency: {t.get('urgency','')}")
    lines.append(f"Status: {t.get('status','')}")
    if t.get("assigned_tech_name"):
        lines.append(f"Technician: {t.get('assigned_tech_name')}")
    if t.get("phone"):
        lines.append(f"Phone: {t.get('phone')}")
    if t.get("availability"):
        lines.append(f"Availability: {t.get('availability')}")
    if t.get("address"):
        lines.append(f"Address: {t.get('address')}")
    if t.get("internal_notes"):
        lines.append("")
        lines.append("Internal notes:")
        lines.append(t.get("internal_notes"))
    return "\n".join(lines).strip()

def _ticket_location(t: dict) -> str:
    return (t.get("address") or t.get("address_raw") or "").strip()

def _ensure_schedule_fields(t: dict):
    t.setdefault("scheduled_start", "")
    t.setdefault("scheduled_end", "")
    t.setdefault("scheduled_tz", "UTC")
    t.setdefault("gcal_event_id", "")
    t.setdefault("gcal_event_link", "")
    t.setdefault("gcal_calendar_id", "")
    t.setdefault("gcal_html_link", "")
    t.setdefault("gcal_start", "")
    t.setdefault("gcal_end", "")
    t.setdefault("gcal_timezone", "")

def _default_schedule_window():
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    end = start + timedelta(hours=2)
    return start, end

def create_or_update_calendar_event(client_id: str, t: dict, start_dt: datetime, end_dt: datetime):
    cfg = get_client_config(client_id)
    cal_id = get_default_calendar_id(cfg)
    service = get_gcal_service(client_id)

    event_body = {
        "summary": _ticket_calendar_summary(t),
        "description": _ticket_calendar_description(t),
        "location": _ticket_location(t),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "UTC"},
    }

    existing_event_id = (t.get("gcal_event_id") or "").strip()
    if existing_event_id:
        ev = service.events().patch(calendarId=cal_id, eventId=existing_event_id, body=event_body).execute()
    else:
        ev = service.events().insert(calendarId=cal_id, body=event_body).execute()

    t["gcal_event_id"] = ev.get("id", "") or ""
    t["gcal_event_link"] = ev.get("htmlLink", "") or ""
    t["gcal_html_link"] = t.get("gcal_event_link") or ""
    t["gcal_calendar_id"] = cal_id

    t["scheduled_start"] = start_dt.isoformat().replace("+00:00", "Z")
    t["scheduled_end"] = end_dt.isoformat().replace("+00:00", "Z")
    t["scheduled_tz"] = "UTC"
    return ev

def delete_calendar_event(client_id: str, t: dict):
    cfg = get_client_config(client_id)
    cal_id = get_default_calendar_id(cfg)
    service = get_gcal_service(client_id)

    existing_event_id = (t.get("gcal_event_id") or "").strip()
    if not existing_event_id:
        return False

    service.events().delete(calendarId=cal_id, eventId=existing_event_id).execute()

    t["gcal_event_id"] = ""
    t["gcal_event_link"] = ""
    t["gcal_html_link"] = ""
    t["gcal_calendar_id"] = cal_id
    return True

@app.route("/c/<client_id>/api/calendar/ping", methods=["GET"])
@login_required
def api_calendar_ping(client_id):
    cfg = get_client_config(client_id)
    connected = google_is_connected(client_id)
    if not connected:
        return jsonify({"ok": True, "connected": False, "calendar_id": ""})

    try:
        _ = get_gcal_service(client_id)
        return jsonify({"ok": True, "connected": True, "calendar_id": get_default_calendar_id(cfg)})
    except Exception as e:
        return jsonify({"ok": True, "connected": False, "error": str(e)})

@app.route("/c/<client_id>/api/tickets/<ticket_id>/calendar", methods=["POST"])
@login_required
def api_ticket_calendar_upsert(client_id, ticket_id):
    if not google_is_connected(client_id):
        return jsonify({"error": "Google Calendar not connected for this client."}), 400

    data = request.get_json(force=True, silent=True) or {}

    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t:
        return jsonify({"error": "Ticket not found"}), 404

    ensure_track_token(t)
    _ensure_schedule_fields(t)

    start_dt = _parse_any_dt(data.get("start"))
    end_dt = _parse_any_dt(data.get("end"))

    if start_dt and end_dt and end_dt <= start_dt:
        return jsonify({"error": "End must be after start."}), 400

    if start_dt and not end_dt:
        mins = int(data.get("duration_minutes") or 120)
        end_dt = start_dt + timedelta(minutes=max(15, mins))

    if not start_dt:
        start_dt, end_dt = _default_schedule_window()

    try:
        ev = create_or_update_calendar_event(client_id, t, start_dt, end_dt)
        if (t.get("status") or "").lower() == "open":
            t["status"] = "scheduled"

        add_timeline(t, "Calendar event synced", by=session.get("admin_user", "system"))
        _notify_and_log(t, "scheduled")
        save_tickets(client_id, tickets)

        return jsonify({
            "ok": True,
            "ticket": t,
            "event": {"id": ev.get("id"), "htmlLink": ev.get("htmlLink")},
            "track_url": build_track_url(t.get("track_token")),
        })
    except Exception as e:
        return jsonify({"error": f"Calendar sync failed: {e}"}), 500

@app.route("/c/<client_id>/api/tickets/<ticket_id>/calendar", methods=["DELETE"])
@login_required
def api_ticket_calendar_delete(client_id, ticket_id):
    if not google_is_connected(client_id):
        return jsonify({"error": "Google Calendar not connected for this client."}), 400

    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t:
        return jsonify({"error": "Ticket not found"}), 404

    _ensure_schedule_fields(t)

    try:
        deleted = delete_calendar_event(client_id, t)
        if deleted:
            add_timeline(t, "Calendar event deleted", by=session.get("admin_user", "system"))
            save_tickets(client_id, tickets)
        return jsonify({"ok": True, "deleted": deleted, "ticket": t})
    except Exception as e:
        return jsonify({"error": f"Calendar delete failed: {e}"}), 500

# =========================
# app.py — PART 4 / 6
# =========================

# =========================================================
# Public Tracking Routes
# =========================================================
def _format_public_address(cfg: dict, t: dict) -> str:
    hide = bool((cfg or {}).get("tracking_hide_street", False))
    if not hide:
        return (t.get("address") or t.get("address_raw") or "").strip()

    city = (t.get("address_city") or "").strip()
    st = (t.get("address_state") or "").strip()
    z = (t.get("address_zip") or "").strip()

    parts = []
    if city:
        parts.append(city)
    if st:
        if parts:
            parts[-1] = f"{parts[-1]}, {st}"
        else:
            parts.append(st)
    if z:
        parts.append(z)
    return " ".join(parts).strip()

    # ✅ Allow UI-only bot messages (for card-only bubbles like screenshot)
    if not parts and (ui is not None or (isinstance(ui_parts, list) and ui_parts)):
        parts = [" "]  # placeholder so UI renders


def find_ticket_by_track_token(token: str):
    token = (token or "").strip()
    if not token:
        return None, None, None

    for c in get_clients():
        cid = (c.get("client_id") or "").strip()
        if not cid:
            continue
        tickets = get_tickets(cid)
        for t in tickets:
            if (t.get("track_token") or "").strip() == token:
                cfg = get_client_config(cid)
                return cid, t, cfg
    return None, None, None

@app.route("/track/<token>", methods=["GET"])
def public_track(token):
    client_id, t, cfg = find_ticket_by_track_token(token)
    if not t:
        return "Tracking link not found.", 404

    public = {
        "ticket_id": t.get("id"),
        "status": t.get("status") or "open",
        "urgency": t.get("urgency") or "normal",
        "service": t.get("service") or "unknown",
        "assigned_tech_name": t.get("assigned_tech_name") or "",
        "scheduled_start": t.get("scheduled_start") or "",
        "scheduled_end": t.get("scheduled_end") or "",
        "eta_minutes": t.get("eta_minutes") or None,
        "eta_at": t.get("eta_at") or "",
        "address": _format_public_address(cfg, t),
        "created_at": t.get("created_at") or "",
        "gcal_html_link": t.get("gcal_html_link") or t.get("gcal_event_link") or "",
        "client_id": client_id,
        "business_name": (get_client(client_id) or {}).get("business_name") or client_id,
    }

    html = """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <title>{{business_name}} — Job Tracking</title>
      <style>
        body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#0b1220;color:#e8eefc;margin:0;padding:24px}
        .card{max-width:780px;margin:0 auto;background:#121b2f;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:18px 18px 14px;box-shadow:0 12px 40px rgba(0,0,0,.35)}
        .row{display:flex;gap:14px;flex-wrap:wrap}
        .pill{display:inline-block;padding:6px 10px;border-radius:999px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);font-size:13px}
        h1{margin:0 0 10px;font-size:20px}
        .muted{opacity:.8}
        .kv{margin-top:12px;display:grid;grid-template-columns:160px 1fr;gap:8px 12px}
        .k{opacity:.75}
        a{color:#8ab4ff}
        .btn{display:inline-block;margin-top:12px;padding:10px 12px;border-radius:12px;border:1px solid rgba(255,255,255,.14);text-decoration:none}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="row" style="justify-content:space-between;align-items:center">
          <div>
            <h1>{{business_name}} — Job Tracking</h1>
            <div class="muted">Ticket {{ticket_id}}</div>
          </div>
          <div class="pill">Status: {{status}}</div>
        </div>

        <div class="row" style="margin-top:10px">
          <div class="pill">Service: {{service}}</div>
          <div class="pill">Urgency: {{urgency}}</div>
          {% if assigned_tech_name %}
          <div class="pill">Tech: {{assigned_tech_name}}</div>
          {% endif %}
          {% if eta_minutes %}
          <div class="pill">ETA: {{eta_minutes}} min</div>
          {% endif %}
        </div>

        <div class="kv">
          <div class="k">Address</div><div>{{address or "—"}}</div>
          <div class="k">Scheduled</div>
          <div>
            {% if scheduled_start or scheduled_end %}
              {{scheduled_start or ""}}{% if scheduled_end %} → {{scheduled_end}}{% endif %}
            {% else %}
              —
            {% endif %}
          </div>
          <div class="k">Created</div><div>{{created_at or "—"}}</div>
        </div>

        <div class="row">
          <a class="btn" href="/track/{{token}}/json">View JSON</a>
          {% if gcal_html_link %}
            <a class="btn" href="{{gcal_html_link}}" target="_blank" rel="noreferrer">Calendar Event</a>
          {% endif %}
        </div>
      </div>
    </body>
    </html>
    """
    return render_template_string(html, token=token, **public)

@app.route("/track/<token>/json", methods=["GET"])
def public_track_json(token):
    client_id, t, cfg = find_ticket_by_track_token(token)
    if not t:
        return jsonify({"error": "Tracking link not found"}), 404
    return jsonify({
        "ok": True,
        "tracking": {
            "ticket_id": t.get("id"),
            "status": t.get("status") or "open",
            "urgency": t.get("urgency") or "normal",
            "service": t.get("service") or "unknown",
            "assigned_tech_name": t.get("assigned_tech_name") or "",
            "scheduled_start": t.get("scheduled_start") or "",
            "scheduled_end": t.get("scheduled_end") or "",
            "eta_minutes": t.get("eta_minutes") or None,
            "eta_at": t.get("eta_at") or "",
            "address": _format_public_address(cfg, t),
            "created_at": t.get("created_at") or "",
            "track_url": build_track_url(token),
        }
    })

# =========================================================
# Upload serving (admin OR tech only)
# =========================================================
@app.route("/c/<client_id>/uploads/<path:subpath>", methods=["GET"])
def serve_upload(client_id, subpath):
    is_admin = (session.get("admin_client_id") == client_id)
    is_tech = (session.get("tech_client_id") == client_id and session.get("tech_id"))
    if not (is_admin or is_tech):
        return "Unauthorized", 401
    root = uploads_root(client_id)
    return send_from_directory(root, subpath, as_attachment=False)

# =========================================================
# Auto-dispatch engine
# =========================================================
def update_tech_last_assigned(cfg: dict, tech_id: str, ticket_id: str = ""):
    for t in cfg.get("techs", []):
        if t.get("id") == tech_id:
            t["last_assigned_at"] = now_iso()
            t["last_assigned_ticket_id"] = ticket_id
            return

def auto_assign_best_tech(cfg: dict, tickets: list, ticket: dict) -> dict:
    techs = [t for t in (cfg.get("techs") or []) if t.get("enabled", True)]
    if not techs:
        return {"tech_id": "", "reason": "No technicians configured."}

    svc = (ticket.get("service") or "").lower()

    def score(tech):
        s = 0

        skill_ok = (not svc) or (svc in (tech.get("skills") or []))
        if skill_ok:
            s += 50

        loads = {"active": 0, "urgent": 0, "en_route": 0, "onsite": 0}
        for tt in tickets:
            if tt.get("deleted"):
                continue
            if tt.get("assigned_tech_id") != tech.get("id"):
                continue

            loads["active"] += 1
            if tt.get("urgency") == "urgent":
                loads["urgent"] += 1
            if tt.get("status") == "en_route":
                loads["en_route"] += 1
            if tt.get("status") == "onsite":
                loads["onsite"] += 1

        s -= loads["active"] * 5
        s -= loads["urgent"] * 5
        s -= loads["en_route"] * 10
        s -= loads["onsite"] * 20

        last = _parse_iso_z(tech.get("last_assigned_at"))
        if last:
            delta = (datetime.now(timezone.utc) - last).total_seconds()
            fairness = min(30, int(delta // 300))
        else:
            fairness = 30
        s += fairness

        zip_bonus = 0
        s += zip_bonus

        return {
            "score": s,
            "skill_ok": skill_ok,
            "load": loads,
            "fairness": fairness,
            "zip_bonus": zip_bonus,
            "last_assigned_at": tech.get("last_assigned_at"),
        }

    best = None
    best_tech = None
    for tech in techs:
        r = score(tech)
        if not best or r["score"] > best["score"]:
            best = r
            best_tech = tech

    if not best_tech:
        return {"tech_id": "", "reason": "No suitable technician."}

    reason = (
        f"skill={'yes' if best['skill_ok'] else 'no'}, "
        f"active={best['load']['active']}, urgent={best['load']['urgent']}, "
        f"en_route={best['load']['en_route']}, onsite={best['load']['onsite']}, "
        f"zip_bonus={best['zip_bonus']}, fairness=+{best['fairness']}, "
        f"last_assigned_at={best['last_assigned_at'] or 'never'}"
    )

    return {"tech_id": best_tech["id"], "tech_name": best_tech["name"], "score": best["score"], "reason": reason}

# =========================================================
# Chat intake engine + UI routes + Billing routes + Webhook
# =========================================================
SERVICE_KEYWORDS = {
    "plumbing": ["toilet", "sink", "drain", "clog", "overflow", "sewage", "pipe", "leak", "water heater"],
    "electrical": ["outlet", "sparking", "breaker", "burning", "smell", "flicker", "panel", "wire", "short"],
    "hvac": ["heater", "furnace", "ac", "air conditioner", "heat pump", "no heat", "no cooling"],
}
ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
ELECTRICAL_HAZARD_KEYWORDS = [
    "sparking", "spark", "arcing", "arc", "buzzing", "crackling",
    "burning smell", "smell burning", "smells like burning",
    "smoke", "smoking", "hot outlet", "melting", "scorch", "blackened"
]
YES_WORDS = ["yes", "yep", "yeah", "yup", "sure", "correct", "it is", "still", "right now", "currently", "active"]
NO_WORDS  = ["no", "nope", "nah", "not now", "stopped", "not anymore", "no longer"]
US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA",
    "HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
    "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
    "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"
}

def detect_service(msg: str) -> str:
    s = (msg or "").lower()
    for svc, words in SERVICE_KEYWORDS.items():
        if any(w in s for w in words):
            return svc
    return "unknown"

def detect_urgency(cfg: dict, msg: str) -> str:
    s = (msg or "").lower()
    emergency = [x.lower() for x in (cfg.get("emergency_keywords", []) or [])]
    if any(k in s for k in emergency):
        return "urgent"
    return "normal"

def normalize_state(raw: str):
    if not raw:
        return None
    tokens = re.findall(r"\b[A-Za-z]{2}\b", raw.upper())
    for tok in tokens:
        if tok in US_STATES:
            return tok
    return None

def normalize_zip(raw: str):
    m = ZIP_RE.search(raw or "")
    if m:
        return m.group(1)
    digits = re.sub(r"\D+", "", raw or "")
    if len(digits) >= 5:
        return digits[:5]
    return None

def normalize_phone(raw: str):
    digits = re.sub(r"\D+", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
    return None

def has_electrical_hazard(text: str) -> bool:
    m = (text or "").lower()
    return any(k in m for k in ELECTRICAL_HAZARD_KEYWORDS)

def parse_yes_no(text: str):
    m = (text or "").lower().strip()
    if any(w in m for w in YES_WORDS):
        return True
    if any(w in m for w in NO_WORDS):
        return False
    return None

def in_service_area(cfg: dict, state: str, zip5: str) -> bool:
    allow_states = [s.lower() for s in (cfg.get("service_states_allowlist", []) or [])]
    allow_zip3 = [z for z in (cfg.get("service_zip3_allowlist", []) or [])]
    st = (state or "").lower()
    if allow_states and st and st not in allow_states:
        return False
    if allow_zip3 and zip5 and zip5[:3] not in allow_zip3:
        return False
    return True

def get_or_create_session_id():
    sid = session.get("chat_session_id")
    if not sid:
        sid = "s_" + secrets.token_urlsafe(10)
        session["chat_session_id"] = sid
    return sid

def parse_full_address(text: str):
    s = (text or "").strip()
    if not s:
        return None
    z = normalize_zip(s)
    tail = " ".join(s.split(",")[-2:])
    st = normalize_state(tail)
    if not st or not z:
        return None

    parts = [p.strip() for p in s.split(",") if p.strip()]
    street = ""
    city = ""

    if len(parts) >= 3:
        street = parts[0]
        city = parts[1]
    elif len(parts) == 2:
        if re.search(r"\d", parts[0]):
            street = parts[0]
            city = parts[1]
        else:
            city = parts[0]
    else:
        tokens = s.split()
        if len(tokens) >= 5:
            try:
                st_idx = [i for i, tok in enumerate(tokens) if tok.upper() == st][-1]
                before_state = tokens[:st_idx]
                if before_state:
                    city = before_state[-1]
                    street = " ".join(before_state[:-1]).strip()
            except Exception:
                pass

    street = (street or "").strip().strip(",")
    city = (city or "").strip().strip(",")
    if not city:
        return None
    return {"street": street, "city": city, "state": st, "zip": z}

def _maybe_geocode_and_store(cfg: dict, t: dict):
    # Kept as safe no-op (as in your code) so we don't break anything.
    return

def ensure_draft_ticket(client_id: str, sid: str):
    tickets = get_tickets(client_id)
    draft = find_draft_ticket_by_session(tickets, sid)
    if draft:
        draft.setdefault("asked_safety", False)
        draft.setdefault("safety_active", None)
        draft.setdefault("asked_city", False)
        draft.setdefault("asked_state", False)
        draft.setdefault("asked_zip", False)
        draft.setdefault("asked_phone", False)
        draft.setdefault("asked_full_address", False)

        draft.setdefault("address_raw", "")
        draft.setdefault("address_street", "")
        draft.setdefault("address_city", "")
        draft.setdefault("address_state", "")
        draft.setdefault("address_zip", "")
        draft.setdefault("address", "")

        draft.setdefault("address_mode", "full")
        draft.setdefault("pending_address_part", "")
        draft.setdefault("address_confirmed", False)
        draft.setdefault("address_attempts", 0)

        draft.setdefault("phone_declined", False)
        draft.setdefault("intake_complete", False)

        draft.setdefault("seen_at", "")
        draft.setdefault("seen_by", "")

        ensure_track_token(draft)

        draft.setdefault("eta_minutes", None)
        draft.setdefault("eta_at", "")
        draft.setdefault("tech_notes", [])
        draft.setdefault("photos", [])

        _ensure_schedule_fields(draft)
        draft.setdefault("lat", None)
        draft.setdefault("lng", None)
        return draft, tickets

    tid = secrets.token_urlsafe(8).replace("-", "_")
    t = {
        "id": tid,
        "draft": True,
        "session_id": sid,
        "service": "unknown",
        "urgency": "normal",
        "status": "open",

        "address_raw": "",
        "address_street": "",
        "address_city": "",
        "address_state": "",
        "address_zip": "",
        "address": "",

        "availability": "",
        "phone": "",
        "assigned_tech_id": "",
        "assigned_tech_name": "",
        "tags": [],
        "internal_notes": "",

        "asked_availability": False,
        "asked_phone": False,
        "phone_declined": False,
        "intake_complete": False,

        "seen_at": "",
        "seen_by": "",

        "deleted": False,
        "deleted_at": "",
        "deleted_by": "",
        "created_at": now_iso(),
        "timeline": [],

        "asked_safety": False,
        "safety_active": None,

        "asked_full_address": False,
        "asked_city": False,
        "asked_state": False,
        "asked_zip": False,

        "address_mode": "full",
        "pending_address_part": "",
        "address_confirmed": False,
        "address_attempts": 0,

        "eta_minutes": None,
        "eta_at": "",
        "tech_notes": [],
        "photos": [],

        "lat": None,
        "lng": None,
    }

    ensure_track_token(t)
    _ensure_schedule_fields(t)
    add_timeline(t, "Session opened")
    tickets.append(t)
    save_tickets(client_id, tickets)
    return t, tickets

def promote_to_active(t: dict):
    if t.get("draft"):
        t["draft"] = False
        add_timeline(t, "Draft promoted to active ticket")
    if not t.get("intake_complete"):
        t["intake_complete"] = True

def reply_and_persist(client_id, tickets, sessions, sid, srec, t, reply_parts, ui=None, ui_parts=None):
    # Keep parts exactly as intended (but remove None)
    raw_parts = reply_parts or []
    parts = [p for p in raw_parts if p is not None]

    # Build an aligned ui_parts list EXACTLY same length as parts
    # Priority:
    #  - ui_parts[i] if provided
    #  - else ui (single ui) if provided
    #  - else None
    if not isinstance(ui_parts, list):
        ui_parts = []

    resolved_ui_parts = []
    for i in range(len(parts)):
        ui_for_part = None
        if i < len(ui_parts):
            ui_for_part = ui_parts[i]
        if ui_for_part is None and ui is not None:
            ui_for_part = ui
        resolved_ui_parts.append(ui_for_part)

    # Save tickets first (ticket mutated during flow)
    save_tickets(client_id, tickets)

    # Persist bot messages (each part becomes its own bubble)
    bot_turn_id = new_id("turn")
    for i, p in enumerate(parts):
        push_message(srec, "bot", p, ui=resolved_ui_parts[i], turn_id=bot_turn_id)

    sessions[sid] = srec
    save_sessions(client_id, sessions)

    ensure_track_token(t)

    payload = {
        "reply": "\n".join([str(p) for p in parts if str(p).strip()]),  # legacy field, harmless
        "reply_parts": [str(p) for p in parts],                         # EXACTLY what chat renders
        "ui_parts": resolved_ui_parts,                                  # ✅ FIX: aligned with reply_parts
        "ticket": t,
        "messages": srec.get("messages", []),
        "turn_id": bot_turn_id,
        "track_url": build_track_url(t.get("track_token")),
    }

    # Keep legacy "ui" field too (not required, but you already use it)
    if ui:
        payload["ui"] = ui

    return jsonify(payload)

def reset_chat_state(client_id: str):
    sessions = get_sessions(client_id)
    sid2 = "s_" + secrets.token_urlsafe(10)
    session["chat_session_id"] = sid2
    sessions[sid2] = {"messages": []}
    save_sessions(client_id, sessions)

    t2, tickets = ensure_draft_ticket(client_id, sid2)
    save_tickets(client_id, tickets)
    return sid2, t2

# =========================================================
# UI ROUTES (NO DUPLICATES) + 404 FIX ALIASES
# =========================================================
@app.route("/", methods=["GET"])
def landing_root():
    return redirect(url_for("select_company"))

@app.route("/select_company", methods=["GET"])
def select_company():
    clients = get_clients()
    return render_template("select_company.html", clients=clients)

@app.route("/c/<client_id>/pricing", methods=["GET"])
def pricing(client_id):
    client = get_client(client_id) or {"client_id": client_id, "business_name": client_id}
    cfg = get_client_config(client_id)
    return render_template("pricing.html", client=client, cfg=cfg)

@app.route("/c/<client_id>/admin/login", methods=["GET", "POST"])
def admin_login(client_id):
    client = get_client(client_id) or {"client_id": client_id, "business_name": client_id}

    next_url = (request.args.get("next") or request.form.get("next") or "").strip()
    if not next_url:
        next_url = url_for("dispatcher", client_id=client_id)

    if request.method == "GET":
        return render_template("admin_login.html", client=client, next=next_url)

    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    attempts = prune_attempts(ip)
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        return render_template("admin_login.html", client=client, next=next_url, error="Too many attempts. Try again later."), 429

    password = (request.form.get("password") or "").strip()
    username = (request.form.get("username") or request.form.get("admin_user") or "admin").strip()

    if _check_login(client_id, username, password):
        session["admin_client_id"] = client_id
        session["admin_user"] = username or "admin"
        return redirect(next_url)

    attempts.append(time.time())
    _login_attempts[ip] = attempts
    return render_template("admin_login.html", client=client, next=next_url, error="Invalid password."), 401

@app.route("/c/<client_id>/admin/logout", methods=["GET", "POST"])
def admin_logout(client_id):
    if session.get("admin_client_id") == client_id:
        session.pop("admin_client_id", None)
        session.pop("admin_user", None)
    return redirect(url_for("admin_login", client_id=client_id))

@app.route("/c/<client_id>/dispatcher")
@login_required
def dispatcher(client_id):
    client = get_client(client_id) or {"client_id": client_id, "business_name": client_id}
    cfg = get_client_config(client_id)
    if not is_client_paid(cfg):
        return redirect(url_for("pricing", client_id=client_id))
    techs = cfg.get("techs", [])
    return render_template("dispatcher.html", client=client, techs=techs, cfg=cfg)

@app.route("/c/<client_id>/chat", methods=["GET"])
def client_chat(client_id):
    session["public_client_id"] = client_id
    client = get_client(client_id) or {"client_id": client_id, "business_name": client_id}
    cfg = get_client_config(client_id)
    return render_template("chat.html", client=client, cfg=cfg)

def _resolve_public_client_id():
    cid = (request.args.get("client_id") or "").strip()
    if cid:
        return cid
    cid = (session.get("public_client_id") or "").strip()
    return cid or None

@app.route("/api/session", methods=["GET"])
def api_session_alias():
    cid = _resolve_public_client_id()
    if not cid:
        return jsonify({"error": "Missing client_id. Visit /c/<client_id>/chat first."}), 400
    return api_session(cid)

@app.route("/api/chat", methods=["POST"])
def api_chat_alias():
    cid = _resolve_public_client_id()
    if not cid:
        return jsonify({"error": "Missing client_id. Visit /c/<client_id>/chat first."}), 400
    return api_chat(cid)

@app.route("/api/reset", methods=["POST"])
def api_reset_alias():
    cid = _resolve_public_client_id()
    if not cid:
        return jsonify({"error": "Missing client_id. Visit /c/<client_id>/chat first."}), 400
    return api_reset(cid)

# =========================================================
# Chat API endpoints
# =========================================================
@app.route("/c/<client_id>/api/reset", methods=["POST"])
def api_reset(client_id):
    _ = get_client_config(client_id)
    sid2, t2 = reset_chat_state(client_id)
    return jsonify({"ok": True, "session_id": sid2, "ticket": t2})

@app.route("/c/<client_id>/api/session", methods=["GET"])
def api_session(client_id):
    sid = get_or_create_session_id()
    sessions = get_sessions(client_id)
    srec = sessions.get(sid) or {"messages": []}

    # ✅ Seed first bot message (must be BEFORE return)
    if not srec.get("messages"):
        push_message(
            srec,
            "bot",
            "Hey — I’m Turbo Dispatch.\nWhat’s going on today? (Example: “outlet sparking” or “toilet overflowing”)"
        )
        sessions[sid] = srec
        save_sessions(client_id, sessions)

    tickets = get_tickets(client_id)
    t = find_draft_ticket_by_session(tickets, sid)

    return jsonify({
        "session_id": sid,
        "messages": srec.get("messages", []),
        "ticket": t
    })


@app.route("/c/<client_id>/api/tickets/<ticket_id>/schedule", methods=["POST"])
@login_required
def api_ticket_schedule_alias(client_id, ticket_id):
    return api_ticket_calendar_upsert(client_id, ticket_id)

# =========================================================
# ✅ Chat intake helper functions (restores original clean flow)
# =========================================================
def _chat_needs_address(t: dict) -> bool:
    return not bool((t.get("address") or "").strip()) or not bool((t.get("address_zip") or "").strip()) or not bool((t.get("address_state") or "").strip())

def _build_address_string(t: dict) -> str:
    street = (t.get("address_street") or "").strip()
    city = (t.get("address_city") or "").strip()
    st = (t.get("address_state") or "").strip()
    z = (t.get("address_zip") or "").strip()
    # Prefer canonical: street, city, ST ZIP
    if street and city and st and z:
        return f"{street}, {city}, {st} {z}".strip()
    return (t.get("address_raw") or "").strip()

def _set_full_address(t: dict, addr: dict):
    t["address_street"] = (addr.get("street") or "").strip()
    t["address_city"] = (addr.get("city") or "").strip()
    t["address_state"] = (addr.get("state") or "").strip()
    t["address_zip"] = (addr.get("zip") or "").strip()
    t["address_raw"] = _build_address_string(t)
    t["address"] = _build_address_string(t)
    t["address_confirmed"] = True
    t["asked_full_address"] = True

def _service_area_block(cfg: dict) -> str:
    sts = cfg.get("service_states_allowlist") or []
    z3 = cfg.get("service_zip3_allowlist") or []
    st_text = ", ".join([s.upper() for s in sts]) if sts else "your area"
    z_text = ", ".join([str(x) for x in z3]) if z3 else ""
    if z_text:
        return f"Right now we only service {st_text} (ZIP prefixes: {z_text})."
    return f"Right now we only service {st_text}."

def ui_card(title: str, body_lines=None, bullets=None, tone="neutral", icon=""):
    """
    Flexible UI payload that chat.js can render as a styled card.
    We include redundant keys (type/kind/variant/tone) so your renderer
    can match whatever schema you already had.
    """
    return {
        "type": "card",
        "kind": "card",
        "variant": tone,
        "tone": tone,
        "title": title,
        "icon": icon,
        "body": "\n".join(body_lines or []),
        "lines": body_lines or [],
        "bullets": bullets or [],
    }

def ui_safety_warning_card():
    return ui_card(
        title="SAFETY WARNING",
        tone="danger",
        icon="⚠️",
        body_lines=[
            "⚠️ Safety warning",
            "If it’s actively sparking, smoking, or you smell burning:",
        ],
        bullets=[
            "Turn OFF the breaker for that circuit",
            "Avoid touching/using the outlet",
            "If fire/smoke is present, call emergency services",
        ],
    )

def ui_urgent_active_hazard_card():
    return ui_card(
        title="URGENT — ACTIVE HAZARD",
        tone="urgent",
        icon="🚨",
        body_lines=[
            "🚨 Urgent — active hazard.",
            "Please turn OFF the breaker now if you haven’t already.",
            "If there’s smoke/fire, call emergency services.",
            "",
            "What’s the service address?",
            "Please send it in this format:",
            "• Street address, City, State ZIP",
            "",
            "Example: 123 Main St, Springfield, IL 62704",
        ],
        bullets=[],
    )

# =========================================================
# ✅ Chat endpoint (restored safety + urgent cards)
# =========================================================
@app.route("/c/<client_id>/api/chat", methods=["POST"])
def api_chat(client_id):
    cfg = get_client_config(client_id)
    data = request.get_json(force=True, silent=True) or {}
    msg = (data.get("message") or "").strip()

    # New chat requested
    if data.get("new_chat") is True:
        sid2, t2 = reset_chat_state(client_id)
        return jsonify({"ok": True, "reset": True, "session_id": sid2, "ticket": t2})

    sid = get_or_create_session_id()
    sessions = get_sessions(client_id)

    # ✅ IMPORTANT: persist the session record (your old code did NOT always save it back)
    srec = sessions.get(sid)
    if not srec:
        srec = {"messages": []}
        sessions[sid] = srec

    t, tickets = ensure_draft_ticket(client_id, sid)

    # Persist user message
    if msg:
        push_message(srec, "user", msg, ui=None, turn_id=None)

    # ✅ ensure track token always exists so we can show tracker at the end
    ensure_track_token(t)

    # If intake already complete, treat as note mode
    if t.get("intake_complete"):
        if msg:
            add_timeline(t, f"Customer note: {msg[:180]}", by="customer")
        return reply_and_persist(
            client_id, tickets, sessions, sid, srec, t,
            ["Got it — I added that note to your ticket. Anything else you want us to know?"]
        )

    # Detect service/urgency from initial description
    if (t.get("service") or "unknown") == "unknown" and msg:
        svc = detect_service(msg)
        t["service"] = svc
        add_timeline(t, f"Service detected: {svc}", by="system")

    if (t.get("urgency") or "normal") == "normal" and msg:
        urg = detect_urgency(cfg, msg)
        t["urgency"] = urg
        add_timeline(t, f"Urgency detected: {urg}", by="system")

    # Electrical hazard flow
    is_electrical = (t.get("service") or "").lower() == "electrical"
    hazard = is_electrical and (has_electrical_hazard(msg) if msg else True)

    # =========================================================
    # SAFETY PROMPT (EXACTLY LIKE YOUR SCREENSHOT)
    # - One bubble
    # - Contains full warning + question
    # =========================================================
        # Electrical hazard flow
    is_electrical = (t.get("service") or "").lower() == "electrical"
    hazard = is_electrical and (has_electrical_hazard(msg) if msg else True)

    # Ask safety check exactly once
    if is_electrical and hazard and not t.get("asked_safety"):
        t["asked_safety"] = True
        t["safety_active"] = None
        add_timeline(t, "Safety check requested (active sparking?)", by="system")

        safety_text = (
            "⚠️ Safety warning\n"
            "If it’s actively sparking, smoking, or you smell burning:\n"
            "• Turn OFF the breaker for that circuit\n"
            "• Avoid touching/using the outlet\n"
            "• If fire/smoke is present, call emergency services\n"
            "\n"
            "Is it actively sparking right now? (yes/no)"
        )

        return reply_and_persist(
            client_id, tickets, sessions, sid, srec, t,
            [safety_text],
            ui_parts=[ui_safety_warning_card()],
        )

    # =========================================================
    # SAFETY ANSWER PARSE (NO MORE “YES TWICE”)
    # =========================================================
    if t.get("asked_safety") and t.get("safety_active") is None:
        ans = parse_yes_no(msg) if msg else None

        if ans is None:
            # ask again but SAME single bubble
            safety_text = (
                "⚠️ **SAFETY WARNING**\n"
                "\n"
                "Quick safety check:\n"
                "Is it actively sparking right now? (yes/no)"
            )
            return reply_and_persist(
                client_id, tickets, sessions, sid, srec, t,
                [safety_text],
                ui_parts=[ui_safety_warning_card()],
            )

        t["safety_active"] = bool(ans)

        if t["safety_active"]:
            t["urgency"] = "urgent"
            add_timeline(t, "Safety confirmed: active hazard", by="system")
        else:
            add_timeline(t, "Safety confirmed: not actively sparking", by="system")

        # ✅ KEY FIX: DO NOT send an empty message with just a UI card.
        # Instead: urgent + address instructions all in ONE bubble (like your screenshot)
        if t["safety_active"]:
            urgent_text = (
                "🚨 **URGENT — ACTIVE HAZARD**\n"
                "🚨 Urgent — active hazard.\n"
                "Please turn OFF the breaker now if you haven’t already.\n"
                "If there’s smoke/fire, call emergency services.\n"
                "\n"
                "**What’s the service address?**\n"
                "Please send it in this format:\n"
                "• Street address, City, State ZIP\n"
                "\n"
                "Example: 123 Main St, Springfield, IL 62704"
            )

            # mark we are now moving into address stage
            t["asked_full_address"] = True

            return reply_and_persist(
                client_id, tickets, sessions, sid, srec, t,
                [urgent_text],
                ui_parts=[ui_urgent_active_hazard_card()],
            )

        # non-active hazard -> proceed to address (single bubble, not 5 bubbles)
        address_text = (
            "What’s the service address?\n"
            "Please send it in this format:\n"
            "• Street address, City, State ZIP\n"
            "\n"
            "Example: 123 Main St, Springfield, IL 62704"
        )
        t["asked_full_address"] = True
        return reply_and_persist(
            client_id, tickets, sessions, sid, srec, t,
            [address_text],
        )

    # =========================================================
    # ADDRESS CAPTURE (single prompt on failure; no spam)
    # =========================================================
    if not t.get("address_confirmed"):
        parsed = parse_full_address(msg) if msg else None

        if not msg or parsed is None:
            t["address_attempts"] = int(t.get("address_attempts") or 0) + (1 if msg else 0)

            if t["address_attempts"] >= 2:
                return reply_and_persist(
                    client_id, tickets, sessions, sid, srec, t,
                    ["I didn’t catch the full address.\nPlease send it like:\n123 Main St, City, ST 12345"],
                )

            return reply_and_persist(
                client_id, tickets, sessions, sid, srec, t,
                ["What’s the service address?\nPlease send it like:\n123 Main St, City, ST 12345"],
            )

        st = parsed.get("state")
        z = parsed.get("zip")
        if not in_service_area(cfg, st, z):
            add_timeline(t, f"Out of service area: {st} {z}", by="system")
            return reply_and_persist(
                client_id, tickets, sessions, sid, srec, t,
                [f"Thanks — it looks like you're in {st} {z}.\nWe may not service that area. Can you confirm the address (or share a nearby ZIP)?"],
            )

        t["address_street"] = parsed.get("street") or ""
        t["address_city"] = parsed.get("city") or ""
        t["address_state"] = parsed.get("state") or ""
        t["address_zip"] = parsed.get("zip") or ""
        t["address_raw"] = msg
        t["address"] = ", ".join([x for x in [
            t["address_street"],
            f"{t['address_city']}, {t['address_state']} {t['address_zip']}".strip()
        ] if x]).strip()

        t["address_confirmed"] = True
        add_timeline(t, "Service location captured (full address)", by="system")

        try:
            _maybe_geocode_and_store(cfg, t)
        except Exception:
            pass

    # =========================================================
    # AVAILABILITY
    # =========================================================
    if not (t.get("availability") or "").strip():
        if not t.get("asked_availability"):
            t["asked_availability"] = True
            return reply_and_persist(
                client_id, tickets, sessions, sid, srec, t,
                ["When would you like us to come? (Example: “Today ASAP”, “Tomorrow morning”, etc.)"]
            )

        if msg:
            t["availability"] = msg
            add_timeline(t, "Availability captured", by="system")

    # =========================================================
    # PHONE
    # =========================================================
    if not (t.get("phone") or "").strip() and not t.get("phone_declined"):
        if not t.get("asked_phone"):
            t["asked_phone"] = True
            return reply_and_persist(
                client_id, tickets, sessions, sid, srec, t,
                ["What’s a good phone number to reach you? (You can also reply “skip”)"]
            )

        if msg:
            if "skip" in msg.lower():
                t["phone_declined"] = True
                add_timeline(t, "Phone declined", by="system")
            else:
                ph = normalize_phone(msg)
                if not ph:
                    return reply_and_persist(
                        client_id, tickets, sessions, sid, srec, t,
                        ["That doesn’t look like a valid phone number. Please send 10 digits, or reply “skip”."]
                    )
                t["phone"] = ph
                add_timeline(t, "Phone captured", by="system")

    # =========================================================
    # INTAKE COMPLETION (include tech + tracker like before)
    # =========================================================
    promote_to_active(t)
    t["intake_complete"] = True  # ✅ THIS is what prevents it from restarting the flow

    add_timeline(t, "Intake completed", by="system")

    if (t.get("status") or "").lower() not in ("open", "scheduled", "en_route", "onsite", "completed", "canceled"):
        t["status"] = "open"

    # ✅ include assignment/tracker (if assigned)
    tech_line = ""
    if (t.get("assigned_tech_name") or "").strip():
        tech_line = f"Technician: {t.get('assigned_tech_name')}\n"

    track_url = build_track_url(t.get("track_token"))

    summary = (
        "Perfect — we’ve got everything we need.\n\n"
        f"Service: {t.get('service') or 'unknown'}\n"
        f"Urgency: {t.get('urgency') or 'normal'}\n"
        f"Address: {t.get('address') or '—'}\n"
        f"Availability: {t.get('availability') or '—'}\n"
        f"Phone: {t.get('phone') or ('declined' if t.get('phone_declined') else '—')}\n"
        f"{tech_line}"
        "\n"
        f"Track your job here:\n{track_url}\n"
        "\n"
        "We’ll get this into dispatch. Anything else you want us to know?"
    )

    return reply_and_persist(client_id, tickets, sessions, sid, srec, t, [summary])

# =========================
# app.py — PART 5 / 6
# =========================

# =========================================================
# Tickets API (admin)
# =========================================================
@app.route("/c/<client_id>/api/tickets", methods=["GET"])
@login_required
@paid_required
def api_tickets(client_id):
    include_deleted = request.args.get("include_deleted") == "1"
    tickets = get_tickets(client_id)
    out = []
    for t in tickets:
        if t.get("deleted") and not include_deleted:
            continue
        ensure_track_token(t)
        _ensure_schedule_fields(t)
        t.setdefault("eta_minutes", None)
        t.setdefault("eta_at", "")
        t.setdefault("tech_notes", [])
        t.setdefault("photos", [])
        t["track_url"] = build_track_url(t.get("track_token"))
        out.append(t)
    save_tickets(client_id, tickets)
    return jsonify(out)

@app.route("/c/<client_id>/api/tickets/<ticket_id>/status", methods=["POST"])
@login_required
@paid_required
def api_ticket_status(client_id, ticket_id):
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get("status") or "").strip().lower()
    if status not in ["open", "scheduled", "en_route", "onsite", "completed", "canceled"]:
        return jsonify({"error": "Invalid status"}), 400

    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t:
        return jsonify({"error": "Ticket not found"}), 404

    ensure_track_token(t)
    t["status"] = status
    add_timeline(t, f"Status set: {status}", by=session.get("admin_user", "system"))

    _status_to_event = {
        "scheduled": "scheduled",
        "en_route": "en_route",
        "onsite": "onsite",
        "completed": "completed",
        "canceled": "canceled",
    }
    ev_key = _status_to_event.get(status)
    if ev_key:
        _notify_and_log(t, ev_key)

    save_tickets(client_id, tickets)
    return jsonify({"ok": True, "ticket": t})

@app.route("/c/<client_id>/api/tickets/<ticket_id>/assign", methods=["POST"])
@login_required
@paid_required
def api_ticket_assign(client_id, ticket_id):
    data = request.get_json(force=True, silent=True) or {}
    tech_id = (data.get("tech_id") or "").strip()

    tickets = get_tickets(client_id)
    cfg = get_client_config(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t:
        return jsonify({"error": "Ticket not found"}), 404

    ensure_track_token(t)

    techs = cfg.get("techs", [])
    tech = next((x for x in techs if x.get("id") == tech_id), None)

    if tech_id and not tech:
        return jsonify({"error": "Tech not found"}), 404

    t["assigned_tech_id"] = tech_id
    t["assigned_tech_name"] = tech.get("name") if tech else ""

    add_timeline(
        t,
        f"Assigned tech: {t['assigned_tech_name'] or 'unassigned'}",
        by=session.get("admin_user", "system"),
    )

    if tech_id:
        _notify_and_log(t, "assigned")

    if tech:
        tech["last_assigned_at"] = now_iso()
        write_json(config_path(client_id), cfg)

    save_tickets(client_id, tickets)
    return jsonify({"ok": True, "ticket": t})

@app.route("/c/<client_id>/api/tickets/<ticket_id>/auto_assign", methods=["POST"])
@login_required
@paid_required
def api_ticket_auto_assign(client_id, ticket_id):
    data = request.get_json(force=True, silent=True) or {}
    force = bool(data.get("force", False))

    cfg = get_client_config(client_id)
    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t:
        return jsonify({"error": "Ticket not found"}), 404
    if t.get("deleted"):
        return jsonify({"error": "Ticket archived"}), 400

    ensure_track_token(t)

    if (t.get("assigned_tech_id") and not force):
        return jsonify({"ok": True, "skipped": True, "reason": "Already assigned. Use force=true to override.", "ticket": t})

    pick = auto_assign_best_tech(cfg, tickets, t)
    if not pick.get("tech_id"):
        return jsonify({"error": pick.get("reason", "No tech available")}), 400

    t["assigned_tech_id"] = pick["tech_id"]
    t["assigned_tech_name"] = pick["tech_name"]

    add_timeline(t, f"Auto-assigned tech: {pick['tech_name']} ({pick['reason']})", by=session.get("admin_user", "system"))

    try:
        update_tech_last_assigned(cfg, pick["tech_id"], ticket_id=t.get("id"))
        write_json(config_path(client_id), cfg)
    except Exception:
        pass

    save_tickets(client_id, tickets)
    return jsonify({"ok": True, "ticket": t, "pick": pick})

@app.route("/c/<client_id>/api/tickets/<ticket_id>/meta", methods=["POST"])
@login_required
@paid_required
def api_ticket_meta(client_id, ticket_id):
    data = request.get_json(force=True, silent=True) or {}
    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t:
        return jsonify({"error": "Ticket not found"}), 404

    ensure_track_token(t)

    if "tags" in data:
        t["tags"] = data.get("tags") or []
    if "internal_notes" in data:
        t["internal_notes"] = data.get("internal_notes") or ""

    add_timeline(t, "Updated internal notes / tags", by=session.get("admin_user", "system"))
    save_tickets(client_id, tickets)
    return jsonify({"ok": True, "ticket": t})

@app.route("/c/<client_id>/api/tickets/<ticket_id>/seen", methods=["POST"])
@login_required
@paid_required
def api_ticket_seen(client_id, ticket_id):
    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t:
        return jsonify({"error": "Ticket not found"}), 404
    if t.get("deleted"):
        return jsonify({"error": "Ticket archived"}), 400

    ensure_track_token(t)
    by = session.get("admin_user", "dispatcher")
    changed = False

    if not t.get("seen_at"):
        t["seen_at"] = now_iso()
        t["seen_by"] = by
        add_timeline(t, "Seen by dispatcher", by=by)
        changed = True

    if changed:
        save_tickets(client_id, tickets)

    return jsonify({"ok": True, "changed": changed, "ticket": t})

@app.route("/c/<client_id>/api/tickets/<ticket_id>/delete", methods=["POST"])
@login_required
@paid_required
def api_ticket_delete(client_id, ticket_id):
    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t:
        return jsonify({"error": "Ticket not found"}), 404

    ensure_track_token(t)
    t["deleted"] = True
    t["deleted_at"] = now_iso()
    t["deleted_by"] = session.get("admin_user", "system")
    add_timeline(t, "Ticket archived", by=session.get("admin_user", "system"))

    save_tickets(client_id, tickets)
    return jsonify({"ok": True})

@app.route("/c/<client_id>/api/tickets/<ticket_id>/restore", methods=["POST"])
@login_required
@paid_required
def api_ticket_restore(client_id, ticket_id):
    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t:
        return jsonify({"error": "Ticket not found"}), 404

    ensure_track_token(t)
    t["deleted"] = False
    t["deleted_at"] = ""
    t["deleted_by"] = ""
    add_timeline(t, "Ticket restored", by=session.get("admin_user", "system"))

    save_tickets(client_id, tickets)
    return jsonify({"ok": True})

@app.route("/c/<client_id>/api/tickets/<ticket_id>/notify_test", methods=["POST"])
@login_required
@paid_required
def api_ticket_notify_test(client_id, ticket_id):
    data = request.get_json(force=True, silent=True) or {}
    event_key = (data.get("event") or "update").strip()

    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t:
        return jsonify({"error": "Ticket not found"}), 404

    ensure_track_token(t)

    ok, info = (False, "notify disabled")
    if NOTIFY_ENABLED:
        ok, info = notify_customer(event_key, t)
        add_timeline(t, f"Notify test ({event_key})", by=session.get("admin_user", "system"), detail=info)
        save_tickets(client_id, tickets)

    return jsonify({"ok": ok, "info": info, "ticket": t})

# =========================================================
# Maps endpoint
# =========================================================
@app.route("/c/<client_id>/api/tickets_map", methods=["GET"])
@login_required
@paid_required
def api_tickets_map(client_id):
    tickets = get_tickets(client_id)
    out = []
    for t in tickets:
        if t.get("deleted"):
            continue
        lat = t.get("lat")
        lng = t.get("lng")
        if lat is None or lng is None:
            continue
        out.append({
            "id": t.get("id"),
            "address": t.get("address") or t.get("address_raw") or "",
            "lat": float(lat),
            "lng": float(lng),
            "urgency": t.get("urgency") or "normal",
            "service": t.get("service") or "unknown",
            "status": t.get("status") or "",
        })
    return jsonify({"ok": True, "tickets": out})

# =========================
# app.py — PART 6 / 6  (RESEND)
# =========================

# =========================================================
# Technician portal UI + API
# =========================================================
@app.route("/c/<client_id>/tech/login", methods=["GET", "POST"])
def tech_login(client_id):
    client = get_client(client_id) or {"client_id": client_id, "business_name": client_id}
    cfg = get_client_config(client_id)

    next_url = (request.args.get("next") or request.form.get("next") or "").strip()
    if not next_url:
        next_url = url_for("tech_portal", client_id=client_id)

    techs = cfg.get("techs", []) or []
    users_map = _get_tech_users_map(cfg)

    if request.method == "GET":
        html = """
        <!doctype html><html><head>
          <meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
          <title>{{client.business_name}} — Tech Login</title>
          <style>
            body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#0b1220;color:#e8eefc;margin:0;padding:24px}
            .card{max-width:520px;margin:0 auto;background:#121b2f;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:18px;box-shadow:0 12px 40px rgba(0,0,0,.35)}
            h1{margin:0 0 12px;font-size:20px}
            label{display:block;margin:10px 0 6px;opacity:.85}
            select,input{width:100%;padding:10px 12px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f1730;color:#e8eefc}
            button{margin-top:14px;width:100%;padding:10px 12px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.08);color:#e8eefc;font-weight:600}
            .err{margin-top:10px;color:#ffb4b4}
            .muted{opacity:.7;font-size:12px;margin-top:10px}
          </style>
        </head><body>
          <div class="card">
            <h1>{{client.business_name}} — Technician Login</h1>
            <form method="post">
              <input type="hidden" name="next" value="{{next_url}}"/>
              <label>Technician</label>
              <select name="tech_id" required>
                <option value="" disabled selected>Select your name…</option>
                {% for t in techs %}
                  {% if t.enabled %}
                    <option value="{{t.id}}">{{t.name}} ({{t.id}})</option>
                  {% endif %}
                {% endfor %}
              </select>
              <label>PIN / Password</label>
              <input type="password" name="pin" autocomplete="current-password" required/>
              <button type="submit">Sign In</button>
              {% if error %}<div class="err">{{error}}</div>{% endif %}
              <div class="muted">Set TECH_USERS in env or config.json (tech_id:pin).</div>
            </form>
          </div>
        </body></html>
        """
        return render_template_string(html, client=client, techs=techs, next_url=next_url, error=None)

    tech_id = (request.form.get("tech_id") or "").strip()
    pin = (request.form.get("pin") or "").strip()
    if not tech_id or not pin:
        return redirect(url_for("tech_login", client_id=client_id, next=next_url))

    expected = users_map.get(tech_id)
    if not expected or pin != expected:
        return render_template_string(
            "<p style='font-family:system-ui;color:#ffb4b4;padding:20px'>Invalid technician or PIN. "
            "<a href='/c/{{cid}}/tech/login'>Try again</a></p>", cid=client_id
        ), 401

    tech = next((x for x in (cfg.get("techs") or []) if x.get("id") == tech_id), None) or {}
    session["tech_client_id"] = client_id
    session["tech_id"] = tech_id
    session["tech_name"] = (tech.get("name") or tech_id)
    return redirect(next_url)

@app.route("/c/<client_id>/tech/logout", methods=["GET", "POST"])
def tech_logout(client_id):
    if session.get("tech_client_id") == client_id:
        session.pop("tech_client_id", None)
        session.pop("tech_id", None)
        session.pop("tech_name", None)
    return redirect(url_for("tech_login", client_id=client_id))

@app.route("/c/<client_id>/tech")
@tech_required
def tech_portal(client_id):
    client = get_client(client_id) or {"client_id": client_id, "business_name": client_id}
    tech = _tech_identity()
    # You likely have a real tech_portal.html — if so, swap render_template_string for render_template.
    return render_template_string(
        """
        <!doctype html><html><head>
          <meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
          <title>{{biz}} — Tech Portal</title>
          <style>
            body{font-family:system-ui;background:#0b1220;color:#e8eefc;margin:0}
            header{padding:18px 20px;border-bottom:1px solid rgba(255,255,255,.10);background:#0f1730}
            a{color:#8ab4ff}
            .wrap{padding:18px 20px}
            .pill{display:inline-block;padding:6px 10px;border-radius:999px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);font-size:13px}
          </style>
        </head><body>
          <header>
            <div style="font-size:18px;font-weight:700">{{biz}} — Tech Portal</div>
            <div style="opacity:.8;margin-top:4px">
              Logged in as <span class="pill">{{name}} ({{tid}})</span>
              &nbsp; • &nbsp;<a href="/c/{{cid}}/tech/logout">Logout</a>
            </div>
          </header>
          <div class="wrap">
            <p>Use your mobile UI to pull jobs via <code>/c/&lt;client_id&gt;/api/tech/my_jobs</code> and post updates.</p>
          </div>
        </body></html>
        """,
        biz=client.get("business_name") or client_id,
        name=tech["tech_name"],
        tid=tech["tech_id"],
        cid=client_id,
    )

def _safe_filename(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = name.strip("._")
    return name or "upload"

@app.route("/c/<client_id>/api/tech/my_jobs", methods=["GET"])
@tech_required
def api_tech_my_jobs(client_id):
    tickets = get_tickets(client_id)
    tech_id = (session.get("tech_id") or "").strip()

    include_closed = request.args.get("include_closed") == "1"
    jobs = []

    for t in tickets:
        if t.get("deleted"):
            continue
        if (t.get("assigned_tech_id") or "").strip() != tech_id:
            continue

        status = (t.get("status") or "").lower()
        if (not include_closed) and status in ("completed", "canceled"):
            continue

        ensure_track_token(t)
        _ensure_schedule_fields(t)
        t.setdefault("eta_minutes", None)
        t.setdefault("eta_at", "")
        t.setdefault("tech_notes", [])
        t.setdefault("photos", [])

        photos = []
        for p in (t.get("photos") or []):
            if not isinstance(p, dict):
                continue
            rel = (p.get("rel_path") or "").strip()
            if rel:
                photos.append({
                    "id": p.get("id") or "",
                    "filename": p.get("filename") or "",
                    "at": p.get("at") or "",
                    "by": p.get("by") or "",
                    "url": f"/c/{client_id}/uploads/{rel}",
                })

        jobs.append({
            "id": t.get("id"),
            "service": t.get("service") or "unknown",
            "urgency": t.get("urgency") or "normal",
            "status": t.get("status") or "open",
            "address": t.get("address") or t.get("address_raw") or "",
            "availability": t.get("availability") or "",
            "phone": t.get("phone") or "",
            "scheduled_start": t.get("scheduled_start") or "",
            "scheduled_end": t.get("scheduled_end") or "",
            "eta_minutes": t.get("eta_minutes"),
            "eta_at": t.get("eta_at") or "",
            "track_url": build_track_url(t.get("track_token")),
            "photos": photos,
        })

    save_tickets(client_id, tickets)
    return jsonify({"ok": True, "jobs": jobs})

@app.route("/c/<client_id>/api/tech/tickets/<ticket_id>/status", methods=["POST"])
@tech_required
def api_tech_ticket_status(client_id, ticket_id):
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get("status") or "").strip().lower()
    if status not in ["open", "scheduled", "en_route", "onsite", "completed", "canceled"]:
        return jsonify({"ok": False, "error": "Invalid status"}), 400

    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t or t.get("deleted"):
        return jsonify({"ok": False, "error": "Ticket not found"}), 404

    tech_id = (session.get("tech_id") or "").strip()
    if (t.get("assigned_tech_id") or "").strip() != tech_id:
        return jsonify({"ok": False, "error": "Not your job"}), 403

    ensure_track_token(t)
    t["status"] = status

    by = f"tech:{session.get('tech_name') or tech_id}"
    add_timeline(t, f"Status set: {status}", by=by)

    _status_to_event = {
        "scheduled": "scheduled",
        "en_route": "en_route",
        "onsite": "onsite",
        "completed": "completed",
        "canceled": "canceled",
    }
    ev_key = _status_to_event.get(status)
    if ev_key:
        _notify_and_log(t, ev_key)

    save_tickets(client_id, tickets)
    return jsonify({"ok": True, "ticket": t})

@app.route("/c/<client_id>/api/tech/tickets/<ticket_id>/eta", methods=["POST"])
@tech_required
def api_tech_ticket_eta(client_id, ticket_id):
    data = request.get_json(force=True, silent=True) or {}
    minutes = data.get("minutes", None)

    if minutes is not None:
        try:
            minutes = int(minutes)
            if minutes < 0:
                minutes = 0
        except Exception:
            return jsonify({"ok": False, "error": "minutes must be an integer or null"}), 400

    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t or t.get("deleted"):
        return jsonify({"ok": False, "error": "Ticket not found"}), 404

    tech_id = (session.get("tech_id") or "").strip()
    if (t.get("assigned_tech_id") or "").strip() != tech_id:
        return jsonify({"ok": False, "error": "Not your job"}), 403

    ensure_track_token(t)
    t["eta_minutes"] = minutes
    t["eta_at"] = now_iso()

    by = f"tech:{session.get('tech_name') or tech_id}"
    add_timeline(t, f"ETA updated: {minutes} minutes" if minutes is not None else "ETA cleared", by=by)

    _notify_and_log(t, "eta")

    save_tickets(client_id, tickets)
    return jsonify({"ok": True, "ticket": t})

@app.route("/c/<client_id>/api/tech/tickets/<ticket_id>/note", methods=["POST"])
@tech_required
def api_tech_ticket_note(client_id, ticket_id):
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Missing text"}), 400

    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t or t.get("deleted"):
        return jsonify({"ok": False, "error": "Ticket not found"}), 404

    tech_id = (session.get("tech_id") or "").strip()
    if (t.get("assigned_tech_id") or "").strip() != tech_id:
        return jsonify({"ok": False, "error": "Not your job"}), 403

    ensure_track_token(t)
    t.setdefault("tech_notes", [])
    note = {"id": new_id("note"), "at": now_iso(), "by": session.get("tech_name") or tech_id, "text": text}
    t["tech_notes"].insert(0, note)

    by = f"tech:{session.get('tech_name') or tech_id}"
    add_timeline(t, "Tech note added", by=by, detail=text[:500])

    save_tickets(client_id, tickets)
    return jsonify({"ok": True, "note": note, "ticket": t})

@app.route("/c/<client_id>/api/tech/tickets/<ticket_id>/photos", methods=["POST"])
@tech_required
def api_tech_ticket_upload(client_id, ticket_id):
    tickets = get_tickets(client_id)
    t = find_ticket(tickets, ticket_id)
    if not t or t.get("deleted"):
        return jsonify({"ok": False, "error": "Ticket not found"}), 404

    tech_id = (session.get("tech_id") or "").strip()
    if (t.get("assigned_tech_id") or "").strip() != tech_id:
        return jsonify({"ok": False, "error": "Not your job"}), 403

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Missing file"}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Empty file"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        return jsonify({"ok": False, "error": f"Unsupported file type {ext}"}), 400

    ensure_client_dirs(client_id)
    os.makedirs(uploads_ticket_dir(client_id, t.get("id")), exist_ok=True)

    safe = _safe_filename(f.filename)
    file_id = new_id("ph")
    fname = f"{file_id}_{safe}"
    abs_path = os.path.join(uploads_ticket_dir(client_id, t.get("id")), fname)
    f.save(abs_path)

    rel_path = f"{t.get('id')}/{fname}"

    entry = {
        "id": file_id,
        "filename": safe,
        "rel_path": rel_path,
        "at": now_iso(),
        "by": session.get("tech_name") or tech_id,
    }
    t.setdefault("photos", []).insert(0, entry)

    by = f"tech:{session.get('tech_name') or tech_id}"
    add_timeline(t, "Photo uploaded", by=by, detail=safe)

    save_tickets(client_id, tickets)
    return jsonify({"ok": True, "photo": entry, "url": f"/c/{client_id}/uploads/{rel_path}", "ticket": t})

# =========================================================
# Stripe Checkout + Webhook (monetization upgrades)
# =========================================================
@app.route("/c/<client_id>/billing/checkout/<plan>", methods=["POST"])
def billing_checkout(client_id, plan):
    plan = (plan or "").lower().strip()
    if plan not in ("starter", "pro"):
        return jsonify({"error": "Invalid plan"}), 400
    if not stripe_enabled():
        return jsonify({"error": "Stripe not configured (missing STRIPE_SECRET_KEY)"}), 400

    price_id = _plan_to_price_id(plan)
    if not price_id:
        return jsonify({"error": "Missing Stripe price id for plan. Set STRIPE_PRICE_STARTER / STRIPE_PRICE_PRO"}), 400

    _stripe_init()

    cfg = get_client_config(client_id)
    customer_id = (cfg.get("billing") or {}).get("customer_id") or ""

    if not customer_id:
        cust = stripe.Customer.create(metadata={"client_id": client_id, "app": APP_NAME})
        customer_id = cust["id"]
        set_client_billing(client_id, customer_id=customer_id, provider="stripe")

    sess = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        client_reference_id=client_id,
        line_items=[{"price": price_id, "quantity": 1}],
        allow_promotion_codes=True,
        success_url=_billing_success_url(client_id) + "?billing=success",
        cancel_url=_billing_cancel_url(client_id) + "?billing=cancel",
        metadata={"client_id": client_id, "plan": plan, "app": APP_NAME},
        subscription_data={"metadata": {"client_id": client_id, "plan": plan, "app": APP_NAME}},
    )
    return jsonify({"ok": True, "checkout_url": sess["url"]})

def _subscription_price_id(sub: dict) -> str:
    try:
        items = (sub.get("items") or {}).get("data") or []
        if not items:
            return ""
        price = (items[0].get("price") or {})
        return (price.get("id") or "").strip()
    except Exception:
        return ""

def _apply_subscription_to_client(client_id: str, sub: dict):
    stripe_status = (sub.get("status") or "").lower().strip()
    price_id = _subscription_price_id(sub)
    plan = _price_id_to_plan(price_id) or ((sub.get("metadata") or {}).get("plan") or DEFAULT_PLAN)

    set_client_billing(
        client_id,
        provider="stripe",
        status=stripe_status,
        plan=plan,
        customer_id=sub.get("customer") or "",
        subscription_id=sub.get("id") or "",
        price_id=price_id,
        current_period_end=_iso_from_unix(sub.get("current_period_end")),
    )

@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    if not stripe_enabled() or not STRIPE_WEBHOOK_SECRET:
        return "Webhook not configured", 400

    _stripe_init()

    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig,
            secret=STRIPE_WEBHOOK_SECRET,
        )
    except Exception:
        return "Invalid signature", 400

    etype = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}

    if etype == "checkout.session.completed":
        sub_id = obj.get("subscription")
        client_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("client_id") or ""
        client_id = (client_id or "").strip()

        if client_id and sub_id:
            try:
                sub = stripe.Subscription.retrieve(sub_id)
                _apply_subscription_to_client(client_id, sub)
            except Exception:
                pass
        return "ok", 200

    if etype in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        sub = obj
        client_id = ((sub.get("metadata") or {}).get("client_id") or "").strip()

        if not client_id:
            try:
                cust = stripe.Customer.retrieve(sub.get("customer"))
                client_id = ((cust.get("metadata") or {}).get("client_id") or "").strip()
            except Exception:
                client_id = ""

        if client_id:
            _apply_subscription_to_client(client_id, sub)
        return "ok", 200

    return "ok", 200

# =========================================================
# Run
# =========================================================
if __name__ == "__main__":
    os.makedirs(BASE_DATA_DIR, exist_ok=True)
    port = int(os.getenv("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True, use_reloader=False, threaded=True)
