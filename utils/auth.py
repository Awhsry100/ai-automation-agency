# utils/auth.py
# ------------------------------------------------------------
# Centralized app user auth + RBAC guards (Flask).
# This does NOT replace your existing admin password gate.
# It adds real users/roles per client: data/<client_id>/users.json
#
# Session keys used by this module:
#   session["user_id"]
#   session["role"]        -> "owner" | "dispatcher" | "tech"
#   session["client_id"]
# ------------------------------------------------------------

from __future__ import annotations

import json
import os
import time
import secrets
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import abort, jsonify, redirect, request, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash


# -----------------------
# Password helpers
# -----------------------

def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return check_password_hash(password_hash, password)
    except Exception:
        return False


# -----------------------
# Storage helpers (users.json per client)
# -----------------------

def _client_dir(base_data_dir: str, client_id: str) -> str:
    if not client_id:
        raise ValueError("client_id required")
    return os.path.join(base_data_dir, client_id)


def users_file(base_data_dir: str, client_id: str) -> str:
    return os.path.join(_client_dir(base_data_dir, client_id), "users.json")


def load_users(base_data_dir: str, client_id: str) -> List[Dict[str, Any]]:
    path = users_file(base_data_dir, client_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_users(base_data_dir: str, client_id: str, users: List[Dict[str, Any]]) -> None:
    os.makedirs(_client_dir(base_data_dir, client_id), exist_ok=True)
    path = users_file(base_data_dir, client_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def find_user_by_email(users: List[Dict[str, Any]], email: str) -> Optional[Dict[str, Any]]:
    if not email:
        return None
    email_norm = email.strip().lower()
    for u in users:
        if (u.get("email") or "").strip().lower() == email_norm:
            return u
    return None


def find_user_by_id(users: List[Dict[str, Any]], user_id: str) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    for u in users:
        if u.get("id") == user_id:
            return u
    return None


def create_user(
    base_data_dir: str,
    client_id: str,
    email: str,
    password: str,
    role: str,
    *,
    name: str = "",
    active: bool = True,
) -> Dict[str, Any]:
    users = load_users(base_data_dir, client_id)
    if find_user_by_email(users, email):
        raise ValueError("Email already exists for this client.")
    user = {
        "id": f"u_{secrets.token_hex(6)}",
        "email": email.strip().lower(),
        "name": name.strip(),
        "password_hash": hash_password(password),
        "role": (role or "").strip().lower(),
        "active": bool(active),
        "created_at": int(time.time()),
        "last_login_at": None,
    }
    users.append(user)
    save_users(base_data_dir, client_id, users)
    return user


# -----------------------
# Session helpers
# -----------------------

def is_logged_in() -> bool:
    return bool(session.get("user_id"))


def current_role() -> str:
    return (session.get("role") or "").strip().lower()


def current_client_id() -> str:
    return (session.get("client_id") or "").strip()


def set_login_session(user: Dict[str, Any], client_id: str) -> None:
    session["user_id"] = user.get("id")
    session["role"] = (user.get("role") or "").strip().lower()
    session["client_id"] = client_id
    session.setdefault("csrf_token", secrets.token_hex(16))


def clear_login_session() -> None:
    # don't nuke legacy admin keys unless you want to; caller can decide
    session.pop("user_id", None)
    session.pop("role", None)
    session.pop("client_id", None)
    session.pop("csrf_token", None)


def wants_json() -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    xrw = (request.headers.get("X-Requested-With") or "").lower()
    if "application/json" in accept:
        return True
    if xrw == "xmlhttprequest":
        return True
    if (request.path or "").startswith("/api/"):
        return True
    return False


def _deny(message: str, status: int = 401):
    if wants_json():
        return jsonify({"ok": False, "error": message}), status
    if status == 401:
        return redirect(url_for("user_login", client_id=current_client_id() or request.view_args.get("client_id"), next=request.path))
    abort(status)


# -----------------------
# Guards (Decorators)
# -----------------------

def require_client(fn: Callable):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_client_id():
            return _deny("No client selected.", 401)
        return fn(*args, **kwargs)
    return wrapper


def login_required(fn: Callable):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_logged_in():
            return _deny("Login required.", 401)
        if not current_client_id():
            return _deny("No client selected.", 401)
        return fn(*args, **kwargs)
    return wrapper


def require_role(role: str):
    role_norm = (role or "").strip().lower()

    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not is_logged_in():
                return _deny("Login required.", 401)
            if not current_client_id():
                return _deny("No client selected.", 401)
            if current_role() != role_norm:
                return _deny("Forbidden.", 403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_any_role(roles: List[str]):
    roles_norm = {(r or "").strip().lower() for r in (roles or []) if (r or "").strip()}

    def decorator(fn: Callable):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not is_logged_in():
                return _deny("Login required.", 401)
            if not current_client_id():
                return _deny("No client selected.", 401)
            if current_role() not in roles_norm:
                return _deny("Forbidden.", 403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# -----------------------
# Credential check helper (for /login route)
# -----------------------

def authenticate_user(
    base_data_dir: str,
    client_id: str,
    email: str,
    password: str,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    if not client_id:
        return False, None, "No client selected."
    if not email or not password:
        return False, None, "Email and password are required."

    users = load_users(base_data_dir, client_id)
    user = find_user_by_email(users, email)
    if not user:
        return False, None, "Invalid credentials."

    if not user.get("active", True):
        return False, None, "Account disabled."

    if not verify_password(password, user.get("password_hash") or ""):
        return False, None, "Invalid credentials."

    try:
        user["last_login_at"] = int(time.time())
        save_users(base_data_dir, client_id, users)
    except Exception:
        pass

    return True, user, ""
