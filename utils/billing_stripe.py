import os, json
import stripe
from datetime import datetime, timezone

# Stripe env
STRIPE_SECRET_KEY = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
STRIPE_WEBHOOK_SECRET = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()

# Plan prices (defaults match your pricing page)
PLAN_PRICES = {
    "starter": 29900,  # $299.00
    "pro": 59900,      # $599.00
}

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _write_json(path, data):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def _client_dir(base_data_dir: str, client_id: str) -> str:
    return os.path.join(base_data_dir, client_id)

def _config_path(base_data_dir: str, client_id: str) -> str:
    return os.path.join(_client_dir(base_data_dir, client_id), "config.json")

def stripe_enabled() -> bool:
    return bool(STRIPE_SECRET_KEY)

def init_stripe():
    if not STRIPE_SECRET_KEY:
        raise Exception("Missing STRIPE_SECRET_KEY")
    stripe.api_key = STRIPE_SECRET_KEY

def create_checkout_session(base_url: str, client_id: str, plan: str) -> str:
    """
    Returns Stripe Checkout URL.
    Uses price_data so you don't need to pre-create Products/Prices.
    """
    plan = (plan or "").strip().lower()
    if plan not in PLAN_PRICES:
        raise Exception("Invalid plan")

    init_stripe()

    amount = PLAN_PRICES[plan]
    success = f"{base_url}/c/{client_id}/pricing?paid=1"
    cancel = f"{base_url}/c/{client_id}/pricing?canceled=1"

    session = stripe.checkout.Session.create(
        mode="subscription",
        success_url=success,
        cancel_url=cancel,
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": amount,
                "recurring": {"interval": "month"},
                "product_data": {"name": f"Turbo Desk — {plan.title()}"},
            },
            "quantity": 1,
        }],
        client_reference_id=client_id,
        metadata={
            "client_id": client_id,
            "plan": plan,
        },
        subscription_data={
            "metadata": {"client_id": client_id, "plan": plan}
        }
    )

    return session.url

def _apply_billing_update(base_data_dir: str, client_id: str, updates: dict):
    path = _config_path(base_data_dir, client_id)
    cfg = _read_json(path, {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.setdefault("plan_tier", "starter")
    cfg.setdefault("billing_status", "")
    cfg.setdefault("stripe_customer_id", "")
    cfg.setdefault("stripe_subscription_id", "")
    cfg.setdefault("billing_updated_at", "")

    for k, v in (updates or {}).items():
        cfg[k] = v

    cfg["billing_updated_at"] = now_iso()
    _write_json(path, cfg)

def handle_webhook(raw_body: bytes, sig_header: str, base_data_dir: str) -> str:
    """
    Validates signature and updates client config billing status.
    Returns a short string summary.
    """
    if not STRIPE_WEBHOOK_SECRET:
        raise Exception("Missing STRIPE_WEBHOOK_SECRET")

    init_stripe()

    event = stripe.Webhook.construct_event(
        payload=raw_body,
        sig_header=sig_header,
        secret=STRIPE_WEBHOOK_SECRET,
    )

    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    # Pull metadata client_id/plan from subscription/invoice/session objects
    metadata = obj.get("metadata") or {}
    client_id = (metadata.get("client_id") or "").strip()
    plan = (metadata.get("plan") or "").strip().lower()

    # Checkout Session Completed (good early signal)
    if etype == "checkout.session.completed":
        client_id = client_id or (obj.get("client_reference_id") or "")
        customer = obj.get("customer") or ""
        subscription = obj.get("subscription") or ""

        if client_id:
            _apply_billing_update(base_data_dir, client_id, {
                "billing_status": "paid",
                "stripe_customer_id": customer,
                "stripe_subscription_id": subscription,
                "plan_tier": plan or "starter",
            })
        return f"{etype} ok"

    # Invoice Payment Succeeded = best confirmation
    if etype in ("invoice.payment_succeeded", "invoice.paid"):
        customer = obj.get("customer") or ""
        subscription = obj.get("subscription") or ""

        # invoice lines sometimes contain price info; we rely on metadata if present
        if client_id:
            _apply_billing_update(base_data_dir, client_id, {
                "billing_status": "paid",
                "stripe_customer_id": customer,
                "stripe_subscription_id": subscription,
                "plan_tier": plan or "starter",
            })
        return f"{etype} ok"

    # Payment failed / subscription canceled
    if etype in ("invoice.payment_failed", "customer.subscription.deleted"):
        customer = obj.get("customer") or ""
        subscription = obj.get("id") or obj.get("subscription") or ""

        if client_id:
            _apply_billing_update(base_data_dir, client_id, {
                "billing_status": "past_due" if etype == "invoice.payment_failed" else "canceled",
                "stripe_customer_id": customer,
                "stripe_subscription_id": subscription,
            })
        return f"{etype} ok"

    return f"{etype} ignored"
